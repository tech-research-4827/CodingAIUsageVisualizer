from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from copilot_usage_visualizer.session_usage.app_db import AppResult, AppValue
from copilot_usage_visualizer.session_usage.contract import (
    AgentKind,
    AgentView,
    AppContextView,
    AppReconciliation,
    AppTotalsView,
    AppView,
    CompactionLimit,
    ContextView,
    CumulativeReconciliation,
    CumulativeTokens,
    GroupTotalsView,
    ModelView,
    RequestView,
    SessionTotalsView,
    SessionUsageView,
    TimedTokens,
    TokenTotals,
    TokenTypeRow,
)
from copilot_usage_visualizer.session_usage.events import (
    CumulativeEvent,
    EventsSummary,
    SubagentRecord,
)
from copilot_usage_visualizer.session_usage.history import HistoryResult, HistoryRow
from copilot_usage_visualizer.session_usage.js_compat import JsValue


# 集計 1 グループ (合計・エージェント・モデル) の途中値。
@dataclass
class _Group:
    """nano は整数で合算し、credits の欠落・token 不正の行は件数で数える。"""

    calls: int = 0
    nano: int = 0
    credits_missing: int = 0
    token_rows_invalid: int = 0
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    uncached_input: int = 0
    reasoning: int = 0
    reasoning_missing: int = 0
    duration_ms: int = 0
    ctx_max: int | None = None
    ctx_last: int | None = None
    first_at: int | None = None
    last_at: int | None = None
    models: dict[str, None] = field(default_factory=dict)
    efforts: dict[str, None] = field(default_factory=dict)
    parent_tool_call_ids: set[str] = field(default_factory=set)

    # 1 行を集計に加える。
    def add(self, row: HistoryRow) -> None:
        """credits の欠落は加算せず件数で数え、token 不正行は token 合計から外す。"""

        self.calls += 1
        if row.nano is None:
            self.credits_missing += 1
        else:
            self.nano += row.nano
        if row.tokens is not None:
            tokens = row.tokens
            self.input += tokens.input
            self.output += tokens.output
            self.cache_read += tokens.cache_read
            self.cache_write += tokens.cache_write
            self.uncached_input += tokens.input - tokens.cache_read - tokens.cache_write
            self.ctx_max = max(self.ctx_max or 0, tokens.input)
            self.ctx_last = tokens.input
        else:
            self.token_rows_invalid += 1
        if row.reasoning is None:
            self.reasoning_missing += 1
        else:
            self.reasoning += row.reasoning
        if row.duration_ms is not None:
            self.duration_ms += row.duration_ms
        if self.first_at is None:
            self.first_at = row.t
        self.last_at = row.t
        if row.model:
            self.models[row.model] = None
        if row.effort:
            self.efforts[row.effort] = None
        if row.parent_tool_call_id:
            self.parent_tool_call_ids.add(row.parent_tool_call_id)

    # 表示用の合計を返す。
    def totals(self, subtotal: bool = False) -> GroupTotalsView:
        """内訳は Credits / token の記録が 1 件も無ければ 0 ではなく None。

        合計 (subtotal) は 0 を保つ。
        """

        observed_credits: int = self.calls - self.credits_missing
        valid_token_rows: int = self.calls - self.token_rows_invalid
        has_tokens: bool = subtotal or valid_token_rows > 0
        tokens: TokenTotals = {
            "input": self.input if has_tokens else None,
            "output": self.output if has_tokens else None,
            "cacheRead": self.cache_read if has_tokens else None,
            "cacheWrite": self.cache_write if has_tokens else None,
            "uncachedInput": self.uncached_input if has_tokens else None,
            "reasoning": self.reasoning if has_tokens else None,
        }
        return {
            "requests": self.calls,
            "creditsNano": str(self.nano) if subtotal or observed_credits > 0 else None,
            "creditsMissing": self.credits_missing,
            "tokenRowsInvalid": self.token_rows_invalid,
            "tokens": tokens,
            "reasoningMissing": self.reasoning_missing,
            "durationMs": self.duration_ms,
            "ctxMax": self.ctx_max,
            "ctxLast": self.ctx_last,
            "firstAt": self.first_at,
            "lastAt": self.last_at,
            "efforts": list(self.efforts),
        }


