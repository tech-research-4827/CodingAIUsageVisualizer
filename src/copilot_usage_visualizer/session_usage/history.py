from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from copilot_usage_visualizer.session_usage.contract import (
    SessionListEntry,
    SessionListResult,
    SourceStatus,
)
from copilot_usage_visualizer.session_usage.js_compat import JsonValue, JsValue, LocaleOrder
from copilot_usage_visualizer.session_usage.paths import CopilotHomePaths
from copilot_usage_visualizer.session_usage.sqlite_source import SqliteSource, SqliteSourceError

# SQL 境界の生の行。列名から SQLite の値 (int / float / str / bytes / None)。
SqlRow = dict[str, object]


# token_details_json の 1 件から求めた token 種別ごとの直接計算値。
@dataclass(frozen=True)
class TokenDetail:
    """本文を含まない token 種別・件数・計算値だけを持つ。"""

    token_type: str
    token_count: int
    nano: int
    exact: bool


# 検証済みの 4 種 token。
@dataclass(frozen=True)
class RowTokens:
    """input は cache read / write を含む総 prompt token。"""

    input: int
    output: int
    cache_read: int
    cache_write: int


# 検証済みの 1 request。
@dataclass(frozen=True)
class HistoryRow:
    """nano は整数のまま保持し、表示境界で換算する。欠落は 0 にせず None。"""

    row_id: int
    t: int
    turn_index: int | None
    agent_id: str | None
    parent_tool_call_id: str | None
    model: str | None
    initiator: str | None
    effort: str | None
    api_endpoint: str | None
    finish_reason: str | None
    content_filter: bool | None
    duration_ms: int | None
    ttft_ms: int | float | None
    multiplier: int | float | None
    nano: int | None
    tokens: RowTokens | None
    reasoning: int | None
    token_details: tuple[TokenDetail, ...] | None


# sessions 表の repository / branch。
@dataclass(frozen=True)
class SessionMeta:
    """cwd・summary は読まない。"""

    repository: str | None
    branch: str | None


# 保存重複と時刻不正を除いた行。
@dataclass(frozen=True)
class NormalizedRows:
    """時刻順の行と、除外した行数。"""

    rows: tuple[HistoryRow, ...]
    duplicate_rows_excluded: int
    invalid_timestamp_rows: int


# 1 セッション分の履歴。
@dataclass(frozen=True)
class HistoryResult:
    """読めなかった時は status に理由を持ち、行は空。"""

    status: SourceStatus
    rows: tuple[HistoryRow, ...]
    duplicate_rows_excluded: int
    invalid_timestamp_rows: int
    session: SessionMeta | None


