from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from copilot_usage_visualizer.ingestion.json_helpers import JsonObjectReader
from copilot_usage_visualizer.ingestion.workspace_metadata import WorkspaceMetadataNormalizer
from copilot_usage_visualizer.normalization.contract_period import UsageDateResolver
from copilot_usage_visualizer.normalization.usage import (
    AiCreditsSource,
    DiagnosticRecord,
    ParsedUsageRecord,
    RunKind,
    RunSelectionPolicy,
    TokenBuckets,
)


# VS Code Copilot Chat session parser。
class VscodeChatSessionsParser:
    """VS Code Copilot Chat JSONL から request-level token usage を抽出する。"""

    # 1 file を parse する。
    def parse_file(
        self, source_file_path: Path
    ) -> tuple[list[ParsedUsageRecord], list[DiagnosticRecord]]:
        """chatSessions JSONL を読み込み、usage records と diagnostics を返す。"""

        records: list[ParsedUsageRecord] = []
        diagnostics: list[DiagnosticRecord] = []
        session_id: str = source_file_path.stem
        parsed_events: list[tuple[int, dict[str, object]]] = []
        workspace_identity: str | None = self._workspace_identity(source_file_path)
        workspace_alias: str | None = WorkspaceMetadataNormalizer.alias(workspace_identity)

        with source_file_path.open("r", encoding="utf-8") as source_file:
            for line_number, line in enumerate(source_file, start=1):
                stripped_line: str = line.strip()
                if not stripped_line:
                    continue

                try:
                    parsed_json: object = json.loads(stripped_line)
                except json.JSONDecodeError:
                    diagnostics.append(
                        self._diagnostic(
                            source_file_path,
                            "malformed_jsonl",
                            "Malformed JSONL line.",
                            line_number,
                        )
                    )
                    continue

                if not isinstance(parsed_json, dict):
                    diagnostics.append(
                        self._diagnostic(
                            source_file_path,
                            "schema_drift",
                            "JSONL line is not an object.",
                            line_number,
                        )
                    )
                    continue

                parsed_events.append((line_number, parsed_json))

        if any("kind" in event_data and "v" in event_data for _, event_data in parsed_events):
            records.extend(
                self._parse_compact_session(
                    source_file_path,
                    parsed_events,
                    session_id,
                    workspace_identity,
                    workspace_alias,
                    diagnostics,
                )
            )
            return records, diagnostics

        for line_number, event_data in parsed_events:
            parsed_record: ParsedUsageRecord | None = self._parse_request(
                source_file_path,
                event_data,
                session_id,
                workspace_identity,
                workspace_alias,
                line_number,
                diagnostics,
            )
            if parsed_record is not None:
                records.append(parsed_record)

        return records, diagnostics

    # Chat request object を usage record へ変換する。
    def _parse_request(
        self,
        source_file_path: Path,
        event_data: dict[str, object],
        fallback_session_id: str,
        fallback_workspace_identity: str | None,
        fallback_workspace_alias: str | None,
        line_number: int,
        diagnostics: list[DiagnosticRecord],
    ) -> ParsedUsageRecord | None:
        """Chat request event から usage record を返す。"""

        request_id: str | None = JsonObjectReader.string_value(
            event_data, ("requestId", "request_id", "id")
        )
        timestamp: str | None = JsonObjectReader.string_value(
            event_data, ("timestamp", "time", "createdAt")
        )
        model_key: str | None = JsonObjectReader.string_value(
            event_data, ("model", "modelKey", "model_key", "modelId")
        )
        if request_id is None or timestamp is None or model_key is None:
            diagnostics.append(
                self._diagnostic(
                    source_file_path,
                    "schema_drift",
                    "Chat request id, timestamp, or model is missing.",
                    line_number,
                )
            )
            return None

        try:
            UsageDateResolver().resolve(timestamp)
        except ValueError:
            diagnostics.append(
                self._diagnostic(
                    source_file_path,
                    "schema_drift",
                    "Chat request timestamp is not an ISO date/time.",
                    line_number,
                )
            )
            return None

        token_buckets: TokenBuckets = self._token_buckets(event_data)
        reported_ai_credits: float | None = self._reported_ai_credits(event_data)
        if "copilotCredits" in event_data and reported_ai_credits is None:
            diagnostics.append(
                self._diagnostic(
                    source_file_path,
                    "invalid_ai_credits_value",
                    "Chat copilotCredits is not a non-negative finite number.",
                    line_number,
                )
            )
        if (
            token_buckets.raw_input_tokens == 0
            and token_buckets.output_tokens == 0
            and reported_ai_credits is None
        ):
            diagnostics.append(
                self._diagnostic(
                    source_file_path,
                    "tokens_unavailable",
                    "Chat request token metadata is missing.",
                    line_number,
                )
            )
            return None

        workspace_identity: str | None = (
            JsonObjectReader.string_value(
                event_data, ("workspace", "workspacePath", "workspace_path")
            )
            or fallback_workspace_identity
        )
        workspace_alias: str | None = WorkspaceMetadataNormalizer.alias(workspace_identity)
        if workspace_alias is None:
            workspace_alias = fallback_workspace_alias

        return ParsedUsageRecord(
            source="copilot_chat",
            source_file_path=str(source_file_path),
            source_file_display_alias=source_file_path.name,
            workspace_identity=workspace_identity,
            workspace_display_alias=workspace_alias,
            session_id=JsonObjectReader.string_value(event_data, ("sessionId", "session_id"))
            or fallback_session_id,
            run_kind=RunKind.CHAT_REQUEST_LEVEL,
            run_timestamp=None,
            run_selection_policy=RunSelectionPolicy.CHAT_REQUEST_LEVEL,
            request_id=request_id,
            timestamp=timestamp,
            model_key=model_key,
            token_buckets=token_buckets,
            confidence=(
                "reported_from_copilot_chat"
                if reported_ai_credits is not None
                else "credits_unavailable"
            ),
            producer_version=JsonObjectReader.string_value(
                event_data, ("producerVersion", "version")
            ),
            reported_ai_credits=reported_ai_credits,
            ai_credits_source=(
                AiCreditsSource.COPILOT_CHAT_CREDITS
                if reported_ai_credits is not None
                else AiCreditsSource.UNAVAILABLE
            ),
        )

    # Chat request から token bucket を作成する。
    def _token_buckets(self, event_data: dict[str, object]) -> TokenBuckets:
        """Chat request metadata から token bucket を抽出する。"""

        usage_data: dict[str, object] = (
            JsonObjectReader.object_value(event_data, "usage") or event_data
        )
        return TokenBuckets(
            raw_input_tokens=JsonObjectReader.int_value(
                usage_data, ("promptTokens", "prompt_tokens", "inputTokens", "rawInputTokens")
            ),
            cached_input_tokens=JsonObjectReader.int_value(
                usage_data, ("cachedInputTokens", "cacheReadInputTokens")
            ),
            cache_write_tokens=JsonObjectReader.int_value(
                usage_data, ("cacheWriteTokens", "cacheCreationInputTokens")
            ),
            output_tokens=JsonObjectReader.int_value(
                usage_data, ("outputTokens", "completionTokens", "completion_tokens")
            ),
            reasoning_tokens=JsonObjectReader.int_value(usage_data, ("reasoningTokens",)),
        )

    # VS Code compact session storage を usage records へ変換する。
    def _parse_compact_session(
        self,
        source_file_path: Path,
        parsed_events: list[tuple[int, dict[str, object]]],
        fallback_session_id: str,
        workspace_identity: str | None,
        workspace_alias: str | None,
        diagnostics: list[DiagnosticRecord],
    ) -> list[ParsedUsageRecord]:
        """kind/k/v 形式の chat session state を replay して request usage を返す。"""

        session_state: dict[str, object] | None = None
        for _line_number, event_data in parsed_events:
            kind: object | None = event_data.get("kind")
            value: object | None = event_data.get("v")
            if kind == 0 and isinstance(value, dict):
                session_state = value
                continue
            if session_state is None:
                continue

            path_value: object | None = event_data.get("k")
            if not isinstance(path_value, list):
                continue
            path: list[str | int] = []
            for part in path_value:
                if isinstance(part, bool) or not isinstance(part, str | int):
                    continue
                path.append(part)
            if len(path) != len(path_value):
                continue
            if kind == 1:
                self._set_path_value(session_state, path, value)
            elif kind == 2 and isinstance(value, list):
                index_value: object | None = event_data.get("i")
                index: int | None = index_value if isinstance(index_value, int) else None
                self._splice_path_values(session_state, path, value, index)

        if session_state is None:
            return []

        requests_value: object | None = session_state.get("requests")
        if not isinstance(requests_value, list):
            return []

        records: list[ParsedUsageRecord] = []
        for request_value in requests_value:
            if not isinstance(request_value, dict):
                continue
            record: ParsedUsageRecord | None = self._parse_compact_request(
                source_file_path,
                request_value,
                fallback_session_id,
                workspace_identity,
                workspace_alias,
                diagnostics,
            )
            if record is not None:
                records.append(record)
                records.extend(
                    self._parse_compact_summaries(
                        source_file_path,
                        request_value,
                        record,
                        diagnostics,
                    )
                )
        return records

    # compact request object を usage record へ変換する。
    def _parse_compact_request(
        self,
        source_file_path: Path,
        request_data: dict[str, object],
        fallback_session_id: str,
        workspace_identity: str | None,
        workspace_alias: str | None,
        diagnostics: list[DiagnosticRecord],
    ) -> ParsedUsageRecord | None:
        """VS Code compact session の request object から usage record を返す。"""

        request_id: str | None = JsonObjectReader.string_value(
            request_data, ("requestId", "request_id", "id")
        )
        model_key: str | None = JsonObjectReader.string_value(
            request_data, ("modelId", "model", "modelKey", "model_key")
        )
        if request_id is None or model_key is None:
            diagnostics.append(
                self._diagnostic(
                    source_file_path,
                    "schema_drift",
                    "Compact chat request id or model is missing.",
                    None,
                )
            )
            return None

        timestamp: str | None = self._timestamp_value(request_data.get("timestamp"))
        if timestamp is None:
            diagnostics.append(
                self._diagnostic(
                    source_file_path,
                    "schema_drift",
                    "Compact chat request timestamp is missing.",
                    None,
                )
            )
            return None

        try:
            UsageDateResolver().resolve(timestamp)
        except ValueError:
            diagnostics.append(
                self._diagnostic(
                    source_file_path,
                    "schema_drift",
                    "Compact chat request timestamp is not an ISO date/time.",
                    None,
                )
            )
            return None

        result_data: dict[str, object] | None = JsonObjectReader.object_value(
            request_data, "result"
        )
        usage_data: dict[str, object] | None = None
        if result_data is not None:
            usage_data = JsonObjectReader.object_value(result_data, "usage")
        token_source: dict[str, object] = usage_data or request_data

        token_buckets: TokenBuckets = self._token_buckets(token_source)
        reported_ai_credits: float | None = self._reported_ai_credits(request_data)
        if "copilotCredits" in request_data and reported_ai_credits is None:
            diagnostics.append(
                self._diagnostic(
                    source_file_path,
                    "invalid_ai_credits_value",
                    "Compact chat copilotCredits is not a non-negative finite number.",
                    None,
                )
            )
        if (
            token_buckets.raw_input_tokens == 0
            and token_buckets.output_tokens == 0
            and reported_ai_credits is None
        ):
            diagnostics.append(
                self._diagnostic(
                    source_file_path,
                    "tokens_unavailable",
                    "Compact chat request token metadata is missing.",
                    None,
                )
            )
            return None

        confidence: str = (
            "reported_from_copilot_chat"
            if reported_ai_credits is not None
            else "credits_unavailable"
        )
        return ParsedUsageRecord(
            source="copilot_chat",
            source_file_path=str(source_file_path),
            source_file_display_alias=source_file_path.name,
            workspace_identity=workspace_identity,
            workspace_display_alias=workspace_alias,
            session_id=fallback_session_id,
            run_kind=RunKind.CHAT_REQUEST_LEVEL,
            run_timestamp=None,
            run_selection_policy=RunSelectionPolicy.CHAT_REQUEST_LEVEL,
            request_id=request_id,
            timestamp=timestamp,
            model_key=model_key,
            token_buckets=token_buckets,
            confidence=confidence,
            reported_ai_credits=reported_ai_credits,
            ai_credits_source=(
                AiCreditsSource.COPILOT_CHAT_CREDITS
                if reported_ai_credits is not None
                else AiCreditsSource.UNAVAILABLE
            ),
        )

    # compact requestに紐づくbackground summaryを別usage recordへ変換する。
    def _parse_compact_summaries(
        self,
        source_file_path: Path,
        request_data: dict[str, object],
        parent_record: ParsedUsageRecord,
        diagnostics: list[DiagnosticRecord],
    ) -> list[ParsedUsageRecord]:
        """成功したsummaryのtoken usageを返す。"""

        result_data: dict[str, object] | None = JsonObjectReader.object_value(
            request_data, "result"
        )
        metadata: dict[str, object] | None = (
            None if result_data is None else JsonObjectReader.object_value(result_data, "metadata")
        )
        summaries_value: object | None = None if metadata is None else metadata.get("summaries")
        if not isinstance(summaries_value, list):
            return []

        records: list[ParsedUsageRecord] = []
        for summary_index, summary_value in enumerate(summaries_value):
            if not isinstance(summary_value, dict):
                continue
            outcome: str | None = JsonObjectReader.string_value(summary_value, ("outcome",))
            if outcome is not None and outcome != "success":
                continue
            model_key: str | None = JsonObjectReader.string_value(
                summary_value, ("model", "modelId", "modelKey")
            )
            usage_data: dict[str, object] | None = JsonObjectReader.object_value(
                summary_value, "usage"
            )
            if model_key is None or usage_data is None:
                diagnostics.append(
                    self._diagnostic(
                        source_file_path,
                        "summary_usage_unavailable",
                        "Compact chat summary model or usage is missing.",
                        None,
                    )
                )
                continue
            token_buckets: TokenBuckets = self._token_buckets(usage_data)
            if token_buckets.raw_input_tokens == 0 and token_buckets.output_tokens == 0:
                diagnostics.append(
                    self._diagnostic(
                        source_file_path,
                        "summary_usage_unavailable",
                        "Compact chat summary token metadata is missing.",
                        None,
                    )
                )
                continue
            tool_call_round_id: str = (
                JsonObjectReader.string_value(summary_value, ("toolCallRoundId",)) or "unknown"
            )
            records.append(
                ParsedUsageRecord(
                    source="copilot_chat",
                    source_file_path=parent_record.source_file_path,
                    source_file_display_alias=parent_record.source_file_display_alias,
                    workspace_identity=parent_record.workspace_identity,
                    workspace_display_alias=parent_record.workspace_display_alias,
                    session_id=parent_record.session_id,
                    run_kind=RunKind.CHAT_REQUEST_LEVEL,
                    run_timestamp=None,
                    run_selection_policy=RunSelectionPolicy.CHAT_REQUEST_LEVEL,
                    request_id=(
                        f"{parent_record.request_id or parent_record.timestamp}:"
                        f"summary:{summary_index}:{tool_call_round_id}"
                    ),
                    timestamp=parent_record.timestamp,
                    model_key=model_key,
                    token_buckets=token_buckets,
                    confidence="reference_only_chat_summary_tokens",
                    ai_credits_source=AiCreditsSource.COPILOT_CHAT_SUMMARY_REFERENCE,
                )
            )
        return records

    # Chat logに直接記録されたAI Creditsを返す。
    def _reported_ai_credits(self, data: dict[str, object]) -> float | None:
        """non-negative finiteなcopilotCreditsだけを返す。"""

        value: object | None = data.get("copilotCredits")
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        numeric_value: float = float(value)
        if not math.isfinite(numeric_value) or numeric_value < 0:
            return None
        return numeric_value

    # path の値を置き換える。
    def _set_path_value(
        self, session_state: dict[str, object], path: list[str | int], value: object
    ) -> bool:
        """session_state 内の path に value を設定する。"""

        parent: object | None = self._path_parent(session_state, path)
        if parent is None or not path:
            return False
        last: str | int = path[-1]
        if isinstance(parent, dict) and isinstance(last, str):
            parent[last] = value
            return True
        if isinstance(parent, list) and isinstance(last, int) and 0 <= last < len(parent):
            parent[last] = value
            return True
        return False

    # path の list に values を挿入する。
    def _splice_path_values(
        self,
        session_state: dict[str, object],
        path: list[str | int],
        values: list[object],
        index: int | None,
    ) -> bool:
        """session_state 内の list path に values を挿入または追記する。"""

        target: object | None = self._path_value(session_state, path)
        if target is None and len(path) == 1 and isinstance(path[0], str):
            session_state[path[0]] = values
            return True
        if not isinstance(target, list):
            return False
        if index is None:
            target.extend(values)
            return True
        bounded_index: int = max(0, min(index, len(target)))
        target[bounded_index:bounded_index] = values
        return True

    # path の親 object を返す。
    def _path_parent(
        self, session_state: dict[str, object], path: list[str | int]
    ) -> object | None:
        """path の最後の要素を持つ親 object を返す。"""

        if not path:
            return None
        return self._path_value(session_state, path[:-1])

    # path の値を返す。
    def _path_value(self, session_state: dict[str, object], path: list[str | int]) -> object | None:
        """session_state 内の path に対応する値を返す。"""

        current: object = session_state
        for part in path:
            if isinstance(current, dict) and isinstance(part, str):
                next_value: object | None = current.get(part)
                if next_value is None:
                    return None
                current = next_value
            elif isinstance(current, list) and isinstance(part, int):
                current_list: list[object] = cast(list[object], current)
                if part < 0 or part >= len(current_list):
                    return None
                current = current_list[part]
            else:
                return None
        return current

    # timestamp を ISO-8601 文字列へ変換する。
    def _timestamp_value(self, value: object) -> str | None:
        """VS Code timestamp value を ISO-8601 に正規化する。"""

        if isinstance(value, str) and value:
            return value
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float):
            seconds: float = float(value) / 1000 if value > 10_000_000_000 else float(value)
            return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
        return None

    # workspaceStorage metadata から workspace identity を取得する。
    def _workspace_identity(self, source_file_path: Path) -> str | None:
        """chatSessions の親 workspaceStorage directory にある workspace.json を読む。"""

        workspace_json_path: Path = source_file_path.parent.parent / "workspace.json"
        if not workspace_json_path.exists():
            return None
        try:
            payload: object = json.loads(workspace_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        workspace_value: str | None = JsonObjectReader.string_value(
            payload, ("folder", "workspace")
        )
        if workspace_value is None:
            return None
        return WorkspaceMetadataNormalizer.identity(workspace_value)

    # diagnostic record を作成する。
    def _diagnostic(
        self, source_file_path: Path, code: str, message: str, line_number: int | None
    ) -> DiagnosticRecord:
        """privacy-safe diagnostic を作成する。"""

        return DiagnosticRecord(
            source="copilot_chat",
            source_file_path=str(source_file_path),
            source_file_display_alias=source_file_path.name,
            code=code,
            message=message,
            line_number=line_number,
        )
