from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from copilot_usage_visualizer.db.repository import UsageRepository
from copilot_usage_visualizer.ingestion.copilot_cli_events import CopilotCliEventsParser
from copilot_usage_visualizer.ingestion.copilot_cli_history import CliHistoryParser
from copilot_usage_visualizer.ingestion.vscode_chat_sessions import VscodeChatSessionsParser
from copilot_usage_visualizer.normalization.credits import DirectCreditsPolicy
from copilot_usage_visualizer.normalization.usage import (
    CliHistorySnapshot,
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
        cli_log_roots: Sequence[Path] = (),
        chat_storage_roots: Sequence[Path] = (),
        cli_history_db: Path | None = None,
    ) -> ParsedScanResult:
        """指定された source roots から usage records と diagnostics を抽出する。"""

        cli_roots = self.resolve_roots(cli_log_roots, "CLI")
        chat_roots = self.resolve_roots(chat_storage_roots, "Chat")
        history_paths = self.history_paths(cli_roots, cli_history_db)
        if not cli_roots and not chat_roots and cli_history_db is None:
            raise ValueError("At least one source root is required.")

        source_files: list[ParsedSourceFile] = []
        records: list[ParsedUsageRecord] = []
        diagnostics: list[DiagnosticRecord] = []
        cli_parser: CopilotCliEventsParser = CopilotCliEventsParser()
        chat_parser: VscodeChatSessionsParser = VscodeChatSessionsParser()

        history_inputs: list[ParsedHistoryInput] = []
        for history_path in dict.fromkeys(history_paths):
            snapshot = UsageRepository.read_cli_history(history_path)
            if cli_history_db is not None and snapshot.read_failed:
                message: str = CliHistorySnapshot.failure_message(snapshot.error or "") or (
                    "No valid history snapshot was obtained."
                )
                raise ValueError("Explicit CLI history database could not be read. " + message)
            history_records, history_diagnostics, history_input = CliHistoryParser.parse(
                history_path, snapshot
            )
            records.extend(history_records)
            diagnostics.extend(history_diagnostics)
            history_inputs.append(history_input)
            source_files.append(ParsedSourceFile(
                "copilot_cli_history", str(history_path), "session-store.db"
            ))

        seen_events: set[Path] = set()
        for cli_log_root, history_path in zip(cli_roots, history_paths, strict=False):
            for events_path in sorted(cli_log_root.glob("*/events.jsonl")):
                events_path = events_path.resolve()
                if events_path in seen_events:
                    continue
                seen_events.add(events_path)
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

        seen_chat: set[Path] = set()
        for chat_storage_root in chat_roots:
            for chat_path in sorted(chat_storage_root.glob("*/chatSessions/*.jsonl")):
                chat_path = chat_path.resolve()
                if chat_path in seen_chat:
                    continue
                seen_chat.add(chat_path)
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

    # 別名の重複を除き、不正な入力を保存処理より前に検出する。
    @staticmethod
    def resolve_roots(roots: Sequence[Path], source: str) -> tuple[Path, ...]:
        """存在する directory の解決済み path を指定順で返す。"""

        resolved: dict[Path, None] = {}
        for root in roots:
            path: Path = root.resolve()
            if not path.is_dir():
                raise ValueError(f"{source} root is not an existing directory: {root}")
            resolved[path] = None
        return tuple(resolved)

    # 明示入力の曖昧な対応付けを防ぎ、各 root の履歴入力を選ぶ。
    @staticmethod
    def history_paths(
        cli_roots: Sequence[Path], explicit: Path | None
    ) -> tuple[Path, ...]:
        """各 CLI root に対応する履歴 path、または履歴単独入力を返す。"""

        if explicit is not None:
            if len(cli_roots) > 1:
                raise ValueError("--cli-history-db requires at most one distinct CLI root.")
            return (explicit.resolve(),)
        return tuple(root.parent / "session-store.db" for root in cli_roots)

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
