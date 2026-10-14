from __future__ import annotations

import multiprocessing
import os
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass, replace
from multiprocessing.connection import wait as wait_connections
from pathlib import Path

from copilot_usage_visualizer.ingestion.copilot_cli_events import CopilotCliEventsParser
from copilot_usage_visualizer.ingestion.copilot_cli_history import CliHistoryParser
from copilot_usage_visualizer.ingestion.scan_progress import ScanControl
from copilot_usage_visualizer.ingestion.vscode_chat_sessions import VscodeChatSessionsParser
from copilot_usage_visualizer.normalization.usage import (
    DiagnosticRecord,
    FileScanState,
    ParsedSourceFile,
    ParsedUsageRecord,
)


# 探索時の順序と履歴対応を解析完了順から独立させる。
@dataclass(frozen=True)
class ParseInput:
    """一つの独立ファイルと、その取得元・履歴scope。"""

    source: str
    path: Path
    history_path: Path | None = None
    scan_state: FileScanState | None = None


# 子からは本文ではなく既存parserの抽出済み結果だけを返す。
@dataclass(frozen=True)
class FileParseResult:
    """一つのファイルの解析結果。読取成功CLIは空でもsource_fileを持つ。"""

    records: list[ParsedUsageRecord]
    diagnostics: list[DiagnosticRecord]
    source_file: ParsedSourceFile | None = None


# executorの所有者が異常終了した時、Queue待ちの子を残さない。
class _ParserParentWatch:
    """解析プロセス自身が親のsentinelを待ち、親喪失時だけ終了する。"""

    # CPU解析はprocessで行い、このdaemon threadはOS通知を待つだけにする。
    @staticmethod
    def start() -> None:
        """spawnしたpool workerの初期化時に親の終了監視を始める。"""

        parent = multiprocessing.parent_process()
        assert parent is not None
        threading.Thread(
            target=_ParserParentWatch.wait, args=(parent.sentinel,), daemon=True,
        ).start()

    # このworkerはDBを書かないため、所有者喪失後に処理を続ける必要がない。
    @staticmethod
    def wait(sentinel: int) -> None:
        """親の終了時に自身だけを終了し、無関係なprocessには触れない。"""

        wait_connections([sentinel])
        os._exit(0)


# workerはcoreのparserだけを使い、DBやDearPyGuiに触れない。
class FileParsing:
    """既存の取得元別parserを逐次・有限並列の両方から呼ぶ。"""

    # 起動コストを上回る入力だけを並列化する。GUI用のCPU余力を残す。
    MIN_PARALLEL_FILES: int = 32
    MIN_PARALLEL_BYTES: int = 128 * 1024 * 1024
    MAX_WORKERS: int = 4

    # 0はGUIの自動選択、1は従来のCLI逐次処理、2以上は明示の解析数。
    @classmethod
    def select_workers(cls, items: Sequence[ParseInput], requested: int) -> int:
        """小規模入力を逐次解析し、大規模入力だけ有限並列にする。"""

        if requested < 0:
            raise ValueError("Parser worker count must not be negative.")
        if requested:
            return min(requested, cls.MAX_WORKERS)
        size: int = 0
        for item in items:
            try:
                size += item.path.stat().st_size
            except OSError:
                # 読み取り不能はparse時の既存診断で扱い、性能選択だけでは失敗にしない。
                continue
        if len(items) < cls.MIN_PARALLEL_FILES or size < cls.MIN_PARALLEL_BYTES:
            return 1
        return min(cls.MAX_WORKERS, max(1, (os.cpu_count() or 1) - 1))

    # static methodならspawnでimportでき、親の状態を複製しない。
    @staticmethod
    def parse(item: ParseInput) -> FileParseResult:
        """一つのログを解析し、読み取り不能を従来の診断へ変換する。"""

        try:
            if item.source == "copilot_cli":
                records, diagnostics = CopilotCliEventsParser().parse_file(item.path)
                if item.history_path is not None:
                    scope: str = CliHistoryParser.scope(item.history_path)
                    records = [replace(record, history_scope=scope) for record in records]
                    diagnostics = [
                        replace(diagnostic, history_scope=scope)
                        if diagnostic.code == "cli_session_identity_conflict" else diagnostic
                        for diagnostic in diagnostics
                    ]
                # 補助入力が読めなければ使用量は残し、復旧後の再解析を省略しない。
                scan_state = None if any(
                    diagnostic.code == "workspace_metadata_unreadable"
                    for diagnostic in diagnostics
                ) else item.scan_state
                return FileParseResult(records, diagnostics, ParsedSourceFile(
                    item.source, str(item.path), item.path.name, scan_state,
                ))
            records, diagnostics = VscodeChatSessionsParser().parse_file(item.path)
            return FileParseResult(records, diagnostics)
        except OSError:
            return FileParseResult([], [DiagnosticRecord(
                source=item.source, source_file_path=str(item.path),
                source_file_display_alias=item.path.name, code="source_unreadable",
                message="Source file could not be read.",
            )])

    # futureを全件登録せず、worker数の2倍までの仕事だけを受付ける。
    @classmethod
    def run(
        cls, items: Sequence[ParseInput], workers: int, control: ScanControl,
        completed: Callable[[], None],
    ) -> list[FileParseResult]:
        """進捗は完了順、返却は元の探索順で解析する。"""

        if workers <= 1 or len(items) <= 1:
            results: list[FileParseResult] = []
            for item in items:
                control.checkpoint()
                results.append(cls.parse(item))
                completed()
            return results

        ordered: list[FileParseResult | None] = [None] * len(items)
        pending: dict[Future[FileParseResult], int] = {}
        next_index: int = 0
        executor = ProcessPoolExecutor(
            max_workers=min(workers, len(items)), mp_context=multiprocessing.get_context("spawn"),
            initializer=_ParserParentWatch.start,
        )
        try:
            while next_index < len(items) or pending:
                control.checkpoint()
                while next_index < len(items) and len(pending) < workers * 2:
                    future = executor.submit(cls.parse, items[next_index])
                    pending[future] = next_index
                    next_index += 1
                done, _ = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
                for future in done:
                    ordered[pending.pop(future)] = future.result()
                    completed()
        finally:
            # 実行中の1ファイルは完走させる。GUIは別プロセスなので描画は止まらない。
            executor.shutdown(wait=True, cancel_futures=True)
        assert all(result is not None for result in ordered)
        return [result for result in ordered if result is not None]
