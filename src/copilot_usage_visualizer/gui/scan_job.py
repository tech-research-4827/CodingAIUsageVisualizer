from __future__ import annotations

import multiprocessing
import sqlite3
import sys
import time
from dataclasses import dataclass, replace
from enum import StrEnum
from multiprocessing.connection import Connection
from multiprocessing.context import SpawnProcess
from typing import TypeAlias, cast

if sys.platform == "win32":
    from multiprocessing.connection import PipeConnection

    ScanConnection: TypeAlias = Connection | PipeConnection
else:
    ScanConnection: TypeAlias = Connection

from copilot_usage_visualizer.app import UsageApplication
from copilot_usage_visualizer.db.repository import StoredScanResult
from copilot_usage_visualizer.gui.settings import DashboardSettings
from copilot_usage_visualizer.ingestion.scan_progress import (
    ScanCancelled,
    ScanControl,
    ScanPhase,
    ScanProgress,
    ScanTiming,
)


# 大きな解析結果をGUIへ送らず、保存後のレポートだけを返す。
@dataclass(frozen=True)
class ScanCompleted:
    """DB保存とレポート取得が成功した結果。"""

    stored: StoredScanResult
    report: dict[str, object]
    timings: tuple[ScanTiming, ...]
    elapsed: float


# 既知の失敗だけを固定分類へ変え、生の例外文字列を共有しない。
class ScanFailureKind(StrEnum):
    """利用者が直す設定を示すための、パスを含まない分類。"""

    UNKNOWN = "scan process"
    SOURCE = "source input"
    SCHEMA = "database schema"
    DATABASE = "database access"
    SETTINGS = "settings"


# 失敗分類と非空入力の番号だけを転送し、本文・パスは送らない。
@dataclass(frozen=True)
class ScanFailed:
    """失敗段階と保存済みかどうかを、固定の語彙で伝える。"""

    phase: ScanPhase
    saved: bool = False
    cancelled: bool = False
    unexpected_exit: bool = False
    kind: ScanFailureKind = ScanFailureKind.UNKNOWN
    source: str | None = None
    source_index: int | None = None

    # rootの設定値は参照せず、footerにも収まる分類を返す。
    @property
    def label(self) -> str:
        """他のタブでも読み取れる短い失敗理由を返す。"""

        if self.unexpected_exit:
            return "unexpected exit"
        if self.kind == ScanFailureKind.SOURCE and self.source in ("CLI", "Chat"):
            index: str = f" #{self.source_index}" if self.source_index is not None else ""
            return f"{self.source} input{index}"
        return self.kind.value

    # アプリが定義する検証文だけを識別し、例外文そのものは結果に保持しない。
    @classmethod
    def from_exception(
        cls, exc: Exception, settings: DashboardSettings, phase: ScanPhase, saved: bool,
    ) -> ScanFailed:
        """既知のroot/schema拒否とDBアクセス失敗を安全な分類に変える。"""

        kind = ScanFailureKind.UNKNOWN
        if isinstance(exc, ValueError):
            message: str = str(exc)
            for source, roots in (("CLI", settings.cli_log_roots),
                                  ("Chat", settings.chat_storage_roots)):
                prefix: str = f"{source} root is not an existing directory: "
                if phase == ScanPhase.DISCOVERING and message.startswith(prefix):
                    invalid: str = message[len(prefix):]
                    index = next((i for i, root in enumerate(roots, 1)
                                  if str(root) == invalid), None)
                    if index is not None:
                        return cls(phase, saved=saved, kind=ScanFailureKind.SOURCE,
                                   source=source, source_index=index)
            if message.startswith(("Unsupported or unknown mixed application database;",
                                   "Unsupported legacy database.")):
                kind = ScanFailureKind.SCHEMA
            elif message in (
                "Dates must be valid YYYY-MM-DD values. Edit the date fields.",
                "Date from must not be after Date to. Edit the date fields.",
                "USD -> JPY rate must be finite and greater than 0. Edit the rate.",
                "Paths must not contain NUL characters. Edit the path fields.",
                "Root paths must not contain line breaks. Edit the source fields.",
                "UI scale must be Auto or a listed percentage. Choose a UI scale.",
                "Font size must be a listed percentage. Choose a font size.",
            ):
                kind = ScanFailureKind.SETTINGS
        elif isinstance(exc, sqlite3.DatabaseError):
            kind = ScanFailureKind.DATABASE
        elif isinstance(exc, OSError) and phase in (ScanPhase.SAVING, ScanPhase.REPORT):
            kind = ScanFailureKind.DATABASE
        return cls(phase, saved=saved, kind=kind)

    # 対処に必要な案内だけをGUIへ渡す。
    @property
    def message(self) -> str:
        """本文やパスを含まない失敗理由と次の操作を返す。"""

        if self.cancelled:
            return "Scan stopped. Database saved." if self.saved else "Scan stopped before saving."
        state: str = "Database saved; report was not updated." if self.saved else (
            "No successful database save was reported."
        )
        if self.kind == ScanFailureKind.SOURCE:
            return (f"{self.label} is not an existing directory. {state} "
                    "Check the corresponding non-empty entry in the source fields.")
        if self.kind == ScanFailureKind.SCHEMA:
            return (f"Database schema is not supported. {state} Use a new DB for an unknown "
                    "schema; full scan can rebuild a recognized legacy application DB.")
        if self.kind == ScanFailureKind.DATABASE:
            action: str = "Retry GUI report refresh." if self.saved else (
                "Check the output DB path and access."
            )
            return f"Database access failed. {state} {action}"
        if self.kind == ScanFailureKind.SETTINGS:
            return f"Settings validation failed. {state} Check dates, rate and path fields."
        cause: str = "Scan process exited unexpectedly." if self.unexpected_exit else (
            f"Scan failed during {self.phase.value.lower()}."
        )
        return f"{cause} {state} Check source folders, dates and DB access/schema before scanning."


