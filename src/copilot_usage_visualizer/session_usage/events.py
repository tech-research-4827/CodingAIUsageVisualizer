from __future__ import annotations

import errno
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from copilot_usage_visualizer.session_usage.contract import (
    CompactionView,
    ErrorView,
    ModelChangeView,
    ModelMetricView,
    ShutdownView,
    SourceStatus,
    ToolView,
)
from copilot_usage_visualizer.session_usage.js_compat import JsonValue, JsValue, LocaleOrder

# JSON object (json.loads の dict)。I/O 境界の値。
JsonObject = dict[str, JsonValue]


# session.start から取る識別子。
@dataclass(frozen=True)
class SessionStart:
    """cwd / gitRoot は絶対パスなので読まない。repository は owner/name 形式の識別子。"""

    start_time: str | None
    copilot_version: str | None
    producer: str | None
    reasoning_effort: str | None
    selected_model: str | None
    context_tier: str | None
    repository: str | None
    branch: str | None


# サブエージェント 1 件。
@dataclass
class SubagentRecord:
    """toolCallId (= 履歴の parent_tool_call_id) か agentId で束ねる。"""

    tool_call_id: str | None
    agent_id: str | None
    name: str | None = None
    display_name: str | None = None
    model: str | None = None
    effort: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    outcome: str | None = None


# 累積イベント (checkpoint / shutdown) の totalNanoAiu。
@dataclass(frozen=True)
class CumulativeEvent:
    """合計に加えず、その時刻以前の履歴小計と並べる参考値。"""

    type: Literal["checkpoint", "shutdown"]
    t: str
    nano: int


# events.jsonl の要約。
@dataclass(frozen=True)
class EventsSummary:
    """値は数値・時刻・モデル名・エージェント名などの識別子だけ。"""

    status: SourceStatus
    event_count: int = 0
    parse_errors: int = 0
    first_event_at: str | None = None
    last_event_at: str | None = None
    start: SessionStart | None = None
    resumes: tuple[str, ...] = ()
    branch_changes: int = 0
    turns: int = 0
    user_messages: int = 0
    aborts: int = 0
    model_changes: tuple[ModelChangeView, ...] = ()
    subagents: tuple[SubagentRecord, ...] = ()
    cumulative: tuple[CumulativeEvent, ...] = ()
    shutdowns: tuple[ShutdownView, ...] = ()
    compactions: tuple[CompactionView, ...] = ()
    tools: tuple[ToolView, ...] = ()
    errors: tuple[ErrorView, ...] = ()


