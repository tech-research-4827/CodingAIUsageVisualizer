from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from copilot_usage_visualizer.app import UsageApplication
from copilot_usage_visualizer.db.repository import StoredScanResult
from copilot_usage_visualizer.gui.settings import DashboardSettings


class _UsageApplicationFacade(Protocol):
    """DashboardDataProvider が利用する application facade の最小 contract。"""

    def report_summary(
        self, db_path: Path, date_from: str, date_to: str, usd_jpy_rate: float
    ) -> dict[str, object]: ...

    def scan(
        self,
        db_path: Path,
        cli_log_roots: Sequence[Path],
        chat_storage_roots: Sequence[Path],
        full: bool = False,
    ) -> StoredScanResult: ...

    def export_html(
        self,
        db_path: Path,
        date_from: str,
        date_to: str,
        output_path: Path,
        usd_jpy_rate: float,
    ) -> Path: ...


# dashboard のデータ取得を担当する。
class DashboardDataProvider:
    """GUI dashboard のデータ操作を `UsageApplication` facade 経由で実行する。

    dearpygui に依存せず、計測・整形ロジックは facade / analytics 層に委ねる。
    """

    def __init__(self, application: _UsageApplicationFacade | None = None) -> None:
        """facade dependency を保持する。"""

        self._application: _UsageApplicationFacade = (
            application if application is not None else UsageApplication()
        )

    # report / reference を読み込む。
    def load(self, settings: DashboardSettings) -> dict[str, object]:
        """期間・rate 設定で report summary と diagnostics を取得する。"""

        report: dict[str, object] = self._application.report_summary(
            settings.db_path,
            settings.date_from,
            settings.date_to,
            settings.usd_jpy_rate,
        )
        return {"report": report}

    # scan を実行する。
    def rescan(self, settings: DashboardSettings, full: bool) -> StoredScanResult:
        """設定された source root を scan する（full=True で全体読み直し）。"""

        return self._application.scan(
            settings.db_path,
            settings.cli_log_roots,
            settings.chat_storage_roots,
            full=full,
        )

    # HTML report を export する。
    def export(self, settings: DashboardSettings):
        """設定された出力先へ offline HTML report を生成する。"""

        self._ensure_parent(settings.report_output)
        return self._application.export_html(
            settings.db_path,
            settings.date_from,
            settings.date_to,
            settings.report_output,
            settings.usd_jpy_rate,
        )

    # 親 directory を作成する。
    def _ensure_parent(self, file_path) -> None:
        """出力 file の親 directory が無ければ作成する。"""

        parent = file_path.parent
        if parent and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
