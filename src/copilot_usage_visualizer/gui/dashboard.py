from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import cast

import dearpygui.dearpygui as dpg

from copilot_usage_visualizer.formatting import UsageValueFormatter
from copilot_usage_visualizer.gui.data import DashboardDataProvider
from copilot_usage_visualizer.gui.date_range import (
    DatePickerValue,
    DateRangePresets,
    PickerDate,
)
from copilot_usage_visualizer.gui.display_scale import (
    DisplayScale,
    DisplayScaleResolver,
    FontSizeChoice,
    UiScaleChoice,
)
from copilot_usage_visualizer.gui.dpg_support import DpgSupport
from copilot_usage_visualizer.gui.layout import (
    DashboardLayout,
    DashboardRenderPlan,
    KpiValues,
    TabLayoutSpec,
    TableRenderPlan,
    TableSpec,
)
from copilot_usage_visualizer.gui.session_usage_view import SessionUsageTab
from copilot_usage_visualizer.gui.settings import DashboardSettings
from copilot_usage_visualizer.gui.settings_store import DashboardSettingsStore, SettingsStoreError
from copilot_usage_visualizer.gui.theme import DashboardTheme, DashboardThemeAssets, DpgItem


# Dear PyGui dashboard の描画を担当する。
class DashboardView:
    """report summary / diagnostics をタブで表示する描画専用 view。

    計測・整形ロジックは持たず、`DashboardDataProvider` の戻り値から
    `DashboardRenderPlan` を構築し、その内容を widget へ流す。
    """

    # action button の tag。
    _ACTION_BUTTON_TAGS: tuple[str, ...] = (
        "btn_scan_delta",
        "btn_scan_full",
        "btn_refresh",
        "btn_export",
        "btn_save_settings",
    )

    # ウィンドウ名。製品名に合わせる (package・command 名は従来のまま)。
    _WINDOW_TITLE: str = "Coding AI Usage Visualizer"

    # 以下の幅・高さは 100% の設計値。描画時に表示倍率を掛ける (`_px`)。
    # 設定欄の title 列幅。最長 label が 18px font で収まる幅にする。
    _LABEL_COLUMN_WIDTH: int = 260
    # 日付入力の幅。YYYY-MM-DD の 10 文字に合わせる。
    _DATE_INPUT_WIDTH: int = 140
    # rate 入力の幅。dearpygui は step button 分も width に含めるため、
    # 値の表示幅（"160.00"）と -/+ button の合計を確保する。
    _RATE_INPUT_WIDTH: int = 160
    # 期間行で日付入力と一括指定 button の間に置く余白。
    _PERIOD_PRESET_GAP: int = 16
    # 表示倍率の選択欄の幅。"自動 (250%)" が収まる幅にする。
    _UI_SCALE_COMBO_WIDTH: int = 160
    # 文字の大きさの選択欄の幅。"150%" と選択 button が収まる幅にする。
    _FONT_SIZE_COMBO_WIDTH: int = 84
    # 表示倍率・文字の大きさの行で部品の間に置く余白。最小幅でも文字 150% で収まる幅にする。
    _DISPLAY_GAP: int = 12
    # 表示倍率・文字の大きさの行に常に表示する、再起動が必要な旨。短くして最小幅でも見切れない
    # ようにし、全文は tooltip と保存時の Log に出す。
    _RESTART_NOTE: str = "Restart to apply"
    # pie plot の描画サイズ。equal_aspects のため正方形の描画領域を確保する。
    _PIE_WIDTH: int = 460
    _PIE_HEIGHT: int = 320
    # daily plot の高さ。
    _DAILY_PLOT_HEIGHT: int = 300

    # viewport の既定サイズと最小サイズ。
    _VIEWPORT_WIDTH: int = 1440
    _VIEWPORT_HEIGHT: int = 960
    _VIEWPORT_MIN_WIDTH: int = 1200
    _VIEWPORT_MIN_HEIGHT: int = 800

    def __init__(
        self, settings: DashboardSettings, data_provider: DashboardDataProvider,
        settings_store: DashboardSettingsStore | None = None,
    ) -> None:
        """設定と data provider を保持する。"""

        self._settings: DashboardSettings = settings
        self._data: DashboardDataProvider = data_provider
        self._settings_store: DashboardSettingsStore = (
            settings_store if settings_store is not None else DashboardSettingsStore()
        )
        self._pending: tuple[str, Callable[[], None]] | None = None
        self._theme: DashboardThemeAssets | None = None
        # 起動時に確定する表示倍率。変更の保存は次回起動で反映する。
        self._display_scale: DisplayScale | None = None
        self._running_scale_percent: int | None = settings.ui_scale_percent
        self._running_font_percent: int = settings.font_size_percent
        # date picker で値を書き込む日付入力欄の tag。
        self._picker_target: str | None = None
        # daily plot の軸 lock を次 frame で解除するための予約。
        self._axis_release_pending: bool = False
        # セッション使用量タブ。CLI log root は scan を待たず設定欄の現在値を使う。
        self._session_tab: SessionUsageTab = SessionUsageTab(
            roots=self._current_cli_log_roots,
            log=self._log,
            theme=self._theme_assets,
        )

    # dashboard を起動する。
    def run(self) -> None:
        """Dear PyGui viewport を作成して dashboard を表示する。

        残余高は viewport resize でしか通知されないため、初回描画前に
        同じ helper を 1 度呼んでから resize callback を登録する。
        """

        # Windows の DPI 対応の宣言は context と window を作る前に行う必要がある。
        self._display_scale = DisplayScaleResolver.resolve(
            self._settings.ui_scale_percent, DisplayScaleResolver.detect_system_percent()
        )
        dpg.create_context()
        self._theme = DashboardTheme.build(
            self._display_scale.factor, self._settings.font_size_percent / 100
        )
        self._build_layout()
        # 文字の大きさで設定タブが伸びた分だけ、window の高さも増やす (button と Log を保つ)。
        growth: float = self._theme_assets().font_growth(
            DashboardLayout.settings_font_lines()
        )
        dpg.create_viewport(
            title=self._WINDOW_TITLE,
            width=self._px(self._VIEWPORT_WIDTH),
            height=self._px(self._VIEWPORT_HEIGHT + growth),
            min_width=self._px(self._VIEWPORT_MIN_WIDTH),
            min_height=self._px(self._VIEWPORT_MIN_HEIGHT + growth),
        )
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("primary_window", True)
        self._apply_residual_heights()
        self._session_tab.on_resize()
        dpg.set_viewport_resize_callback(self._on_viewport_resize)
        self._log_degraded_modes()
        for message in self._settings.startup_messages:
            self._log(message)
        self._initial_load()
        self._event_loop()
        dpg.destroy_context()

    # 残余消費 widget の高さを適用する。
    def _apply_residual_heights(self) -> None:
        """現在の viewport 高から各タブの残余消費 widget の高さを設定する。"""

        heights: dict[str, int] = TabLayoutSpec.residual_heights(
            DashboardLayout.TAB_LAYOUTS,
            dpg.get_viewport_height(),
            self._theme_assets().scale,
            self._theme_assets().font_factor,
        )
        for tag, height in heights.items():
            dpg.configure_item(tag, height=height)
        # ネストした panel の余白を引き、viewport resize にも追従させる。
        content_width: int = max(
            self._fpx(self._LABEL_COLUMN_WIDTH),
            dpg.get_viewport_client_width() - self._px(6 * DashboardTheme.WINDOW_PADDING[0]),
        )
        dpg.configure_item("diagnostics_summary_text", wrap=content_width)
        dpg.configure_item("pie_model_names", wrap=content_width)
        dpg.configure_item("breakdown_note", wrap=content_width)
        dpg.configure_item("daily_note", wrap=content_width)
        dpg.configure_item(
            "coverage_text",
            wrap=max(
                self._fpx(self._LABEL_COLUMN_WIDTH),
                content_width - self._px(self._PIE_WIDTH + DashboardTheme.ITEM_SPACING[0]),
            ),
        )

    # viewport resize callback。
    def _on_viewport_resize(self, *_args: object) -> None:
        """resize 時に初期適用と同じ helper を呼ぶ。

        dearpygui は callback へ sender / app_data を渡すが、本 callback は
        現在の viewport 高だけを使うため引数を受け取らない。
        """

        self._apply_residual_heights()
        self._session_tab.on_resize()

    # degraded mode を Log へ出す。
    def _log_degraded_modes(self) -> None:
        """font 未解決による degraded mode を無言にせず Log へ出力する。"""

        for message in self._theme_assets().degraded_messages():
            self._log(message)

    # render loop を回す。
    def _event_loop(self) -> None:
        """frame を描画し、pending action を実行する manual loop。"""

        while dpg.is_dearpygui_running():
            dpg.render_dearpygui_frame()
            self._release_daily_axis_lock()
            self._session_tab.on_frame()
            if self._pending is None:
                continue
            # 無効化したボタンと開始 Log を 1 frame 描画してから実行する。
            dpg.render_dearpygui_frame()
            name, worker = self._pending
            try:
                worker()
            except (OSError, ValueError) as exc:
                self._log(f"{name} error: {exc}")
            finally:
                self._pending = None
                self._set_buttons_enabled(True)
                self._log(f"{name}: 終了")

    # daily plot の軸 lock を解除する。
    def _release_daily_axis_lock(self) -> None:
        """描画後 1 frame 経ってから daily plot の軸 lock を解除する。

        `set_axis_limits` は範囲を設定すると同時に pan / zoom を禁止する。
        更新時だけ全体を収め、その後はユーザが拡大縮小できるようにするため、
        設定した範囲が描画へ反映されてから `set_axis_limits_auto` で
        lock だけを外す。解除後も設定した範囲は保持される。

        範囲は plot が描画された frame でのみ反映されるため、
        非表示タブのまま解除すると範囲が適用されないまま lock だけが外れる。
        plot が可視になるまで解除を保留し、可視になった次の frame で外す。
        """

        if not self._axis_release_pending:
            return
        # 非表示タブの間は lock を保持し、タブを開いた時に範囲が反映されるようにする。
        if not dpg.is_item_visible("daily_plot"):
            return
        self._axis_release_pending = False
        dpg.set_axis_limits_auto("daily_bar_x")
        dpg.set_axis_limits_auto("daily_bar_y")

    # 初期表示を読み込む。
    def _initial_load(self) -> None:
        """起動時に report / diagnostics を読み込む。"""

        try:
            self._settings.validate()
        except ValueError as exc:
            # 起動時の検証通知を既に Log に出している時だけ、重複を抑える。
            if str(exc) not in self._settings.startup_messages:
                self._log(f"load error: {exc}")
            return
        try:
            self._load_and_render()
        except (OSError, ValueError) as exc:
            self._log(f"load error: {exc}")

    # 構築済み theme を返す。
    def _theme_assets(self) -> DashboardThemeAssets:
        """構築済みの theme asset を返す（未構築は `ValueError`）。"""

        if self._theme is None:
            raise ValueError("theme assets are not built yet")
        return self._theme

    # 起動時に確定した表示倍率を返す。
    def _scale_info(self) -> DisplayScale:
        """確定済みの表示倍率を返す（未確定は `ValueError`）。"""

        if self._display_scale is None:
            raise ValueError("display scale is not resolved yet")
        return self._display_scale

    # 文字の大きさに連動する設計値の px を実寸にする。
    def _fpx(self, value: float) -> int:
        """文字数で決める幅の設計値に、表示倍率と文字の大きさを掛ける。"""

        return self._theme_assets().fpx(value)

    # 設計値の px を表示倍率の実寸にする。
    def _px(self, value: float) -> int:
        """幅・高さ・余白の設計値に表示倍率を掛ける。"""

        return self._theme_assets().px(value)

    # layout を構築する。
    def _build_layout(self) -> None:
        """primary window とタブを構築する。"""

        with DpgSupport.context(dpg.window(tag="primary_window")):
            with DpgSupport.context(dpg.tab_bar(callback=self._on_tab_changed)):
                self._build_settings_tab()
                self._build_diagnostics_tab()
                self._build_model_tab()
                self._build_daily_tab()
                self._build_detail_tab()
                self._session_tab.build()
        self._build_date_picker_popup()

    # タブが切り替わった時。
    def _on_tab_changed(self, _sender: DpgItem, _tab: DpgItem) -> None:
        """セッション使用量タブが前面になった時だけ、そのタブに読み込ませる。

        判定はタブ側が tab bar の値で行う (dearpygui の app_data は選ばれたタブ)。
        """

        if self._session_tab.is_active():
            self._session_tab.on_tab_selected()

    # 設定欄の CLI log root の現在値。
    def _current_cli_log_roots(self) -> tuple[Path, ...]:
        """scan していない編集中の値も使う (セッション使用量は DB を使わず記録を直接読むため)。"""

        return DashboardSettings.parse_roots(str(dpg.get_value("in_cli_log_roots")))

    # 設定・アクションタブを構築する。
    def _build_settings_tab(self) -> None:
        """設定入力欄・アクション・実行 Log を構築する。"""

        with DpgSupport.context(dpg.tab(label="設定・アクション")):
            with DpgSupport.context(dpg.child_window(autosize_x=True, autosize_y=True)):
                self._form_group("データソース", self._add_source_rows)
                self._form_group("保存先", self._add_storage_rows)
                self._form_group("集計条件", self._add_condition_rows)
                self._form_group("GUI 設定", self._add_gui_setting_rows)
                self._add_gui_setting_tooltips()
                self._build_action_buttons()
                dpg.add_spacer(height=self._px(DashboardTheme.GROUP_SPACER_HEIGHT))
                self._heading("Log:")
                dpg.add_input_text(
                    tag="log_box",
                    multiline=True,
                    readonly=True,
                    width=-1,
                    height=TabLayoutSpec.MIN_RESIDUAL_HEIGHT,
                )

    # データソース欄を追加する。
    def _add_source_rows(self) -> None:
        """scan 対象 log root の入力欄を追加する。"""

        self._form_roots_row(
            "Copilot CLI log roots", "in_cli_log_roots", self._settings.cli_log_roots
        )
        self._form_roots_row(
            "VS Code Chat storage roots", "in_chat_storage_roots", self._settings.chat_storage_roots
        )
        self._form_row(
            "Source input",
            lambda: dpg.add_text(
                "One path per line; blank disables a source.\n"
                "Data from removed roots stays in the DB until scan (全体読み直し).",
                wrap=0,
            ),
        )

    # 保存先欄を追加する。設定タブの高さを保つため DB と report 出力先を 1 群にまとめる。
    def _add_storage_rows(self) -> None:
        """DB と report 出力先の入力欄を追加する。"""

        self._form_path_row("DB path", "in_db_path", self._settings.db_path)
        self._form_path_row("Report output", "in_report_output", self._settings.report_output)

    # 集計条件欄を追加する。
    def _add_condition_rows(self) -> None:
        """期間と換算 rate の入力欄を追加する。"""

        self._form_row("Period (YYYY-MM-DD)", self._add_period_widgets)
        self._form_row(
            "USD -> JPY rate",
            lambda: dpg.add_input_double(
                tag="in_usd_jpy_rate",
                default_value=self._settings.usd_jpy_rate,
                width=self._fpx(self._RATE_INPUT_WIDTH),
                format="%.2f",
            ),
        )

    # 期間の 1 行。2 行に分けないことで設定タブの固定高を保つ。
    def _add_period_widgets(self) -> None:
        """開始・終了の入力欄、date picker の button、一括指定の button を 1 行に並べる。"""

        with DpgSupport.context(dpg.group(horizontal=True)):
            dpg.add_input_text(
                tag="in_date_from",
                default_value=self._settings.date_from,
                width=self._fpx(self._DATE_INPUT_WIDTH),
            )
            dpg.add_button(
                label="選択", tag="btn_pick_date_from",
                callback=self._on_pick_date, user_data="in_date_from",
            )
            dpg.add_text("-")
            dpg.add_input_text(
                tag="in_date_to",
                default_value=self._settings.date_to,
                width=self._fpx(self._DATE_INPUT_WIDTH),
            )
            dpg.add_button(
                label="選択", tag="btn_pick_date_to",
                callback=self._on_pick_date, user_data="in_date_to",
            )
            dpg.add_spacer(width=self._px(self._PERIOD_PRESET_GAP))
            # 時系列順に並べる (ユーザーの選択)。
            dpg.add_button(
                label="先月", tag="btn_period_last_month",
                callback=self._on_period_preset, user_data=DateRangePresets.last_month,
            )
            dpg.add_button(
                label="今月", tag="btn_period_this_month",
                callback=self._on_period_preset, user_data=DateRangePresets.this_month,
            )
            dpg.add_button(
                label="今日", tag="btn_period_today",
                callback=self._on_period_preset, user_data=DateRangePresets.today,
            )

    # 設定ファイルの保存先と、表示倍率・文字の大きさの欄を追加する。
    def _add_gui_setting_rows(self) -> None:
        """保存先は読み取り専用で示し、表示倍率と文字の大きさは 1 行の選択肢から選ぶ。

        どちらも起動時だけ適用するため、再起動が必要な旨を同じ行に常に表示する。
        1 行にまとめて、設定タブの固定高を保つ。
        """

        self._form_row(
            "Settings file",
            lambda: dpg.add_input_text(
                tag="settings_location", default_value=self._settings_location(),
                readonly=True, width=-1,
            ),
        )
        self._form_row("Display", self._add_display_widgets)

    # 表示倍率と文字の大きさの選択肢を 1 行に並べる。
    def _add_display_widgets(self) -> None:
        """UI scale と Font size の combo、再起動が必要な旨の注記を横に並べる。"""

        auto_percent: int = self._scale_info().auto_percent
        with DpgSupport.context(dpg.group(horizontal=True)):
            dpg.add_text("UI scale")
            dpg.add_combo(
                list(UiScaleChoice.labels(auto_percent)),
                tag="in_ui_scale",
                default_value=UiScaleChoice.label_for(
                    self._settings.ui_scale_percent, auto_percent
                ),
                width=self._fpx(self._UI_SCALE_COMBO_WIDTH),
            )
            dpg.add_spacer(width=self._px(self._DISPLAY_GAP))
            dpg.add_text("Font size")
            dpg.add_combo(
                list(FontSizeChoice.labels()),
                tag="in_font_size",
                default_value=FontSizeChoice.label_for(self._settings.font_size_percent),
                width=self._fpx(self._FONT_SIZE_COMBO_WIDTH),
            )
            dpg.add_spacer(width=self._px(self._DISPLAY_GAP))
            dpg.add_text(self._RESTART_NOTE, tag="display_restart_note")

    # table cell に tooltip を混ぜないよう、form の外で補足を付ける。
    def _add_gui_setting_tooltips(self) -> None:
        """保存先の全文と、表示倍率・文字の大きさが次回起動で反映されることを示す。"""

        wrap: int = self._px(DashboardTheme.SETTINGS_TOOLTIP_WRAP)
        with DpgSupport.context(dpg.tooltip("settings_location")):
            dpg.add_text("Local settings file (not included in usage reports).")
            dpg.add_text(self._settings_location(), wrap=wrap)
        with DpgSupport.context(dpg.tooltip("in_ui_scale")):
            dpg.add_text(
                "Whole-window scale. Saved changes apply after restart.",
                wrap=wrap,
            )
        with DpgSupport.context(dpg.tooltip("in_font_size")):
            dpg.add_text(
                "Text size on top of UI scale. Saved changes apply after restart.", wrap=wrap
            )

    # 設定ファイルの保存先の表示文字列。
    def _settings_location(self) -> str:
        """保存先を求められない環境では、その旨を表示する。"""

        try:
            return str(self._settings_store.path)
        except SettingsStoreError:
            return "Settings location unavailable."

    # 期間の入力欄の横から date picker を開く。
    def _on_pick_date(self, sender: DpgItem, _app_data: object, target: str) -> None:
        """入力欄の日付 (不正なら今日) で date picker を開き、書き込み先を覚える。"""

        self._picker_target = target
        dpg.set_value(
            "date_picker",
            DatePickerValue.to_picker(str(dpg.get_value(target)), date.today()),
        )
        # button の左下に開き、入力欄を隠さない。
        left, _ = dpg.get_item_rect_min(sender)
        _, bottom = dpg.get_item_rect_max(sender)
        dpg.configure_item("date_picker_popup", show=True, pos=(left, bottom))

    # date picker で日を選んだ時。
    def _on_date_picked(self, _sender: DpgItem, app_data: object) -> None:
        """選んだ日を入力欄へ書き込んで閉じる。集計は「GUI report 更新」で反映する。"""

        target: str | None = self._picker_target
        dpg.configure_item("date_picker_popup", show=False)
        if target is None:
            return
        # dearpygui の境界では dict で渡るため、契約どおりの TypedDict として読む。
        dpg.set_value(target, DatePickerValue.from_picker(cast(PickerDate, app_data)))
        self._picker_target = None

    # 一括指定の button。
    def _on_period_preset(
        self, _sender: DpgItem, _app_data: object,
        preset: Callable[[date], tuple[str, str]],
    ) -> None:
        """開始・終了の入力欄だけを変える (ユーザーの選択: 自動では再集計しない)。"""

        date_from, date_to = preset(date.today())
        dpg.set_value("in_date_from", date_from)
        dpg.set_value("in_date_to", date_to)

    # date picker の popup を構築する。
    def _build_date_picker_popup(self) -> None:
        """開始・終了で共用する 1 つの popup。外側を押すと閉じる。"""

        with DpgSupport.context(
            dpg.window(
                tag="date_picker_popup", popup=True, show=False,
                no_title_bar=True, autosize=True,
            )
        ):
            dpg.add_date_picker(tag="date_picker", callback=self._on_date_picked)

    # action button 群を構築する。
    def _build_action_buttons(self) -> None:
        """主操作 / 警告 / 副操作の重み付けを与えた button 群を構築する。"""

        theme: DashboardThemeAssets = self._theme_assets()
        with DpgSupport.context(dpg.group(horizontal=True)):
            dpg.add_button(
                label="scan (差分のみ)", tag="btn_scan_delta", callback=self._on_scan_delta
            )
            dpg.add_button(
                label="scan (全体読み直し)", tag="btn_scan_full", callback=self._on_scan_full
            )
            dpg.add_button(label="GUI report 更新", tag="btn_refresh", callback=self._on_refresh)
            dpg.add_button(label="report HTML export", tag="btn_export", callback=self._on_export)
            # 保存は副操作として既定色で末尾に置き、結果は Log に出す。
            dpg.add_button(
                label="設定を保存", tag="btn_save_settings", callback=self._on_save_settings
            )
        dpg.bind_item_theme("btn_scan_delta", theme.primary_button_theme)
        dpg.bind_item_theme("btn_refresh", theme.primary_button_theme)
        dpg.bind_item_theme("btn_scan_full", theme.caution_button_theme)

    # Diagnosticsタブを構築する。
    def _build_diagnostics_tab(self) -> None:
        """diagnostics の table を構築する。"""

        with DpgSupport.context(dpg.tab(label="Diagnostics")):
            with DpgSupport.context(dpg.child_window(autosize_x=True, autosize_y=True)):
                with DpgSupport.context(
                    dpg.tree_node(label="Diagnostics (全期間 code 別件数)", default_open=True)
                ) as node:
                    self._apply_heading_style(node)
                    summary_text: DpgItem = dpg.add_text("", tag="diagnostics_summary_text")
                    self._apply_body_style(summary_text)
                    self._create_table(DashboardLayout.table_spec("diagnostics_table"))

    # モデル概要タブを構築する。
    def _build_model_tab(self) -> None:
        """KPI panel・model 円グラフ・model table・confidence table を構築する。"""

        with DpgSupport.context(dpg.tab(label="モデルごと概要")):
            with DpgSupport.context(dpg.child_window(autosize_x=True, autosize_y=True)):
                with DpgSupport.context(dpg.group(horizontal=True)):
                    self._build_model_pie()
                    self._build_kpi_panel()
                dpg.add_spacer(height=self._px(DashboardTheme.GROUP_SPACER_HEIGHT))
                with DpgSupport.context(dpg.tree_node(label="Legend model names")):
                    dpg.add_text("", tag="pie_model_names")
                self._table_section("Model summary", "model_table")
                dpg.add_text(UsageValueFormatter.BREAKDOWN_NOTE, tag="breakdown_note", wrap=0)
                self._table_section("Model breakdown", "model_breakdown_table")
                self._table_section("Confidence breakdown", "confidence_table")

    # model 円グラフを構築する。
    def _build_model_pie(self) -> None:
        """AI Credits の内訳を示す正方形の円グラフを構築する。"""

        with DpgSupport.context(
            dpg.plot(
                tag="model_pie_plot",
                label="Known AI Credits by model",
                width=self._px(self._PIE_WIDTH),
                height=self._px(self._PIE_HEIGHT),
                equal_aspects=True,
                no_mouse_pos=True,
            )
        ):
            dpg.add_plot_legend(location=dpg.mvPlot_Location_East, outside=True)
            dpg.add_plot_axis(
                dpg.mvXAxis,
                tag="model_pie_x",
                no_gridlines=True,
                no_tick_marks=True,
                no_tick_labels=True,
            )
            dpg.set_axis_limits("model_pie_x", 0.0, 1.0)
            dpg.add_plot_axis(
                dpg.mvYAxis,
                tag="model_pie_y",
                no_gridlines=True,
                no_tick_marks=True,
                no_tick_labels=True,
            )
            dpg.set_axis_limits("model_pie_y", 0.0, 1.0)

    # KPI panel を構築する。
    def _build_kpi_panel(self) -> None:
        """合計値を円グラフ右の空き領域へ最上位情報として配置する。"""

        placeholder: KpiValues = KpiValues.placeholder()
        with DpgSupport.context(dpg.child_window(width=-1, height=self._px(self._PIE_HEIGHT))):
            self._kpi_entry("集計期間", "kpi_period", placeholder.period)
            self._kpi_entry("JP¥", "kpi_jpy", placeholder.jpy)
            self._kpi_entry("US$", "kpi_usd", placeholder.usd)
            self._kpi_entry("AI Credits", "kpi_credits", placeholder.credits)
            dpg.add_text("", tag="coverage_text", wrap=0)

    # KPI の 1 項目を追加する。
    def _kpi_entry(self, title: str, tag: str, initial: str) -> None:
        """見出しと値の組を縦に並べる。"""

        self._heading(title)
        dpg.add_text(initial, tag=tag)
        dpg.add_spacer(height=self._px(DashboardTheme.GROUP_SPACER_HEIGHT))

    # 日付概要タブを構築する。
    def _build_daily_tab(self) -> None:
        """daily 棒グラフ・daily table を構築する。"""

        with DpgSupport.context(dpg.tab(label="日付ごと概要")):
            with DpgSupport.context(dpg.child_window(autosize_x=True, autosize_y=True)):
                with DpgSupport.context(
                    dpg.plot(
                        tag="daily_plot",
                        label="Known credits converted to JP¥ by day",
                        width=-1,
                        height=self._px(self._DAILY_PLOT_HEIGHT),
                        no_mouse_pos=True,
                    )
                ):
                    dpg.add_plot_legend()
                    dpg.add_plot_axis(dpg.mvXAxis, tag="daily_bar_x")
                    dpg.add_plot_axis(dpg.mvYAxis, label="JP¥", tag="daily_bar_y")
                    # lock 解除後もユーザ操作で負値域へ入らないよう下限を拘束する。
                    dpg.set_axis_limits_constraints("daily_bar_y", 0.0, float("inf"))
                dpg.add_spacer(height=self._px(DashboardTheme.GROUP_SPACER_HEIGHT))
                dpg.add_text(UsageValueFormatter.PRIMARY_MODEL_NOTE, tag="daily_note", wrap=0)
                self._table_section("Daily usage", "daily_table")

    # 詳細タブを構築する。
    def _build_detail_tab(self) -> None:
        """session 単位の detail table を構築する。"""

        with DpgSupport.context(dpg.tab(label="セッションごとの詳細")):
            with DpgSupport.context(dpg.child_window(autosize_x=True, autosize_y=True)):
                self._table_section("Detail", "detail_table")

    # 見出し + table の section を構築する。
    def _table_section(self, title: str, table_tag: str) -> None:
        """折りたたみ可能な見出し node に table を配置する。"""

        with DpgSupport.context(dpg.tree_node(label=title, default_open=True)) as node:
            self._apply_heading_style(node)
            self._create_table(DashboardLayout.table_spec(table_tag))

    # spec から table を構築する。
    def _create_table(self, spec: TableSpec) -> DpgItem:
        """`TableSpec` の設定で table を構築する。

        残余消費 table の高さは起動時と resize 時に上書きされるため、
        ここでは最低実用高で初期化する。
        """

        height: int = self._px(
            TabLayoutSpec.MIN_RESIDUAL_HEIGHT if spec.residual else spec.height
        )
        table: DpgItem = dpg.add_table(
            tag=spec.tag,
            header_row=True,
            resizable=True,
            policy=dpg.mvTable_SizingFixedFit,
            scrollX=True,
            scrollY=True,
            height=height,
            width=-1,
            row_background=True,
            clipper=spec.clipper,
            freeze_columns=spec.freeze_columns,
            borders_innerH=True,
            borders_outerH=True,
            borders_innerV=True,
            borders_outerV=True,
        )
        if spec.restores_body_style:
            self._apply_body_style(table)
        return table

    # 見出し style を適用する。
    def _apply_heading_style(self, item: DpgItem) -> None:
        """見出し item へ heading font と heading 色を bind する。"""

        theme: DashboardThemeAssets = self._theme_assets()
        dpg.bind_item_theme(item, theme.heading_theme)
        if theme.heading_font is not None:
            dpg.bind_item_font(item, theme.heading_font)

    # 本文 style へ復帰させる。
    def _apply_body_style(self, item: DpgItem) -> None:
        """見出し container から継承された style を本文 style へ戻す。"""

        theme: DashboardThemeAssets = self._theme_assets()
        dpg.bind_item_theme(item, theme.body_theme)
        if theme.body_font is not None:
            dpg.bind_item_font(item, theme.body_font)

    # 見出し text を追加する。
    def _heading(self, title: str) -> None:
        """heading style を適用した見出し text を追加する。"""

        self._apply_heading_style(dpg.add_text(title))

    # 見出し + 2 列 form table の group を追加する。
    def _form_group(self, title: str, add_rows: Callable[[], None]) -> None:
        """設定項目を意味単位でまとめた group を追加する。"""

        self._heading(title)
        with DpgSupport.context(
            dpg.table(header_row=False, resizable=False, policy=dpg.mvTable_SizingFixedFit)
        ) as table:
            self._apply_body_style(table)
            dpg.add_table_column(
                init_width_or_weight=self._fpx(self._LABEL_COLUMN_WIDTH), width_fixed=True
            )
            dpg.add_table_column(width_stretch=True)
            add_rows()
        dpg.add_spacer(height=self._px(DashboardTheme.GROUP_SPACER_HEIGHT))

    # form の path 行を追加する。
    def _form_path_row(self, label: str, tag: str, value: object) -> None:
        """title 列 + 入力欄列の path 行を追加する。"""

        self._form_row(
            label,
            lambda: dpg.add_input_text(tag=tag, default_value=str(value), width=-1),
        )

    # root の個数に依存しない高さで複数行を編集する。
    def _form_roots_row(self, label: str, tag: str, roots: tuple[Path, ...]) -> None:
        """複数 root の入力欄を既存の2列 form に追加する。"""

        self._form_row(
            label,
            lambda: dpg.add_input_text(
                tag=tag, default_value="\n".join(str(root) for root in roots),
                multiline=True, width=-1, height=self._px(
                    DashboardTheme.SOURCE_INPUT_HEIGHT + self._theme_assets().font_growth(3)
                ),
            ),
        )

    # form 行を追加する。
    def _form_row(self, label: str, add_widget: Callable[[], object]) -> None:
        """title を左列、widget を右列に置く form 行を追加する。"""

        with DpgSupport.context(dpg.table_row()):
            dpg.add_text(label)
            add_widget()

    # widget 値を settings に反映する。
    def _sync_settings(self) -> None:
        """入力欄の現在値を DashboardSettings へ反映する。"""

        db_text: str = str(dpg.get_value("in_db_path"))
        report_text: str = str(dpg.get_value("in_report_output"))
        if not db_text.strip() or not report_text.strip():
            raise ValueError("DB path and Report output must not be blank. Edit the path fields.")
        self._settings.db_path = Path(db_text)
        self._settings.cli_log_roots = DashboardSettings.parse_roots(
            str(dpg.get_value("in_cli_log_roots"))
        )
        self._settings.chat_storage_roots = DashboardSettings.parse_roots(
            str(dpg.get_value("in_chat_storage_roots"))
        )
        self._settings.report_output = Path(report_text)
        self._settings.date_from = str(dpg.get_value("in_date_from"))
        self._settings.date_to = str(dpg.get_value("in_date_to"))
        self._settings.usd_jpy_rate = float(dpg.get_value("in_usd_jpy_rate"))
        self._settings.ui_scale_percent = UiScaleChoice.parse_label(
            str(dpg.get_value("in_ui_scale"))
        )
        self._settings.font_size_percent = FontSizeChoice.parse_label(
            str(dpg.get_value("in_font_size"))
        )

    # action button の有効/無効を切り替える。
    def _set_buttons_enabled(self, enabled: bool) -> None:
        """全 action button の enabled 状態を切り替える。"""

        for tag in self._ACTION_BUTTON_TAGS:
            dpg.configure_item(tag, enabled=enabled)

    # Log に追記する。
    def _log(self, message: str) -> None:
        """実行 Log textbox に 1 メッセージを追記する。"""

        existing: str = dpg.get_value("log_box") or ""
        dpg.set_value("log_box", f"{existing}{message}\n")

    # JSON を整形する。
    def _pretty(self, payload: object) -> str:
        """結果 dict を読みやすい JSON 文字列に整形する。"""

        return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)

    # action を予約する。
    def _request_action(self, name: str, worker: Callable[[], None]) -> None:
        """二重実行を防ぎつつ action を予約し、ボタンを無効化する。"""

        if self._pending is not None:
            self._log(f"{name}: 実行中のため無視しました")
            return
        try:
            self._sync_settings()
        except ValueError as exc:
            self._log(f"{name} error: {exc}")
            return
        self._set_buttons_enabled(False)
        self._log(f"{name}: 開始")
        self._pending = (name, worker)

    # 保存はアクション予約で二重実行を防ぐが scan は呼ばない。
    def _on_save_settings(self) -> None:
        """現在の入力の保存を予約する。"""

        self._request_action("Save settings", self._do_save_settings)

    # 完成した設定を置き換えた時だけ表示値も保存値に合わせる。
    def _do_save_settings(self) -> None:
        """全入力を保存し、成功・失敗を Log へ表示する。"""

        try:
            saved: DashboardSettings = self._settings_store.save(self._settings)
        except (SettingsStoreError, ValueError) as exc:
            message: str = str(exc)
        else:
            self._settings = saved
            dpg.set_value("in_db_path", str(saved.db_path))
            dpg.set_value("in_report_output", str(saved.report_output))
            dpg.set_value("in_cli_log_roots", "\n".join(str(root) for root in saved.cli_log_roots))
            dpg.set_value(
                "in_chat_storage_roots", "\n".join(str(root) for root in saved.chat_storage_roots),
            )
            message = "Settings saved."
            # 文字・余白は起動時に作るため、倍率・文字の大きさの変更は再起動まで反映されない。
            if saved.ui_scale_percent != self._running_scale_percent:
                message += " UI scale applies after restart."
            if saved.font_size_percent != self._running_font_percent:
                message += " Font size applies after restart."
        self._log(message)

    # report 更新 callback。
    def _on_refresh(self) -> None:
        """設定を反映して全タブを更新する。"""

        self._request_action("GUI report 更新", self._load_and_render)

    # delta scan callback。
    def _on_scan_delta(self) -> None:
        """差分 scan を実行して更新する。"""

        self._request_action("scan (差分のみ)", lambda: self._do_scan(full=False))

    # full scan callback。
    def _on_scan_full(self) -> None:
        """全体読み直し scan を実行して更新する。"""

        self._request_action("scan (全体読み直し)", lambda: self._do_scan(full=True))

    # export callback。
    def _on_export(self) -> None:
        """HTML report を export して Log を更新する。"""

        self._request_action("report HTML export", self._do_export)

    # scan を実行する。
    def _do_scan(self, full: bool) -> None:
        """scan を実行し、結果を Log に出して再描画する。"""

        result = self._data.rescan(self._settings, full=full)
        self._log(
            "scan:\n"
            + self._pretty(
                {
                    "records_count": result.records_count,
                    "diagnostics_count": result.diagnostics_count,
                    "history_warnings": result.history_warnings,
                    "scan_id": result.scan_id,
                }
            )
        )
        self._load_and_render()

    # export を実行する。
    def _do_export(self) -> None:
        """HTML report を export し、結果を Log に出す。"""

        output_path = self._data.export(self._settings)
        self._log(f"export:\n{self._pretty({'output': str(output_path)})}")

    # 全表示を更新する。
    def _load_and_render(self) -> None:
        """report / diagnostics を読み込み、plan 構築後にまとめて再描画する。

        plan 構築が失敗した場合は widget を一切変更しないため、
        一部だけ新しい表示になる中間状態が発生しない。
        """

        loaded = self._data.load(self._settings)
        started: float = time.perf_counter()
        plan: DashboardRenderPlan = DashboardRenderPlan.build(
            report=self._as_dict(loaded["report"]),
            table_specs=DashboardLayout.TABLE_SPECS,
            numeric_alignment=self._theme_assets().numeric_alignment,
            date_from=self._settings.date_from,
            date_to=self._settings.date_to,
        )
        self._apply_plan(plan)
        elapsed_ms: float = (time.perf_counter() - started) * 1000.0
        self._log(f"render: {elapsed_ms:.1f} ms")

    # 描画計画を widget へ適用する。
    def _apply_plan(self, plan: DashboardRenderPlan) -> None:
        """構築済み plan の内容を table / chart / KPI へ反映する。"""

        for table_plan in plan.tables:
            self._render_table(table_plan)
        dpg.set_value("diagnostics_summary_text", plan.diagnostics_summary)
        dpg.set_value("coverage_text", plan.diagnostics_summary)
        dpg.set_value("pie_model_names", plan.pie_model_names)
        dpg.set_value("kpi_period", plan.kpi.period)
        dpg.set_value("kpi_jpy", plan.kpi.jpy)
        dpg.set_value("kpi_usd", plan.kpi.usd)
        dpg.set_value("kpi_credits", plan.kpi.credits)
        self._render_model_pie(plan)
        self._render_daily_bar(plan)

    # table を再描画する。
    def _render_table(self, plan: TableRenderPlan) -> None:
        """plan に従って column / row / Total 行 style を再構築する。"""

        theme: DashboardThemeAssets = self._theme_assets()
        dpg.delete_item(plan.tag, children_only=True)
        dpg.configure_item(plan.tag, freeze_rows=plan.freeze_rows)
        numeric_columns: tuple[int, ...] = plan.numeric_font_columns
        for index, header in enumerate(plan.headers):
            # 数値列は content 幅へ固定し、桁揃えが resize で崩れないようにする。
            dpg.add_table_column(
                label=header, parent=plan.tag, no_resize=index in plan.numeric_columns
            )
        for row_index, row in enumerate(plan.cells):
            is_total: bool = row_index in plan.total_row_indices
            with DpgSupport.context(dpg.table_row(parent=plan.tag)):
                for column_index, value in enumerate(row):
                    cell: DpgItem = self._add_cell(value, is_total)
                    if column_index in numeric_columns and theme.mono_font is not None:
                        dpg.bind_item_font(cell, theme.mono_font)
        for row_index in plan.total_row_indices:
            dpg.highlight_table_row(plan.tag, row_index, DashboardTheme.TOTAL_ROW_BG)

    # cell を追加する。
    def _add_cell(self, value: str, is_total: bool) -> DpgItem:
        """cell text を追加する。Total 行は専用文字色を per-item で指定する。"""

        if is_total:
            return dpg.add_text(value, color=DashboardTheme.TOTAL_ROW_TEXT)
        return dpg.add_text(value)

    # model 円グラフを再描画する。
    def _render_model_pie(self, plan: DashboardRenderPlan) -> None:
        """plan の pie series を再構築する。"""

        dpg.delete_item("model_pie_y", children_only=True)
        if not plan.pie_values:
            return
        dpg.add_pie_series(
            0.5, 0.5, 0.45, list(plan.pie_values), list(plan.pie_labels), parent="model_pie_y"
        )

    # daily 棒グラフを再描画する。
    def _render_daily_bar(self, plan: DashboardRenderPlan) -> None:
        """plan の bar series を再構築する。

        Y 軸下限 0 は series の有無にかかわらず保証する。
        X 軸 tick も常に reset してから設定し、前回期間の日付ラベルが
        残らないようにする。X / Y 軸の表示範囲も毎回設定するため、
        更新時は必ず対象期間の data 全体が収まる。設定した lock は
        `_release_daily_axis_lock` が次 frame で解除し、以降は
        ユーザが拡大縮小できる状態にする。
        """

        dpg.delete_item("daily_bar_y", children_only=True)
        dpg.reset_axis_ticks("daily_bar_x")
        dpg.set_axis_limits("daily_bar_x", *plan.daily_axis_x_bounds)
        dpg.set_axis_limits("daily_bar_y", 0.0, plan.daily_axis_upper)
        self._axis_release_pending = True
        if not plan.daily_values:
            # 欠落・参照日の説明は棒が無くても残す。
            if plan.daily_ticks:
                dpg.set_axis_ticks("daily_bar_x", plan.daily_ticks)
            return
        if any(plan.daily_unattributed_values):
            half_weight = DashboardRenderPlan.BAR_WEIGHT / 2
            dpg.add_bar_series(
                [x - half_weight / 2 for x in plan.daily_positions],
                [
                    total - unattributed
                    for total, unattributed in zip(
                        plan.daily_values, plan.daily_unattributed_values, strict=True
                    )
                ],
                label="Attributed credits",
                weight=half_weight,
                parent="daily_bar_y",
            )
            dpg.add_bar_series(
                [x + half_weight / 2 for x in plan.daily_positions],
                list(plan.daily_unattributed_values),
                label="Unattributed (incl. estimates)",
                weight=half_weight,
                parent="daily_bar_y",
            )
            dpg.set_axis_ticks("daily_bar_x", plan.daily_ticks)
            return
        dpg.add_bar_series(
            list(plan.daily_positions),
            list(plan.daily_values),
            weight=DashboardRenderPlan.BAR_WEIGHT,
            parent="daily_bar_y",
        )
        dpg.set_axis_ticks("daily_bar_x", plan.daily_ticks)

    # dict へ変換する。
    def _as_dict(self, value: object) -> dict[str, object]:
        """value が dict ならそのまま、そうでなければ空 dict を返す。"""

        return value if isinstance(value, dict) else {}