# GUIはメインプロセスで一度だけ書き、coordinatorだけが読み取る。
class _ScanChannel:
    """終了要求とGUI切断を同じ協調終了として扱う。"""

    # 毎入力の同期送信で解析速度をGUIの描画周期に結び付けない。
    PROGRESS_INTERVAL_SECONDS: float = 0.1

    # poolへConnectionを渡さず、EOFを確実に検出する。
    def __init__(self, connection: ScanConnection) -> None:
        """子専用の通信端点を保持する。"""

        self.connection = connection
        self._stopped: bool = False
        self._last_progress: ScanProgress | None = None
        self._last_sent: float = 0.0

    # 長い履歴取得や1ファイル解析の後でも、保存前に必ず確認する。
    def cancelled(self) -> bool:
        """終了要求か親の切断が到着しているかを非同期に調べる。"""

        try:
            if not self._stopped and self.connection.poll():
                self.connection.recv()
                self._stopped = True
        except (EOFError, OSError):
            self._stopped = True
        return self._stopped

    # 解析の速度に対して進捗通知は小さく保ち、GUIで件数制限して受ける。
    def notify(self, progress: ScanProgress) -> None:
        """進捗だけを送り、切断なら保存前の処理を終了する。"""

        now: float = time.monotonic()
        changed: bool = self._last_progress is None or (
            progress.phase != self._last_progress.phase
        )
        final: bool = progress.total is not None and progress.completed == progress.total
        if not changed and not final and now - self._last_sent < self.PROGRESS_INTERVAL_SECONDS:
            return
        try:
            self.connection.send(progress)
            self._last_progress = progress
            self._last_sent = now
        except (BrokenPipeError, EOFError, OSError):
            self._stopped = True
            raise ScanCancelled("GUI disconnected.") from None


# spawnからimport可能なclass methodをentryにし、GUIを子で作らない。
class _ScanWorker:
    """一つの非daemonプロセス内でscan・保存・report取得を実行する。"""

    # GUIから実行時の設定snapshotだけを受け取る。
    @staticmethod
    def run(
        settings: DashboardSettings, full: bool, connection: ScanConnection,
        parser_workers: int = 0,
    ) -> None:
        """所有するpoolを回収してから最後の結果を送る。"""

        channel = _ScanChannel(connection)
        control = ScanControl(channel.notify, channel.cancelled)
        started: float = time.perf_counter()
        saved: bool = False
        outcome: ScanCompleted | ScanFailed
        try:
            settings.validate()
            application = UsageApplication()
            stored = application.scan(
                settings.db_path, settings.cli_log_roots, settings.chat_storage_roots,
                full=full, control=control, parser_workers=parser_workers,
            )
            saved = True
            control.update(ScanPhase.REPORT, control.progress.completed, control.progress.total)
            report = application.report_summary(
                settings.db_path, settings.date_from, settings.date_to, settings.usd_jpy_rate,
            )
            control.checkpoint()
            outcome = ScanCompleted(
                stored, report, control.timings(), time.perf_counter() - started,
            )
        except ScanCancelled:
            outcome = ScanFailed(control.progress.phase, saved=saved, cancelled=True)
        except Exception as exc:
            # この境界だけで例外を固定の失敗へ変換する。生のstderrをGUIへ転送しない。
            outcome = ScanFailed.from_exception(exc, settings, control.progress.phase, saved)
        try:
            connection.send(outcome)
        except (BrokenPipeError, EOFError, OSError):
            # GUIは消えている。保存済みtransactionは取り消さず、所有資源を閉じて終える。
            pass
        finally:
            connection.close()