# 行ごとの集計状態。
class EventsState:
    """lib/events.mjs の EventsState と同じ規則。

    本文 (message content, tool の引数と結果, summary, path) は残さない。
    """

    # 解析する種類。これ以外の行は JSON として解析せず件数だけ数える。
    PARSED_TYPES: frozenset[str] = frozenset(
        {
            "session.start",
            "session.resume",
            "session.model_change",
            "session.context_changed",
            "session.usage_checkpoint",
            "session.shutdown",
            "session.compaction_complete",
            "session.error",
            "abort",
            "subagent.started",
            "subagent.configured",
            "subagent.completed",
            "subagent.failed",
            "tool.execution_start",
            "tool.execution_complete",
        }
    )
    COUNTED_TYPES: frozenset[str] = frozenset({"assistant.turn_start", "user.message"})
    TYPE_PREFIX: re.Pattern[str] = re.compile(r'^\{"type":"([A-Za-z0-9_.]+)"')
    TIMESTAMP: re.Pattern[str] = re.compile(r'"timestamp":"([^"]+)"')

    # 空の状態を作る。
    def __init__(self) -> None:
        """件数と一覧を初期化する。"""

        self.event_count: int = 0
        self.parse_errors: int = 0
        self.first_event_at: str | None = None
        self.last_event_at: str | None = None
        self.start: SessionStart | None = None
        self.resumes: list[str] = []
        self.branch_changes: int = 0
        self.turns: int = 0
        self.user_messages: int = 0
        self.aborts: int = 0
        self.model_changes: list[ModelChangeView] = []
        self.subagents: list[SubagentRecord] = []
        self.cumulative: list[CumulativeEvent] = []
        self.shutdowns: list[ShutdownView] = []
        self.compactions: list[CompactionView] = []
        self.tool_call_names: dict[str, str] = {}
        self.tools: dict[str, list[int]] = {}
        self.errors: dict[str, int] = {}

    # 1 行を処理する。
    def line(self, text: str) -> None:
        """対象外の種類は JSON 解析しない (時刻だけ拾う)。"""

        if not text.strip(JsValue.WHITESPACE):
            return
        self.event_count += 1
        match: re.Match[str] | None = self.TYPE_PREFIX.match(text)
        prefix_type: str | None = match.group(1) if match else None
        if prefix_type is not None and prefix_type in self.COUNTED_TYPES:
            if prefix_type == "assistant.turn_start":
                self.turns += 1
            else:
                self.user_messages += 1
            self._observe_time(text)
            return
        if prefix_type is not None and prefix_type not in self.PARSED_TYPES:
            self._observe_time(text)
            return
        try:
            event: JsonValue = JsValue.parse_json(text)
        except ValueError:
            self.parse_errors += 1
            return
        # JSON.parse の結果が object (配列を含む) でなければ不正な行。
        if isinstance(event, list):
            event = {}
        if not isinstance(event, dict):
            self.parse_errors += 1
            return
        t: str | None = JsValue.iso_time(event.get("timestamp"))
        if t is not None:
            self._touch(t)
        event_type: JsonValue = event.get("type")
        if prefix_type is None and not (
            isinstance(event_type, str) and event_type in self.PARSED_TYPES
        ):
            if event_type == "assistant.turn_start":
                self.turns += 1
            if event_type == "user.message":
                self.user_messages += 1
            return
        # 配列の data もプロパティを持たないので、object 以外と同じく空として扱う。
        data: JsonObject = self._object(event.get("data"))
        self._apply(event_type if isinstance(event_type, str) else "", event, data, t)

    # 解析しない行からも timestamp だけ拾う。
    def _observe_time(self, text: str) -> None:
        """最終イベント時刻を正しく保つため。"""

        match: re.Match[str] | None = self.TIMESTAMP.search(text)
        t: str | None = JsValue.iso_time(match.group(1)) if match else None
        if t is not None:
            self._touch(t)

    # 最初と最後のイベント時刻を更新する。
    def _touch(self, t: str) -> None:
        """ISO 文字列は同じ形なので文字列の比較で前後が決まる。"""

        if self.first_event_at is None or t < self.first_event_at:
            self.first_event_at = t
        if self.last_event_at is None or t > self.last_event_at:
            self.last_event_at = t

    # 種類ごとに必要なフィールドだけを取り出す。
    def _apply(self, event_type: str, event: JsonObject, data: JsonObject, t: str | None) -> None:
        """未知の種類は何もしない。"""

        if event_type == "session.start":
            context: JsonObject = self._object(data.get("context"))
            self.start = SessionStart(
                start_time=JsValue.iso_time(data.get("startTime")) or t,
                copilot_version=JsValue.text(data.get("copilotVersion")),
                producer=JsValue.text(data.get("producer")),
                reasoning_effort=JsValue.text(data.get("reasoningEffort")),
                selected_model=JsValue.text(data.get("selectedModel")),
                context_tier=JsValue.text(data.get("contextTier")),
                repository=JsValue.text(context.get("repository")),
                branch=JsValue.text(context.get("branch")),
            )
        elif event_type == "session.resume":
            if t is not None:
                self.resumes.append(t)
        elif event_type == "session.context_changed":
            self.branch_changes += 1
        elif event_type == "session.model_change":
            self.model_changes.append(
                {
                    "t": t,
                    "newModel": JsValue.text(data.get("newModel")),
                    "previousModel": JsValue.text(data.get("previousModel")),
                    "reasoningEffort": JsValue.text(data.get("reasoningEffort")),
                    "previousReasoningEffort": JsValue.text(data.get("previousReasoningEffort")),
                }
            )
        elif event_type == "session.usage_checkpoint":
            nano: int | None = JsValue.safe_int(data.get("totalNanoAiu"))
            if t is not None and nano is not None:
                self.cumulative.append(CumulativeEvent("checkpoint", t, nano))
        elif event_type == "session.shutdown":
            self._shutdown(data, t)
        elif event_type == "session.compaction_complete":
            self._compaction(data, t)
        elif event_type == "session.error":
            key: str = JsValue.text(data.get("errorType")) or "unknown"
            self.errors[key] = self.errors.get(key, 0) + 1
        elif event_type == "abort":
            self.aborts += 1
        elif event_type in (
            "subagent.started",
            "subagent.configured",
            "subagent.completed",
            "subagent.failed",
        ):
            self._subagent(event_type, event, data, t)
        elif event_type == "tool.execution_start":
            name: str = JsValue.text(data.get("toolName")) or "unknown"
            call_id: str | None = JsValue.text(data.get("toolCallId"))
            if call_id is not None:
                self.tool_call_names[call_id] = name
            self.tools.setdefault(name, [0, 0])[0] += 1
        elif event_type == "tool.execution_complete":
            if data.get("success") is False:
                failed: str = self.tool_call_names.get(
                    JsValue.text(data.get("toolCallId")) or "", "unknown"
                )
                self.tools.setdefault(failed, [0, 0])[1] += 1

    # session.shutdown の数値と識別子。
    def _shutdown(self, data: JsonObject, t: str | None) -> None:
        """変更ファイルはパスなので件数だけを残す。"""

        nano: int | None = JsValue.safe_int(data.get("totalNanoAiu"))
        if t is not None and nano is not None:
            self.cumulative.append(CumulativeEvent("shutdown", t, nano))
        details: JsonObject = self._object(data.get("tokenDetails"))
        changes: JsonObject = self._object(data.get("codeChanges"))
        files: JsonValue = changes.get("filesModified")
        self.shutdowns.append(
            {
                "t": t,
                "shutdownType": JsValue.text(data.get("shutdownType")),
                "nano": None if nano is None else str(nano),
                "tokens": {
                    "input": self._token_count(details, "input"),
                    "output": self._token_count(details, "output"),
                    "cacheRead": self._token_count(details, "cache_read"),
                    "cacheWrite": self._token_count(details, "cache_write"),
                },
                "currentModel": JsValue.text(data.get("currentModel")),
                "currentTokens": JsValue.safe_int(data.get("currentTokens")),
                "systemTokens": JsValue.safe_int(data.get("systemTokens")),
                "conversationTokens": JsValue.safe_int(data.get("conversationTokens")),
                "toolDefinitionsTokens": JsValue.safe_int(data.get("toolDefinitionsTokens")),
                "totalApiDurationMs": JsValue.safe_int(data.get("totalApiDurationMs")),
                "linesAdded": JsValue.safe_int(changes.get("linesAdded")),
                "linesRemoved": JsValue.safe_int(changes.get("linesRemoved")),
                "filesModified": len(files) if isinstance(files, list) else None,
                "modelMetrics": self._model_metrics(data.get("modelMetrics")),
            }
        )

    # tokenDetails の 1 種別の件数。
    def _token_count(self, details: JsonObject, key: str) -> int | None:
        """値が object でなければ None。"""

        return JsValue.safe_int(self._object(details.get(key)).get("tokenCount"))

    # modelMetrics のモデル別値。
    def _model_metrics(self, value: JsonValue) -> list[ModelMetricView]:
        """Object.entries と同じ順 (配列なら添字を key とする)。"""

        entries: list[tuple[str, JsonValue]]
        if isinstance(value, dict):
            entries = self._entries_in_js_order(value)
        elif isinstance(value, list):
            entries = [(str(index), item) for index, item in enumerate(value)]
        else:
            entries = []
        metrics: list[ModelMetricView] = []
        for model, item in entries:
            metric: JsonObject = self._object(item)
            nano: int | None = JsValue.safe_int(metric.get("totalNanoAiu"))
            metrics.append(
                {
                    "model": model,
                    "nano": None if nano is None else str(nano),
                    "requests": JsValue.safe_int(self._object(metric.get("requests")).get("count")),
                }
            )
        return metrics

    # session.compaction_complete の数値と識別子。
    def _compaction(self, data: JsonObject, t: str | None) -> None:
        """summaryContent・checkpointPath は読まない。"""

        used: JsonObject = self._object(data.get("compactionTokensUsed"))
        success: JsonValue = data.get("success")
        nano: int | None = JsValue.safe_int(
            self._object(used.get("copilotUsage")).get("totalNanoAiu")
        )
        self.compactions.append(
            {
                "t": t,
                "success": success if isinstance(success, bool) else None,
                "trigger": JsValue.text(data.get("trigger")),
                "preTokens": JsValue.safe_int(data.get("preCompactionTokens")),
                "tokenLimit": JsValue.safe_int(data.get("tokenLimit")),
                "model": JsValue.text(used.get("model")),
                "input": JsValue.safe_int(used.get("inputTokens")),
                "output": JsValue.safe_int(used.get("outputTokens")),
                "cacheRead": JsValue.safe_int(used.get("cacheReadTokens")),
                "cacheWrite": JsValue.safe_int(used.get("cacheWriteTokens")),
                "nano": None if nano is None else str(nano),
                "durationMs": JsValue.safe_int(used.get("duration")),
            }
        )

    # サブエージェントのイベントを束ねる。
    def _subagent(
        self, event_type: str, event: JsonObject, data: JsonObject, t: str | None
    ) -> None:
        """subagent.configured は toolCallId を持たないため、agentId でも同じ entry を探す。"""

        tool_call_id: str | None = JsValue.text(data.get("toolCallId"))
        agent_id: str | None = JsValue.text(event.get("agentId"))
        if tool_call_id is None and agent_id is None:
            return
        entry: SubagentRecord | None = next(
            (
                item
                for item in self.subagents
                if (tool_call_id is not None and item.tool_call_id == tool_call_id)
                or (agent_id is not None and item.agent_id == agent_id)
            ),
            None,
        )
        if entry is None:
            entry = SubagentRecord(tool_call_id, agent_id)
            self.subagents.append(entry)
        entry.tool_call_id = entry.tool_call_id or tool_call_id
        entry.agent_id = entry.agent_id or agent_id
        entry.name = entry.name or JsValue.text(data.get("agentName"))
        entry.display_name = entry.display_name or JsValue.text(data.get("agentDisplayName"))
        entry.model = entry.model or JsValue.text(data.get("model"))
        entry.effort = entry.effort or JsValue.text(data.get("reasoningEffort"))
        if event_type == "subagent.started":
            entry.started_at = entry.started_at or t
        if event_type in ("subagent.completed", "subagent.failed"):
            entry.ended_at = t
            entry.outcome = "completed" if event_type == "subagent.completed" else "failed"

    # 要約を返す。
    def summary(self) -> EventsSummary:
        """tool は呼び出しの多い順 (同数は名前順)、エラーは件数の多い順。"""

        tools: list[ToolView] = [
            {"name": name, "calls": counts[0], "failures": counts[1]}
            for name, counts in self.tools.items()
        ]
        tools.sort(key=lambda tool: LocaleOrder.key(tool["name"]))
        tools.sort(key=lambda tool: tool["calls"], reverse=True)
        errors: list[ErrorView] = [
            {"errorType": key, "count": count} for key, count in self.errors.items()
        ]
        errors.sort(key=lambda error: error["count"], reverse=True)
        return EventsSummary(
            status={"status": "ok"},
            event_count=self.event_count,
            parse_errors=self.parse_errors,
            first_event_at=self.first_event_at,
            last_event_at=self.last_event_at,
            start=self.start,
            resumes=tuple(self.resumes),
            branch_changes=self.branch_changes,
            turns=self.turns,
            user_messages=self.user_messages,
            aborts=self.aborts,
            model_changes=tuple(self.model_changes),
            subagents=tuple(replace(item) for item in self.subagents),
            cumulative=tuple(self.cumulative),
            shutdowns=tuple(self.shutdowns),
            compactions=tuple(self.compactions),
            tools=tuple(tools),
            errors=tuple(errors),
        )

    # object (dict) だけを返す。
    @staticmethod
    def _object(value: JsonValue) -> JsonObject:
        """配列や数値は、プロパティを持たない object と同じに扱う。"""

        return value if isinstance(value, dict) else {}

    # Object.entries の順で並べる。
    @staticmethod
    def _entries_in_js_order(value: JsonObject) -> list[tuple[str, JsonValue]]:
        """JavaScript の object は配列添字の key を数値順で先に並べ、他は挿入順。"""

        index_keys: list[str] = [
            key for key in value if key.isdigit() and str(int(key)) == key and int(key) < 2**32 - 1
        ]
        index_set: set[str] = set(index_keys)
        ordered: list[str] = sorted(index_keys, key=int) + [
            key for key in value if key not in index_set
        ]
        return [(key, value[key]) for key in ordered]


