from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from copilot_usage_visualizer.normalization.contract_period import UsageDateResolver
from copilot_usage_visualizer.normalization.usage import (
    AiCreditsSource,
    CliHistorySnapshot,
    DiagnosticRecord,
    NanoAiuAmount,
    ParsedHistoryInput,
    ParsedUsageRecord,
    RunKind,
    RunSelectionPolicy,
    TokenBuckets,
)


# 本文を捨てた圧縮イベントの照合情報。
@dataclass(frozen=True)
class CliCompaction:
    """観測値と同一イベント判定に必要なメタデータだけを保持する。"""

    timestamp: str | None
    model: str | None
    amount: int | None
    tokens: tuple[int, ...] | None
    identifiers: tuple[str, ...]
    order: int

    # 外部時刻を比較用UTCへ変換する。
    @staticmethod
    def time(value: object) -> datetime:
        """timezone不明の時刻を推測しない。"""
        if not isinstance(value, str):
            raise ValueError("Missing timestamp.")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Timestamp has no timezone.")
        return parsed.astimezone(UTC)

    # 外部token値は完全な四bucketだけを照合に使う。
    @staticmethod
    def token_tuple(values: list[object]) -> tuple[int, ...] | None:
        """欠落や負数を0に置換しない。"""
        if any(type(v) is not int or v < 0 for v in values):
            return None
        return tuple(int(v) for v in values)  # type: ignore[arg-type]

    # session本文やsummaryContentを読み出さず必要なキーだけ抽出する。
    @classmethod
    def parse(cls, event: dict, order: int) -> CliCompaction:
        """JSON入出力境界で既知の値を検証する。"""
        data = event.get("data")
        data = data if isinstance(data, dict) else {}
        usage = data.get("compactionTokensUsed")
        usage = usage if isinstance(usage, dict) else {}
        billing = usage.get("copilotUsage")
        billing = billing if isinstance(billing, dict) else {}
        try:
            amount = NanoAiuAmount.from_json_value(billing.get("totalNanoAiu")).value
        except ValueError:
            amount = None
        timestamp = event.get("timestamp", event.get("time"))
        try:
            if not isinstance(timestamp, str):
                raise ValueError("Missing timestamp.")
            cls.time(timestamp)
            UsageDateResolver().resolve(timestamp)
        except ValueError:
            timestamp = None
        models = [x for x in (data.get("behaviorModelId"), usage.get("model")) if x is not None]
        model = (
            models[0]
            if models and all(isinstance(x, str) and x.strip() and x == models[0] for x in models)
            else None
        )
        ids = tuple(
            f"{kind}:{value}"
            for kind, value in (
                ("request", data.get("requestId")),
                ("service", data.get("serviceRequestId")),
                ("event", event.get("id")),
            )
            if isinstance(value, str) and value.strip()
        )
        tokens = cls.token_tuple(
            [
                usage.get(k)
                for k in ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")
            ]
        )
        return cls(timestamp, model, amount, tokens, ids, order)

    # 金額だけではなく時刻とモデルとtokenも比較する。
    def signature(self) -> tuple[object, ...]:
        """行番号とファイル位置を含まない照合キー。"""
        return (
            self.time(self.timestamp) if self.timestamp else None,
            self.model,
            self.amount,
            self.tokens,
        )