# 読み取った 3 つの記録から表示用データを作る。
class SessionViewBuilder:
    """lib/aggregate.mjs の buildSessionView と同じ規則の純粋関数群。"""

    MAIN: str = "main"
    COMPACTION: str = "compaction"
    UNKNOWN_MODEL: str = "(unknown)"

    # セッション 1 件の表示用データを組み立てる。
    @classmethod
    def build(
        cls,
        session_id: str,
        is_current: bool,
        history: HistoryResult,
        events: EventsSummary,
        app: AppResult | None,
        read_at: str,
    ) -> SessionUsageView:
        """nano は整数で合算し、表示用データでは 10 進文字列にする。"""

        rows: tuple[HistoryRow, ...] = history.rows
        requests: list[RequestView] = []
        agent_groups: dict[str, _Group] = {}
        model_groups: dict[str, _Group] = {}
        token_types: dict[str, list[int]] = {}
        totals: _Group = _Group()
        cum_nano: int = 0
        cum: CumulativeTokens = {
            "uncached": 0,
            "cacheRead": 0,
            "cacheWrite": 0,
            "output": 0,
            "input": 0,
        }
        detail_rows: int = 0
        detail_mismatch_rows: int = 0
        detail_inexact: int = 0

        for index, row in enumerate(rows):
            agent_key: str = cls.agent_key(row)
            totals.add(row)
            agent_groups.setdefault(agent_key, _Group()).add(row)
            model_groups.setdefault(row.model or cls.UNKNOWN_MODEL, _Group()).add(row)
            if row.nano is not None:
                cum_nano += row.nano
            tokens = row.tokens
            if tokens is not None:
                cum["uncached"] += tokens.input - tokens.cache_read - tokens.cache_write
                cum["cacheRead"] += tokens.cache_read
                cum["cacheWrite"] += tokens.cache_write
                cum["output"] += tokens.output
                cum["input"] += tokens.input
            detail_mismatch: bool | None = None
            if row.token_details is not None:
                detail_rows += 1
                detail_sum: int = 0
                for detail in row.token_details:
                    entry: list[int] = token_types.setdefault(detail.token_type, [0, 0])
                    entry[0] += detail.token_count
                    entry[1] += detail.nano
                    detail_sum += detail.nano
                    if not detail.exact:
                        detail_inexact += 1
                detail_mismatch = row.nano is not None and detail_sum != row.nano
                if detail_mismatch:
                    detail_mismatch_rows += 1
            requests.append(
                {
                    "i": index + 1,
                    "t": row.t,
                    "agent": agent_key,
                    "model": row.model,
                    "effort": row.effort,
                    "initiator": row.initiator,
                    "turn": row.turn_index,
                    "input": tokens.input if tokens else None,
                    "output": tokens.output if tokens else None,
                    "cacheRead": tokens.cache_read if tokens else None,
                    "cacheWrite": tokens.cache_write if tokens else None,
                    "uncached": tokens.input - tokens.cache_read - tokens.cache_write
                    if tokens
                    else None,
                    "reasoning": row.reasoning,
                    "nano": None if row.nano is None else str(row.nano),
                    "cumNano": str(cum_nano),
                    "cum": {
                        "uncached": cum["uncached"],
                        "cacheRead": cum["cacheRead"],
                        "cacheWrite": cum["cacheWrite"],
                        "output": cum["output"],
                        "input": cum["input"],
                    },
                    "durationMs": row.duration_ms,
                    "ttftMs": row.ttft_ms,
                    "finishReason": row.finish_reason,
                    "apiEndpoint": row.api_endpoint,
                    "multiplier": row.multiplier,
                    "contentFilter": row.content_filter,
                    "detailMismatch": detail_mismatch,
                }
            )

        subagents: tuple[SubagentRecord, ...] = events.subagents
        matched: list[SubagentRecord] = []
        agents: list[AgentView] = [
            cls._agent_view(key, group, subagents, matched) for key, group in agent_groups.items()
        ]
        # 履歴に現れないサブエージェント (使用量の記録が無いもの) も、監査のため一覧に残す。
        for sub in subagents:
            if not any(sub is item for item in matched):
                agents.append(
                    cls._agent_view(
                        f"sub:{sub.agent_id or sub.tool_call_id}", _Group(), (sub,), matched
                    )
                )
        agents.sort(
            key=lambda agent: (
                cls._kind_order(agent["kind"]),
                math.inf if agent["firstAt"] is None else agent["firstAt"],
            )
        )

        app_result: AppResult = (
            app if app is not None else AppResult({"status": "missing"}, None, ())
        )
        app_view: AppView = cls._app_view(app_result)
        group_totals: GroupTotalsView = totals.totals(subtotal=True)
        session_totals: SessionTotalsView = {
            **group_totals,
            "duplicateRowsExcluded": history.duplicate_rows_excluded,
            "invalidTimestampRows": history.invalid_timestamp_rows,
        }
        models: list[ModelView] = [
            {"model": model, **group.totals()} for model, group in model_groups.items()
        ]
        models.sort(key=lambda item: cls._nano_desc_key(item["creditsNano"]))
        token_rows: list[TokenTypeRow] = [
            {"tokenType": name, "tokens": values[0], "nano": str(values[1])}
            for name, values in token_types.items()
        ]
        token_rows.sort(key=lambda item: cls._nano_desc_key(item["nano"]))
        start = events.start
        meta = history.session
        app_totals: AppTotalsView | None = app_view["totals"]
        return {
            "schema": 1,
            "readAt": read_at,
            "session": {
                "id": session_id,
                "isCurrent": is_current,
                "repository": (start.repository if start else None)
                or (meta.repository if meta else None),
                "branch": (start.branch if start else None) or (meta.branch if meta else None),
                "selectedModel": start.selected_model if start else None,
                "startTime": start.start_time if start else None,
                "copilotVersion": start.copilot_version if start else None,
                "producer": start.producer if start else None,
                "reasoningEffort": start.reasoning_effort if start else None,
                "contextTier": start.context_tier if start else None,
            },
            "sources": {
                "history": history.status,
                "events": events.status,
                "app": app_result.status,
            },
            "totals": session_totals,
            "requests": requests,
            "agents": agents,
            "models": models,
            "tokenTypes": {
                "rows": token_rows,
                "rowsWithDetails": detail_rows,
                "rowsWithoutDetails": len(rows) - detail_rows,
                "mismatchRows": detail_mismatch_rows,
                "inexactEntries": detail_inexact,
            },
            "reconciliation": {
                "cumulative": [cls._cumulative_view(event, rows) for event in events.cumulative],
                "app": cls._app_reconciliation(app_totals, totals) if app_totals else None,
            },
            "context": cls._context_view(rows, app_view, events),
            "app": app_view,
            "events": {
                "eventCount": events.event_count,
                "parseErrors": events.parse_errors,
                "firstEventAt": events.first_event_at,
                "lastEventAt": events.last_event_at,
                "turns": events.turns,
                "userMessages": events.user_messages,
                "aborts": events.aborts,
                "resumes": list(events.resumes),
                "branchChanges": events.branch_changes,
                "modelChanges": list(events.model_changes),
                "compactions": list(events.compactions),
                "shutdowns": list(events.shutdowns),
                "tools": list(events.tools),
                "errors": list(events.errors),
            },
        }

    # 履歴行のエージェント区分。
    @classmethod
    def agent_key(cls, row: HistoryRow) -> str:
        """initiator = compaction は圧縮、agent_id があればサブエージェント、それ以外はメイン。"""

        if row.initiator == cls.COMPACTION:
            return cls.COMPACTION
        if row.agent_id:
            return f"sub:{row.agent_id}"
        return cls.MAIN

    # エージェントの並び順。
    @staticmethod
    def _kind_order(kind: str) -> int:
        """メイン、サブエージェント、圧縮の順。"""

        return 0 if kind == "main" else 1 if kind == "subagent" else 2

    # nano の降順の key (記録なしは末尾)。
    @staticmethod
    def _nano_desc_key(nano: str | None) -> tuple[int, int]:
        """None は最後、値は大きい順。"""

        return (1, 0) if nano is None else (0, -int(nano))

    # 履歴の parent_tool_call_id と events の toolCallId、または agent 識別子同士で対応付ける。
    @staticmethod
    def _matches(agent_id: str | None, sub: SubagentRecord, parent_ids: set[str]) -> bool:
        """toolCallId が agent 識別子か履歴の親 tool call に一致するか、agentId が一致する。"""

        if sub.tool_call_id and (sub.tool_call_id == agent_id or sub.tool_call_id in parent_ids):
            return True
        return bool(sub.agent_id and sub.agent_id == agent_id)

    # エージェント 1 件の表示用データ。
    @classmethod
    def _agent_view(
        cls,
        key: str,
        group: _Group,
        subagents: tuple[SubagentRecord, ...],
        matched: list[SubagentRecord],
    ) -> AgentView:
        """名前は events.jsonl から、使用量は履歴から取る。"""

        kind: AgentKind = (
            "main" if key == cls.MAIN else "compaction" if key == cls.COMPACTION else "subagent"
        )
        agent_id: str | None = key[4:] if kind == "subagent" else None
        sub: SubagentRecord | None = None
        if kind == "subagent":
            sub = next(
                (
                    item
                    for item in subagents
                    if cls._matches(agent_id, item, group.parent_tool_call_ids)
                ),
                None,
            )
        if sub is not None and not any(sub is item for item in matched):
            matched.append(sub)
        label: str
        if kind == "main":
            label = "Main agent"
        elif kind == "compaction":
            label = "Compaction"
        else:
            label = (
                sub.display_name or sub.name if sub else None
            ) or f"Sub-agent {cls._short_id(agent_id)}"
        models: list[str] = list(group.models)
        if not models and sub is not None and sub.model:
            models.append(sub.model)
        efforts: list[str] = list(group.efforts)
        if not efforts and sub is not None and sub.effort:
            efforts.append(sub.effort)
        named: bool = kind != "subagent" or bool(sub and (sub.display_name or sub.name))
        totals: GroupTotalsView = group.totals()
        return {
            **totals,
            "key": key,
            "kind": kind,
            "label": label,
            "name": sub.name if sub else None,
            "agentId": agent_id,
            "shortId": cls._short_id(agent_id) if agent_id else None,
            "named": named,
            "models": models,
            "efforts": efforts,
            "startedAt": sub.started_at if sub else None,
            "endedAt": sub.ended_at if sub else None,
            "outcome": sub.outcome if sub else None,
        }

    # 識別子の先頭 8 文字。
    @staticmethod
    def _short_id(value: str | None) -> str:
        """識別子が無ければ unknown。"""

        return value[:8] if value else "unknown"

    # 累積イベントを、そのイベント時刻以前の履歴だけと比べる。
    @staticmethod
    def _cumulative_view(
        event: CumulativeEvent, rows: tuple[HistoryRow, ...]
    ) -> CumulativeReconciliation:
        """差は参考値で、補正には使わない。"""

        at: int | None = JsValue.parse_time(event.t)
        nano: int = 0
        missing: int = 0
        count: int = 0
        for row in rows:
            if at is None or row.t > at:
                break
            count += 1
            if row.nano is None:
                missing += 1
            else:
                nano += row.nano
        return {
            "type": event.type,
            "t": event.t,
            "nano": str(event.nano),
            "historyNanoAtT": str(nano),
            "historyRowsAtT": count,
            "historyMissingAtT": missing,
            "diffNano": str(event.nano - nano),
        }

    # App の参考値を表示用に整える。
    @classmethod
    def _app_view(cls, app: AppResult) -> AppView:
        """App が管理しないセッションでは totals / context が None。"""

        session: dict[str, AppValue] | None = app.session

        # 列の値 (無ければ None)。
        def value(name: str) -> AppValue:
            return session.get(name) if session is not None else None

        # nano 系の列を整数の 10 進文字列にする。
        def nano_text(name: str) -> str | None:
            raw: AppValue = value(name)
            if isinstance(raw, int):
                return str(raw)
            if isinstance(raw, float) and raw.is_integer():
                return str(int(raw))
            return None

        totals: AppTotalsView | None = None
        context: AppContextView | None = None
        if session is not None:
            totals = {
                "nano": nano_text("total_nano_aiu"),
                "agentMergeNano": nano_text("total_agent_merge_nano_aiu"),
                "input": value("total_input_tokens"),
                "output": value("total_output_tokens"),
                "cached": value("total_cached_tokens"),
                "reasoning": value("total_reasoning_tokens"),
            }
            context = {
                "current": value("context_current_tokens"),
                "inputLimit": value("context_input_token_limit"),
                "outputLimit": value("context_output_token_limit"),
                "system": value("context_system_tokens"),
                "conversation": value("context_conversation_tokens"),
                "toolDefinitions": value("context_tool_definitions_tokens"),
                "mcpTools": value("context_mcp_tools_tokens"),
                "buffer": value("context_buffer_tokens"),
            }
        return {
            "model": value("model"),
            "reasoningEffort": value("reasoning_effort"),
            "contextTier": value("context_tier"),
            "totals": totals,
            "context": context,
            "contextSeries": list(app.context_series),
        }

    # App が持つ累計値と履歴の取得済み小計を並べる。
    @staticmethod
    def _app_reconciliation(app_totals: AppTotalsView, history: _Group) -> AppReconciliation:
        """App の値の定義は公開されていないため参考比較に留める。"""

        app_nano: str | None = app_totals["nano"]
        return {
            "appNano": app_nano,
            "historyNano": str(history.nano),
            "diffNano": None if app_nano is None else str(int(app_nano) - history.nano),
            "appInput": app_totals["input"],
            "historyInput": history.input,
            "appOutput": app_totals["output"],
            "historyOutput": history.output,
            "appCached": app_totals["cached"],
            "historyCached": history.cache_read + history.cache_write,
            "appAgentMergeNano": app_totals["agentMergeNano"],
        }

    # 最新のコンテキストサイズと上限。
    @classmethod
    def _context_view(
        cls, rows: tuple[HistoryRow, ...], app: AppView, events: EventsSummary
    ) -> ContextView:
        """上限は記録がある所 (App、圧縮) だけから取る。"""

        series = app["contextSeries"]
        app_last = series[-1] if series else None
        app_context: AppContextView | None = app["context"]
        main_rows: list[HistoryRow] = [
            row for row in rows if cls.agent_key(row) == cls.MAIN and row.tokens is not None
        ]
        last_main: HistoryRow | None = main_rows[-1] if main_rows else None
        compaction_limit: int | None = next(
            (
                item["tokenLimit"]
                for item in reversed(events.compactions)
                if item["tokenLimit"] is not None
            ),
            None,
        )
        app_limit: AppValue = app_last["limit"] if app_last is not None else None
        input_limit: AppValue = app_context["inputLimit"] if app_context is not None else None
        limit: AppValue = (
            app_limit
            if app_limit is not None
            else input_limit
            if input_limit is not None
            else compaction_limit
        )
        limit_source: Literal["app", "compaction"] | None
        if app_limit is not None or input_limit is not None:
            limit_source = "app"
        elif compaction_limit is not None:
            limit_source = "compaction"
        else:
            limit_source = None
        latest_main: TimedTokens | None = None
        if last_main is not None and last_main.tokens is not None:
            latest_main = {"tokens": last_main.tokens.input, "t": last_main.t}
        app_current: TimedTokens | None = None
        if app_last is not None:
            app_current = {"tokens": app_last["current"], "t": app_last["t"]}
        elif app_context is not None and app_context["current"] is not None:
            app_current = {"tokens": app_context["current"], "t": None}
        limits: list[CompactionLimit] = [
            {"t": item["t"], "limit": item["tokenLimit"]}
            for item in events.compactions
            if item["tokenLimit"] is not None
        ]
        return {
            "latestMain": latest_main,
            "appCurrent": app_current,
            "limit": limit,
            "limitSource": limit_source,
            "compactionLimits": limits,
        }