# 追記された行だけを読む events.jsonl の増分 reader の 1 ファイル分の状態。
@dataclass
class _ReaderEntry:
    """inode・読み取り済みの位置・未完の行のバイト列・集計状態。"""

    inode: int
    offset: int
    rest: bytes = b""
    state: EventsState = field(default_factory=EventsState)


# events.jsonl の増分 reader。
class EventsReader:
    """ファイルが縮んだ・別ファイルに替わった (inode が変わった)・force の時は最初から読み直す。

    CLI は追記しかしない。
    """

    CHUNK: int = 1 << 20

    # 空の cache を作る。
    def __init__(self) -> None:
        """パスごとに読み取り状態を持つ。"""

        self._cache: dict[Path, _ReaderEntry] = {}

    # 最新の要約を返す。
    def read(self, path: Path, force: bool = False) -> EventsSummary:
        """ファイルが無ければ status missing、読めなければ unreadable (OS のエラー名だけ)。"""

        if not path.exists():
            self._cache.pop(path, None)
            return EventsSummary(status={"status": "missing"})
        try:
            stat: os.stat_result = path.stat()
        except OSError as error:
            return EventsSummary(status={"status": "unreadable", "detail": self._error_code(error)})
        entry: _ReaderEntry | None = self._cache.get(path)
        if entry is None or force or stat.st_ino != entry.inode or stat.st_size < entry.offset:
            entry = _ReaderEntry(inode=stat.st_ino, offset=0)
            self._cache[path] = entry
        if stat.st_size > entry.offset:
            try:
                self._read_appended(path, entry, stat.st_size)
            except OSError as error:
                self._cache.pop(path, None)
                return EventsSummary(
                    status={"status": "unreadable", "detail": self._error_code(error)}
                )
        return entry.state.summary()

    # offset から size までを読み、改行で区切れた行だけを処理する。
    def _read_appended(self, path: Path, entry: _ReaderEntry, size: int) -> None:
        """末尾の未完行は、UTF-8 の文字が途中で切れていても壊さないよう
        バイト列のまま次回へ持ち越す。
        """

        with path.open("rb") as handle:
            handle.seek(entry.offset)
            position: int = entry.offset
            pending: bytes = entry.rest
            while position < size:
                chunk: bytes = handle.read(min(self.CHUNK, size - position))
                if not chunk:
                    break
                position += len(chunk)
                data: bytes = pending + chunk
                start: int = 0
                newline: int = data.find(b"\n", start)
                while newline != -1:
                    entry.state.line(data[start:newline].decode("utf-8", errors="replace"))
                    start = newline + 1
                    newline = data.find(b"\n", start)
                pending = data[start:]
            entry.offset = position
            entry.rest = pending

    # OS のエラーを固定コードにする。
    @staticmethod
    def _error_code(error: OSError) -> str:
        """errno の名前 (EACCES など) だけを残し、メッセージのパスは出さない。"""

        if error.errno is not None and error.errno in errno.errorcode:
            return errno.errorcode[error.errno]
        return "error"

    # 文字列化した行群から要約を作る。
    @staticmethod
    def summarize_lines(lines: list[str]) -> EventsSummary:
        """テスト用。"""

        state: EventsState = EventsState()
        for text in lines:
            state.line(text)
        return state.summary()
