from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from copilot_usage_visualizer.defaults import AppDefaults


# dashboard のフォーム状態を保持する。
@dataclass
class DashboardSettings:
    """GUI dashboard の設定入力値を保持する mutable data holder。

    dearpygui に依存せず、CLI 初期値の上書きと GUI 入力の保持に使う。
    """

    db_path: Path
    cli_log_root: Path
    chat_storage_root: Path
    report_output: Path
    date_from: str
    date_to: str
    usd_jpy_rate: float

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
        return cls(
            db_path=resolved_defaults.db_path(),
            cli_log_root=resolved_defaults.cli_log_root(),
            chat_storage_root=resolved_defaults.chat_storage_root(),
            report_output=resolved_defaults.report_output(),
            date_from=date_from,
            date_to=date_to,
            usd_jpy_rate=resolved_defaults.DEFAULT_USD_JPY_RATE,
        )
