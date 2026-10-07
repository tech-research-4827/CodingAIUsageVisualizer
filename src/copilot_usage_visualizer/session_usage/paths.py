from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePath

from copilot_usage_visualizer.ingestion.wsl_cli_history import WslHistoryLocation


# 1 セッション分の読み取り元。絶対パスは読み取りにだけ使い、表示用データには出さない。
@dataclass(frozen=True)
class SessionSourcePaths:
    """履歴 DB・events.jsonl・App の data.db の位置。"""

    history_db: Path
    events_file: Path
    app_db: Path


# Copilot home の中の記録の位置とセッション ID の検証。
class CopilotHomePaths:
    """lib/paths.mjs と同じ位置と ID 規則。"""

    # セッション ID はパスの一部になるため、区切り文字や `..` を含む値を拒否する。
    SESSION_ID: re.Pattern[str] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
    HISTORY_DB_NAME: str = "session-store.db"
    APP_DB_NAME: str = "data.db"
    SESSION_STATE_DIR: str = "session-state"
    EVENTS_FILE_NAME: str = "events.jsonl"

    # パス走査に使えない ID だけを受け付ける。
    @classmethod
    def is_valid_session_id(cls, value: object) -> bool:
        """英数字で始まり、英数字と `._-` だけの 128 文字以内で `..` を含まない ID。"""

        return (
            isinstance(value, str)
            and cls.SESSION_ID.fullmatch(value) is not None
            and (".." not in value)
        )

    # 履歴 DB の位置を返す。
    @classmethod
    def history_db(cls, home: Path) -> Path:
        """セッション一覧と履歴を持つ session-store.db。"""

        return home / cls.HISTORY_DB_NAME

    # 3 つの読み取り元の位置を組み立てる。
    @classmethod
    def sources(cls, home: Path, session_id: str) -> SessionSourcePaths:
        """不正な ID は ValueError (パスへ組み込む前に拒否する)。"""

        if not cls.is_valid_session_id(session_id):
            raise ValueError("Invalid session id.")
        return SessionSourcePaths(
            history_db=cls.history_db(home),
            events_file=home / cls.SESSION_STATE_DIR / session_id / cls.EVENTS_FILE_NAME,
            app_db=home / cls.APP_DB_NAME,
        )

    # GUI の CLI log root から Copilot home を求める。
    @staticmethod
    def home_from_cli_log_root(root: Path) -> Path:
        """CLI log root は `<home>/session-state` なので、その親を Copilot home とする。"""

        return root.parent

    # このプロセスでは読まない root かどうかを返す。
    @staticmethod
    def is_wsl_share(root: PurePath) -> bool:
        """Windows から見た WSL の UNC パスは
        共有越しの SQLite がロックで失敗するため読まない。
        """

        return os.name == "nt" and WslHistoryLocation.from_path(str(root)) is not None
