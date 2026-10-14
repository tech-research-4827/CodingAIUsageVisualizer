from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from copilot_usage_visualizer.db.repository import UsageRepository
from copilot_usage_visualizer.ingestion.copilot_cli_history import CliHistoryParser
from copilot_usage_visualizer.ingestion.parallel_parsing import FileParsing, ParseInput
from copilot_usage_visualizer.ingestion.scan_progress import ScanControl, ScanPhase
from copilot_usage_visualizer.ingestion.scan_state import FileChangeDetector
from copilot_usage_visualizer.normalization.credits import DirectCreditsPolicy
from copilot_usage_visualizer.normalization.usage import (
    CliHistorySnapshot,
    DiagnosticRecord,
    FileScanState,
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
        control: ScanControl | None = None,
        parser_workers: int = 1,
        scan_states: Mapping[tuple[str, str], str] | None = None,
    ) -> ParsedScanResult:
        """指定された source roots から usage records と diagnostics を抽出する。"""

        observer: ScanControl = control if control is not None else ScanControl()
        observer.update(ScanPhase.DISCOVERING)
        cli_roots = self.resolve_roots(cli_log_roots, "CLI")
        chat_roots = self.resolve_roots(chat_storage_roots, "Chat")
        history_paths = self.history_paths(cli_roots, cli_history_db)
        if not cli_roots and not chat_roots and cli_history_db is None:
            raise ValueError("At least one source root is required.")

        # 一度だけ探索し、同じpathの履歴対応は最初のrootから引き継ぐ。
        inputs: list[ParseInput] = []
        skipped: int = 0
        for roots, pattern, source in (
            (cli_roots, "*/events.jsonl", "copilot_cli"),
            (chat_roots, "*/chatSessions/*.jsonl", "copilot_chat"),
        ):
            seen: set[Path] = set()
            for index, root in enumerate(roots):
                observer.checkpoint()
                for path in sorted(root.glob(pattern)):
                    observer.checkpoint()
                    path = path.resolve()
                    if path in seen:
                        continue
                    seen.add(path)
                    history: Path | None = history_paths[index] if source == "copilot_cli" else None
                    state: FileScanState | None = FileChangeDetector.capture(
                        path, CliHistoryParser.scope(history) if history is not None else None,
                    ) if source == "copilot_cli" else None
                    if (state is not None and scan_states is not None
                            and scan_states.get((source, FileChangeDetector.path_hash(path)))
                            == state.fingerprint):
                        skipped += 1
                        continue
                    inputs.append(ParseInput(source, path, history, state))
        unique_history: tuple[Path, ...] = tuple(dict.fromkeys(history_paths))
        total: int = len(inputs) + skipped + len(unique_history)
        count: int = skipped
        source_files: list[ParsedSourceFile] = []
        records: list[ParsedUsageRecord] = []
        diagnostics: list[DiagnosticRecord] = []
        history_inputs: list[ParsedHistoryInput] = []
        for history_path in unique_history:
            observer.update(ScanPhase.HISTORY, count, total)
            snapshot = UsageRepository.read_cli_history(history_path)
            observer.checkpoint()
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
            count += 1
            observer.update(ScanPhase.HISTORY, count, total)

        # 解析完了順で件数だけ通知し、結果の連結は必ず探索順にする。
        def file_completed() -> None:
            """一つの入力を読み終えたことを通知する。"""

            nonlocal count
            count += 1
            observer.update(ScanPhase.READING, count, total)

        observer.update(ScanPhase.READING, count, total)
        workers: int = FileParsing.select_workers(inputs, parser_workers)
        for result in FileParsing.run(inputs, workers, observer, file_completed):
            records.extend(result.records)
            diagnostics.extend(result.diagnostics)
            if result.source_file is not None:
                source_files.append(result.source_file)
        observer.update(ScanPhase.NORMALIZING, count, total)
        records = DirectCreditsPolicy.normalize(records, diagnostics)
        observer.checkpoint()
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