# イベント直接値と履歴の包含関係を照合する。
class CliCompactionReconciler:
    """圧縮の観測値を正規化し、採用判定は保存済み履歴との共通選択へ委ねる。"""

    # 最新累積候補が確定してから全圧縮を一括照合する。
    @classmethod
    def reconcile(
        cls,
        path: Path,
        session_id: str,
        records: list[ParsedUsageRecord],
        events: list[CliCompaction],
        diagnostics: list[DiagnosticRecord],
    ) -> list[ParsedUsageRecord]:
        """同一圧縮を一度だけ数え、曖昧な場合は観測値のみ残す。"""
        if not events:
            return records
        unique: list[CliCompaction] = []
        conflict = False
        for event in events:
            matches = [
                old
                for old in unique
                if (
                    set(old.identifiers) & set(event.identifiers)
                    or (
                        not old.identifiers
                        and not event.identifiers
                        and old.signature() == event.signature()
                    )
                )
            ]
            if matches:
                if len(matches) != 1 or matches[0].signature() != event.signature():
                    conflict = True
            else:
                unique.append(event)
        base = records[0] if records else None
        for event in unique:
            if event.timestamp is None:
                diagnostics.append(
                    DiagnosticRecord(
                        "copilot_cli",
                        str(path),
                        path.name,
                        "cli_compaction_invalid_timestamp",
                        "Compaction timestamp is invalid; no period attribution available.",
                        event.order,
                    )
                )
                continue
            # identityはhashだけを保存し、元のrequest/service IDは外へ出さない。
            identity = json.dumps(
                [
                    session_id,
                    CliCompaction.time(event.timestamp).isoformat(timespec="microseconds"),
                    event.model,
                    event.amount,
                    event.tokens,
                ],
                separators=(",", ":"),
                ensure_ascii=True,
            )
            key = hashlib.sha256(identity.encode()).hexdigest()
            records.append(
                ParsedUsageRecord(
                    source="copilot_cli",
                    source_file_path=str(path),
                    source_file_display_alias=path.name,
                    workspace_identity=base.workspace_identity if base else None,
                    workspace_display_alias=base.workspace_display_alias if base else None,
                    session_id=session_id,
                    run_kind=RunKind.COMPACTION,
                    run_timestamp=event.timestamp,
                    run_selection_policy="reconciled_compaction",
                    request_id=key,
                    timestamp=event.timestamp,
                    model_key=event.model,
                    token_buckets=TokenBuckets(0, 0, 0, 0, 0),
                    confidence="credits_unavailable",
                    reported_nano_aiu=None,
                    ai_credits_source=AiCreditsSource.CLI_COMPACTION,
                    event_order=event.order,
                    observed_nano_aiu=event.amount,
                    inclusion_status="unresolved",
                    match_key=key,
                    evidence_error=(
                        "identity_conflict" if conflict else
                        "invalid_event_metadata" if (
                            event.model is None or event.amount is None or event.tokens is None
                        ) else None
                    ),
                    attribution_scope="model" if event.model else "session_total",
                )
            )
        return records


