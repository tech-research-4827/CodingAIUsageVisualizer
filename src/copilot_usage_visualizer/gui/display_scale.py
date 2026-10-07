from __future__ import annotations

import sys
from dataclasses import dataclass


# 表示倍率の選択肢と画面上の表記を扱う。
class UiScaleChoice:
    """保存できる表示倍率 (百分率) と、設定欄の選択肢の表記を対応づける。

    `None` は自動 (OS から求めた倍率) を表す。dearpygui に依存しない。
    """

    # 4K 画面で最小 window (1200x800 の 250%) が収まる範囲に限る。
    PERCENTS: tuple[int, ...] = (100, 125, 150, 175, 200, 225, 250)
    AUTO_LABEL: str = "自動"

    # 設定欄に並べる選択肢を返す。
    @classmethod
    def labels(cls, auto_percent: int) -> tuple[str, ...]:
        """自動の行には現在の自動倍率を併記する。"""

        return (cls.label_for(None, auto_percent), *(f"{percent}%" for percent in cls.PERCENTS))

    # 保存値から選択肢の表記を返す。
    @classmethod
    def label_for(cls, percent: int | None, auto_percent: int) -> str:
        """`None` は自動倍率を併記した自動の表記にする。"""

        if percent is None:
            return f"{cls.AUTO_LABEL} ({auto_percent}%)"
        return f"{percent}%"

    # 選択肢の表記から保存値へ戻す。
    @classmethod
    def parse_label(cls, label: str) -> int | None:
        """自動の表記は `None`、それ以外は百分率に戻す。未知の表記は明示エラーにする。"""

        if label.startswith(cls.AUTO_LABEL):
            return None
        text: str = label.removesuffix("%")
        if not text.isdigit() or int(text) not in cls.PERCENTS:
            raise ValueError("UI scale must be Auto or a listed percentage. Choose a UI scale.")
        return int(text)

    # 保存値を検証する。
    @classmethod
    def validate(cls, percent: int | None) -> None:
        """自動か選択肢の百分率だけを受け入れる。"""

        if percent is not None and percent not in cls.PERCENTS:
            raise ValueError("UI scale must be Auto or a listed percentage. Choose a UI scale.")


# 文字の大きさの選択肢と画面上の表記を扱う。
class FontSizeChoice:
    """表示倍率に重ねる文字の大きさ (百分率) と、設定欄の選択肢の表記を対応づける。

    自動はなく、100 が従来の大きさ。dearpygui に依存しない。
    """

    # 上限は最小画面 (window 高 800px) で設定タブの操作 button が見える範囲に限る。
    # 実測: 115% で button 下端が 715px、120% で 732px (可視 736px) と余白が無くなる。
    PERCENTS: tuple[int, ...] = (100, 115, 125, 150)
    DEFAULT_PERCENT: int = 100

    # 設定欄に並べる選択肢を返す。
    @classmethod
    def labels(cls) -> tuple[str, ...]:
        """選択肢の表記を小さい順に返す。"""

        return tuple(cls.label_for(percent) for percent in cls.PERCENTS)

    # 保存値から選択肢の表記を返す。
    @staticmethod
    def label_for(percent: int) -> str:
        """百分率の表記にする。"""

        return f"{percent}%"

    # 選択肢の表記から保存値へ戻す。
    @classmethod
    def parse_label(cls, label: str) -> int:
        """百分率に戻す。未知の表記は明示エラーにする。"""

        text: str = label.removesuffix("%")
        if not text.isdigit() or int(text) not in cls.PERCENTS:
            raise ValueError("Font size must be a listed percentage. Choose a font size.")
        return int(text)

    # 保存値を検証する。
    @classmethod
    def validate(cls, percent: int) -> None:
        """選択肢の百分率だけを受け入れる。"""

        if isinstance(percent, bool) or percent not in cls.PERCENTS:
            raise ValueError("Font size must be a listed percentage. Choose a font size.")


# 起動時に確定した表示倍率。
@dataclass(frozen=True)
class DisplayScale:
    """GUI 全体の文字・余白・幅に掛ける係数と、自動の場合の倍率を保持する。"""

    factor: float
    auto_percent: int


# 保存値と OS の倍率から起動時の表示倍率を決める。
class DisplayScaleResolver:
    """Windows だけ OS の倍率を自動判定し、他の OS の自動は 100% とする。

    WSLg は X に 96dpi だけを報告し Windows の倍率を取得できないため、
    自動判定は Windows に限り、それ以外は保存した固定倍率で指定する。
    """

    # Windows の 100% に当たる dpi。
    BASE_DPI: int = 96
    # SetProcessDpiAwareness の PROCESS_SYSTEM_DPI_AWARE。
    _SYSTEM_DPI_AWARE: int = 1

    # 保存値と自動倍率から係数を決める。
    @classmethod
    def resolve(cls, percent: int | None, auto_percent: int) -> DisplayScale:
        """固定倍率があればそれを、なければ自動倍率を係数にする。"""

        UiScaleChoice.validate(percent)
        chosen: int = auto_percent if percent is None else percent
        return DisplayScale(factor=chosen / 100.0, auto_percent=auto_percent)

    # OS の倍率を百分率で返す。
    @classmethod
    def detect_system_percent(cls) -> int:
        """Windows ではプロセスを DPI 対応にしてから system DPI を読む。

        DPI 非対応のままだと OS が描画済みの画面を引き伸ばし文字がにじむため、
        固定倍率を選んだ場合も含めて Windows では必ず宣言する。dearpygui の context を
        作る前に 1 度だけ呼ぶ。宣言が拒否された場合 (実行ファイルの manifest で既に
        決まっている等) は、OS が報告する dpi をそのまま使う。
        """

        if sys.platform == "win32":
            import ctypes

            # 戻り値の HRESULT は使わない。拒否時も報告される dpi が実際の描画倍率になる。
            ctypes.windll.shcore.SetProcessDpiAwareness(cls._SYSTEM_DPI_AWARE)
            dpi: int = int(ctypes.windll.user32.GetDpiForSystem())
            return round(dpi * 100 / cls.BASE_DPI)
        return 100
