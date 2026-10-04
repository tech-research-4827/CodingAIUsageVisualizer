from __future__ import annotations

from datetime import date
from pathlib import Path

from copilot_usage_visualizer.analytics.summary import SummaryService
from copilot_usage_visualizer.db.repository import StoredScanResult, UsageRepository
from copilot_usage_visualizer.ingestion.scanning import ScanService
from copilot_usage_visualizer.report.html import HtmlReportService
from copilot_usage_visualizer.report.json_report import JsonReportService
from copilot_usage_visualizer.report.markdown import MarkdownReportService


# 日付引数の validation を担当する。
class DateRangeValidator:
    """CLI date range 引数を検証する。"""

    # ISO date 文字列を検証する。
    @staticmethod
    def validate_date(value: str) -> str:
        """YYYY-MM-DD 形式の実在日なら value を返す。"""

        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise ValueError("date must use YYYY-MM-DD format.")
        date.fromisoformat(value)
        return value


# CLI から呼ばれる application facade。
class UsageApplication:
    """CLI command と core services を接続する。"""

    # scan を実行する。
    def scan(
        self,
        db_path: Path,
        cli_log_root: Path | None,
        chat_storage_root: Path | None,
        full: bool = False,
        cli_history_db: Path | None = None,
    ) -> StoredScanResult:
        """source roots を scan して DB に保存する。

        full=True の場合は parse 後に scan-derived data を reset してから再構築する
        （全体読み直し）。reset と保存は repository 側で同一 transaction にまとめる。
        """

        if cli_history_db is not None and cli_history_db.resolve() == db_path.resolve():
            raise ValueError("Input history and output database must be different files.")
        if (cli_log_root is not None and cli_history_db is None
                and (cli_log_root.resolve().parent / "session-store.db") == db_path.resolve()):
            raise ValueError("Input history and output database must be different files.")
        parsed_scan = ScanService().scan(
            cli_log_root=cli_log_root,
            chat_storage_root=chat_storage_root,
            cli_history_db=cli_history_db,
        )
        root_label: str = self._root_label(cli_log_root, chat_storage_root, cli_history_db)
        return UsageRepository(db_path).store_scan(
            parsed_scan.records,
            parsed_scan.diagnostics,
            root_label,
            full=full,
            source_files=parsed_scan.source_files,
            history_inputs=parsed_scan.history_inputs,
        )

    # summary を生成する。
    def summary(self, db_path: Path, date_from: str, date_to: str) -> dict[str, object]:
        """指定日付範囲の summary dict を返す。"""

        repository: UsageRepository = UsageRepository(db_path)
        return SummaryService(repository).build_summary(date_from, date_to)

    # report summary を生成する。
    def report_summary(
        self, db_path: Path, date_from: str, date_to: str, usd_jpy_rate: float
    ) -> dict[str, object]:
        """HTML report / GUI 共通の report summary dict を返す。"""

        if usd_jpy_rate <= 0:
            raise ValueError("usd-jpy-rate must be greater than 0.")
        validated_from: str = DateRangeValidator.validate_date(date_from)
        validated_to: str = DateRangeValidator.validate_date(date_to)
        repository: UsageRepository = UsageRepository(db_path)
        return SummaryService(repository).build_report_summary(
            validated_from, validated_to, usd_jpy_rate
        )

    # Agent 可読 report を生成する。
    def render_report(
        self,
        db_path: Path,
        date_from: str,
        date_to: str,
        usd_jpy_rate: float,
        report_format: str,
        include_detail: bool,
    ) -> str:
        """report_summary を markdown / json の Agent 可読 report 文字列へ変換する。

        HTML report と同じ data source（build_report_summary）を再利用し、形式のみ
        renderer で切り替える。detail 明細は include_detail=True のときだけ含める。
        """

        summary: dict[str, object] = self.report_summary(db_path, date_from, date_to, usd_jpy_rate)
        if report_format == "json":
            return JsonReportService().render(summary, include_detail=include_detail)
        if report_format == "markdown":
            return MarkdownReportService().render(summary, include_detail=include_detail)
        raise ValueError(f"unsupported report format: {report_format}")

    # HTML report を生成する。
    def export_html(
        self,
        db_path: Path,
        date_from: str,
        date_to: str,
        output_path: Path,
        usd_jpy_rate: float,
    ) -> Path:
        """指定日付範囲の offline HTML report を生成する。"""

        summary: dict[str, object] = self.report_summary(db_path, date_from, date_to, usd_jpy_rate)
        return HtmlReportService().render_to_file(summary, output_path)

    # root label を作成する。
    def _root_label(
        self, cli_log_root: Path | None, chat_storage_root: Path | None,
        cli_history_db: Path | None = None,
    ) -> str:
        """scan root の raw path を DB 保存しないため hash 入力用 label を作る。"""

        parts: list[str] = []
        if cli_log_root is not None:
            parts.append(f"cli:{cli_log_root}")
        if chat_storage_root is not None:
            parts.append(f"chat:{chat_storage_root}")
        if cli_history_db is not None:
            parts.append(f"history:{cli_history_db}")
        return "|".join(parts)
