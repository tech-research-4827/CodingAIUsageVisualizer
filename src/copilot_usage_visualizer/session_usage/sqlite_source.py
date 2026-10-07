from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from copilot_usage_visualizer.session_usage.contract import SourceStatus


# 読み取り失敗を例外ではなく状態として返すための例外。
class SqliteSourceError(Exception):
    """読み取り元の状態を運ぶ。メッセージにはパスや DB の内容を含めない。"""

    # 状態を受け取る。
    def __init__(self, status: SourceStatus) -> None:
        """status は固定コードだけを持つ。"""

        detail: str | None = status.get("detail")
        super().__init__(f"{status['status']}{f': {detail}' if detail else ''}")
        self.source_status: SourceStatus = status


# Copilot CLI / App の SQLite を読み取り専用で開き、表の列を検査する。
class SqliteSource:
    """lib/sqlite.mjs と同じ状態コードを返す。

    CLI / App が書き込み中でも WAL の読み取りとして共存する。
    """

    BUSY_TIMEOUT_SECONDS: float = 2.0

    # 存在する DB だけを読み取り専用で開く。
    @classmethod
    def open_read_only(cls, path: Path) -> sqlite3.Connection:
        """存在しない DB を作らないよう開く前に確かめ、`mode=ro` で開く。"""

        if not path.exists():
            raise SqliteSourceError({"status": "missing"})
        try:
            return sqlite3.connect(
                f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=cls.BUSY_TIMEOUT_SECONDS
            )
        except sqlite3.Error as error:
            raise SqliteSourceError(
                {"status": "unreadable", "detail": cls.error_code(error)}
            ) from error

    # 表の列名を集める。
    @staticmethod
    def table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
        """表の定義順の列名。表が無ければ空。"""

        with closing(
            connection.execute("SELECT name FROM pragma_table_info(?)", (table,))
        ) as cursor:
            return [str(row[0]) for row in cursor.fetchall()]

    # 必須列を検査する。
    @classmethod
    def require_columns(
        cls, connection: sqlite3.Connection, table: str, required: tuple[str, ...]
    ) -> list[str]:
        """必須列が欠けた表は形式非対応として扱い、推測で読まない。"""

        columns: list[str] = cls.table_columns(connection, table)
        if not columns:
            raise SqliteSourceError(
                {"status": "unsupported_schema", "detail": f"missing table {table}"}
            )
        missing: list[str] = [name for name in required if name not in columns]
        if missing:
            raise SqliteSourceError(
                {"status": "unsupported_schema", "detail": f"missing {', '.join(missing)}"}
            )
        return columns

    # SQLite のエラーを固定コードにする。
    @staticmethod
    def error_code(error: BaseException) -> str:
        """メッセージにはパスが入り得るため、SQLite の拡張結果コード (sqlite_<n>) だけを残す。"""

        if isinstance(error, sqlite3.Error):
            return f"sqlite_{error.sqlite_errorcode}"
        return "error"

    # 例外を読み取り元の状態に変える。
    @classmethod
    def status_from_error(cls, error: BaseException) -> SourceStatus:
        """想定外の例外は unreadable とする。"""

        if isinstance(error, SqliteSourceError):
            return error.source_status
        return {"status": "unreadable", "detail": cls.error_code(error)}
