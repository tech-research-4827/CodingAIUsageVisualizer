from __future__ import annotations

from dataclasses import dataclass

from copilot_usage_visualizer.session_usage.charts import SessionCharts
from copilot_usage_visualizer.session_usage.contract import (
    AgentView,
    RequestView,
    SessionUsageView,
    SourceStatus,
)
from copilot_usage_visualizer.session_usage.format import SessionUsageFormat as Fmt
from copilot_usage_visualizer.session_usage.js_compat import JsValue


# 表 1 つ分の見出しと文字列。
@dataclass(frozen=True)
class TableData:
    """columns は (見出し, 数値列か)。rows は表示する文字列。"""

    columns: tuple[tuple[str, bool], ...]
    rows: tuple[tuple[str, ...], ...]


# 要約のカード 1 枚。
@dataclass(frozen=True)
class SummaryCard:
    """label・大きく出す値・補足・正確な値 (ツールチップ)。"""

    label: str
    value: str
    sub: str
    title: str


# 表示用データから画面の文字列 (通知・カード・表・事実) を作る。
class SessionPresentation:
    """web/app.mjs の render* と同じ文言と表の列。Dear PyGui に依存しない。"""

    STATUS_TEXT: dict[str, str] = {
        "missing": "not found",
        "unreadable": "could not be read",
        "unsupported_schema": "has an unsupported format",
        "not_found": "has no record of this session",
    }

    # 読み取り元の状態を文にする。
    @classmethod
    def status_text(cls, status: SourceStatus) -> str:
        """固定コードの detail を括弧で添える (パスは含まれない)。"""

        code: str = status["status"]
        detail: str | None = status.get("detail")
        return f"{cls.STATUS_TEXT.get(code, code)}{f' ({detail})' if detail else ''}"

    # 読み取り元の状態 (ok はそのまま)。
    @classmethod
    def source_text(cls, status: SourceStatus) -> str:
        """Session and sources の欄。"""

        return "ok" if status["status"] == "ok" else cls.status_text(status)

    # repository@branch。
    @staticmethod
    def project(view: SessionUsageView) -> str | None:
        """repository が無ければ None。"""

        repository: str | None = view["session"]["repository"]
        branch: str | None = view["session"]["branch"]
        if not repository:
            return None
        return f"{repository}@{branch}" if branch else repository

    # 要約の見出しに添えるセッションの識別。
    @classmethod
    def session_line(cls, view: SessionUsageView) -> str:
        """Session · ID の先頭 8 文字 · repository@branch。"""

        parts: tuple[str | None, ...] = ("Session", view["session"]["id"][:8], cls.project(view))
        return " · ".join(part for part in parts if part)

    # Credits の欄 (欠落件数を添える)。
    @staticmethod
    def credits_cell(nano: str | None, missing: int) -> str:
        """記録なしは `-`。"""

        text: str = Fmt.credits_text(nano)
        return f"{text} ({missing} missing)" if missing else text

    # 部分表示・失敗の理由。
    @classmethod
    def alerts(cls, view: SessionUsageView) -> list[str]:
        """色ではなく文字 (Error / Warning / Note) で種類を示す。"""

        items: list[str] = []
        sources = view["sources"]
        totals = view["totals"]
        if sources["history"]["status"] != "ok":
            items.append(
                f"Error · Request history: session-store.db "
                f"{cls.status_text(sources['history'])}. "
                "Totals, charts and agents are unavailable."
            )
        elif totals["requests"] == 0:
            items.append(
                "Warning · No request history: this session has no rows in session-store.db. "
                "Cumulative events (if any) are shown under Reconciliation as reference only."
            )
        if sources["events"]["status"] != "ok":
            items.append(
                f"Warning · Session events: events.jsonl {cls.status_text(sources['events'])}. "
                "Sub-agent names, compactions and cumulative totals are unavailable."
            )
        if sources["app"]["status"] not in ("ok", "not_found", "missing"):
            items.append(
                f"Warning · App data: data.db {cls.status_text(sources['app'])}. "
                "Context limits from the App are unavailable."
            )
        if totals["creditsMissing"] > 0:
            items.append(
                f"Warning · Credits missing: {totals['creditsMissing']} request(s) have no "
                "AI Credits value. Totals are an observed subtotal; missing values are not "
                "counted as 0."
            )
        if totals["tokenRowsInvalid"] > 0:
            items.append(
                f"Warning · Invalid tokens: {totals['tokenRowsInvalid']} request(s) have "
                "inconsistent token counts and are excluded from token totals."
            )
        if totals["invalidTimestampRows"] > 0:
            items.append(
                f"Warning · Invalid time: {totals['invalidTimestampRows']} request(s) without "
                "a valid timestamp are excluded."
            )
        if view["tokenTypes"]["mismatchRows"] > 0:
            items.append(
                f"Warning · Cost detail mismatch: {view['tokenTypes']['mismatchRows']} "
                "request(s) whose per-token-type cost does not add up to the recorded AI Credits."
            )
        if any(not agent["named"] for agent in view["agents"]):
            items.append(
                "Note · Unnamed sub-agent: some sub-agents have no name in events.jsonl and are "
                "shown by a short id."
            )
        if totals["duplicateRowsExcluded"] > 0:
            items.append(
                f"Note · Duplicates excluded: {totals['duplicateRowsExcluded']} identical history "
                "row(s) counted once (see Session and sources)."
            )
        return items

    # 要約のカード。
    @classmethod
    def summary_cards(cls, view: SessionUsageView) -> list[SummaryCard]:
        """履歴を読めない時は実測 0 と区別するため、履歴由来の値をすべて `-` にする。"""

        totals = view["totals"]
        tokens = totals["tokens"]
        no_history: bool = view["sources"]["history"]["status"] != "ok"
        context = view["context"]
        app_current = context["appCurrent"]
        latest_main = context["latestMain"]
        # App の現在値が無い (None) 時だけ履歴の値で補う。実測 0 は 0 のまま (Node の `??` と同じ)。
        ctx_value: object = app_current["tokens"] if app_current is not None else None
        if ctx_value is None and latest_main is not None:
            ctx_value = latest_main["tokens"]
        limit: object = context["limit"]
        ctx_sub: str = "limit not recorded"
        if limit:
            source: str = "App" if context["limitSource"] == "app" else "compaction"
            percent: str = Fmt.percent_text(ctx_value, limit)
            ctx_sub = f"of {Fmt.compact_text(limit)} ({percent}) · {source} limit"
        span: str = (
            f"{Fmt.time_text(totals['firstAt'])}–{Fmt.time_text(totals['lastAt'])}"
            if totals["firstAt"] is not None
            else "no requests"
        )
        missing: str = (
            f"{totals['creditsMissing']} missing"
            if totals["creditsMissing"]
            else "observed subtotal"
        )

        # 履歴由来の値。
        def history(text: str) -> str:
            return "-" if no_history else text

        input_share: str = Fmt.percent_text(tokens["cacheRead"], tokens["input"])
        write_share: str = Fmt.percent_text(tokens["cacheWrite"], tokens["input"])
        return [
            SummaryCard(
                "AI Credits",
                history(Fmt.credits_rounded(totals["creditsNano"])),
                history(f"≈ {Fmt.usd_text(totals['creditsNano'])} · {missing}"),
                history(f"Exact: {Fmt.credits_text(totals['creditsNano'])} AI Credits"),
            ),
            SummaryCard(
                "Requests",
                history(Fmt.int_text(totals["requests"])),
                history(span),
                history(f"API time {Fmt.duration_text(totals['durationMs'])}"),
            ),
            SummaryCard(
                "Input tokens",
                history(Fmt.compact_text(tokens["input"])),
                history(f"uncached {Fmt.compact_text(tokens['uncachedInput'])}"),
                history(f"{Fmt.int_text(tokens['input'])} (includes cache read and cache write)"),
            ),
            SummaryCard(
                "Output tokens",
                history(Fmt.compact_text(tokens["output"])),
                history(f"reasoning {Fmt.compact_text(tokens['reasoning'])}"),
                history(Fmt.int_text(tokens["output"])),
            ),
            SummaryCard(
                "Cache read",
                history(Fmt.compact_text(tokens["cacheRead"])),
                history(f"{input_share} of input"),
                history(Fmt.int_text(tokens["cacheRead"])),
            ),
            SummaryCard(
                "Cache write",
                history(Fmt.compact_text(tokens["cacheWrite"])),
                history(f"{write_share} of input"),
                history(Fmt.int_text(tokens["cacheWrite"])),
            ),
            SummaryCard(
                "Context (main)",
                Fmt.compact_text(ctx_value),
                ctx_sub,
                "No main-agent request yet" if ctx_value is None else Fmt.int_text(ctx_value),
            ),
        ]

    # エージェントの表。
    @classmethod
    def agents_table(cls, view: SessionUsageView) -> TableData:
        """使用量の記録が無いサブエージェントも監査のため残す。"""

        columns: tuple[tuple[str, bool], ...] = (
            ("Agent", False),
            ("Model", False),
            ("Effort", False),
            ("Requests", True),
            ("AI Credits", True),
            ("Input", True),
            ("Output", True),
            ("Cache read", True),
            ("Cache write", True),
            ("Context max", True),
            ("Context last", True),
            ("Outcome", False),
        )
        rows: list[tuple[str, ...]] = []
        for agent in view["agents"]:
            kind_tag: str = {"main": "", "subagent": " [sub-agent]"}.get(
                agent["kind"], " [compaction]"
            )
            name: str = f"{agent['label']}{kind_tag}"
            models: str = ", ".join(agent["models"]) or "-"
            efforts: str = ", ".join(agent["efforts"]) or "-"
            if agent["requests"] == 0:
                rows.append(
                    (name, models, efforts, "-", "no usage rows in request history")
                    + ("",) * 6
                    + (agent["outcome"] or "-",)
                )
                continue
            tokens = agent["tokens"]
            outcome: str = agent["outcome"] or (
                "running/unknown" if agent["kind"] == "subagent" else "-"
            )
            rows.append(
                (
                    name,
                    models,
                    efforts,
                    Fmt.int_text(agent["requests"]),
                    cls.credits_cell(agent["creditsNano"], agent["creditsMissing"]),
                    Fmt.int_text(tokens["input"]),
                    Fmt.int_text(tokens["output"]),
                    Fmt.int_text(tokens["cacheRead"]),
                    Fmt.int_text(tokens["cacheWrite"]),
                    Fmt.int_text(agent["ctxMax"]),
                    Fmt.int_text(agent["ctxLast"]),
                    outcome,
                )
            )
        return TableData(columns, tuple(rows))

    # モデルの表。
    @classmethod
    def models_table(cls, view: SessionUsageView) -> TableData:
        """Credits の多い順 (記録なしは末尾)。"""

        columns: tuple[tuple[str, bool], ...] = (
            ("Model", False),
            ("Effort", False),
            ("Requests", True),
            ("AI Credits", True),
            ("Input", True),
            ("Output", True),
            ("Cache read", True),
            ("Cache write", True),
        )
        rows: tuple[tuple[str, ...], ...] = tuple(
            (
                model["model"],
                ", ".join(model["efforts"]) or "-",
                Fmt.int_text(model["requests"]),
                cls.credits_cell(model["creditsNano"], model["creditsMissing"]),
                Fmt.int_text(model["tokens"]["input"]),
                Fmt.int_text(model["tokens"]["output"]),
                Fmt.int_text(model["tokens"]["cacheRead"]),
                Fmt.int_text(model["tokens"]["cacheWrite"]),
            )
            for model in view["models"]
        )
        return TableData(columns, rows)

    # request ごとの表 (1 ページ分)。
    @staticmethod
    def requests_table(view: SessionUsageView, requests: list[RequestView]) -> TableData:
        """token が不正な行は 4 列を invalid と示し、単価の詳細が合わない行に ≠ を付ける。"""

        columns: tuple[tuple[str, bool], ...] = (
            ("#", True),
            ("Time", False),
            ("Agent", False),
            ("Model", False),
            ("Effort", False),
            ("Initiator", False),
            ("AI Credits", True),
            ("Input", True),
            ("Output", True),
            ("Cache read", True),
            ("Cache write", True),
            ("Reasoning", True),
            ("Duration", True),
            ("TTFT", True),
            ("Finish", False),
            ("Endpoint", False),
        )
        rows: list[tuple[str, ...]] = []
        for request in requests:
            invalid: bool = request["input"] is None
            token_cells: tuple[str, ...] = (
                ("invalid",) * 4
                if invalid
                else tuple(
                    Fmt.int_text(value)
                    for value in (
                        request["input"],
                        request["output"],
                        request["cacheRead"],
                        request["cacheWrite"],
                    )
                )
            )
            mismatch: str = " ≠" if request["detailMismatch"] else ""
            rows.append(
                (
                    f"#{request['i']}",
                    Fmt.time_text(request["t"]),
                    SessionCharts.agent_label(view, request["agent"]),
                    request["model"] or "-",
                    request["effort"] or "-",
                    request["initiator"] or "-",
                    Fmt.credits_text(request["nano"]),
                    *token_cells,
                    Fmt.int_text(request["reasoning"]),
                    Fmt.duration_text(request["durationMs"]),
                    Fmt.duration_text(request["ttftMs"]),
                    f"{request['finishReason'] or '-'}{mismatch}",
                    request["apiEndpoint"] or "-",
                )
            )
        return TableData(columns, tuple(rows))

    # token 種別ごとの Credits の表と注記。
    @staticmethod
    def token_types(view: SessionUsageView) -> tuple[TableData, str]:
        """各 request の単価の詳細から計算した値。"""

        token_types = view["tokenTypes"]
        total: int = sum(int(row["nano"]) for row in token_types["rows"])
        table: TableData = TableData(
            (("Token type", False), ("Tokens", True), ("AI Credits", True), ("Share", True)),
            tuple(
                (
                    row["tokenType"],
                    Fmt.int_text(row["tokens"]),
                    Fmt.credits_text(row["nano"]),
                    Fmt.percent_text(int(row["nano"]), total),
                )
                for row in token_types["rows"]
            ),
        )
        note: str = (
            f"Computed from each request's per-token-type rates. "
            f"{token_types['rowsWithoutDetails']} request(s) without rate details are not included "
            f"here; {token_types['mismatchRows']} request(s) do not add up to the recorded value."
        )
        return table, note

    # 累積イベントとの照合の表。
    @classmethod
    def cumulative_table(cls, view: SessionUsageView) -> TableData:
        """イベント時刻以前の履歴小計との参考差分。"""

        return TableData(
            (
                ("Event", False),
                ("Time", False),
                ("Event AI Credits", True),
                ("History at event", True),
                ("Rows", True),
                ("Difference", True),
            ),
            tuple(
                (
                    item["type"],
                    Fmt.date_time_text(JsValue.parse_time(item["t"])),
                    Fmt.credits_text(item["nano"]),
                    cls.credits_cell(item["historyNanoAtT"], item["historyMissingAtT"]),
                    Fmt.int_text(item["historyRowsAtT"]),
                    Fmt.credits_text(item["diffNano"]),
                )
                for item in view["reconciliation"]["cumulative"]
            ),
        )

    # 照合の注記。
    @staticmethod
    def reconciliation_note(view: SessionUsageView) -> str:
        """差は参考値で補正ではない。"""

        read_at: str = Fmt.date_time_text(JsValue.parse_time(view["readAt"]))
        return (
            f"History read at {read_at}. Each cumulative event is compared with history rows "
            "recorded at or before the event time; differences are references, not corrections."
        )

    # App の累計との照合の表。
    @staticmethod
    def app_table(view: SessionUsageView) -> TableData | None:
        """App がこのセッションの累計を持たなければ None。"""

        app = view["reconciliation"]["app"]
        if app is None:
            return None
        return TableData(
            (("Value", False), ("App (data.db)", True), ("Request history", True)),
            (
                (
                    "AI Credits",
                    Fmt.credits_text(app["appNano"]),
                    Fmt.credits_text(app["historyNano"]),
                ),
                ("Input tokens", Fmt.int_text(app["appInput"]), Fmt.int_text(app["historyInput"])),
                (
                    "Output tokens",
                    Fmt.int_text(app["appOutput"]),
                    Fmt.int_text(app["historyOutput"]),
                ),
                (
                    "Cached tokens (read + write)",
                    Fmt.int_text(app["appCached"]),
                    Fmt.int_text(app["historyCached"]),
                ),
            ),
        )

    # 圧縮の表。
    @staticmethod
    def compactions_table(view: SessionUsageView) -> TableData:
        """events.jsonl の圧縮イベント。"""

        return TableData(
            (
                ("Time", False),
                ("Result", False),
                ("Trigger", False),
                ("Before", True),
                ("Limit", True),
                ("Model", False),
                ("AI Credits", True),
                ("Input", True),
                ("Output", True),
                ("Duration", True),
            ),
            tuple(
                (
                    Fmt.date_time_text(JsValue.parse_time(item["t"])),
                    "-" if item["success"] is None else "success" if item["success"] else "failed",
                    item["trigger"] or "-",
                    Fmt.int_text(item["preTokens"]),
                    Fmt.int_text(item["tokenLimit"]),
                    item["model"] or "-",
                    Fmt.credits_text(item["nano"]),
                    Fmt.int_text(item["input"]),
                    Fmt.int_text(item["output"]),
                    Fmt.duration_text(item["durationMs"]),
                )
                for item in view["events"]["compactions"]
            ),
        )

    # モデル変更の表。
    @staticmethod
    def model_changes_table(view: SessionUsageView) -> TableData:
        """effort の変化は「前 → 後」。"""

        rows: list[tuple[str, ...]] = []
        for change in view["events"]["modelChanges"]:
            efforts: tuple[str | None, ...] = (
                change["previousReasoningEffort"],
                change["reasoningEffort"],
            )
            rows.append(
                (
                    Fmt.date_time_text(JsValue.parse_time(change["t"])),
                    change["previousModel"] or "-",
                    change["newModel"] or "-",
                    " → ".join(value for value in efforts if value) or "-",
                )
            )
        return TableData(
            (("Time", False), ("From", False), ("To", False), ("Effort", False)), tuple(rows)
        )

    # サブエージェントの表。
    @classmethod
    def subagents_table(cls, view: SessionUsageView) -> TableData:
        """名前・ID・モデル・開始と終了・結果・使用量。"""

        subagents: list[AgentView] = [
            agent for agent in view["agents"] if agent["kind"] == "subagent"
        ]
        return TableData(
            (
                ("Agent", False),
                ("Id", False),
                ("Model", False),
                ("Effort", False),
                ("Started", False),
                ("Ended", False),
                ("Outcome", False),
                ("Requests", True),
                ("AI Credits", True),
            ),
            tuple(
                (
                    agent["label"],
                    agent["shortId"] or "-",
                    ", ".join(agent["models"]) or "-",
                    ", ".join(agent["efforts"]) or "-",
                    Fmt.time_text(JsValue.parse_time(agent["startedAt"])),
                    Fmt.time_text(JsValue.parse_time(agent["endedAt"])),
                    agent["outcome"] or "-",
                    Fmt.int_text(agent["requests"]),
                    cls.credits_cell(agent["creditsNano"], agent["creditsMissing"]),
                )
                for agent in subagents
            ),
        )

    # tool の表。
    @staticmethod
    def tools_table(view: SessionUsageView) -> TableData:
        """呼び出しの多い順。"""

        return TableData(
            (("Tool", False), ("Calls", True), ("Failures", True)),
            tuple(
                (tool["name"], Fmt.int_text(tool["calls"]), Fmt.int_text(tool["failures"]))
                for tool in view["events"]["tools"]
            ),
        )

    # エラーと中断。
    @staticmethod
    def error_facts(view: SessionUsageView) -> list[tuple[str, str]]:
        """エラー種別の件数・中断・再開・読めなかったイベント行。"""

        events = view["events"]
        errors: str = ", ".join(
            f"{item['errorType']} ×{item['count']}" for item in events["errors"]
        )
        resumes: str = ", ".join(
            Fmt.date_time_text(JsValue.parse_time(t)) for t in events["resumes"]
        )
        return [
            ("Errors", errors or "none"),
            ("Aborts", Fmt.int_text(events["aborts"])),
            ("Resumes", resumes or "none"),
            ("Unreadable event lines", Fmt.int_text(events["parseErrors"])),
        ]

    # セッションと読み取り元。
    @classmethod
    def session_facts(cls, view: SessionUsageView) -> list[tuple[str, str]]:
        """読み取り元の状態と、保存重複の扱いの限界を示す。"""

        session = view["session"]
        events = view["events"]
        app_view = view["app"]
        context = app_view["context"]
        app_now: str = "-"
        if context is not None:
            parts: str = ", ".join(
                f"{name} {Fmt.int_text(value)}"
                for name, value in (
                    ("system", context["system"]),
                    ("conversation", context["conversation"]),
                    ("tools", context["toolDefinitions"]),
                    ("MCP", context["mcpTools"]),
                    ("buffer", context["buffer"]),
                )
            )
            current: str = Fmt.int_text(context["current"])
            app_now = f"{current} / {Fmt.int_text(context['inputLimit'])} ({parts})"
        shutdowns = events["shutdowns"]
        shutdown: str = "-"
        if shutdowns:
            last = shutdowns[-1]
            lines: str = (
                f"+{Fmt.int_text(last['linesAdded'])}/-{Fmt.int_text(last['linesRemoved'])}"
            )
            shutdown = " · ".join(
                (
                    Fmt.date_time_text(JsValue.parse_time(last["t"])),
                    last["shutdownType"] or "-",
                    f"{Fmt.credits_text(last['nano'])} AI Credits",
                    f"{lines} lines in {Fmt.int_text(last['filesModified'])} file(s)",
                )
            )
        initial: str = " / ".join(
            str(value)
            for value in (
                session["selectedModel"],
                session["reasoningEffort"],
                session["contextTier"],
            )
            if value
        )
        app_model: str = " / ".join(
            str(value) for value in (app_view["model"], app_view["reasoningEffort"]) if value
        )
        last_event: str = Fmt.date_time_text(JsValue.parse_time(events["lastEventAt"]))
        duplicates: str = (
            f"{Fmt.int_text(view['totals']['duplicateRowsExcluded'])} — identical rows are counted "
            "once; distinct calls with identical recorded values cannot be told apart and are also "
            "counted once."
        )
        return [
            ("Session id", session["id"]),
            ("Repository", cls.project(view) or "-"),
            ("CLI version", session["copilotVersion"] or "-"),
            ("Started", Fmt.date_time_text(JsValue.parse_time(session["startTime"]))),
            ("Initial model / effort / tier", initial or "-"),
            ("App model / effort", app_model or "-"),
            (
                "Turns / user messages",
                f"{Fmt.int_text(events['turns'])} / {Fmt.int_text(events['userMessages'])}",
            ),
            ("Events", f"{Fmt.int_text(events['eventCount'])} (last {last_event})"),
            ("App context now", app_now),
            ("Last shutdown", shutdown),
            ("Request history", cls.source_text(view["sources"]["history"])),
            ("Session events", cls.source_text(view["sources"]["events"])),
            ("App data", cls.source_text(view["sources"]["app"])),
            ("Duplicates excluded", duplicates),
            ("Read at", Fmt.date_time_text(JsValue.parse_time(view["readAt"]))),
        ]
