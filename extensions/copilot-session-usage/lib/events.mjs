// セッションの events.jsonl から、使用量の監査に要る数値と識別子だけを取り出す。
// 本文 (message content, tool arguments / result, summary, path) は読み出し後すぐ捨て、要約に残さない。
import { closeSync, existsSync, openSync, readSync, statSync } from "node:fs";

// 解析する種類。これ以外の行は JSON として解析せず件数だけ数える。
const PARSED_TYPES = new Set([
    "session.start", "session.resume", "session.model_change", "session.context_changed",
    "session.usage_checkpoint", "session.shutdown", "session.compaction_complete",
    "session.error", "abort", "subagent.started", "subagent.configured", "subagent.completed",
    "subagent.failed", "tool.execution_start", "tool.execution_complete",
]);
const COUNTED_TYPES = new Set(["assistant.turn_start", "user.message"]);
const TYPE_PREFIX = /^\{"type":"([A-Za-z0-9_.]+)"/;
const CHUNK = 1 << 20;

/**
 * events.jsonl の要約。値は数値・時刻・モデル名・エージェント名などの識別子だけ。
 * @typedef {{
 *   status: import("./sqlite.mjs").SourceStatus,
 *   eventCount: number, parseErrors: number, firstEventAt: string | null, lastEventAt: string | null,
 *   start: { startTime: string | null, copilotVersion: string | null, producer: string | null,
 *            reasoningEffort: string | null, selectedModel: string | null, contextTier: string | null,
 *            repository: string | null, branch: string | null } | null,
 *   resumes: string[], branchChanges: number, turns: number, userMessages: number, aborts: number,
 *   modelChanges: { t: string, newModel: string | null, previousModel: string | null,
 *                   reasoningEffort: string | null, previousReasoningEffort: string | null }[],
 *   subagents: { toolCallId: string | null, agentId: string | null, name: string | null,
 *                displayName: string | null, model: string | null, effort: string | null,
 *                startedAt: string | null, endedAt: string | null, outcome: string | null }[],
 *   cumulative: { type: "checkpoint" | "shutdown", t: string, nano: bigint }[],
 *   shutdowns: { t: string, shutdownType: string | null, nano: bigint | null,
 *                tokens: { input: number | null, output: number | null, cacheRead: number | null, cacheWrite: number | null },
 *                currentModel: string | null, currentTokens: number | null, systemTokens: number | null,
 *                conversationTokens: number | null, toolDefinitionsTokens: number | null,
 *                totalApiDurationMs: number | null, linesAdded: number | null, linesRemoved: number | null,
 *                filesModified: number | null,
 *                modelMetrics: { model: string, nano: bigint | null, requests: number | null }[] }[],
 *   compactions: { t: string, success: boolean | null, trigger: string | null, preTokens: number | null,
 *                  tokenLimit: number | null, model: string | null, input: number | null, output: number | null,
 *                  cacheRead: number | null, cacheWrite: number | null, nano: bigint | null, durationMs: number | null }[],
 *   tools: { name: string, calls: number, failures: number }[],
 *   errors: { errorType: string, count: number }[],
 * }} EventsSummary
 */

// 追記されたバイトだけを読む増分 reader。ファイルが縮んだ・別ファイルに替わった (inode が変わった)・
// force の時は最初から読み直す。CLI は追記しかしないため、内容の比較はしない。
export class EventsReader {
    constructor() {
        /** @type {Map<string, { ino: number, offset: number, rest: Buffer, state: EventsState }>} */
        this.cache = new Map();
    }

    // 最新の要約を返す。ファイルが無ければ status missing。
    read(path, { force = false } = {}) {
        if (!existsSync(path)) {
            this.cache.delete(path);
            return emptySummary({ status: "missing" });
        }
        let stat;
        try {
            stat = statSync(path);
        } catch (error) {
            return emptySummary({ status: "unreadable", detail: error?.code ?? "error" });
        }
        let entry = this.cache.get(path);
        if (!entry || force || stat.ino !== entry.ino || stat.size < entry.offset) {
            entry = { ino: stat.ino, offset: 0, rest: Buffer.alloc(0), state: new EventsState() };
            this.cache.set(path, entry);
        }
        if (stat.size > entry.offset) {
            try {
                readAppended(path, entry, stat.size);
            } catch (error) {
                this.cache.delete(path);
                return emptySummary({ status: "unreadable", detail: error?.code ?? "error" });
            }
        }
        return entry.state.summary();
    }
}

// offset から size までを読み、改行で区切れた行だけを処理する。末尾の未完行は、UTF-8 の文字が途中で切れていても
// 壊さないようバイト列のまま次回へ持ち越す。
function readAppended(path, entry, size) {
    const fd = openSync(path, "r");
    try {
        const buffer = Buffer.alloc(CHUNK);
        let position = entry.offset;
        let pending = entry.rest;
        while (position < size) {
            const length = readSync(fd, buffer, 0, Math.min(CHUNK, size - position), position);
            if (length === 0) break;
            position += length;
            let data = Buffer.concat([pending, buffer.subarray(0, length)]);
            let start = 0;
            for (let i = data.indexOf(10); i !== -1; i = data.indexOf(10, start)) {
                entry.state.line(data.subarray(start, i).toString("utf8"));
                start = i + 1;
            }
            pending = Buffer.from(data.subarray(start));
            data = null;
        }
        entry.offset = position;
        entry.rest = pending;
    } finally {
        closeSync(fd);
    }
}

// 文字列化した行群から要約を作る。テスト用。
export function summarizeEventLines(lines) {
    const state = new EventsState();
    for (const line of lines) state.line(line);
    return state.summary();
}

function emptySummary(status) {
    const summary = new EventsState().summary();
    summary.status = status;
    return summary;
}

// 行ごとの集計状態。
class EventsState {
    constructor() {
        this.eventCount = 0;
        this.parseErrors = 0;
        this.firstEventAt = null;
        this.lastEventAt = null;
        this.start = null;
        this.resumes = [];
        this.branchChanges = 0;
        this.turns = 0;
        this.userMessages = 0;
        this.aborts = 0;
        this.modelChanges = [];
        /** @type {Map<string, any>} */
        this.subagents = new Map();
        this.cumulative = [];
        this.shutdowns = [];
        this.compactions = [];
        /** @type {Map<string, string>} toolCallId → toolName */
        this.toolCallNames = new Map();
        /** @type {Map<string, { calls: number, failures: number }>} */
        this.tools = new Map();
        /** @type {Map<string, number>} */
        this.errors = new Map();
    }

    // 1 行を処理する。対象外の種類は JSON 解析しない。
    line(text) {
        if (!text.trim()) return;
        this.eventCount += 1;
        const match = TYPE_PREFIX.exec(text);
        const type = match ? match[1] : null;
        if (type && COUNTED_TYPES.has(type)) {
            if (type === "assistant.turn_start") this.turns += 1;
            else this.userMessages += 1;
            this.observeTime(text);
            return;
        }
        if (type && !PARSED_TYPES.has(type)) {
            this.observeTime(text);
            return;
        }
        let event;
        try {
            event = JSON.parse(text);
        } catch {
            this.parseErrors += 1;
            return;
        }
        if (!event || typeof event !== "object") {
            this.parseErrors += 1;
            return;
        }
        const t = isoTime(event.timestamp);
        if (t) this.touch(t);
        if (!type && !PARSED_TYPES.has(event.type)) {
            if (event.type === "assistant.turn_start") this.turns += 1;
            if (event.type === "user.message") this.userMessages += 1;
            return;
        }
        this.apply(event.type, event, event.data && typeof event.data === "object" ? event.data : {}, t);
    }

    // 解析しない行からも timestamp だけは拾い、最終イベント時刻を正しく保つ。
    observeTime(text) {
        const match = /"timestamp":"([^"]+)"/.exec(text);
        const t = match ? isoTime(match[1]) : null;
        if (t) this.touch(t);
    }

    touch(t) {
        if (!this.firstEventAt || t < this.firstEventAt) this.firstEventAt = t;
        if (!this.lastEventAt || t > this.lastEventAt) this.lastEventAt = t;
    }

    // 種類ごとに必要なフィールドだけを取り出す。
    apply(type, event, data, t) {
        switch (type) {
            case "session.start": {
                const context = data.context && typeof data.context === "object" ? data.context : {};
                this.start = {
                    startTime: isoTime(data.startTime) ?? t,
                    copilotVersion: str(data.copilotVersion),
                    producer: str(data.producer),
                    reasoningEffort: str(data.reasoningEffort),
                    selectedModel: str(data.selectedModel),
                    contextTier: str(data.contextTier),
                    // repository は owner/name 形式の識別子。cwd / gitRoot は絶対パスなので読まない。
                    repository: str(context.repository),
                    branch: str(context.branch),
                };
                break;
            }
            case "session.resume":
                if (t) this.resumes.push(t);
                break;
            case "session.context_changed":
                this.branchChanges += 1;
                break;
            case "session.model_change":
                this.modelChanges.push({
                    t, newModel: str(data.newModel), previousModel: str(data.previousModel),
                    reasoningEffort: str(data.reasoningEffort),
                    previousReasoningEffort: str(data.previousReasoningEffort),
                });
                break;
            case "session.usage_checkpoint": {
                const nano = nanoOf(data.totalNanoAiu);
                if (t && nano !== null) this.cumulative.push({ type: "checkpoint", t, nano });
                break;
            }
            case "session.shutdown":
                this.shutdown(data, t);
                break;
            case "session.compaction_complete":
                this.compaction(data, t);
                break;
            case "session.error": {
                const key = str(data.errorType) ?? "unknown";
                this.errors.set(key, (this.errors.get(key) ?? 0) + 1);
                break;
            }
            case "abort":
                this.aborts += 1;
                break;
            case "subagent.started":
            case "subagent.configured":
            case "subagent.completed":
            case "subagent.failed":
                this.subagent(type, event, data, t);
                break;
            case "tool.execution_start": {
                const name = str(data.toolName) ?? "unknown";
                const id = str(data.toolCallId);
                if (id) this.toolCallNames.set(id, name);
                const tool = this.tools.get(name) ?? { calls: 0, failures: 0 };
                tool.calls += 1;
                this.tools.set(name, tool);
                break;
            }
            case "tool.execution_complete": {
                if (data.success === false) {
                    const name = this.toolCallNames.get(str(data.toolCallId) ?? "") ?? "unknown";
                    const tool = this.tools.get(name) ?? { calls: 0, failures: 0 };
                    tool.failures += 1;
                    this.tools.set(name, tool);
                }
                break;
            }
            default:
                break;
        }
    }

    shutdown(data, t) {
        const nano = nanoOf(data.totalNanoAiu);
        if (t && nano !== null) this.cumulative.push({ type: "shutdown", t, nano });
        const details = data.tokenDetails && typeof data.tokenDetails === "object" ? data.tokenDetails : {};
        const count = (key) => int(details[key]?.tokenCount);
        const changes = data.codeChanges && typeof data.codeChanges === "object" ? data.codeChanges : {};
        const metrics = data.modelMetrics && typeof data.modelMetrics === "object" ? data.modelMetrics : {};
        this.shutdowns.push({
            t, shutdownType: str(data.shutdownType), nano,
            tokens: { input: count("input"), output: count("output"), cacheRead: count("cache_read"), cacheWrite: count("cache_write") },
            currentModel: str(data.currentModel), currentTokens: int(data.currentTokens),
            systemTokens: int(data.systemTokens), conversationTokens: int(data.conversationTokens),
            toolDefinitionsTokens: int(data.toolDefinitionsTokens), totalApiDurationMs: int(data.totalApiDurationMs),
            linesAdded: int(changes.linesAdded), linesRemoved: int(changes.linesRemoved),
            // 変更ファイルはパスなので件数だけを残す。
            filesModified: Array.isArray(changes.filesModified) ? changes.filesModified.length : null,
            modelMetrics: Object.entries(metrics).map(([model, value]) => ({
                model,
                nano: nanoOf(value?.totalNanoAiu),
                requests: int(value?.requests?.count),
            })),
        });
    }

    compaction(data, t) {
        const used = data.compactionTokensUsed && typeof data.compactionTokensUsed === "object" ? data.compactionTokensUsed : {};
        this.compactions.push({
            t, success: typeof data.success === "boolean" ? data.success : null, trigger: str(data.trigger),
            preTokens: int(data.preCompactionTokens), tokenLimit: int(data.tokenLimit), model: str(used.model),
            input: int(used.inputTokens), output: int(used.outputTokens), cacheRead: int(used.cacheReadTokens),
            cacheWrite: int(used.cacheWriteTokens), nano: nanoOf(used.copilotUsage?.totalNanoAiu),
            durationMs: int(used.duration),
        });
    }

    // サブエージェントは toolCallId (= 履歴の parent_tool_call_id) で束ねる。
    subagent(type, event, data, t) {
        const toolCallId = str(data.toolCallId);
        const agentId = str(event.agentId);
        if (!toolCallId && !agentId) return;
        // subagent.configured は toolCallId を持たないため、agentId でも同じ entry を探す。
        let entry = [...this.subagents.values()].find((e) => (toolCallId && e.toolCallId === toolCallId)
            || (agentId && e.agentId === agentId));
        if (!entry) {
            entry = {
                toolCallId, agentId, name: null, displayName: null, model: null, effort: null,
                startedAt: null, endedAt: null, outcome: null,
            };
            this.subagents.set(`${this.subagents.size}`, entry);
        }
        entry.toolCallId ??= toolCallId;
        entry.agentId ??= agentId;
        entry.name ??= str(data.agentName);
        entry.displayName ??= str(data.agentDisplayName);
        entry.model ??= str(data.model);
        entry.effort ??= str(data.reasoningEffort);
        if (type === "subagent.started") entry.startedAt ??= t;
        if (type === "subagent.completed" || type === "subagent.failed") {
            entry.endedAt = t;
            entry.outcome = type === "subagent.completed" ? "completed" : "failed";
        }
    }

    /** @returns {EventsSummary} */
    summary() {
        return {
            status: { status: "ok" },
            eventCount: this.eventCount,
            parseErrors: this.parseErrors,
            firstEventAt: this.firstEventAt,
            lastEventAt: this.lastEventAt,
            start: this.start ? { ...this.start } : null,
            resumes: [...this.resumes],
            branchChanges: this.branchChanges,
            turns: this.turns,
            userMessages: this.userMessages,
            aborts: this.aborts,
            modelChanges: this.modelChanges.map((x) => ({ ...x })),
            subagents: [...this.subagents.values()].map((x) => ({ ...x })),
            cumulative: this.cumulative.map((x) => ({ ...x })),
            shutdowns: this.shutdowns.map((x) => ({ ...x })),
            compactions: this.compactions.map((x) => ({ ...x })),
            tools: [...this.tools.entries()].map(([name, v]) => ({ name, ...v }))
                .sort((a, b) => b.calls - a.calls || a.name.localeCompare(b.name)),
            errors: [...this.errors.entries()].map(([errorType, count]) => ({ errorType, count }))
                .sort((a, b) => b.count - a.count),
        };
    }
}

function str(value) {
    return typeof value === "string" && value.trim() ? value : null;
}

function int(value) {
    return Number.isSafeInteger(value) && value >= 0 ? value : null;
}

// JSON の数値は 2^53 を超えると精度を失うため、安全な整数だけを受け付ける。
function nanoOf(value) {
    return Number.isSafeInteger(value) && value >= 0 ? BigInt(value) : null;
}

function isoTime(value) {
    if (typeof value !== "string") return null;
    const t = Date.parse(value);
    return Number.isFinite(t) && /(Z|[+-]\d{2}:?\d{2})$/.test(value) ? new Date(t).toISOString() : null;
}
