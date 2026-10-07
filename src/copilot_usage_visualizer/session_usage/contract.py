from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

# 表示用データとセッション一覧の契約。Node 版 (lib/aggregate.mjs, lib/history.mjs) の JSON と
# 同じ形にし、期待値ファイルでそのまま比べられるよう key は Node と同じ camelCase にする。
# nano AIU は整数の 10 進文字列。

SourceStatusCode = Literal["ok", "missing", "unreadable", "unsupported_schema", "not_found"]


# 読み取り元の状態。detail は固定コードだけでパスや内容を含まない。
class SourceStatus(TypedDict):
    """読み取り元 1 つの状態 (`detail` は失敗時の固定コード)。"""

    status: SourceStatusCode
    detail: NotRequired[str]


# セッション一覧の 1 行。
class SessionListEntry(TypedDict):
    """セッション選択用の 1 行。本文・要約・作業ディレクトリは含めない。"""

    id: str
    repository: str | None
    branch: str | None
    lastActivity: str | None
    requests: int
    creditsNano: str | None
    creditsMissing: int


# セッション一覧の読み取り結果。
class SessionListResult(TypedDict):
    """一覧と、その読み取り元 (履歴 DB) の状態。"""

    status: SourceStatus
    sessions: list[SessionListEntry]


# token 種別ごとの合計。内訳で記録が無い時は全項目 None。
class TokenTotals(TypedDict):
    """input は cache read / write を含む総 prompt token。"""

    input: int | None
    output: int | None
    cacheRead: int | None
    cacheWrite: int | None
    uncachedInput: int | None
    reasoning: int | None


# 表示画面のセッション情報。
class SessionInfo(TypedDict):
    """events.jsonl の session.start と sessions 表から取る識別子だけを持つ。"""

    id: str
    isCurrent: bool
    repository: str | None
    branch: str | None
    selectedModel: str | None
    startTime: str | None
    copilotVersion: str | None
    producer: str | None
    reasoningEffort: str | None
    contextTier: str | None


# 3 つの読み取り元の状態。
class SourcesView(TypedDict):
    """履歴 DB・events.jsonl・App の data.db の状態。"""

    history: SourceStatus
    events: SourceStatus
    app: SourceStatus


# 集計 1 グループ (合計・エージェント・モデル) の共通項目。
class GroupTotalsView(TypedDict):
    """Credits / token は記録が 1 件も無い内訳では None (合計は取得済み小計として 0 を保つ)。"""

    requests: int
    creditsNano: str | None
    creditsMissing: int
    tokenRowsInvalid: int
    tokens: TokenTotals
    reasoningMissing: int
    durationMs: int
    ctxMax: int | None
    ctxLast: int | None
    firstAt: int | None
    lastAt: int | None
    efforts: list[str]


# セッション合計。
class SessionTotalsView(GroupTotalsView):
    """合計に、保存重複と時刻不正で除外した行数を加える。"""

    duplicateRowsExcluded: int
    invalidTimestampRows: int


# 累積 token の途中値。
class CumulativeTokens(TypedDict):
    """その request までの累積値。"""

    uncached: int
    cacheRead: int
    cacheWrite: int
    output: int
    input: int


# 1 request の表示用データ。
class RequestView(TypedDict):
    """token が不正な行は token 系を None にし、credits の欠落は nano を None にする。"""

    i: int
    t: int
    agent: str
    model: str | None
    effort: str | None
    initiator: str | None
    turn: int | None
    input: int | None
    output: int | None
    cacheRead: int | None
    cacheWrite: int | None
    uncached: int | None
    reasoning: int | None
    nano: str | None
    cumNano: str
    cum: CumulativeTokens
    durationMs: int | None
    ttftMs: float | None
    finishReason: str | None
    apiEndpoint: str | None
    multiplier: float | None
    contentFilter: bool | None
    detailMismatch: bool | None


AgentKind = Literal["main", "subagent", "compaction"]


# エージェント 1 件。
class AgentView(GroupTotalsView):
    """名前は events.jsonl から、使用量は履歴から取る。"""

    key: str
    kind: AgentKind
    label: str
    name: str | None
    agentId: str | None
    shortId: str | None
    named: bool
    models: list[str]
    startedAt: str | None
    endedAt: str | None
    outcome: str | None


# モデル 1 件。
class ModelView(GroupTotalsView):
    """モデル名 (記録が無い時は `(unknown)`) ごとの内訳。"""

    model: str


# token 種別ごとの Credits の 1 行。
class TokenTypeRow(TypedDict):
    """token_details_json の件数と単価から計算した値。"""

    tokenType: str
    tokens: int
    nano: str


# token 種別ごとの Credits。
class TokenTypesView(TypedDict):
    """詳細を持つ行と、記録値と合わない行の件数を併記する。"""

    rows: list[TokenTypeRow]
    rowsWithDetails: int
    rowsWithoutDetails: int
    mismatchRows: int
    inexactEntries: int


# 累積イベントと履歴の比較 1 行。
class CumulativeReconciliation(TypedDict):
    """イベント時刻以前の履歴小計との参考差分。"""

    type: Literal["checkpoint", "shutdown"]
    t: str
    nano: str
    historyNanoAtT: str
    historyRowsAtT: int
    historyMissingAtT: int
    diffNano: str