# 履歴DBの許可列を本文なしのrequest観測値へ変換する。
class CliHistoryParser:
    """外部SQLiteの行を検証し、直接値と識別fingerprintだけを保存境界へ渡す。"""

    # 入力位置の共有キーを永続化前に匿名化する。
    @staticmethod
    def scope(path: Path) -> str:
        """同じ履歴DBを参照するイベントとrequestを結び付ける。"""
        return hashlib.sha256(str(path.resolve()).encode()).hexdigest()

    # SQL境界で取得した履歴の全sessionを正規化する。
    @classmethod
    def parse(
        cls, path: Path, snapshot: CliHistorySnapshot,
    ) -> tuple[list[ParsedUsageRecord], list[DiagnosticRecord], ParsedHistoryInput]:
        """不正時刻に日付を捏造せず、そのsessionの照合可否を別に保持する。"""
        records: list[ParsedUsageRecord] = []
        diagnostics: list[DiagnosticRecord] = []
        invalid: set[str] = set()
        error: str | None = snapshot.error
        for order, values in enumerate(snapshot.rows):
            row = dict(zip(snapshot.columns, values, strict=True))
            session = row.get("session_id")
            if not isinstance(session, str) or not session.strip():
                error = "invalid_history_session"
                continue
            try:
                timestamp = CliCompaction.time(row.get("created_at")).isoformat(
                    timespec="microseconds"
                )
                UsageDateResolver().resolve(timestamp)
                # 元時刻も共通ISO契約を満たすことを確認する。
                UsageDateResolver().resolve(str(row.get("created_at")))
            except ValueError:
                invalid.add(session)
                continue
            row_error = snapshot.error
            try:
                amount = NanoAiuAmount.from_json_value(row.get("total_nano_aiu")).value
            except ValueError:
                amount = None
                row_error = row_error or "invalid_history_amount"
            model = row.get("model")
            if not isinstance(model, str) or not model.strip():
                model = None
                row_error = row_error or "invalid_history_model"
            kind = row.get("initiator")
            if kind not in (None, "user", "agent", "sub-agent", "compaction"):
                kind = "unknown"
                row_error = row_error or "unsupported_initiator"
            tokens = CliCompaction.token_tuple([
                row.get(k) for k in (
                    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"
                )
            ])
            reasoning = row.get("reasoning_tokens")
            valid_reasoning = (
                type(reasoning) is int and 0 <= reasoning <= NanoAiuAmount.SQLITE_MAX_INTEGER
            )
            valid_tokens = (
                tokens is not None and tokens[0] >= tokens[2] + tokens[3]
                and all(v <= NanoAiuAmount.SQLITE_MAX_INTEGER for v in tokens)
            )
            if not valid_tokens:
                diagnostics.append(cls.diagnostic(path, "invalid_history_tokens", session))
            buckets = (
                TokenBuckets(tokens[0], tokens[2], tokens[3], tokens[1],
                             reasoning if valid_reasoning and isinstance(reasoning, int) else 0)
                if valid_tokens and tokens is not None
                else TokenBuckets(
                    0, 0, 0, 0,
                    reasoning if valid_reasoning and isinstance(reasoning, int) else 0,
                )
            )
            try:
                # 全許可メタデータを比較し、ローカルidは識別子として使用しない。
                identity = json.dumps(values, ensure_ascii=True, separators=(",", ":"),
                                      allow_nan=False)
            except (ValueError, TypeError):
                invalid.add(session)
                continue
            if snapshot.error:
                identity += f":unresolved:{order}"
            key = hashlib.sha256(identity.encode()).hexdigest()
            match = json.dumps([session, timestamp, model, amount, tokens],
                               ensure_ascii=True, separators=(",", ":"))
            records.append(ParsedUsageRecord(
                source="copilot_cli", source_file_path=str(path),
                source_file_display_alias="session-store.db", workspace_identity=None,
                workspace_display_alias=None, session_id=session, run_kind=RunKind.HISTORY,
                run_timestamp=timestamp, run_selection_policy=RunSelectionPolicy.RECONCILED_HISTORY,
                request_id=key, timestamp=timestamp, model_key=model, token_buckets=buckets,
                confidence="measured_from_request_history", reported_nano_aiu=amount,
                observed_nano_aiu=amount, ai_credits_source=AiCreditsSource.CLI_HISTORY,
                event_order=order, attribution_scope="model" if model else "session_total",
                history_scope=cls.scope(path), match_key=hashlib.sha256(match.encode()).hexdigest(),
                initiator=kind, evidence_error=row_error, tokens_valid=valid_tokens,
                reasoning_tokens_valid=valid_reasoning,
            ))
            if row_error:
                diagnostics.append(cls.diagnostic(path, row_error, session))
        if error:
            diagnostics.append(cls.diagnostic(path, error))
        for session in sorted(invalid):
            diagnostics.append(cls.diagnostic(path, "invalid_history_metadata", session))
        return records, diagnostics, ParsedHistoryInput(str(path), error, tuple(sorted(invalid)))

    # 外部値をエラー本文に混ぜない。
    @staticmethod
    def diagnostic(path: Path, reason: str, session: str | None = None) -> DiagnosticRecord:
        """理由は固定コードで通知し、使用量以外のDB内容を漏らさない。"""
        return DiagnosticRecord(
            "copilot_cli_history", str(path), "session-store.db", f"cli_history_{reason}",
            CliHistorySnapshot.failure_message(reason) or (
                "Request history is incomplete or unavailable; "
                "inspect coverage before using totals."
            ),
            session_id=session,
        )
