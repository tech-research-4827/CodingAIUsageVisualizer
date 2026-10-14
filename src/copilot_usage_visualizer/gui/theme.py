from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import dearpygui.dearpygui as dpg

from copilot_usage_visualizer.gui.dpg_support import DpgSupport

# dearpygui の item id。runtime では int、tag 指定時は str になる。
DpgItem = int | str


# theme 構築の結果を保持する。
@dataclass(frozen=True)
class DashboardThemeAssets:
    """構築済みの font handle と item theme handle を保持する。

    font handle が `None` の場合は該当 font を解決できなかった degraded mode を表す。
    `scale` は起動時に確定した表示倍率で、設計値 (100% の px) に掛けて実寸にする。
    """

    body_font: DpgItem | None
    heading_font: DpgItem | None
    mono_font: DpgItem | None
    heading_theme: DpgItem
    body_theme: DpgItem
    primary_button_theme: DpgItem
    caution_button_theme: DpgItem
    scale: float = 1.0
    # 文字の大きさの係数 (百分率 / 100)。文字の描画 size と、それに連動する寸法だけに掛ける。
    font_factor: float = 1.0

    # 設計値の px を表示倍率の実寸にする。
    def px(self, value: float) -> int:
        """幅・高さ・余白の設計値に表示倍率を掛け、整数 px に丸める。"""

        return DashboardTheme.scaled(value, self.scale)

    # 文字の大きさに連動する設計値の px を実寸にする。
    def fpx(self, value: float) -> int:
        """文字の行高・文字数で決める幅の設計値に、表示倍率と文字の大きさを掛ける。"""

        return DashboardTheme.scaled(value, self.scale * self.font_factor)

    # 文字の大きさで伸びる行高の増分 (設計値の px) を返す。
    def font_growth(self, lines: float) -> float:
        """本文 font `lines` 行ぶんが、文字の大きさの係数で増える設計値の px。"""

        return lines * DashboardTheme.FONT_SIZE_BODY * (self.font_factor - 1.0)

    # 数値の桁揃えが有効かを返す。
    @property
    def numeric_alignment(self) -> bool:
        """等幅 font を解決できた場合だけ数値の桁揃えを行う。"""

        return self.mono_font is not None

    # degraded mode の通知メッセージを返す。
    def degraded_messages(self) -> tuple[str, ...]:
        """解決できなかった font に対応する通知メッセージを返す。

        degraded を無言で進めないため、呼び出し側は必ず Log へ出力する。
        """

        messages: list[str] = []
        if self.mono_font is None:
            messages.append("numeric alignment disabled: no monospace font found")
        if self.heading_font is None:
            messages.append("heading size scale disabled: no japanese font found")
        return tuple(messages)


