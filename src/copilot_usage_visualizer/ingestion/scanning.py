from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from copilot_usage_visualizer.db.repository import UsageRepository
from copilot_usage_visualizer.ingestion.copilot_cli_events import CopilotCliEventsParser
from copilot_usage_visualizer.ingestion.copilot_cli_history import CliHistoryParser
from copilot_usage_visualizer.ingestion.vscode_chat_sessions import VscodeChatSessionsParser
from copilot_usage_visualizer.normalization.credits import DirectCreditsPolicy
from copilot_usage_visualizer.normalization.usage import (
    DiagnosticRecord,
    ParsedHistoryInput,
    ParsedSourceFile,
    ParsedUsageRecord,
)


# scan 結果を表す。
@dataclass(frozen=True)
class ParsedScanResult:
    """scan service が抽出した records と diagnostics を保持する。"""

    records: list[ParsedUsageRecord]
    diagnostics: list[DiagnosticRecord]
    source_files: list[ParsedSourceFile]
    history_inputs: list[ParsedHistoryInput]


# source root の探索と parser 呼び出しを担当する。
class ScanService:
    """Copilot CLI / Chat source root を走査して usage records を抽出する。"""

    # source root を scan する。
    def scan(
        self,
        cli_log_root: Path | None = None,
        chat_storage_root: Path | None = None,
        cli_history_db: Path | None = None,
    ) -> ParsedScanResult:
        """指定された source roots から usage records と diagnostics を抽出する。"""

        if cli_log_root is None and chat_storage_root is None and cli_history_db is None:
            raise ValueError("At least one source root is required.")

        source_files: list[ParsedSourceFile] = []
        records: list[ParsedUsageRecord] = []
        diagnostics: list[DiagnosticRecord] = []
        cli_parser: CopilotCliEventsParser = CopilotCliEventsParser()
        chat_parser: VscodeChatSessionsParser = VscodeChatSessionsParser()

        history_inputs: list[ParsedHistoryInput] = []
        history_path = cli_history_db
        if history_path is None and cli_log_root is not None:
            history_path = cli_log_root.resolve().parent / "session-store.db"
        if history_path is not None:
            snapshot = UsageRepository.read_cli_history(history_path)
            if cli_history_db is not None and snapshot.error == "history_unavailable":
                raise ValueError("Explicit CLI history database could not be read.")
            history_records, history_diagnostics, history_input = CliHistoryParser.parse(
                history_path, snapshot
            )
            records.extend(history_records)
            diagnostics.extend(history_diagnostics)
            history_inputs.append(history_input)
            source_files.append(ParsedSourceFile(
                "copilot_cli_history", str(history_path), "session-store.db"
            ))

        if cli_log_root is not None:
            for events_path in sorted(cli_log_root.glob("*/events.jsonl")):
                try:
                    parsed_records, parsed_diagnostics = cli_parser.parse_file(events_path)
                    source_files.append(
                        ParsedSourceFile("copilot_cli", str(events_path), events_path.name)
                    )
                    records.extend(
                        replace(r, history_scope=CliHistoryParser.scope(history_path))
                        if history_path is not None else r for r in parsed_records
                    )
                    diagnostics.extend(
                        replace(d, history_scope=CliHistoryParser.scope(history_path))
                        if d.code == "cli_session_identity_conflict"
                        and history_path is not None else d
                        for d in parsed_diagnostics
                    )
                except OSError:
                    diagnostics.append(
                        self._source_unreadable_diagnostic("copilot_cli", events_path)
                    )

        if chat_storage_root is not None:
            for chat_path in sorted(chat_storage_root.glob("*/chatSessions/*.jsonl")):
                try:
                    parsed_records, parsed_diagnostics = chat_parser.parse_file(chat_path)
                    records.extend(parsed_records)
                    diagnostics.extend(parsed_diagnostics)
                except OSError:
                    diagnostics.append(
                        self._source_unreadable_diagnostic("copilot_chat", chat_path)
                    )

        records = DirectCreditsPolicy.normalize(records, diagnostics)
        return ParsedScanResult(records, diagnostics, source_files, history_inputs)

    # 読み取り不能 source file の diagnostic を作成する。
    def _source_unreadable_diagnostic(
        self, source: str, source_file_path: Path
    ) -> DiagnosticRecord:
        """raw path を message に含めず、読み取り不能 file を diagnostic 化する。"""

        return DiagnosticRecord(
            source=source,
            source_file_path=str(source_file_path),
            source_file_display_alias=source_file_path.name,
            code="source_unreadable",
            message="Source file could not be read.",
        )