# request 使用履歴 (session-store.db の assistant_usage_events) の読み取りと検証。
class HistoryReader:
    """lib/history.mjs と同じ規則で読む。"""

    TABLE: str = "assistant_usage_events"
    REQUIRED_COLUMNS: tuple[str, ...] = (
        "id",
        "session_id",
        "model",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "total_nano_aiu",
        "created_at",
    )

    # 1 セッション分の履歴を、保存重複を除いた時刻順で返す。
    @classmethod
    def read_history(cls, db_path: Path, session_id: str) -> HistoryResult:
        """読み取りの失敗は status で返す。"""

        try:
            with closing(SqliteSource.open_read_only(db_path)) as connection:
                columns: list[str] = SqliteSource.require_columns(
                    connection, cls.TABLE, cls.REQUIRED_COLUMNS
                )
                raw: list[SqlRow] = cls._select(
                    connection,
                    f"SELECT * FROM {cls.TABLE} WHERE session_id = ? ORDER BY created_at, id",
                    (session_id,),
                )
                normalized: NormalizedRows = cls.normalize_rows(raw, columns)
                return HistoryResult(
                    status={"status": "ok"},
                    rows=normalized.rows,
                    duplicate_rows_excluded=normalized.duplicate_rows_excluded,
                    invalid_timestamp_rows=normalized.invalid_timestamp_rows,
                    session=cls._session_meta(connection, session_id),
                )
        except (sqlite3.Error, OSError, SqliteSourceError) as error:
            return HistoryResult(
                status=SqliteSource.status_from_error(error),
                rows=(),
                duplicate_rows_excluded=0,
                invalid_timestamp_rows=0,
                session=None,
            )

    # 全セッションの一覧を最終活動の新しい順で返す。
    @classmethod
    def list_sessions(cls, db_path: Path) -> SessionListResult:
        """credits / requests は表示画面と同じ規則 (normalize_rows) で数える。"""

        try:
            with closing(SqliteSource.open_read_only(db_path)) as connection:
                columns: list[str] = SqliteSource.require_columns(
                    connection, cls.TABLE, cls.REQUIRED_COLUMNS
                )
                return {"status": {"status": "ok"}, "sessions": cls._list(connection, columns)}
        except (sqlite3.Error, OSError, SqliteSourceError) as error:
            return {"status": SqliteSource.status_from_error(error), "sessions": []}

    # 一覧の本体。
    @classmethod
    def _list(cls, connection: sqlite3.Connection, columns: list[str]) -> list[SessionListEntry]:
        """sessions 表に先に現れた順、次に履歴だけのセッションの順で集め、最後に並べ替える。"""

        entries: dict[str, _ListEntry] = {}
        for meta in cls._session_metas(connection):
            entries[meta.session_id] = meta
        with closing(connection.cursor()) as cursor:
            cursor.execute(f"SELECT * FROM {cls.TABLE} ORDER BY session_id, created_at, id")
            names: list[str] = [str(item[0]) for item in cursor.description]
            for values in cursor:
                raw: SqlRow = dict(zip(names, values, strict=True))
                session_id: object = raw.get("session_id")
                if not isinstance(session_id, str) or not CopilotHomePaths.is_valid_session_id(
                    session_id
                ):
                    continue
                entry: _ListEntry | None = entries.get(session_id)
                if entry is None:
                    entry = _ListEntry(session_id, None, None, None)
                    entries[session_id] = entry
                entry.rows.append(raw)
        sessions: list[SessionListEntry] = [
            cls._list_entry(entry, columns) for entry in entries.values()
        ]
        # 最終活動の新しい順、同じなら ID 順 (Node の localeCompare と同じ順)。
        sessions.sort(key=lambda item: LocaleOrder.key(item["id"]))
        sessions.sort(key=lambda item: LocaleOrder.key(item["lastActivity"] or ""), reverse=True)
        return sessions

    # 一覧の 1 行を作る。
    @classmethod
    def _list_entry(cls, entry: _ListEntry, columns: list[str]) -> SessionListEntry:
        """履歴行が無ければ sessions.updated_at を最終活動にする。"""

        rows: tuple[HistoryRow, ...] = cls.normalize_rows(entry.rows, columns).rows
        nano: int = 0
        missing: int = 0
        last_at: int | None = None
        for row in rows:
            if row.nano is None:
                missing += 1
            else:
                nano += row.nano
            last_at = row.t if last_at is None else max(last_at, row.t)
        last_activity: str | None = (
            JsValue.iso_string(last_at)
            if last_at is not None
            else cls._activity_time(entry.updated_at)
        )
        return {
            "id": entry.session_id,
            "repository": entry.repository,
            "branch": entry.branch,
            "lastActivity": last_activity,
            "requests": len(rows),
            "creditsNano": str(nano) if rows else None,
            "creditsMissing": missing,
        }

    # SQL 境界の生の行を検証済みの行へ変換する。
    @classmethod
    def normalize_rows(cls, raw_rows: Iterable[SqlRow], columns: Sequence[str]) -> NormalizedRows:
        """ローカル id 以外の全列が一致する行は保存重複として 1 回だけ数える。"""

        seen: set[tuple[tuple[str, object], ...]] = set()
        rows: list[HistoryRow] = []
        duplicates: int = 0
        invalid_time: int = 0
        identity_columns: list[str] = sorted(name for name in columns if name != "id")
        for raw in raw_rows:
            identity: tuple[tuple[str, object], ...] = tuple(
                cls._identity_value(raw.get(name)) for name in identity_columns
            )
            if identity in seen:
                duplicates += 1
                continue
            seen.add(identity)
            t: int | None = JsValue.parse_time(raw.get("created_at"))
            if t is None:
                invalid_time += 1
                continue
            rows.append(cls._to_row(raw, t))
        rows.sort(key=lambda row: (row.t, row.row_id))
        return NormalizedRows(tuple(rows), duplicates, invalid_time)

    # 同一性の比較に使う値。
    @staticmethod
    def _identity_value(value: object) -> tuple[str, object]:
        """INTEGER と REAL は等しい数値でも別の値として扱う (Node は INTEGER を BigInt で読む)。"""

        if value is None:
            return ("null", None)
        if isinstance(value, int):
            return ("integer", value)
        if isinstance(value, float):
            return ("real", value)
        if isinstance(value, bytes):
            return ("blob", value)
        return ("text", value)

    # 外部値を検証し、欠落を 0 に置き換えずに None で表す。
    @classmethod
    def _to_row(cls, raw: SqlRow, t: int) -> HistoryRow:
        """input は cache を含む総 prompt token。

        cache の合計を下回る行は不正として token を None にする。
        """

        input_tokens: int | None = JsValue.safe_int(raw.get("input_tokens"))
        output_tokens: int | None = JsValue.safe_int(raw.get("output_tokens"))
        cache_read: int | None = JsValue.safe_int(raw.get("cache_read_tokens"))
        cache_write: int | None = JsValue.safe_int(raw.get("cache_write_tokens"))
        tokens: RowTokens | None = None
        if (
            input_tokens is not None
            and output_tokens is not None
            and cache_read is not None
            and cache_write is not None
            and input_tokens >= cache_read + cache_write
        ):
            tokens = RowTokens(input_tokens, output_tokens, cache_read, cache_write)
        content_filter: object = raw.get("content_filter_triggered")
        row_id: float = JsValue.to_number(raw.get("id"))
        return HistoryRow(
            row_id=int(row_id),
            t=t,
            turn_index=JsValue.safe_int(raw.get("turn_index")),
            agent_id=JsValue.text(raw.get("agent_id")),
            parent_tool_call_id=JsValue.text(raw.get("parent_tool_call_id")),
            model=JsValue.text(raw.get("model")),
            initiator=JsValue.text(raw.get("initiator")),
            effort=JsValue.text(raw.get("reasoning_effort")),
            api_endpoint=JsValue.text(raw.get("api_endpoint")),
            finish_reason=JsValue.text(raw.get("finish_reason")),
            content_filter=None
            if content_filter is None
            else JsValue.to_number(content_filter) != 0,
            duration_ms=JsValue.safe_int(raw.get("duration_ms")),
            ttft_ms=JsValue.finite(raw.get("time_to_first_token_ms")),
            multiplier=JsValue.finite(raw.get("request_multiplier")),
            nano=JsValue.nano_from_sqlite(raw.get("total_nano_aiu")),
            tokens=tokens,
            reasoning=JsValue.safe_int(raw.get("reasoning_tokens")),
            token_details=cls.parse_token_details(raw.get("token_details_json")),
        )

    # token_details_json から token 種別・件数・単価だけを読む。
    @staticmethod
    def parse_token_details(value: object) -> tuple[TokenDetail, ...] | None:
        """1 件でも不正な項目があれば全体を None にする (記録値の照合に使わない)。"""

        if not isinstance(value, str) or not value.strip(JsValue.WHITESPACE):
            return None
        try:
            parsed: JsonValue = JsValue.parse_json(value)
        except ValueError:
            return None
        if not isinstance(parsed, list):
            return None
        details: list[TokenDetail] = []
        for item in parsed:
            if not isinstance(item, dict):
                return None
            token_type: JsonValue = item.get("tokenType")
            count: int | None = JsValue.safe_int(item.get("tokenCount"))
            cost: int | None = JsValue.safe_int(item.get("costPerBatch"))
            batch: int | None = JsValue.safe_int(item.get("batchSize"))
            if not isinstance(token_type, str) or count is None or cost is None or not batch:
                return None
            product: int = count * cost
            details.append(TokenDetail(token_type, count, product // batch, product % batch == 0))
        return tuple(details)

    # sessions 表から repository / branch だけを読む。
    @staticmethod
    def _session_meta(connection: sqlite3.Connection, session_id: str) -> SessionMeta | None:
        """App のセッションは events.jsonl に無いため sessions 表でも識別子を補う。"""

        columns: list[str] = SqliteSource.table_columns(connection, "sessions")
        if "id" not in columns:
            return None
        repository: str = "repository" if "repository" in columns else "NULL"
        branch: str = "branch" if "branch" in columns else "NULL"
        with closing(
            connection.execute(
                f"SELECT {repository} AS repository, {branch} AS branch FROM sessions WHERE id = ?",
                (session_id,),
            )
        ) as cursor:
            row: tuple[object, ...] | None = cursor.fetchone()
        if row is None:
            return None
        return SessionMeta(JsValue.text(row[0]), JsValue.text(row[1]))

    # sessions 表から一覧用の repository / branch / updated_at だけを読む。
    @staticmethod
    def _session_metas(connection: sqlite3.Connection) -> list[_ListEntry]:
        """ID の検証を通る行だけを返す。"""

        columns: list[str] = SqliteSource.table_columns(connection, "sessions")
        if "id" not in columns:
            return []
        picked: list[str] = [
            name if name in columns else "NULL" for name in ("repository", "branch", "updated_at")
        ]
        sql: str = (
            f"SELECT id, {picked[0]} AS repository, {picked[1]} AS branch, "
            f"{picked[2]} AS updated_at FROM sessions"
        )
        with closing(connection.execute(sql)) as cursor:
            rows: list[tuple[object, ...]] = cursor.fetchall()
        entries: list[_ListEntry] = []
        for row in rows:
            session_id: object = row[0]
            if isinstance(session_id, str) and CopilotHomePaths.is_valid_session_id(session_id):
                entries.append(
                    _ListEntry(session_id, JsValue.text(row[1]), JsValue.text(row[2]), row[3])
                )
        return entries

    # sessions.updated_at を ISO 文字列にする。
    @staticmethod
    def _activity_time(value: object) -> str | None:
        """SQLite の datetime('now') は UTC で timezone 表記が無いため、無ければ UTC として読む。"""

        if not isinstance(value, str):
            return None
        millis: int | None = JsValue.parse_time(value)
        if millis is None:
            millis = JsValue.parse_time(f"{value.replace(' ', 'T', 1)}Z")
        return None if millis is None else JsValue.iso_string(millis)

    # SELECT の結果を列名付きの行にする。
    @staticmethod
    def _select(
        connection: sqlite3.Connection, sql: str, params: tuple[object, ...]
    ) -> list[SqlRow]:
        """列名は cursor.description (SELECT * の列順) から取る。"""

        with closing(connection.execute(sql, params)) as cursor:
            names: list[str] = [str(item[0]) for item in cursor.description]
            return [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]


# 一覧を作る途中のセッション。
class _ListEntry:
    """sessions 表の識別子と、そのセッションの生の履歴行。"""

    # 識別子を受け取る。
    def __init__(
        self, session_id: str, repository: str | None, branch: str | None, updated_at: object
    ) -> None:
        """rows は履歴を読みながら追加する。"""

        self.session_id: str = session_id
        self.repository: str | None = repository
        self.branch: str | None = branch
        self.updated_at: object = updated_at
        self.rows: list[SqlRow] = []
