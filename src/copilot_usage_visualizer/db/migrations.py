from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection


# 派生DBの形式判定と明示的な全件再生成を担当する。
class MigrationService:
    """既知の旧DBだけをfull scanで原子的に作り直す。"""

    BASELINE: str = "010_cli_request_history_baseline"
    # 旧形式の識別に使う正確なmigration名。
    LEGACY_IDS: frozenset[str] = frozenset(
        {
            "001_initial",
            "002_ai_credits_validation_evidence",
            "003_copilot_pricing_rules",
            "004_reported_ai_credits",
            "005_cli_reported_nano_aiu",
            "006_direct_credits_baseline",
            "007_cli_cumulative_baseline",
            "008_estimated_primary_model",
            "009_cli_compaction_baseline",
        }
    )
    # 別アプリや混在DBを再生成しないための構造判定。
    CORE_COLUMNS: dict[str, set[str]] = {
        "schema_migrations": {"migration_id", "applied_at"},
        "source_files": {"source_id", "source_type", "path_hash", "display_alias"},
        "sessions": {"session_id", "source", "workspace_id"},
        "workspaces": {"workspace_id", "workspace_path_hash", "display_alias"},
        "runs": {"run_id", "session_id", "source", "run_kind", "run_selection_policy"},
        "usage_records": {"usage_id", "run_id", "source_file_id", "record_key", "model_key"},
        "scan_runs": {"scan_id", "started_at", "root", "warnings_count"},
        "diagnostics": {"diagnostic_id", "scan_id", "diagnostic_code", "message"},
    }

    def __init__(self, schema_path: Path) -> None:
        """新schemaの場所を保持する。"""
        self._schema_path = schema_path

    # full scanの保存transactionとschema再生成を一体化する。
    def apply(self, connection: Connection, rebuild: bool = False) -> None:
        """非fullでは旧DBを変更せず、未知DBはfullでも拒否する。"""
        tables = {
            str(r[0])
            for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        ids: set[str] = set()
        if "schema_migrations" in tables:
            columns = self._columns(connection, "schema_migrations")
            if "migration_id" in columns:
                ids = {
                    str(r[0])
                    for r in connection.execute("SELECT migration_id FROM schema_migrations")
                }
        if self.BASELINE in ids:
            current_tables = set(self.CORE_COLUMNS) | {"session_aggregates", "cli_history_inputs"}
            required_usage = {"history_scope", "match_key", "initiator", "evidence_error",
                              "tokens_valid", "reasoning_tokens_valid"}
            required_diagnostics = {"history_scope"}
            if (ids != {self.BASELINE} or tables != current_tables
                    or not all(cols <= self._columns(connection, table)
                               for table, cols in self.CORE_COLUMNS.items())
                    or not all(required_usage <= self._columns(connection, table)
                               for table in ("usage_records", "session_aggregates"))
                    or not required_diagnostics <= self._columns(connection, "diagnostics")
                    or connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type IN ('view', 'trigger') LIMIT 1"
                    ).fetchone() is not None):
                raise ValueError("Unsupported or unknown mixed application database; "
                                 "use a new output database.")
            return
        if tables:
            allowed = set(self.CORE_COLUMNS) | {
                "session_aggregates",
                "cli_history_inputs",
                "model_prices",
                "model_aliases",
                "ai_credits_validation_evidence",
            }
            extra_objects = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type IN ('view', 'trigger') LIMIT 1"
            ).fetchone()
            owned = (
                extra_objects is None
                and bool(ids & self.LEGACY_IDS)
                and ids <= self.LEGACY_IDS
                and tables <= allowed
                and all(
                    table in tables and cols <= self._columns(connection, table)
                    for table, cols in self.CORE_COLUMNS.items()
                )
            )
            if not rebuild or not owned:
                raise ValueError(
                    "Unsupported legacy database. Use scan --full to rebuild a recognized "
                    "application database (default: output/copilot-usage.db); "
                    "unknown databases are never rebuilt. Use a new database for unknown files."
                )
            # DDLを含めてrollback可能にし、executescriptの暗黙commitを避ける。
            connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN")
        if tables:
            for table in sorted(tables):
                connection.execute(f'DROP TABLE "{table}"')
        for statement in self._schema_path.read_text(encoding="utf-8").split(";"):
            if statement.strip():
                connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?, datetime('now'))", (self.BASELINE,)
        )

    # 入力DBの構造を読み取り専用で検査する。
    @staticmethod
    def _columns(connection: Connection, table: str) -> set[str]:
        """呼出側で定数に限定したtableのcolumn集合を返す。"""
        return {str(r[1]) for r in connection.execute(f'PRAGMA table_info("{table}")')}
