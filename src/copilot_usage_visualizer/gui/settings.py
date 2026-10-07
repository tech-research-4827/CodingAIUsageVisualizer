from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from copilot_usage_visualizer.defaults import AppDefaults
from copilot_usage_visualizer.gui.display_scale import FontSizeChoice, UiScaleChoice


# dashboard のフォーム状態を保持する。
@dataclass
class DashboardSettings:
    """GUI dashboard の設定入力値を保持する mutable data holder。

    dearpygui に依存せず、CLI 初期値の上書きと GUI 入力の保持に使う。
    """

    db_path: Path
    cli_log_roots: tuple[Path, ...]
    chat_storage_roots: tuple[Path, ...]
    report_output: Path
    date_from: str
    date_to: str
    usd_jpy_rate: float
    # 表示倍率 (百分率)。None は自動。起動時だけ適用し、変更は次回起動で反映する。
    ui_scale_percent: int | None = None
    # 文字の大きさ (百分率)。表示倍率に重ねて掛ける。起動時だけ適用し、変更は次回起動で反映する。
    font_size_percent: int = FontSizeChoice.DEFAULT_PERCENT
    # 復元結果は起動時の通知だけに使い、設定ファイルには保存しない。
    startup_messages: tuple[str, ...] = field(default=(), compare=False)

    # 入力元に依らず、保存可能な値を検証する。
    def validate(self) -> None:
        """期間と換算値を検証し、固定メッセージで修正を案内する。"""

        try:
            start: date = date.fromisoformat(self.date_from)
            end: date = date.fromisoformat(self.date_to)
            if start.isoformat() != self.date_from or end.isoformat() != self.date_to:
                raise ValueError
        except ValueError:
            raise ValueError(
                "Dates must be valid YYYY-MM-DD values. Edit the date fields."
            ) from None
        if start > end:
            raise ValueError("Date from must not be after Date to. Edit the date fields.")
        if not math.isfinite(self.usd_jpy_rate) or self.usd_jpy_rate <= 0:
            raise ValueError("USD -> JPY rate must be finite and greater than 0. Edit the rate.")
        UiScaleChoice.validate(self.ui_scale_percent)
        FontSizeChoice.validate(self.font_size_percent)
        paths: tuple[Path, ...] = (
            self.db_path, self.report_output, *self.cli_log_roots, *self.chat_storage_roots,
        )
        for path in paths:
            if "\x00" in str(path):
                raise ValueError("Paths must not contain NUL characters. Edit the path fields.")
        # 改行を含む1つの root を複数行 form の複数 root に変えてしまわない。
        for root in (*self.cli_log_roots, *self.chat_storage_roots):
            if "\n" in str(root) or "\r" in str(root):
                raise ValueError("Root paths must not contain line breaks. Edit the source fields.")

    # 既定値から構築する。
    @classmethod
    def from_defaults(
        cls,
        defaults: AppDefaults | None = None,
        today: date | None = None,
    ) -> DashboardSettings:
        """AppDefaults と起動日から既定の設定を構築する。"""

        resolved_defaults: AppDefaults = defaults if defaults is not None else AppDefaults()
        resolved_today: date = today if today is not None else date.today()
        date_from, date_to = AppDefaults.month_range(resolved_today)
        cli_root: Path = resolved_defaults.cli_log_root()
        chat_root: Path = resolved_defaults.chat_storage_root()
        return cls(
            db_path=resolved_defaults.db_path(),
            cli_log_roots=(cli_root,) if cli_root.is_dir() else (),
            chat_storage_roots=(chat_root,) if chat_root.is_dir() else (),
            report_output=resolved_defaults.report_output(),
            date_from=date_from,
            date_to=date_to,
            usd_jpy_rate=resolved_defaults.DEFAULT_USD_JPY_RATE,
        )

    # Explorer の引用符付きコピーを扱い、空欄を directory に変換しない。
    @staticmethod
    def parse_roots(text: str) -> tuple[Path, ...]:
        """複数行入力を1行1パスとして読み、囲み引用符と前後空白を除く。"""

        roots: list[Path] = []
        for line in text.splitlines():
            value: str = line.strip()
            if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            if value:
                roots.append(Path(value))
        return tuple(roots)
