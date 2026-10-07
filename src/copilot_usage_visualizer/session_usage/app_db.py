from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from copilot_usage_visualizer.session_usage.contract import AppContextPoint, SourceStatus
from copilot_usage_visualizer.session_usage.js_compat import JsValue
from copilot_usage_visualizer.session_usage.sqlite_source import SqliteSource, SqliteSourceError

# App の sessions 表の値 (列名から int / float / str)。
AppValue = int | float | str | None


# App 側の参考値。
@dataclass(frozen=True)
class AppResult:
    """App が管理しないセッション (CLI 単体など) は status not_found。"""

    status: SourceStatus
    session: dict[str, AppValue] | None
    context_series: tuple[AppContextPoint, ...]


# GitHub Copilot App の data.db から、セッションの累計値とコンテキスト使用量の推移を読む。
class AppDataReader:
    """lib/app.mjs と同じ規則。title など本文由来の列は読まない。"""

    SESSION_COLUMNS: tuple[str, ...] = (
        "model",
        "reasoning_effort",
        "total_input_tokens",
        "total_output_tokens",
        "total_cached_tokens",
        "total_reasoning_tokens",
        "total_nano_aiu",
        "context_current_tokens",
        "context_input_token_limit",
        "context_output_token_limit",
        "context_system_tokens",
        "context_conversation_tokens",
        "context_tool_definitions_tokens",
        "context_mcp_tools_tokens",
        "context_buffer_tokens",
        "total_agent_merge_nano_aiu",
        "context_tier",
    )
    CONTEXT_COLUMNS: tuple[str, ...] = ("session_id", "ts_ms", "current_tokens", "token_limit")

    # 1 セッション分の App 記録を返す。
    @classmethod
    def read(cls, db_path: Path, session_id: str) -> AppResult:
        """読み取りの失敗は status で返す。"""

        try:
            with closing(SqliteSource.open_read_only(db_path)) as connection:
                return cls._read(connection, session_id)
        except (sqlite3.Error, OSError, SqliteSourceError) as error:
            return AppResult(SqliteSource.status_from_error(error), None, ())

    # 開いた DB から読む。
    @classmethod
    def _read(cls, connection: sqlite3.Connection, session_id: str) -> AppResult:
        """sessions 表に無い列は読まない。"""

        session_columns: list[str] = SqliteSource.table_columns(connection, "sessions")
        if "id" not in session_columns:
            raise SqliteSourceError(
                {"status": "unsupported_schema", "detail": "missing sessions.id"}
            )
        picked: list[str] = [name for name in cls.SESSION_COLUMNS if name in session_columns]
        sql: str = f"SELECT {', '.join(picked) if picked else 'id'} FROM sessions WHERE id = ?"
        with closing(connection.execute(sql, (session_id,))) as cursor:
            row: tuple[object, ...] | None = cursor.fetchone()
        series: list[AppContextPoint] = []
        context_columns: list[str] = SqliteSource.table_columns(connection, "session_context_usage")
        if all(name in context_columns for name in cls.CONTEXT_COLUMNS):
            with closing(
                connection.execute(
                    "SELECT ts_ms, current_tokens, token_limit FROM session_context_usage "
                    "WHERE session_id = ? ORDER BY id",
                    (session_id,),
                )
            ) as cursor:
                points: list[tuple[object, ...]] = cursor.fetchall()
            for ts_ms, current, limit in points:
                values: tuple[float, float, float] = (
                    JsValue.to_number(ts_ms),
                    JsValue.to_number(current),
                    JsValue.to_number(limit),
                )
                if all(math.isfinite(value) and value >= 0 for value in values):
                    series.append(
                        {
                            "t": cls._plain_number(values[0]),
                            "current": cls._plain_number(values[1]),
                            "limit": cls._plain_number(values[2]),
                        }
                    )
        if row is None and not series:
            return AppResult({"status": "not_found"}, None, ())
        session: dict[str, AppValue] | None = None
        if row is not None:
            session = {name: cls._value(value) for name, value in zip(picked, row, strict=False)}
        return AppResult({"status": "ok"}, session, tuple(series))

    # 列の値を表示用データに入れられる形にする。
    @staticmethod
    def _value(value: object) -> AppValue:
        """BLOB は数値でも識別子でもないため読まない。"""

        if isinstance(value, bytes):
            return None
        if isinstance(value, int | float | str):
            return value
        return None

    # 整数値の float を int にする。
    @staticmethod
    def _plain_number(value: float) -> int | float:
        """JSON 上の数値の表記を Node (number) と揃える。"""

        return int(value) if value.is_integer() else value