# GUIは待機せずpollする。joinで描画を止めない。
class ScanJob:
    """一つのscanプロセスを起動し、進捗と確定した終了結果を取り出す。"""

    # GUIのper-frame処理量を有限にする。
    MAX_MESSAGES_PER_FRAME: int = 32

    # 任意のテスト用Fakeではなく、実spawnを通常経路にする。
    def __init__(self) -> None:
        """現在のプロセス・チャネルと受信状態を初期化する。"""

        self._process: SpawnProcess | None = None
        self._connection: ScanConnection | None = None
        self._received: ScanCompleted | ScanFailed | None = None
        self.outcome: ScanCompleted | ScanFailed | None = None
        self.progress = ScanProgress(ScanPhase.DISCOVERING)
        self.pid: int | None = None
        self._closing: bool = False
        self._eof: bool = False
        self._started: float = 0.0
        self.elapsed: float = 0.0

    # outcomeの受信だけではpool回収が終わったとはみなさない。
    @property
    def active(self) -> bool:
        """起動から子の終了を確定するまでTrue。"""

        return self._process is not None

    # 必ずsnapshotを渡し、親側の編集で処理期間が変わるのを防ぐ。
    def start(self, settings: DashboardSettings, full: bool, parser_workers: int = 0) -> None:
        """一つのscanを開始する。二重起動は拒否する。"""

        if self.active:
            raise ValueError("A scan is already running.")
        context = multiprocessing.get_context("spawn")
        self._started = time.perf_counter()
        self.elapsed = 0.0
        self.progress = ScanProgress(ScanPhase.DISCOVERING)
        parent, child = context.Pipe()
        process = context.Process(
            target=_ScanWorker.run, args=(replace(settings), full, child, parser_workers),
        )
        try:
            process.start()
        except Exception:
            parent.close()
            process.close()
            raise RuntimeError(
                "Scan process could not start. Check the Python installation."
            ) from None
        finally:
            child.close()
        self._connection = parent
        self._process = process
        self.pid = process.pid
        self._received = None
        self.outcome = None
        self.progress = ScanProgress(ScanPhase.DISCOVERING)
        self._closing = False
        self._eof = False

    # GUI callbackと描画は同じthreadで動かし、ここではblocking waitをしない。
    def poll(self) -> None:
        """有限個の進捗通知を受け、子が終了した時だけ結果を確定する。"""

        process, connection = self._process, self._connection
        if process is None or connection is None:
            return
        # 空判定の後で子が結果を送って終了しても、次フレームで受信できる順序にする。
        alive: bool = process.is_alive()
        drained = False
        for _ in range(self.MAX_MESSAGES_PER_FRAME):
            try:
                if not connection.poll():
                    drained = True
                    break
                # pickleは当アプリが起動したローカル子だけから受け取るI/O境界。
                message = cast(ScanProgress | ScanCompleted | ScanFailed, connection.recv())
            except (EOFError, OSError):
                self._eof = True
                break
            if isinstance(message, ScanProgress):
                self.progress = message
            else:
                self._received = message
        if not alive and (
            self._received is not None or self._eof or drained
        ):
            process.join(timeout=0)
            self.outcome = self._received or ScanFailed(self.progress.phase, unexpected_exit=True)
            self.elapsed = time.perf_counter() - self._started
            connection.close()
            process.close()
            self._connection = None
            self._process = None

    # close requestは1回だけ送信する。保存中は子側がtransactionを完走する。
    def request_close(self) -> None:
        """終了を要求するが、GUIで子の終了を待ち合わせない。"""

        if self._connection is not None and not self._closing:
            try:
                self._connection.send("close")
            except (BrokenPipeError, EOFError, OSError):
                pass
            self._closing = True

    # GUIの例外終了時にも親端点を閉じて、子に保存前の中止を伝える。
    def disconnect(self) -> None:
        """GUIを維持できない時の後始末。子はEOFから終了を判断する。"""

        self.request_close()
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if self._process is not None:
            self._process.join()
            self._process.close()
            self._process = None
