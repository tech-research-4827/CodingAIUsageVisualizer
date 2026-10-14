from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from copilot_usage_visualizer.ingestion.copilot_cli_history import (
    CliCompaction,
    CliCompactionReconciler,
)
from copilot_usage_visualizer.ingestion.json_helpers import JsonObjectReader
from copilot_usage_visualizer.ingestion.workspace_metadata import WorkspaceMetadataNormalizer
from copilot_usage_visualizer.normalization.contract_period import UsageDateResolver
from copilot_usage_visualizer.normalization.usage import (
    AiCreditsSource,
    DiagnosticRecord,
    NanoAiuAmount,
    ParsedUsageRecord,
    RunKind,
    RunSelectionPolicy,
    TokenBuckets,
)


# 累積期間の根拠を候補行とは別に保持し、後着の矛盾を過去へ遡及しない。
@dataclass
class _PrimaryModelEvidence:
    """主モデルの一貫性と実使用の根拠だけを保持する。"""

    # 選択・変更・実使用で一貫しているモデル。
    model: str | None = None
    # 選択情報だけでは推定を成立させない。
    has_usage: bool = False
    # 曖昧な累積区間は後続の単一名で回復しない。
    ambiguous: bool = False
    # 再開後に増加した総額を前区間の使用根拠だけで帰属させない。
    resumed: bool = False
    # 現在の再開区間に実使用のモデル根拠があるか。
    segment_usage: bool = False
    # 最後の有効な累積値と比較して、無使用の resume/exit を区別する。
    last_total: int | None = None

    # モデル名を検証し、累積期間全体の矛盾を記録する。
    def observe(self, value: object, *, usage: bool = False) -> None:
        """不正・異なるモデル名は推定を無効にする。"""
        if not isinstance(value, str) or not value.strip():
            self.ambiguous = True
            return
        if self.model is not None and self.model != value:
            self.ambiguous = True
        self.model = value
        self.has_usage |= usage
        self.segment_usage |= usage

    # 累積値の増加に対応する再開区間の実使用根拠を確認する。
    def select_total(self, total: int) -> None:
        """再開後の増分に根拠がなければ累積全体の推定を止める。"""
        if (self.resumed and not self.segment_usage
                and (self.last_total is None or total > self.last_total)):
            self.ambiguous = True
        self.last_total = total

    # その時点での根拠を候補に保存する。
    def hint(self) -> str | None:
        """実使用があり累積期間に矛盾がない場合だけ名を返す。"""
        return self.model if self.has_usage and not self.ambiguous else None