# GUI dashboard の表示設計値と theme 構築を担当する。
class DashboardTheme:
    """余白・type scale・配色の設計値を保持し、dearpygui theme を構築する。

    値はすべて絶対値で設定し、framework 既定値に依存しない。
    """

    # spacing scale。P1（4px baseline grid）に従い、すべて 4 の倍数にする。
    WINDOW_PADDING: tuple[int, int] = (12, 12)
    FRAME_PADDING: tuple[int, int] = (8, 4)
    ITEM_SPACING: tuple[int, int] = (8, 4)
    ITEM_INNER_SPACING: tuple[int, int] = (8, 4)
    CELL_PADDING: tuple[int, int] = (8, 4)
    INDENT_SPACING: int = 20
    SCROLLBAR_SIZE: int = 12
    GRAB_MIN_SIZE: int = 12
    FRAME_ROUNDING: int = 4
    CHILD_ROUNDING: int = 4
    TAB_ROUNDING: int = 4

    # group 間の区切り余白。group 内の行間（ITEM_SPACING.y）より大きくする（P2）。
    GROUP_SPACER_HEIGHT: int = 8

    # 設定欄だけの余白を詰め、最小画面でfooterぶんの領域を確保する。
    FORM_GROUP_SPACER_HEIGHT: int = 4
    # 全タブ共通の1行footer。文字の増分は設定欄の確保分で吸収する。
    STATUS_HEIGHT: int = 32
    STATUS_BAR_HEIGHT: int = 24
    STATUS_PROGRESS_WIDTH: int = 280
    STATUS_BOTTOM_PADDING: int = 4

    # 3行分を表示し、root が増えても操作ボタンへの到達を保つ。
    SOURCE_INPUT_HEIGHT: int = 60
    # 補足表示は固定行を広げず、長い文・パスを約70文字の幅で折り返す。
    SETTINGS_TOOLTIP_WRAP: int = 560

    # type scale。heading / body は同一 font file から size 違いで構築する（P3）。
    FONT_SIZE_BODY: int = 16
    FONT_SIZE_HEADING: int = 20
    FONT_SIZE_MONO: int = 16

    # 行高 / font size の下限比率。framework 既定（18px font で form 1.56 / text 1.22）
    # より余裕を保ちつつ、画面に収まる密度にする。
    # input widget を含む form 行と、text だけの table 行は FramePadding の有無で高さが違う。
    MIN_FORM_ROW_HEIGHT_RATIO: float = 1.75
    MIN_TEXT_ROW_HEIGHT_RATIO: float = 1.40

    # 基準色。文字色は global の mvThemeCol_Text に BODY_TEXT だけを設定する。
    BODY_TEXT: tuple[int, int, int] = (232, 232, 232)
    WINDOW_BG: tuple[int, int, int] = (30, 30, 30)
    HEADING_TEXT: tuple[int, int, int] = (214, 222, 234)
    SEPARATOR: tuple[int, int, int] = (70, 74, 82)

    # 副操作 button の配色。
    BUTTON_BG: tuple[int, int, int] = (58, 58, 62)
    BUTTON_BG_HOVERED: tuple[int, int, int] = (76, 76, 82)
    BUTTON_BG_ACTIVE: tuple[int, int, int] = (48, 48, 52)

    # 主操作 button の配色。hover は BODY_TEXT に対し 4.5:1 を割らない最も明るい値。
    ACCENT_PRIMARY: tuple[int, int, int] = (33, 94, 153)
    ACCENT_PRIMARY_HOVERED: tuple[int, int, int] = (37, 106, 172)
    ACCENT_PRIMARY_ACTIVE: tuple[int, int, int] = (27, 76, 123)

    # 高コスト操作 button の配色。
    ACCENT_CAUTION: tuple[int, int, int] = (130, 80, 21)
    ACCENT_CAUTION_HOVERED: tuple[int, int, int] = (147, 90, 24)
    ACCENT_CAUTION_ACTIVE: tuple[int, int, int] = (106, 65, 17)

    # table の配色。
    TABLE_ROW_BG: tuple[int, int, int] = (32, 32, 34)
    TABLE_ROW_BG_ALT: tuple[int, int, int] = (42, 42, 46)
    TABLE_HEADER_BG: tuple[int, int, int] = (56, 60, 68)
    TOTAL_ROW_BG: tuple[int, int, int] = (44, 62, 82)
    TOTAL_ROW_TEXT: tuple[int, int, int] = (156, 203, 255)

    # セッション使用量タブのグラフ系列の色。dataviz の既定パレットの dark 段から選び、
    # 暗い面 (#1e1e1e) に対して validate_palette.js の全検査を通した組み合わせ。
    # 同じ量は同じ色: token 種別は 4 色、参考値はマゼンタのひし形、上限は灰色の破線
    # (色だけに頼らない)。
    SERIES_CREDITS: tuple[int, int, int] = (57, 135, 229)
    SERIES_REFERENCE: tuple[int, int, int] = (213, 81, 129)
    SERIES_INPUT: tuple[int, int, int] = (144, 133, 233)
    SERIES_UNCACHED: tuple[int, int, int] = (57, 135, 229)
    SERIES_CACHE_READ: tuple[int, int, int] = (217, 89, 38)
    SERIES_CACHE_WRITE: tuple[int, int, int] = (25, 158, 112)
    SERIES_OUTPUT: tuple[int, int, int] = (201, 133, 0)
    SERIES_LIMIT: tuple[int, int, int] = (195, 194, 183)
    # エージェントは並び順にこの順で割り当て、7 番目以降は繰り返す (canvas と同じ 6 色)。
    AGENT_SERIES: tuple[tuple[int, int, int], ...] = (
        (57, 135, 229),
        (217, 89, 38),
        (25, 158, 112),
        (201, 133, 0),
        (144, 133, 233),
        (0, 131, 0),
    )
    # 線は 2px、マーカーは半径 4px (直径 8px)。
    SERIES_LINE_WEIGHT: float = 2.0
    SERIES_MARKER_SIZE: float = 4.0
    # グラフ 1 枚の高さと、破線 1 区間の長さ (x 範囲に対する比率)。
    CHART_HEIGHT: int = 220
    DASH_FRACTION: float = 0.012

    # WCAG 2.1 AA の判定対象となる 文字色 / 背景色 の組。
    CONTRAST_PAIRS: tuple[tuple[tuple[int, int, int], tuple[int, int, int]], ...] = (
        (BODY_TEXT, WINDOW_BG),
        (HEADING_TEXT, WINDOW_BG),
        (BODY_TEXT, BUTTON_BG),
        (BODY_TEXT, BUTTON_BG_HOVERED),
        (BODY_TEXT, BUTTON_BG_ACTIVE),
        (BODY_TEXT, ACCENT_PRIMARY),
        (BODY_TEXT, ACCENT_PRIMARY_HOVERED),
        (BODY_TEXT, ACCENT_PRIMARY_ACTIVE),
        (BODY_TEXT, ACCENT_CAUTION),
        (BODY_TEXT, ACCENT_CAUTION_HOVERED),
        (BODY_TEXT, ACCENT_CAUTION_ACTIVE),
        (BODY_TEXT, TABLE_ROW_BG),
        (BODY_TEXT, TABLE_ROW_BG_ALT),
        (BODY_TEXT, TABLE_HEADER_BG),
        (TOTAL_ROW_TEXT, TOTAL_ROW_BG),
    )

    # 日本語対応 font の候補（OS 別）。body / heading の双方をここから構築する。
    JAPANESE_FONT_CANDIDATES: tuple[str, ...] = (
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/YuGothM.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
    )

    # 等幅 font の候補（OS 別）。数値 cell の桁揃えに使う。
    MONOSPACE_FONT_CANDIDATES: tuple[str, ...] = (
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Monaco.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansMono-Regular.ttf",
        "C:/Windows/Fonts/consola.ttf",
    )

    # 入力欄 / button の高さを返す。
    @classmethod
    def frame_height(cls) -> int:
        """本文 font と FramePadding から widget の高さを求める。"""

        return cls.FONT_SIZE_BODY + cls.FRAME_PADDING[1] * 2

    # 設定 form の 1 行の高さを返す。
    @classmethod
    def form_row_height(cls) -> int:
        """input widget を含む table 行の高さを求める。

        cell 内の widget は framed rectangle のため `FramePadding` が効き、
        さらに table cell 側の `CellPadding` が加わる。
        `gui/layout.py` の設定タブ固定高見積りはこの値を前提に置いている。
        """

        return cls.frame_height() + cls.CELL_PADDING[1] * 2

    # text のみの table 1 行の高さを返す。
    @classmethod
    def text_row_height(cls) -> int:
        """`add_text` だけを持つ table 行の高さを求める。

        text item は framed widget ではないため `FramePadding` は効かず、
        font size に `CellPadding` だけが加わる。model / daily / detail table の
        data 行はこちらに当たる。
        """

        return cls.FONT_SIZE_BODY + cls.CELL_PADDING[1] * 2

    # 見出し 1 行の占有高を返す。
    @classmethod
    def heading_height(cls) -> int:
        """見出し font と前後の ItemSpacing から占有高を求める。"""

        return cls.FONT_SIZE_HEADING + cls.ITEM_SPACING[1] * 2

    # 設計値に表示倍率を掛ける。
    @staticmethod
    def scaled(value: float, scale: float) -> int:
        """設計値 (100% の px) を表示倍率の実寸 px に丸める。"""

        return round(value * scale)

    # theme と font を構築して global へ bind する。
    @classmethod
    def build(cls, scale: float = 1.0, font_factor: float = 1.0) -> DashboardThemeAssets:
        """font registry と theme を構築し、global theme / font を bind する。

        dearpygui context の生成後、layout 構築前に 1 度だけ呼ぶ。
        文字は表示倍率を掛けた大きさで描き直し (拡大による にじみを避ける)、
        余白・角丸も同じ倍率にする。文字の大きさは font だけに重ねて掛ける。
        """

        body_font, heading_font, mono_font = cls._build_fonts(scale * font_factor)
        cls._bind_global_theme(scale)
        if body_font is not None:
            dpg.bind_font(body_font)
        return DashboardThemeAssets(
            body_font=body_font,
            heading_font=heading_font,
            mono_font=mono_font,
            heading_theme=cls._build_text_theme(cls.HEADING_TEXT),
            body_theme=cls._build_text_theme(cls.BODY_TEXT),
            primary_button_theme=cls._build_button_theme(
                cls.ACCENT_PRIMARY, cls.ACCENT_PRIMARY_HOVERED, cls.ACCENT_PRIMARY_ACTIVE
            ),
            caution_button_theme=cls._build_button_theme(
                cls.ACCENT_CAUTION, cls.ACCENT_CAUTION_HOVERED, cls.ACCENT_CAUTION_ACTIVE
            ),
            scale=scale,
            font_factor=font_factor,
        )

    # font を構築する。
    @classmethod
    def _build_fonts(
        cls, scale: float
    ) -> tuple[DpgItem | None, DpgItem | None, DpgItem | None]:
        """body / heading / mono の font handle を `scale` の大きさで構築する（未解決は None）。

        `scale` は表示倍率と文字の大きさの積。
        """

        japanese_path: str | None = cls._resolve_font(cls.JAPANESE_FONT_CANDIDATES)
        mono_path: str | None = cls._resolve_font(cls.MONOSPACE_FONT_CANDIDATES)
        body_font: DpgItem | None = None
        heading_font: DpgItem | None = None
        mono_font: DpgItem | None = None
        with DpgSupport.context(dpg.font_registry()):
            # heading は body と同じ font file から size 違いで構築する。
            if japanese_path is not None:
                body_font = dpg.add_font(japanese_path, cls.scaled(cls.FONT_SIZE_BODY, scale))
                heading_font = dpg.add_font(
                    japanese_path, cls.scaled(cls.FONT_SIZE_HEADING, scale)
                )
            if mono_path is not None:
                mono_font = dpg.add_font(mono_path, cls.scaled(cls.FONT_SIZE_MONO, scale))
        return body_font, heading_font, mono_font

    # global theme を構築して bind する。
    @classmethod
    def _bind_global_theme(cls, scale: float) -> None:
        """表示倍率を掛けた spacing と基準色を持つ global theme を構築して bind する。"""

        def px(value: int) -> int:
            """style の設計値を表示倍率の実寸にする。"""

            return cls.scaled(value, scale)

        with DpgSupport.context(dpg.theme()) as global_theme:
            with DpgSupport.context(dpg.theme_component(dpg.mvAll)):
                # spacing scale（P1）。
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, *map(px, cls.WINDOW_PADDING))
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, *map(px, cls.FRAME_PADDING))
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, *map(px, cls.ITEM_SPACING))
                dpg.add_theme_style(
                    dpg.mvStyleVar_ItemInnerSpacing, *map(px, cls.ITEM_INNER_SPACING)
                )
                dpg.add_theme_style(dpg.mvStyleVar_CellPadding, *map(px, cls.CELL_PADDING))
                dpg.add_theme_style(dpg.mvStyleVar_IndentSpacing, px(cls.INDENT_SPACING))
                dpg.add_theme_style(dpg.mvStyleVar_ScrollbarSize, px(cls.SCROLLBAR_SIZE))
                dpg.add_theme_style(dpg.mvStyleVar_GrabMinSize, px(cls.GRAB_MIN_SIZE))
                # 面の識別（P10）。
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, px(cls.FRAME_ROUNDING))
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, px(cls.CHILD_ROUNDING))
                dpg.add_theme_style(dpg.mvStyleVar_TabRounding, px(cls.TAB_ROUNDING))
                # 基準色。文字色は 1 色に統一し、button 文字色の前提を固定する（P9）。
                dpg.add_theme_color(dpg.mvThemeCol_Text, cls.BODY_TEXT)
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, cls.WINDOW_BG)
                dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram, cls.ACCENT_PRIMARY)
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, cls.WINDOW_BG)
                dpg.add_theme_color(dpg.mvThemeCol_Separator, cls.SEPARATOR)
                dpg.add_theme_color(dpg.mvThemeCol_Button, cls.BUTTON_BG)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, cls.BUTTON_BG_HOVERED)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, cls.BUTTON_BG_ACTIVE)
                # table の配色（P7）。
                dpg.add_theme_color(dpg.mvThemeCol_TableRowBg, cls.TABLE_ROW_BG)
                dpg.add_theme_color(dpg.mvThemeCol_TableRowBgAlt, cls.TABLE_ROW_BG_ALT)
                dpg.add_theme_color(dpg.mvThemeCol_TableHeaderBg, cls.TABLE_HEADER_BG)
        dpg.bind_theme(global_theme)

    # 文字色だけを差し替える item theme を構築する。
    @classmethod
    def _build_text_theme(cls, color: tuple[int, int, int]) -> DpgItem:
        """指定した文字色を持つ item theme を構築する。

        dearpygui の item theme は subtree へ継承されるため、見出し container へ
        heading 用を bind した場合は直下 content へ body 用を bind して打ち消す。
        """

        with DpgSupport.context(dpg.theme()) as text_theme:
            with DpgSupport.context(dpg.theme_component(dpg.mvAll)):
                dpg.add_theme_color(dpg.mvThemeCol_Text, color)
        return text_theme

    # button の塗りを差し替える item theme を構築する。
    @classmethod
    def _build_button_theme(
        cls,
        base: tuple[int, int, int],
        hovered: tuple[int, int, int],
        active: tuple[int, int, int],
    ) -> DpgItem:
        """通常 / hover / 押下の 3 状態を持つ button theme を構築する（P4）。"""

        with DpgSupport.context(dpg.theme()) as button_theme:
            with DpgSupport.context(dpg.theme_component(dpg.mvButton)):
                dpg.add_theme_color(dpg.mvThemeCol_Button, base)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, hovered)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, active)
        return button_theme

    # font path を解決する。
    @classmethod
    def _resolve_font(cls, candidates: tuple[str, ...]) -> str | None:
        """存在する font path を返す（見つからなければ None）。"""

        for candidate in candidates:
            if Path(candidate).exists():
                return candidate
        return None

    # 相対輝度からコントラスト比を求める。
    @staticmethod
    def contrast_ratio(
        foreground: tuple[int, int, int], background: tuple[int, int, int]
    ) -> float:
        """WCAG 2.1 の相対輝度式でコントラスト比を返す。"""

        def relative_luminance(color: tuple[int, int, int]) -> float:
            """sRGB 値から相対輝度を求める。"""

            channels: list[float] = []
            for raw in color:
                value: float = raw / 255.0
                linear: float = (
                    value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
                )
                channels.append(linear)
            return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

        first: float = relative_luminance(foreground)
        second: float = relative_luminance(background)
        brighter: float = max(first, second)
        darker: float = min(first, second)
        return (brighter + 0.05) / (darker + 0.05)