# App の累計と履歴の比較。
class AppReconciliation(TypedDict):
    """App の値の定義は公開されていないため参考比較に留める。"""

    appNano: str | None
    historyNano: str
    diffNano: str | None
    appInput: int | float | str | None
    historyInput: int | None
    appOutput: int | float | str | None
    historyOutput: int | None
    appCached: int | float | str | None
    historyCached: int
    appAgentMergeNano: str | None


# 照合の表示用データ。
class ReconciliationView(TypedDict):
    """累積イベントと App の参考比較。合計には加えない。"""

    cumulative: list[CumulativeReconciliation]
    app: AppReconciliation | None


# 時刻付きのコンテキストサイズ。
class TimedTokens(TypedDict):
    """t は epoch ミリ秒 (App の現在値で時刻が無ければ None)。"""

    tokens: int | float | str | None
    t: int | float | None


# 圧縮時の上限。
class CompactionLimit(TypedDict):
    """圧縮イベントの tokenLimit。"""

    t: str | None
    limit: int


# コンテキストの表示用データ。
class ContextView(TypedDict):
    """上限は App の記録と圧縮時の tokenLimit だけから取る。"""

    latestMain: TimedTokens | None
    appCurrent: TimedTokens | None
    limit: int | float | str | None
    limitSource: Literal["app", "compaction"] | None
    compactionLimits: list[CompactionLimit]


# App のコンテキスト推移の 1 点。
class AppContextPoint(TypedDict):
    """data.db の session_context_usage の 1 行。"""

    t: int | float
    current: int | float
    limit: int | float


# App の累計値。
class AppTotalsView(TypedDict):
    """data.db の sessions 表の累計列。"""

    nano: str | None
    agentMergeNano: str | None
    input: int | float | str | None
    output: int | float | str | None
    cached: int | float | str | None
    reasoning: int | float | str | None


# App の現在のコンテキスト。
class AppContextView(TypedDict):
    """data.db の sessions 表のコンテキスト列。"""

    current: int | float | str | None
    inputLimit: int | float | str | None
    outputLimit: int | float | str | None
    system: int | float | str | None
    conversation: int | float | str | None
    toolDefinitions: int | float | str | None
    mcpTools: int | float | str | None
    buffer: int | float | str | None


# App の参考値の表示用データ。
class AppView(TypedDict):
    """App が管理しないセッションでは totals / context が None。"""

    model: int | float | str | None
    reasoningEffort: int | float | str | None
    contextTier: int | float | str | None
    totals: AppTotalsView | None
    context: AppContextView | None
    contextSeries: list[AppContextPoint]


# モデル変更イベント。
class ModelChangeView(TypedDict):
    """session.model_change のモデル名と effort。"""

    t: str | None
    newModel: str | None
    previousModel: str | None
    reasoningEffort: str | None
    previousReasoningEffort: str | None


# 圧縮イベント。
class CompactionView(TypedDict):
    """session.compaction_complete の数値と識別子 (要約本文は持たない)。"""

    t: str | None
    success: bool | None
    trigger: str | None
    preTokens: int | None
    tokenLimit: int | None
    model: str | None
    input: int | None
    output: int | None
    cacheRead: int | None
    cacheWrite: int | None
    nano: str | None
    durationMs: int | None


# shutdown の token 件数。
class ShutdownTokens(TypedDict):
    """session.shutdown の tokenDetails の件数。"""

    input: int | None
    output: int | None
    cacheRead: int | None
    cacheWrite: int | None


# shutdown のモデル別値。
class ModelMetricView(TypedDict):
    """session.shutdown の modelMetrics の 1 件。"""

    model: str
    nano: str | None
    requests: int | None


# shutdown イベント。
class ShutdownView(TypedDict):
    """変更ファイルはパスなので件数だけを持つ。"""

    t: str | None
    shutdownType: str | None
    nano: str | None
    tokens: ShutdownTokens
    currentModel: str | None
    currentTokens: int | None
    systemTokens: int | None
    conversationTokens: int | None
    toolDefinitionsTokens: int | None
    totalApiDurationMs: int | None
    linesAdded: int | None
    linesRemoved: int | None
    filesModified: int | None
    modelMetrics: list[ModelMetricView]


# tool の呼び出し件数。
class ToolView(TypedDict):
    """tool 名ごとの呼び出し数と失敗数。"""

    name: str
    calls: int
    failures: int


# エラー種別の件数。
class ErrorView(TypedDict):
    """session.error の errorType ごとの件数。"""

    errorType: str
    count: int


# events.jsonl の表示用データ。
class EventsView(TypedDict):
    """件数・時刻・識別子だけを持つ。"""

    eventCount: int
    parseErrors: int
    firstEventAt: str | None
    lastEventAt: str | None
    turns: int
    userMessages: int
    aborts: int
    resumes: list[str]
    branchChanges: int
    modelChanges: list[ModelChangeView]
    compactions: list[CompactionView]
    shutdowns: list[ShutdownView]
    tools: list[ToolView]
    errors: list[ErrorView]


# セッション 1 件の表示用データ。
class SessionUsageView(TypedDict):
    """canvas が受け取るものと同じ表示用データ。"""

    schema: int
    readAt: str
    session: SessionInfo
    sources: SourcesView
    totals: SessionTotalsView
    requests: list[RequestView]
    agents: list[AgentView]
    models: list[ModelView]
    tokenTypes: TokenTypesView
    reconciliation: ReconciliationView
    context: ContextView
    app: AppView
    events: EventsView
