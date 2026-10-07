from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from copilot_usage_visualizer.session_usage.aggregate import SessionViewBuilder
from copilot_usage_visualizer.session_usage.app_db import AppDataReader
from copilot_usage_visualizer.session_usage.contract import SessionListResult, SessionUsageView
from copilot_usage_visualizer.session_usage.events import EventsReader
from copilot_usage_visualizer.session_usage.history import HistoryReader
from copilot_usage_visualizer.session_usage.js_compat import JsValue
from copilot_usage_visualizer.session_usage.paths import CopilotHomePaths, SessionSourcePaths


# 表示用データと、その元になった記録の署名。
@dataclass(frozen=True)
class ViewResult:
    """signature が同じ間は記録が変わっていない。"""

    signature: str
    view: SessionUsageView


# 記録の読み取り・変更検知・表示用データの生成をまとめる。
class SessionUsageProvider:
    """lib/provider.mjs と同じく、セッションごとに最新の表示用データを持ち、
    記録が変わった時だけ作り直す。
    """

    # Copilot home と時計を受け取る。
    def __init__(self, copilot_home: Path, now: Callable[[], datetime] | None = None) -> None:
        """now は readAt の時刻 (テストでは固定する)。"""

        self.copilot_home: Path = copilot_home
        self._now: Callable[[], datetime] = now if now is not None else self._utc_now
        self._events: EventsReader = EventsReader()
        self._views: dict[str, ViewResult] = {}
        self._session_list: tuple[str, SessionListResult] | None = None

    # 現在時刻 (UTC)。
    @staticmethod
    def _utc_now() -> datetime:
        """readAt の既定の時計。"""

        return datetime.now(UTC)

    # 表示用データを返す。
    def get_view(self, session_id: str, force: bool = False) -> ViewResult:
        """署名が前回と同じなら作り直さない。

        force は events.jsonl も最初から読み直す。不正な ID は ValueError。
        """

        paths: SessionSourcePaths = CopilotHomePaths.sources(self.copilot_home, session_id)
        signature: str = self.signature(self._watched_files(paths))
        cached: ViewResult | None = self._views.get(session_id)
        if not force and cached is not None and cached.signature == signature:
            return cached
        view: SessionUsageView = SessionViewBuilder.build(
            session_id=session_id,
            is_current=False,
            history=HistoryReader.read_history(paths.history_db, session_id),
            events=self._events.read(paths.events_file, force=force),
            app=AppDataReader.read(paths.app_db, session_id),
            read_at=self._read_at(),
        )
        result: ViewResult = ViewResult(signature, view)
        self._views[session_id] = result
        return result

    # セッション選択用の一覧。
    def list_sessions(self, force: bool = False) -> SessionListResult:
        """全履歴を読むため、履歴 DB (と WAL) が変わった時だけ作り直す。"""

        history_db: Path = CopilotHomePaths.history_db(self.copilot_home)
        signature: str = self.signature([history_db, self._wal(history_db)])
        if force or self._session_list is None or self._session_list[0] != signature:
            self._session_list = (signature, HistoryReader.list_sessions(history_db))
        return self._session_list[1]

    # ファイル群の (mtime, size) から変更検知用の署名を作る。
    @staticmethod
    def signature(files: list[Path]) -> str:
        """存在しないファイルも状態として含める。"""

        parts: list[str] = []
        for file in files:
            try:
                stat = file.stat()
                parts.append(f"{stat.st_mtime_ns}:{stat.st_size}")
            except OSError:
                parts.append("-")
        return "|".join(parts)

    # 変更検知の対象。
    @classmethod
    def _watched_files(cls, paths: SessionSourcePaths) -> list[Path]:
        """SQLite は WAL に追記されると本体の mtime が動かないため -wal も見る。"""

        return [
            paths.history_db,
            cls._wal(paths.history_db),
            paths.events_file,
            paths.app_db,
            cls._wal(paths.app_db),
        ]

    # WAL ファイルの位置。
    @staticmethod
    def _wal(db: Path) -> Path:
        """`<db>-wal`。"""

        return db.with_name(f"{db.name}-wal")

    # readAt の文字列。
    def _read_at(self) -> str:
        """toISOString と同じ形 (UTC、ミリ秒)。"""

        moment: datetime = self._now()
        return JsValue.iso_string((moment - JsValue.EPOCH) // timedelta(milliseconds=1))
