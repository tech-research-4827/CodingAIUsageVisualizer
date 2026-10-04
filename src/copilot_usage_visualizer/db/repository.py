from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection, Row
from typing import cast

from copilot_usage_visualizer.db.migrations import MigrationService
from copilot_usage_visualizer.normalization.credits import CliHistorySelection
from copilot_usage_visualizer.normalization.usage import (
    CliHistorySnapshot,
    DiagnosticRecord,
    ParsedHistoryInput,
    ParsedSourceFile,
    ParsedUsageRecord,
    RunKind,
    UsageRow,
    UsageSelection,
)


# scan 保存結果を表す。
@dataclass(frozen=True)
class StoredScanResult:
    """scan persistence 後の件数を保持する。"""

    scan_id: int
    records_count: int
    diagnostics_count: int


# SQLite repository。
class UsageRepository:
    """Copilot usage visualizer の SQLite access を集約する。"""

    def __init__(self, db_path: Path) -> None:
        """DB path と migration service を初期化する。"""

        self._db_path: Path = db_path
        self._migration_service: MigrationService = MigrationService(
            Path(__file__).with_name("schema.sql")
        )

    # 使用履歴は加算元にせず、照合用許可列のみ読み取る。
    @staticmethod
    def read_cli_history(path: Path, session_id: str | None = None) -> CliHistorySnapshot:
        """同じhomeの使用履歴をread-onlyで取得し、本文を境界の外へ出さない。"""

        allowed = (
            "session_id", "turn_index", "agent_id", "parent_tool_call_id", "model",
            "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
            "reasoning_tokens", "total_nano_aiu", "request_multiplier", "duration_ms",
            "time_to_first_token_ms", "inter_token_latency_ms", "initiator", "api_endpoint",
            "reasoning_effort", "finish_reason", "content_filter_triggered",
            "token_details_json", "created_at", "output_ttft_ms", "copilot_usage_model",
        )
        required = {"session_id", "initiator", "model", "created_at", "total_nano_aiu",
                    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"}
        try:
            connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            connection.execute("BEGIN")
            try:
                columns = {str(r[1]) for r in connection.execute(
                    "PRAGMA table_info(assistant_usage_events)"
                )}
                if not required <= columns:
                    return CliHistorySnapshot(error="unsupported_history_schema")
                error = "unsupported_history_schema" if columns - {*allowed, "id"} else None
                selected = tuple(c for c in allowed if c in columns)
                rows = tuple(tuple(r) for r in connection.execute(
                    ("SELECT " if error else "SELECT DISTINCT ") + ", ".join(selected)
                    + " FROM assistant_usage_events"
                    + (" WHERE session_id = ?" if session_id is not None else ""),
                    (session_id,) if session_id is not None else (),
                ))
                return CliHistorySnapshot(selected, rows, error)
            finally:
                connection.close()
        except sqlite3.Error:
            return CliHistorySnapshot(error="history_unavailable")

    # DB 接続を作成する。
    def connect(self) -> Connection:
        """row factory 設定済み SQLite connection を返す。"""

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        connection: Connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    # schema を初期化する。
    def initialize(self) -> None:
        """DB schema を初期化する。"""

        with self.connect() as connection:
            self._migration_service.apply(connection)

    # scan records と diagnostics を保存する。
    def store_scan(
        self,
        records: list[ParsedUsageRecord],
        diagnostics: list[DiagnosticRecord],
        root_label: str,
        full: bool = False,
        source_files: list[ParsedSourceFile] | None = None,
        history_inputs: list[ParsedHistoryInput] | None = None,
    ) -> StoredScanResult:
        """scan result を DB に保存して件数を返す。

        full=True の場合は、scan-derived data を
        同一 transaction 内で削除してから保存する（全体読み直し）。parse 済み records を
        受け取ってから reset するため、保存に失敗すれば rollback され既存データは保持される。
        """

        with self.connect() as connection:
            self._migration_service.apply(connection, rebuild=full)
            if full:
                self._reset_scan_data(connection)
            else:
                self._delete_replaced_diagnostics(connection, records, diagnostics)
                self._replace_cli_files(connection, records, source_files or [])
            scan_id: int = self._insert_scan_run(connection, root_label, len(diagnostics))
            records_count: int = 0
            for record in records:
                inserted: bool = self._insert_usage_record(connection, scan_id, record)
                if inserted:
                    records_count += 1
            for history in history_inputs or []:
                source_id = self._upsert_source_file(connection, ParsedSourceFile(
                    "copilot_cli_history", history.path, "session-store.db"
                ))
                scope = self._hash_text(str(Path(history.path).resolve()))
                connection.execute("DELETE FROM cli_history_inputs WHERE scope = ?", (scope,))
                connection.executemany(
                    "INSERT INTO cli_history_inputs VALUES (?, ?, ?, ?)",
                    [(scope, "", source_id, history.error)] + [
                        (scope, sid, source_id, "invalid_history_metadata")
                        for sid in history.invalid_sessions
                    ],
                )
            # 別ファイルに同一sessionがある場合は順序を推測せず両方を除外する。
            connection.execute(
                "DELETE FROM diagnostics WHERE diagnostic_code = ?", ("cli_duplicate_session",)
            )
            connection.execute(
                f"""WITH {self._all_usage_cte()}
                INSERT INTO diagnostics
                    (scan_id, source_file_id, diagnostic_code, message)
                SELECT ?, MIN(source_file_id), 'cli_duplicate_session',
                    'Session identity occurs in multiple files; usage excluded.'
                FROM all_usage u JOIN runs r ON r.run_id = u.run_id
                WHERE r.source = 'copilot_cli' AND r.run_kind != 'request_history'
                GROUP BY r.session_id HAVING COUNT(DISTINCT source_file_id) > 1""",
                (scan_id,),
            )
            for diagnostic in diagnostics:
                self._insert_diagnostic(connection, scan_id, diagnostic)
            connection.execute(
                "DELETE FROM diagnostics WHERE diagnostic_code LIKE 'cli_compaction_unresolved_%' "
                "OR diagnostic_code LIKE 'cli_history_selection_%'"
            )
            selection = self._select_usage(connection)
            connection.executemany(
                "INSERT INTO diagnostics (scan_id, source_file_id, diagnostic_code, message) "
                "VALUES (?, ?, ?, ?)",
                [(scan_id, source_id, code,
                  "Usage overlap is unresolved; unconfirmed observations are not added.")
                 for source_id, code in sorted(set(selection.issues))],
            )
            diagnostics_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM diagnostics WHERE scan_id = ?", (scan_id,)
                ).fetchone()[0]
            )
            connection.execute(
                "UPDATE scan_runs SET ended_at = datetime('now'), result = ?, "
                "warnings_count = ? WHERE scan_id = ?",
                ("ok", diagnostics_count, scan_id),
            )
            return StoredScanResult(
                scan_id=scan_id,
                records_count=records_count,
                diagnostics_count=diagnostics_count,
            )

    # summary とreportは同じ選択済み射影を使う。
    def summary_records(self, date_from: str, date_to: str) -> list[UsageRow]:
        """選択後に日付で絞った使用量を返す。"""

        return self.report_records(date_from, date_to)

    # model行とsession aggregateを出力境界でのみ結合する。
    def report_records(self, date_from: str, date_to: str) -> list[UsageRow]:
        """全期間で照合してから日付で絞り、各frontendへ同じ採用射影を渡す。"""
        self.initialize()
        with self.connect() as connection:
            selection = self._select_usage(connection)
        return sorted(
            [row for row in selection.rows
             if date_from <= row['timestamp'][:10] <= date_to],
            key=lambda row: (row['workspace_alias'], row['timestamp'], row['session_id'],
                             row['model_key'] or ''),
        )

    # SQLは読出しとidentity joinだけを担当し、採用規則は共通のpure処理へ渡す。
    def _select_usage(self, connection: Connection) -> UsageSelection:
        """保存済みの両入力を参照し、差分scanでも片側の情報を失わない。"""
        rows = connection.execute(
            f"""WITH {self._all_usage_cte()}, duplicates AS (
                SELECT r.session_id FROM all_usage u JOIN runs r ON r.run_id = u.run_id
                WHERE r.source = 'copilot_cli' AND r.run_kind != 'request_history'
                GROUP BY r.session_id HAVING COUNT(DISTINCT u.source_file_id) > 1
            ), identity_blocked_history AS (
                SELECT DISTINCT hr.session_id FROM all_usage h
                JOIN runs hr ON hr.run_id = h.run_id
                JOIN diagnostics d ON d.session_id = hr.session_id
                    AND d.history_scope = h.history_scope
                    AND d.diagnostic_code = 'cli_session_identity_conflict'
                WHERE hr.source = 'copilot_cli' AND hr.run_kind = 'request_history'
                AND NOT EXISTS (
                    SELECT 1 FROM all_usage e JOIN runs er ON er.run_id = e.run_id
                    WHERE er.source = 'copilot_cli' AND er.run_kind != 'request_history'
                    AND er.session_id = hr.session_id AND e.history_scope = h.history_scope
                    AND e.source_file_id != d.source_file_id AND e.reported_nano_aiu IS NOT NULL
                )
            )
            SELECT u.*, r.source, r.session_id, r.run_kind, r.run_selection_policy,
                s.workspace_id, COALESCE(w.display_alias, '-') AS workspace_alias,
                CASE WHEN r.run_kind = 'request_history'
                    AND r.session_id IN (SELECT session_id FROM identity_blocked_history)
                    THEN 'event_identity_conflict' ELSE COALESCE(hs.error, hi.error) END
                    AS history_error, '' AS coverage
            FROM all_usage u JOIN runs r ON r.run_id = u.run_id
            LEFT JOIN sessions s ON s.session_id = r.session_id AND s.source = r.source
            LEFT JOIN workspaces w ON w.workspace_id = s.workspace_id
            LEFT JOIN cli_history_inputs hi ON hi.scope = u.history_scope AND hi.session_id = ''
            LEFT JOIN cli_history_inputs hs ON hs.scope = u.history_scope
                AND hs.session_id = r.session_id
            WHERE r.source != 'copilot_cli' OR (
                r.session_id NOT IN (SELECT * FROM duplicates)
                AND (r.run_kind != 'request_history'
                     OR r.session_id NOT IN (SELECT session_id FROM identity_blocked_history))
            )
            """
        )
        # SQLite境界の列契約を一度だけ型付き射影へ変換する。
        return CliHistorySelection.select([cast(UsageRow, dict(row)) for row in rows])

    # 日付行を捏造せず、期間に関係する重複除外session数を返す。
    def excluded_cli_sessions(self, date_from: str, date_to: str) -> int:
        """全期間で重複を判定し、選択済み行が指定期間にあるsessionを数える。"""

        self.initialize()
        with self.connect() as connection:
            row: Row = connection.execute(
                f"""WITH {self._all_usage_cte()}
                SELECT COUNT(*) AS count FROM (
                    SELECT d.session_id FROM diagnostics d
                    WHERE d.diagnostic_code = 'cli_session_identity_conflict'
                    AND d.session_id IS NOT NULL AND EXISTS (
                        SELECT 1 FROM all_usage h JOIN runs hr ON hr.run_id = h.run_id
                        WHERE hr.source = 'copilot_cli' AND hr.run_kind = 'request_history'
                        AND hr.session_id = d.session_id AND h.history_scope = d.history_scope
                        AND substr(h.timestamp, 1, 10) BETWEEN ? AND ?
                    ) AND NOT EXISTS (
                        SELECT 1 FROM all_usage e JOIN runs er ON er.run_id = e.run_id
                        WHERE er.source = 'copilot_cli' AND er.run_kind != 'request_history'
                        AND er.session_id = d.session_id AND e.history_scope = d.history_scope
                        AND e.source_file_id != d.source_file_id
                        AND e.reported_nano_aiu IS NOT NULL
                    )
                    UNION
                    SELECT r.session_id FROM all_usage u JOIN runs r ON r.run_id = u.run_id
                    WHERE r.source = 'copilot_cli' AND r.run_kind != 'request_history'
                    GROUP BY r.session_id
                    HAVING COUNT(DISTINCT u.source_file_id) > 1
                       AND MAX(CASE WHEN substr(u.timestamp, 1, 10) BETWEEN ? AND ?
                                    THEN 1 ELSE 0 END) = 1
                )""",
                (date_from, date_to, date_from, date_to),
            ).fetchone()
            return int(row["count"])

    # 同一列の射影でのみaggregateとmodelを結合する。
    @staticmethod
    def _all_usage_cte() -> str:
        """架空modelを永続化せず、未配賦行をNULL modelとして射影する。"""

        return """all_usage AS (
            SELECT usage_id, run_id, source_file_id, timestamp, model_key, event_order,
                raw_input_tokens, fresh_input_tokens, cached_input_tokens, cache_write_tokens,
                output_tokens, reasoning_tokens, reported_ai_credits, reported_nano_aiu,
                ai_credits_source, confidence, 'model' AS attribution_scope,
                NULL AS estimated_primary_model, observed_nano_aiu, inclusion_status,
                request_id, history_scope, match_key, initiator, evidence_error,
                tokens_valid, reasoning_tokens_valid
            FROM usage_records
            UNION ALL
            SELECT -aggregate_id, run_id, source_file_id, timestamp, NULL, event_order,
                0, 0, 0, 0, 0, 0, NULL, reported_nano_aiu, ai_credits_source, confidence,
                'session_total', estimated_primary_model, observed_nano_aiu, inclusion_status,
                request_id, history_scope, match_key, initiator, evidence_error,
                tokens_valid, reasoning_tokens_valid
            FROM session_aggregates
        )"""

    # 空/truncateを含むCLIファイルを同一transactionで置換する。
    def _replace_cli_files(
        self,
        connection: Connection,
        records: list[ParsedUsageRecord],
        source_files: list[ParsedSourceFile],
    ) -> None:
        """再解析したCLIファイルだけを置換し、Chat行や読取失敗ファイルは保持する。"""

        files = {item.source_file_path: item for item in source_files}
        for record in records:
            if record.source == "copilot_cli":
                files[record.source_file_path] = ParsedSourceFile(
                    "copilot_cli_history" if record.run_kind == RunKind.HISTORY else record.source,
                    record.source_file_path, record.source_file_display_alias
                )
        for item in files.values():
            source_id = self._upsert_source_file(connection, item)
            connection.execute("DELETE FROM usage_records WHERE source_file_id = ?", (source_id,))
            connection.execute(
                "DELETE FROM session_aggregates WHERE source_file_id = ?", (source_id,)
            )
            connection.execute("DELETE FROM diagnostics WHERE source_file_id = ?", (source_id,))
            connection.execute(
                "DELETE FROM cli_history_inputs WHERE source_file_id = ?", (source_id,)
            )
        connection.execute("""DELETE FROM runs WHERE source = 'copilot_cli'
            AND run_id NOT IN (SELECT run_id FROM usage_records)
            AND run_id NOT IN (SELECT run_id FROM session_aggregates)""")

    # diagnostic 集計を返す。
    def diagnostic_counts(self) -> list[Row]:
        """diagnostic code ごとの件数を返す。"""

        self.initialize()
        with self.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT
                      diagnostic_code AS code,
                      COUNT(*) AS count
                    FROM diagnostics
                    GROUP BY diagnostic_code
                    ORDER BY diagnostic_code
                    """
                )
            )

    # scan-derived data を削除する。
    def _reset_scan_data(self, connection: Connection) -> None:
        """schema を保持し scan 由来 table を削除する。"""

        for table in (
            "cli_history_inputs",
            "usage_records",
            "session_aggregates",
            "diagnostics",
            "runs",
            "sessions",
            "source_files",
            "workspaces",
            "scan_runs",
        ):
            connection.execute(f"DELETE FROM {table}")

    # 今回再parseしたsource fileの旧diagnosticを置換する。
    def _delete_replaced_diagnostics(
        self,
        connection: Connection,
        records: list[ParsedUsageRecord],
        diagnostics: list[DiagnosticRecord],
    ) -> None:
        """incremental scanで再観測したfileの旧scan diagnosticを削除する。"""

        source_files: dict[tuple[str, str], ParsedUsageRecord | DiagnosticRecord] = {}
        for item in [*records, *diagnostics]:
            key: tuple[str, str] = (
                item.source,
                str(Path(item.source_file_path).resolve()),
            )
            source_files[key] = item
        for item in source_files.values():
            source_file_id: int = self._upsert_source_file(connection, item)
            connection.execute(
                """
                DELETE FROM diagnostics
                WHERE source_file_id = ?
                """,
                (source_file_id,),
            )

    # source file を upsert する。
    def _upsert_source_file(
        self,
        connection: Connection,
        record: ParsedUsageRecord | DiagnosticRecord | ParsedSourceFile,
    ) -> int:
        """source file metadata を保存して ID を返す。"""

        source_type = (
            "copilot_cli_history"
            if isinstance(record, ParsedUsageRecord) and record.run_kind == RunKind.HISTORY
            else record.source
        )
        source_path: Path = Path(record.source_file_path)
        path_hash: str = self._hash_text(str(source_path.resolve()))
        mtime_ns: int = source_path.stat().st_mtime_ns if source_path.exists() else 0
        size_bytes: int = source_path.stat().st_size if source_path.exists() else 0
        connection.execute(
            """
            INSERT INTO source_files
              (source_type, path_hash, display_alias, mtime_ns, size_bytes, last_offset,
               last_event_hash, scan_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_type, path_hash) DO UPDATE SET
              display_alias = excluded.display_alias,
              mtime_ns = excluded.mtime_ns,
              size_bytes = excluded.size_bytes,
              scan_status = excluded.scan_status
            """,
            (
                source_type,
                path_hash,
                record.source_file_display_alias,
                mtime_ns,
                size_bytes,
                size_bytes,
                self._hash_text(f"{mtime_ns}:{size_bytes}"),
                "ok",
            ),
        )
        row: Row = connection.execute(
            "SELECT source_id FROM source_files WHERE source_type = ? AND path_hash = ?",
            (source_type, path_hash),
        ).fetchone()
        return int(row["source_id"])

    # workspace を upsert する。
    def _upsert_workspace(self, connection: Connection, record: ParsedUsageRecord) -> int | None:
        """workspace identity があれば保存して ID を返す。"""

        if record.workspace_identity is None:
            return None
        workspace_hash: str = self._hash_text(record.workspace_identity)
        display_alias: str = record.workspace_display_alias or "workspace"
        connection.execute(
            """
            INSERT INTO workspaces (workspace_path_hash, display_alias, source)
            VALUES (?, ?, ?)
            ON CONFLICT(source, workspace_path_hash) DO UPDATE SET
              display_alias = excluded.display_alias
            """,
            (workspace_hash, display_alias, record.source),
        )
        row: Row = connection.execute(
            "SELECT workspace_id FROM workspaces WHERE source = ? AND workspace_path_hash = ?",
            (record.source, workspace_hash),
        ).fetchone()
        return int(row["workspace_id"])

    # session を upsert する。
    def _upsert_session(self, connection: Connection, record: ParsedUsageRecord) -> None:
        """session metadata を保存する。"""

        workspace_id: int | None = self._upsert_workspace(connection, record)
        connection.execute(
            """
            INSERT INTO sessions
              (session_id, source, workspace_id, started_at, ended_at,
               producer_version)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, source) DO UPDATE SET
              workspace_id = COALESCE(excluded.workspace_id, sessions.workspace_id),
              ended_at = excluded.ended_at,
              producer_version = COALESCE(excluded.producer_version, sessions.producer_version)
            """,
            (
                record.session_id,
                record.source,
                workspace_id,
                record.timestamp,
                record.timestamp,
                record.producer_version,
            ),
        )

    # run を upsert する。
    def _upsert_run(self, connection: Connection, scan_id: int, record: ParsedUsageRecord) -> int:
        """run metadata を保存して ID を返す。"""

        self._upsert_session(connection, record)
        existing_row: Row | None = self._select_run(connection, record)
        if existing_row is not None:
            return int(existing_row["run_id"])

        connection.execute(
            """
            INSERT OR IGNORE INTO runs
              (session_id, source, run_kind, shutdown_timestamp, task_manifest_id,
               run_selection_policy, is_delta, scan_id)
            VALUES (?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                record.session_id,
                record.source,
                record.run_kind,
                record.run_timestamp,
                record.run_selection_policy,
                0,
                scan_id,
            ),
        )
        row: Row | None = self._select_run(connection, record)
        if row is None:
            raise RuntimeError("Run row was not persisted.")
        return int(row["run_id"])

    # run row を取得する。
    def _select_run(self, connection: Connection, record: ParsedUsageRecord) -> Row | None:
        """source-specific run identity に対応する row を返す。"""

        return connection.execute(
            """
            SELECT run_id FROM runs
            WHERE session_id = ?
              AND source = ?
              AND run_kind = ?
              AND run_selection_policy = ?
              AND (
                    shutdown_timestamp = ?
                 OR (shutdown_timestamp IS NULL AND ? IS NULL)
              )
            """,
            (
                record.session_id,
                record.source,
                record.run_kind,
                record.run_selection_policy,
                record.run_timestamp,
                record.run_timestamp,
            ),
        ).fetchone()

    # usage record を保存する。
    def _insert_usage_record(
        self, connection: Connection, scan_id: int, record: ParsedUsageRecord
    ) -> bool:
        """usage record を保存し、新規挿入なら True を返す。"""

        source_file_id: int = self._upsert_source_file(connection, record)
        run_id: int = self._upsert_run(connection, scan_id, record)
        if record.model_key is None:
            connection.execute(
                """INSERT INTO session_aggregates
                    (run_id, source_file_id, timestamp, event_order, reported_nano_aiu,
                     ai_credits_source, confidence, estimated_primary_model,
                     observed_nano_aiu, inclusion_status, request_id,
                     history_scope, match_key, initiator, evidence_error,
                     tokens_valid, reasoning_tokens_valid)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    source_file_id,
                    record.timestamp,
                    record.event_order,
                    record.reported_nano_aiu,
                    record.ai_credits_source,
                    record.confidence,
                    record.estimated_primary_model,
                    record.observed_nano_aiu,
                    record.inclusion_status,
                    record.request_id,
                    record.history_scope, record.match_key, record.initiator,
                    record.evidence_error, int(record.tokens_valid),
                    int(record.reasoning_tokens_valid),
                ),
            )
            return True
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO usage_records
              (run_id, source_file_id, record_key, request_id, timestamp, model_key,
               raw_input_tokens, fresh_input_tokens, cached_input_tokens,
               cache_write_tokens, output_tokens, reasoning_tokens,
               reported_ai_credits, reported_nano_aiu, ai_credits_source, confidence,
               context_window_tokens, event_order, observed_nano_aiu, inclusion_status,
               history_scope, match_key, initiator, evidence_error,
                     tokens_valid, reasoning_tokens_valid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                source_file_id,
                self._record_key(record),
                record.request_id,
                record.timestamp,
                record.model_key,
                record.token_buckets.raw_input_tokens,
                record.token_buckets.fresh_input_tokens,
                record.token_buckets.cached_input_tokens,
                record.token_buckets.cache_write_tokens,
                record.token_buckets.output_tokens,
                record.token_buckets.reasoning_tokens,
                record.reported_ai_credits,
                record.reported_nano_aiu,
                record.ai_credits_source,
                record.confidence,
                record.event_order,
                record.observed_nano_aiu,
                record.inclusion_status,
                record.history_scope, record.match_key, record.initiator,
                record.evidence_error, int(record.tokens_valid),
                    int(record.reasoning_tokens_valid),
            ),
        )
        return cursor.rowcount > 0

    # source 種別ごとの natural key を保存用 key に変換する。
    def _record_key(self, record: ParsedUsageRecord) -> str:
        """source-specific natural key から duplicate avoidance 用 key を返す。"""

        if record.run_kind == RunKind.HISTORY:
            return f"history:{record.session_id}:{record.request_id}"
        if record.run_kind == "compaction":
            key = f"compaction:{record.session_id}:{record.request_id}"
            # 未確定な別イベントは照合キーが同じでも欠落件数を縮約しない。
            if record.inclusion_status == "unresolved":
                key += f":unresolved:{record.event_order}"
            return key
        if record.source == "copilot_cli":
            return (
                f"{record.session_id}:{record.event_order}:"
                f"{record.model_key}:{record.ai_credits_source}"
            )
        return f"{record.request_id or record.timestamp}:{record.model_key}"

    # diagnostic を保存する。
    def _insert_diagnostic(
        self, connection: Connection, scan_id: int, diagnostic: DiagnosticRecord
    ) -> None:
        """diagnostic record を保存する。"""

        source_file_id: int = self._upsert_source_file(connection, diagnostic)
        connection.execute(
            """
            INSERT INTO diagnostics
              (scan_id, source_file_id, line_number, diagnostic_code, message,
               session_id, history_scope)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scan_id,
                source_file_id,
                diagnostic.line_number,
                diagnostic.code,
                diagnostic.message,
                diagnostic.session_id,
                diagnostic.history_scope,
            ),
        )

    # scan run を保存する。
    def _insert_scan_run(self, connection: Connection, root_label: str, warnings_count: int) -> int:
        """scan run を作成して ID を返す。"""

        cursor = connection.execute(
            """
            INSERT INTO scan_runs (started_at, ended_at, root, result, warnings_count)
            VALUES (datetime('now'), NULL, ?, ?, ?)
            """,
            (self._hash_text(root_label), "running", warnings_count),
        )
        last_row_id: int | None = cursor.lastrowid
        if last_row_id is None:
            raise RuntimeError("Scan run row was not persisted.")
        return last_row_id

    # SHA-256 hash を返す。
    def _hash_text(self, text: str) -> str:
        """raw text を SHA-256 hash に変換する。"""

        return hashlib.sha256(text.encode("utf-8")).hexdigest()
