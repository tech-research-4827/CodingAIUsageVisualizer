from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Sequence
from contextlib import ExitStack, closing
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from copilot_usage_visualizer.db.migrations import MigrationService
from copilot_usage_visualizer.db.repository import UsageRepository

# SQLite の行コピーだけに使う I/O 境界の値型。
SqlValue = str | int | float | bytes | None


# 衝突の報告に絶対パスを含めない。
@dataclass(frozen=True)
class SessionOverlap:
    """取得元・session ID と、1 始まりの入力番号を返す。"""

    source: str
    session_id: str
    input_numbers: tuple[int, ...]


# CLI と application facade の間の merge 結果。
@dataclass(frozen=True)
class MergeResult:
    """統合した入力数と重なる session を保持する。"""

    inputs_count: int
    overlaps: tuple[SessionOverlap, ...]


# 派生 DB だけを扱い、既存の schema と選択を使う repository service。
class DerivedDatabaseMerger:
    """入力を読み取り専用で保持し、新規 SQLite へ観測値を詰め直す。"""

    # table 名は外部入力から組み立てず、現行 schema の識別順を使う。
    ID_TABLES: tuple[tuple[str, str], ...] = (
        ("source_files", "source_id"), ("workspaces", "workspace_id"),
        ("scan_runs", "scan_id"),
    )

    # 元の値が同じでも入力番号で namespace を分け、パスを保存しない。
    @staticmethod
    def _identity(input_number: int, value: SqlValue) -> SqlValue:
        """NULL を維持し、非 NULL の匿名化済み識別を再 hash する。"""

        if value is None:
            return None
        return hashlib.sha256(f"merge:{input_number}:{value}".encode()).hexdigest()

    # schema.sql が正本なので、独自の列・制約リストは持たない。
    @staticmethod
    def _schema(connection: sqlite3.Connection) -> tuple[tuple[str, str, str], ...]:
        """SQLite 保存時の空白と IF NOT EXISTS の差だけを除いて比較する。"""

        return tuple(
            (str(row[0]), str(row[1]), re.sub(r"\s+", " ", str(row[2])).strip()
             .replace("IF NOT EXISTS ", ""))
            for row in connection.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
        )

    # 入力の形式を初期化・migration せず、既存の baseline 判定へ渡す。
    @staticmethod
    def _validate(connection: sqlite3.Connection) -> None:
        """空ファイル・旧形式・別 DB を入力として受け付けない。"""

        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'schema_migrations' AND type = 'table'"
        ).fetchone() is None:
            raise ValueError("Merge inputs must be supported derived databases.")
        MigrationService(Path(__file__).with_name("schema.sql")).apply(connection)

    # 全接続を同じ処理寿命で保持し、WAL を無視せず snapshot を読む。
    def merge(self, input_paths: Sequence[Path], output_path: Path) -> MergeResult:
        """2 個以上の別ファイルを統合し、失敗時は自分の新規出力を除去する。"""

        if len(input_paths) < 2:
            raise ValueError("Merge requires at least two input databases.")
        for index, path in enumerate(input_paths):
            if any(path.samefile(previous) for previous in input_paths[:index]):
                raise ValueError("Merge input databases must be different files.")
        if output_path.exists() or output_path.is_symlink():
            raise ValueError("Merge output must be a new file.")
        created: bool = False
        completed: bool = False
        try:
            with ExitStack() as stack:
                inputs: list[sqlite3.Connection] = []
                for path in input_paths:
                    source: sqlite3.Connection = stack.enter_context(closing(sqlite3.connect(
                        path.resolve().as_uri() + "?mode=ro", uri=True
                    )))
                    source.row_factory = sqlite3.Row
                    source.execute("BEGIN")
                    self._validate(source)
                    inputs.append(source)
                # アプリが生成する schema を比較元にし、契約を複製しない。
                reference: sqlite3.Connection = stack.enter_context(closing(
                    sqlite3.connect(":memory:")
                ))
                MigrationService(Path(__file__).with_name("schema.sql")).apply(reference)
                expected_schema: tuple[tuple[str, str, str], ...] = self._schema(reference)
                if any(self._schema(source) != expected_schema for source in inputs):
                    raise ValueError("Merge input schemas must match the supported schema.")
                with output_path.open("xb"):
                    created = True
                repository: UsageRepository = UsageRepository(output_path)
                with closing(repository.connect()) as destination, destination:
                    # Connection の context manager は close しないため、所有権を明示する。
                    MigrationService(Path(__file__).with_name("schema.sql")).apply(destination)
                    owners: dict[tuple[str, str], list[int]] = {}
                    run_keys: dict[tuple[SqlValue, ...], int] = {}
                    for number, source in enumerate(inputs, 1):
                        self._copy_input(source, destination, number, owners, run_keys)
                    cursor: sqlite3.Cursor = destination.execute(
                        "INSERT INTO scan_runs "
                        "(started_at, ended_at, root, result, warnings_count) "
                        "VALUES (datetime('now'), datetime('now'), ?, 'ok', 0)",
                        (hashlib.sha256(b"derived-database-merge").hexdigest(),),
                    )
                    scan_id: int = cast(int, cursor.lastrowid)
                    overlaps: tuple[SessionOverlap, ...] = tuple(
                        SessionOverlap(source, session, tuple(numbers))
                        for (source, session), numbers in sorted(owners.items())
                        if len(numbers) > 1
                    )
                    for overlap in overlaps:
                        destination.execute(
                            "INSERT INTO diagnostics (scan_id, session_id, diagnostic_code, "
                            "message) VALUES (?, ?, 'merge_session_overlap', ?)",
                            (scan_id, overlap.session_id,
                             f"{overlap.source}: session occurs in inputs "
                             + ", ".join(map(str, overlap.input_numbers))
                             + "; existing selection rules apply."),
                        )
                    repository.refresh_selection_diagnostics(destination, scan_id)
                    destination.execute(
                        "UPDATE scan_runs SET warnings_count = "
                        "(SELECT COUNT(*) FROM diagnostics WHERE scan_id = ?) WHERE scan_id = ?",
                        (scan_id, scan_id),
                    )
                completed = True
                return MergeResult(len(inputs), overlaps)
        except sqlite3.Error as exc:
            raise ValueError("Merge could not read or combine the derived databases.") from exc
        finally:
            # 外部ファイルを掃除しない。今回 exclusive 作成した失敗出力だけが対象。
            if created and not completed:
                output_path.unlink()

    # GUI 検証と共有場所への転送は、WAL 込みの整合した native backup を使う。
    @classmethod
    def snapshot(cls, input_path: Path, output_path: Path) -> None:
        """派生 DB を読み取り専用で開き、新しい snapshot だけを作る。"""

        created: bool = False
        completed: bool = False
        try:
            with closing(sqlite3.connect(
                input_path.resolve().as_uri() + "?mode=ro", uri=True,
            )) as source:
                source.execute("BEGIN")
                cls._validate(source)
                with output_path.open("xb"):
                    created = True
                with closing(sqlite3.connect(output_path)) as destination:
                    source.backup(destination)
                completed = True
        except sqlite3.Error as exc:
            raise ValueError("Could not snapshot the derived database.") from exc
        finally:
            if created and not completed:
                output_path.unlink()

    # SQL の列名は schema 比較済みの SQLite 行だけから組み立てる。
    @staticmethod
    def _insert(
        destination: sqlite3.Connection, table: str, values: dict[str, SqlValue],
    ) -> int:
        """行を保存し、autoincrement の新しい ID を返す。"""

        columns: str = ", ".join(f'"{name}"' for name in values)
        placeholders: str = ", ".join("?" for _ in values)
        cursor: sqlite3.Cursor = destination.execute(
            f'INSERT INTO "{table}" ({columns}) VALUES ({placeholders})',
            tuple(values.values()),
        )
        return cast(int, cursor.lastrowid)

    # I/O 境界の Row をコピーし、参照先と照合範囲を同じ入力 namespace にする。
    @classmethod
    def _remap(
        cls, row: sqlite3.Row, maps: dict[str, dict[int, int]], number: int,
    ) -> dict[str, SqlValue]:
        """入力 row を変更せず、新 ID への対応をコピーへ反映する。"""

        values: dict[str, SqlValue] = {name: row[name] for name in row.keys()}
        for column, table in (
            ("workspace_id", "workspaces"), ("scan_id", "scan_runs"),
            ("source_file_id", "source_files"), ("run_id", "runs"),
        ):
            if column in values and values[column] is not None:
                old_id: int = cast(int, values[column])
                if old_id not in maps[table]:
                    raise ValueError("Merge input contains invalid database relationships.")
                values[column] = maps[table][old_id]
        for column in ("path_hash", "workspace_path_hash", "history_scope", "scope", "root"):
            if column in values:
                values[column] = cls._identity(number, values[column])
        return values

    # 主キーの共有は既存 scan と同じ identity に限り、usage の行を削らない。
    def _copy_input(
        self, source: sqlite3.Connection, destination: sqlite3.Connection, number: int,
        owners: dict[tuple[str, str], list[int]],
        run_keys: dict[tuple[SqlValue, ...], int],
    ) -> None:
        """関係の親から順にコピーし、session/run の NULL 同値を維持する。"""

        maps: dict[str, dict[int, int]] = {
            "source_files": {}, "workspaces": {}, "scan_runs": {}, "runs": {},
        }
        for table, primary_key in self.ID_TABLES:
            for row in source.execute(f'SELECT * FROM "{table}" ORDER BY "{primary_key}"'):
                values: dict[str, SqlValue] = {name: row[name] for name in row.keys()}
                old_id: int = cast(int, values.pop(primary_key))
                for column in ("path_hash", "workspace_path_hash", "root"):
                    if column in values:
                        values[column] = self._identity(number, values[column])
                maps[table][old_id] = self._insert(destination, table, values)
        for row in source.execute("SELECT * FROM sessions ORDER BY source, session_id"):
            key: tuple[str, str] = (str(row["source"]), str(row["session_id"]))
            owners.setdefault(key, []).append(number)
            if destination.execute(
                "SELECT 1 FROM sessions WHERE source = ? AND session_id = ?", key,
            ).fetchone() is None:
                self._insert(destination, "sessions", self._remap(row, maps, number))
        for row in source.execute("SELECT * FROM runs ORDER BY run_id"):
            # Python tuple の None 同値は _select_run の NULL 比較と同じ。
            run_key: tuple[SqlValue, ...] = tuple(row[column] for column in (
                "session_id", "source", "run_kind", "run_selection_policy", "shutdown_timestamp",
            ))
            if run_key not in run_keys:
                values = {name: row[name] for name in row.keys() if name != "run_id"}
                if row["scan_id"] not in maps["scan_runs"]:
                    raise ValueError("Merge input contains invalid database relationships.")
                values["scan_id"] = maps["scan_runs"][row["scan_id"]]
                run_keys[run_key] = self._insert(destination, "runs", values)
            maps["runs"][row["run_id"]] = run_keys[run_key]
        for table, primary_key in (
            ("usage_records", "usage_id"), ("session_aggregates", "aggregate_id"),
            ("cli_history_inputs", None), ("diagnostics", "diagnostic_id"),
        ):
            for row in source.execute(f'SELECT * FROM "{table}"'):
                if table == "diagnostics" and (
                    row["diagnostic_code"] == "cli_duplicate_session"
                    or str(row["diagnostic_code"]).startswith((
                        "cli_history_selection_", "cli_compaction_unresolved_",
                    ))
                ):
                    continue
                values = self._remap(row, maps, number)
                if primary_key is not None:
                    values.pop(primary_key)
                self._insert(destination, table, values)