# Copilot CLI events.jsonl parser。
class CopilotCliEventsParser:
    """CLIイベント全体から最後の完全な累積候補とtoken参照を選択する。"""

    # 1 file を parse する。
    def parse_file(
        self, source_file_path: Path
    ) -> tuple[list[ParsedUsageRecord], list[DiagnosticRecord]]:
        """events.jsonl を読み込み、usage records と diagnostics を返す。"""

        diagnostics: list[DiagnosticRecord] = []
        compactions: list[CliCompaction] = []
        session_id: str = source_file_path.parent.name
        explicit_session_id: str | None = None
        workspace_identity: str | None = self._workspace_identity(source_file_path, diagnostics)
        workspace_alias: str | None = WorkspaceMetadataNormalizer.alias(workspace_identity)
        selected: list[ParsedUsageRecord] = []
        tokens: list[ParsedUsageRecord] = []
        last_timestamp: str | None = None
        unfinished: bool = False
        saw_lifecycle: bool = False
        primary = _PrimaryModelEvidence()
        zero = TokenBuckets(0, 0, 0, 0, 0)
        with source_file_path.open("r", encoding="utf-8") as source_file:
            for line_number, line in enumerate(source_file, start=1):
                if not line.strip():
                    continue
                try:
                    event: object = json.loads(line)
                except json.JSONDecodeError:
                    primary.ambiguous = True
                    diagnostics.append(
                        self._diagnostic(
                            source_file_path,
                            "malformed_jsonl",
                            "Malformed JSONL line.",
                            line_number,
                        )
                    )
                    continue
                if not isinstance(event, dict):
                    primary.ambiguous = True
                    diagnostics.append(
                        self._diagnostic(
                            source_file_path,
                            "schema_drift",
                            "JSONL line is not an object.",
                            line_number,
                        )
                    )
                    continue
                name = JsonObjectReader.string_value(event, ("event", "type", "eventName"))
                if name not in {
                    "session.start",
                    "session.resume",
                    "session.shutdown",
                    "session.usage_checkpoint",
                    "session.compaction_complete",
                    "session.model_change",
                    "assistant.message",
                }:
                    continue
                payload = JsonObjectReader.object_value(event, "data") or {}
                # 応答・選択メタデータの別session IDで直接使用量を除外しない。
                if name == "assistant.message":
                    agent_source = event if "agentId" in event else payload
                    if "agentId" not in agent_source:
                        primary.observe(payload.get("model"), usage=True)
                    elif (not isinstance(agent_source["agentId"], str)
                          or not agent_source["agentId"].strip()):
                        primary.ambiguous = True
                    continue
                if name == "session.model_change":
                    primary.observe(payload.get("previousModel"))
                    primary.observe(payload.get("newModel"))
                    continue
                saw_lifecycle = True
                identity = JsonObjectReader.string_value(payload, ("sessionId", "session_id"))
                identity = identity or JsonObjectReader.string_value(
                    event, ("sessionId", "session_id")
                )
                if identity is not None:
                    if explicit_session_id is not None and identity != explicit_session_id:
                        diagnostics.append(
                            self._diagnostic(
                                source_file_path,
                                "cli_session_identity_conflict",
                                "Conflicting session identities; file usage excluded.",
                                line_number,
                            )
                        )
                        diagnostics[-1] = replace(diagnostics[-1], session_id=session_id)
                        diagnostics.append(replace(diagnostics[-1], session_id=identity))
                        return [], diagnostics
                    explicit_session_id = identity
                    session_id = identity
                if name == "session.compaction_complete":
                    compactions.append(CliCompaction.parse(event, line_number))
                    continue
                timestamp = JsonObjectReader.string_value(
                    payload, ("timestamp", "shutdownTimestamp", "shutdown_timestamp")
                ) or JsonObjectReader.string_value(event, ("timestamp", "time"))
                try:
                    if timestamp is None:
                        raise ValueError("missing")
                    UsageDateResolver().resolve(timestamp)
                except ValueError:
                    timestamp = None
                if timestamp is not None:
                    last_timestamp = timestamp
                if name in {"session.start", "session.resume"}:
                    if "selectedModel" in payload or name == "session.resume":
                        primary.observe(payload.get("selectedModel"))
                    if name == "session.resume":
                        primary.resumed = True
                        primary.segment_usage = False
                    unfinished = True
                    continue
                if timestamp is None:
                    unfinished = True
                    diagnostics.append(
                        self._diagnostic(
                            source_file_path,
                            "schema_drift",
                            "Usage event date is missing or invalid; prior candidate retained.",
                            line_number,
                        )
                    )
                    continue
                base = ParsedUsageRecord(
                    source="copilot_cli",
                    source_file_path=str(source_file_path),
                    source_file_display_alias=source_file_path.name,
                    workspace_identity=workspace_identity,
                    workspace_display_alias=workspace_alias,
                    session_id=session_id,
                    run_kind=RunKind.CHECKPOINT,
                    run_timestamp=timestamp,
                    run_selection_policy=RunSelectionPolicy.LATEST_PER_SESSION,
                    request_id=None,
                    timestamp=timestamp,
                    model_key=None,
                    token_buckets=zero,
                    confidence="credits_unavailable",
                    event_order=line_number,
                    attribution_scope="session_total",
                )
                total, present = self._total_nano_aiu(payload, allow_nested_usage=False)
                if name == "session.usage_checkpoint":
                    unfinished = True
                    if total is not None:
                        primary.select_total(total)
                        selected = [
                            replace(
                                base,
                                reported_nano_aiu=total,
                                ai_credits_source=AiCreditsSource.CLI_CHECKPOINT,
                                confidence="measured_from_usage_checkpoint",
                                estimated_primary_model=primary.hint(),
                            )
                        ]
                    else:
                        diagnostics.append(
                            self._diagnostic(
                                source_file_path,
                                "invalid_ai_credits_value" if present else "schema_drift",
                                "Usage checkpoint direct total is missing or invalid.",
                                line_number,
                            )
                        )
                    continue
                rows = self._parse_shutdown_event(
                    source_file_path,
                    event,
                    session_id,
                    workspace_identity,
                    workspace_alias,
                    line_number,
                    diagnostics,
                )
                rows = [replace(row, event_order=line_number) for row in rows]
                metrics = JsonObjectReader.object_value(payload, "modelMetrics") or {}
                if "modelMetrics" in payload and not isinstance(payload["modelMetrics"], dict):
                    primary.ambiguous = True
                if metrics:
                    model = self._primary_model_hint(metrics)
                    if model is None:
                        primary.ambiguous = True
                    else:
                        # ゼロ内訳の終了名だけでは過去の使用を証明しない。
                        has_usage = any(
                            (row.reported_nano_aiu or 0) > 0
                            or any(value > 0 for value in (
                                row.token_buckets.raw_input_tokens,
                                row.token_buckets.cached_input_tokens,
                                row.token_buckets.cache_write_tokens,
                                row.token_buckets.output_tokens,
                            ))
                            for row in rows
                        )
                        primary.observe(model, usage=has_usage)
                token_rows = [
                    row for row in rows
                    if (self._has_tokens(metrics.get(row.model_key or ""))
                        or row.reported_nano_aiu is not None)
                ]
                if token_rows:
                    tokens = token_rows
                complete = (
                    bool(metrics)
                    and len(rows) == len(metrics)
                    and all(row.reported_nano_aiu is not None for row in rows)
                )
                model_total = sum(row.reported_nano_aiu or 0 for row in rows)
                if complete and (total is None or total == model_total):
                    primary.select_total(model_total)
                    selected = rows
                    unfinished = False
                elif total is not None:
                    primary.select_total(total)
                    selected = [
                        replace(
                            base,
                            run_kind=RunKind.SHUTDOWN,
                            reported_nano_aiu=total,
                            ai_credits_source=AiCreditsSource.CLI_SHUTDOWN_TOTAL,
                            confidence="measured_from_shutdown_total_nano_aiu",
                            estimated_primary_model=primary.hint(),
                        )
                    ]
                    unfinished = False
                else:
                    unfinished = bool(selected)

        if any(row.attribution_scope == "session_total"
               and row.reported_nano_aiu is not None
               and row.estimated_primary_model is None for row in selected):
            diagnostics.append(self._diagnostic(
                source_file_path,
                "cli_primary_model_unresolved",
                "Cumulative primary model evidence is missing, invalid or conflicting.",
                None,
            ))
        if saw_lifecycle and unfinished:
            diagnostics.append(
                self._diagnostic(
                    source_file_path,
                    "cli_usage_not_finalized",
                    (
                        "Session total may omit later usage; selected credits are already included."
                        if selected
                        else "Session usage is not finalized; no complete credits available."
                    ),
                    None,
                )
            )
        if saw_lifecycle and not selected:
            diagnostics.append(
                self._diagnostic(
                    source_file_path,
                    "cli_credits_unavailable",
                    "No complete direct credits candidate is available.",
                    None,
                )
            )
            if last_timestamp is not None:
                selected = [
                    ParsedUsageRecord(
                        source="copilot_cli",
                        source_file_path=str(source_file_path),
                        source_file_display_alias=source_file_path.name,
                        workspace_identity=workspace_identity,
                        workspace_display_alias=workspace_alias,
                        session_id=session_id,
                        run_kind=RunKind.SESSION,
                        run_timestamp=last_timestamp,
                        run_selection_policy=RunSelectionPolicy.LATEST_PER_SESSION,
                        request_id=None,
                        timestamp=last_timestamp,
                        model_key=None,
                        token_buckets=zero,
                        confidence="credits_unavailable",
                        attribution_scope="session_total",
                    )
                ]
        # token と credits の選択イベントが同じ時だけ1行へまとめる。
        token_map = {(row.event_order, row.model_key): row for row in tokens}
        result: list[ParsedUsageRecord] = []
        for row in selected:
            token = token_map.pop((row.event_order, row.model_key), None)
            result.append(
                replace(
                    row, session_id=session_id, token_buckets=token.token_buckets if token else zero
                )
            )
        result.extend(
            replace(
                row,
                session_id=session_id,
                reported_nano_aiu=None,
                ai_credits_source=AiCreditsSource.CLI_TOKEN_REFERENCE,
                confidence="reference_only_cli_tokens",
                observed_nano_aiu=row.observed_nano_aiu,
                tokens_valid=row.tokens_valid,
            )
            for row in token_map.values()
        )
        return CliCompactionReconciler.reconcile(
            source_file_path, session_id, result, compactions, diagnostics
        ), diagnostics

    # token fieldの存在と値を検証し、欠落を実測0にしない。
    @staticmethod
    def _has_tokens(metrics: object) -> bool:
        """少なくとも1つの有効なtoken fieldがあるモデル行を識別する。"""

        if not isinstance(metrics, dict):
            return False
        usage = JsonObjectReader.object_value(metrics, "usage") or metrics
        keys = (
            "rawInputTokens",
            "inputTokens",
            "promptTokens",
            "cachedInputTokens",
            "cacheReadInputTokens",
            "cacheReadTokens",
            "cached_input_tokens",
            "cacheWriteTokens",
            "cacheCreationInputTokens",
            "cache_write_tokens",
            "outputTokens",
            "completionTokens",
            "reasoningTokens",
            "reasoning_tokens",
        )
        for key in keys:
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return True
        return False

    # 解析済み行ではなく元の内訳を使い、不正行を除いて単一と誤認しない。
    @staticmethod
    def _primary_model_hint(metrics: dict[str, object]) -> str | None:
        """単一の非空モデル名とobject内訳がある場合だけ推定名を返す。"""

        if len(metrics) != 1:
            return None
        model, value = next(iter(metrics.items()))
        return model if model.strip() and isinstance(value, dict) else None

    # shutdown event を usage records へ変換する。
    def _parse_shutdown_event(
        self,
        source_file_path: Path,
        event_data: dict[str, object],
        fallback_session_id: str,
        workspace_identity: str | None,
        workspace_alias: str | None,
        line_number: int,
        diagnostics: list[DiagnosticRecord],
    ) -> list[ParsedUsageRecord]:
        """session.shutdown event から model ごとの usage record を返す。"""

        payload: dict[str, object] | None = JsonObjectReader.object_value(event_data, "data")
        if payload is None:
            diagnostics.append(
                self._diagnostic(
                    source_file_path, "schema_drift", "Shutdown event data is missing.", line_number
                )
            )
            return []

        model_metrics: dict[str, object] | None = JsonObjectReader.object_value(
            payload, "modelMetrics"
        )
        if model_metrics is None:
            diagnostics.append(
                self._diagnostic(
                    source_file_path,
                    "tokens_unavailable",
                    "Shutdown event model metrics are missing.",
                    line_number,
                )
            )
            model_metrics = {}

        session_id: str = (
            JsonObjectReader.string_value(payload, ("sessionId", "session_id"))
            or JsonObjectReader.string_value(event_data, ("sessionId", "session_id"))
            or fallback_session_id
        )
        timestamp: str | None = JsonObjectReader.string_value(
            payload, ("timestamp", "shutdownTimestamp", "shutdown_timestamp")
        ) or JsonObjectReader.string_value(event_data, ("timestamp", "time"))
        if timestamp is None:
            diagnostics.append(
                self._diagnostic(
                    source_file_path,
                    "schema_drift",
                    "Shutdown timestamp is missing.",
                    line_number,
                )
            )
            return []
        try:
            UsageDateResolver().resolve(timestamp)
        except ValueError:
            diagnostics.append(
                self._diagnostic(
                    source_file_path,
                    "schema_drift",
                    "Shutdown timestamp is not an ISO date/time.",
                    line_number,
                )
            )
            return []

        records: list[ParsedUsageRecord] = []
        for model_key, metrics_value in model_metrics.items():
            if not isinstance(metrics_value, dict):
                diagnostics.append(
                    self._diagnostic(
                        source_file_path,
                        "schema_drift",
                        "Model metrics entry is not an object.",
                        line_number,
                    )
                )
                continue

            token_buckets: TokenBuckets = self._token_buckets(metrics_value)
            tokens_valid: bool = self._has_tokens(metrics_value)
            total_nano_aiu, has_total_nano_aiu = self._total_nano_aiu(metrics_value)
            if has_total_nano_aiu and total_nano_aiu is None:
                diagnostics.append(
                    self._diagnostic(
                        source_file_path,
                        "invalid_ai_credits_value",
                        "Model metrics totalNanoAiu is not a non-negative finite number.",
                        line_number,
                    )
                )
            if (
                token_buckets.raw_input_tokens == 0
                and token_buckets.cached_input_tokens == 0
                and token_buckets.cache_write_tokens == 0
                and token_buckets.output_tokens == 0
                and total_nano_aiu is None
                and not self._has_tokens(metrics_value)
            ):
                diagnostics.append(
                    self._diagnostic(
                        source_file_path,
                        "tokens_unavailable",
                        "Model metrics token buckets are unavailable.",
                        line_number,
                    )
                )
                continue

            records.append(
                ParsedUsageRecord(
                    source="copilot_cli",
                    source_file_path=str(source_file_path),
                    source_file_display_alias=source_file_path.name,
                    workspace_identity=workspace_identity,
                    workspace_display_alias=workspace_alias,
                    session_id=session_id,
                    run_kind=RunKind.SHUTDOWN,
                    run_timestamp=timestamp,
                    run_selection_policy=RunSelectionPolicy.LATEST_PER_SESSION,
                    request_id=None,
                    timestamp=timestamp,
                    model_key=model_key,
                    token_buckets=token_buckets,
                    confidence=(
                        "measured_from_total_nano_aiu"
                        if total_nano_aiu is not None
                        else "credits_unavailable"
                    ),
                    producer_version=JsonObjectReader.string_value(
                        payload, ("producerVersion", "version")
                    ),
                    reported_ai_credits=None,
                    reported_nano_aiu=total_nano_aiu,
                    observed_nano_aiu=total_nano_aiu,
                    tokens_valid=tokens_valid,
                    ai_credits_source=(
                        AiCreditsSource.COPILOT_CLI_TOTAL_NANO_AIU
                        if total_nano_aiu is not None
                        else AiCreditsSource.UNAVAILABLE
                    ),
                )
            )

        top_level_nano_aiu, has_top_level_nano_aiu = self._total_nano_aiu(
            payload, allow_nested_usage=False
        )
        if has_top_level_nano_aiu and top_level_nano_aiu is None:
            diagnostics.append(
                self._diagnostic(
                    source_file_path,
                    "invalid_ai_credits_value",
                    "Shutdown totalNanoAiu is not a valid integer.",
                    line_number,
                )
            )
        model_nano_values: list[int] = [
            record.reported_nano_aiu for record in records if record.reported_nano_aiu is not None
        ]
        if (
            top_level_nano_aiu is not None
            and model_nano_values
            and top_level_nano_aiu != sum(model_nano_values)
        ):
            diagnostics.append(
                self._diagnostic(
                    source_file_path,
                    "cli_nano_aiu_reconciliation_mismatch",
                    "Shutdown totalNanoAiu does not match the model total; "
                    "session total is selected without model allocation.",
                    line_number,
                )
            )
        elif top_level_nano_aiu is not None and top_level_nano_aiu > 0 and not model_nano_values:
            diagnostics.append(
                self._diagnostic(
                    source_file_path,
                    "cli_nano_aiu_model_totals_missing",
                    "Shutdown totalNanoAiu is present but model nano AIU totals are missing; "
                    "session total is selected without model allocation.",
                    line_number,
                )
            )

        return records

    # model metrics から token bucket を作成する。
    def _token_buckets(self, metrics: dict[str, object]) -> TokenBuckets:
        """metrics object から token bucket を抽出する。"""

        usage_metrics: dict[str, object] = (
            JsonObjectReader.object_value(metrics, "usage") or metrics
        )
        # 不正token値は直接creditsの採用を妨げない。
        usage_metrics = {
            key: value
            for key, value in usage_metrics.items()
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        }
        return TokenBuckets(
            raw_input_tokens=JsonObjectReader.int_value(
                usage_metrics, ("rawInputTokens", "inputTokens", "promptTokens")
            ),
            cached_input_tokens=JsonObjectReader.int_value(
                usage_metrics,
                (
                    "cachedInputTokens",
                    "cacheReadInputTokens",
                    "cacheReadTokens",
                    "cached_input_tokens",
                ),
            ),
            cache_write_tokens=JsonObjectReader.int_value(
                usage_metrics,
                (
                    "cacheWriteTokens",
                    "cacheCreationInputTokens",
                    "cache_write_tokens",
                ),
            ),
            output_tokens=JsonObjectReader.int_value(
                usage_metrics, ("outputTokens", "completionTokens")
            ),
            reasoning_tokens=JsonObjectReader.int_value(
                usage_metrics, ("reasoningTokens", "reasoning_tokens")
            ),
        )

    # model metricsのnano AIU直接値を返す。
    def _total_nano_aiu(
        self, metrics: dict[str, object], allow_nested_usage: bool = True
    ) -> tuple[int | None, bool]:
        """totalNanoAiuの検証値とfield存在有無を返す。"""

        usage_metrics: dict[str, object] | None = (
            JsonObjectReader.object_value(metrics, "usage") if allow_nested_usage else None
        )
        value_source: dict[str, object] = (
            usage_metrics
            if usage_metrics is not None and "totalNanoAiu" in usage_metrics
            else metrics
        )
        if "totalNanoAiu" not in value_source:
            return None, False
        value: object | None = value_source.get("totalNanoAiu")
        try:
            amount: NanoAiuAmount = NanoAiuAmount.from_json_value(value)
        except ValueError:
            return None, True
        return amount.value, True

    # session metadata から workspace identity を取得する。
    def _workspace_identity(
        self, source_file_path: Path, diagnostics: list[DiagnosticRecord],
    ) -> str | None:
        """events.jsonl と同じ session directory の workspace.yaml から raw identity を返す。"""

        workspace_yaml_path: Path = source_file_path.with_name("workspace.yaml")
        try:
            values_by_key: dict[str, str] = {}
            for line in workspace_yaml_path.read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition(":")
                if separator != ":" or key not in {"git_root", "cwd"}:
                    continue
                normalized_value: str | None = WorkspaceMetadataNormalizer.identity(value)
                if normalized_value is not None:
                    values_by_key[key] = normalized_value
        except FileNotFoundError:
            return None
        except OSError:
            diagnostics.append(self._diagnostic(
                source_file_path, "workspace_metadata_unreadable",
                "Workspace metadata could not be read.", None,
            ))
            return None
        return values_by_key.get("git_root") or values_by_key.get("cwd")

    # diagnostic record を作成する。
    def _diagnostic(
        self,
        source_file_path: Path,
        code: str,
        message: str,
        line_number: int | None,
    ) -> DiagnosticRecord:
        """privacy-safe diagnostic を作成する。"""

        return DiagnosticRecord(
            source="copilot_cli",
            source_file_path=str(source_file_path),
            source_file_display_alias=source_file_path.name,
            code=code,
            message=message,
            line_number=line_number,
        )
