// 読み取った 3 つの記録から、canvas に渡す表示用データ (JSON 化可能) を作る純粋関数群。
// nano AIU は BigInt で合算し、JSON では文字列にする。換算と丸めは表示側で行う。

/**
 * @typedef {import("./history.mjs").HistoryResult} HistoryResult
 * @typedef {import("./events.mjs").EventsSummary} EventsSummary
 * @typedef {import("./app.mjs").AppResult} AppResult
 * @typedef {{ input: number, output: number, cacheRead: number, cacheWrite: number, uncachedInput: number, reasoning: number }} TokenTotals
 */

const MAIN = "main";
const COMPACTION = "compaction";

// セッション 1 件の表示用データを組み立てる。
export function buildSessionView({ sessionId, isCurrent, history, events, app: appResult, readAt }) {
    const rows = history.rows;
    const subagents = events.subagents ?? [];
    const requests = [];
    const agentGroups = new Map();
    const modelGroups = new Map();
    const tokenTypes = new Map();
    const totals = newGroup();
    let cumNano = 0n;
    const cum = { uncached: 0, cacheRead: 0, cacheWrite: 0, output: 0, input: 0 };
    let detailRows = 0;
    let detailMismatchRows = 0;
    let detailInexactRows = 0;

    rows.forEach((row, index) => {
        const agentKey = agentKeyOf(row);
        addRow(totals, row);
        addRow(groupFor(agentGroups, agentKey), row);
        addRow(groupFor(modelGroups, row.model ?? "(unknown)"), row);
        if (row.nano !== null) cumNano += row.nano;
        const tk = row.tokens;
        if (tk) {
            cum.uncached += tk.input - tk.cacheRead - tk.cacheWrite;
            cum.cacheRead += tk.cacheRead;
            cum.cacheWrite += tk.cacheWrite;
            cum.output += tk.output;
            cum.input += tk.input;
        }
        let detailMismatch = null;
        if (row.tokenDetails) {
            detailRows += 1;
            let sum = 0n;
            for (const d of row.tokenDetails) {
                const entry = tokenTypes.get(d.tokenType) ?? { tokenType: d.tokenType, tokens: 0, nano: 0n };
                entry.tokens += d.tokenCount;
                entry.nano += d.nano;
                tokenTypes.set(d.tokenType, entry);
                sum += d.nano;
                if (!d.exact) detailInexactRows += 1;
            }
            detailMismatch = row.nano !== null && sum !== row.nano;
            if (detailMismatch) detailMismatchRows += 1;
        }
        requests.push({
            i: index + 1,
            t: row.t,
            agent: agentKey,
            model: row.model,
            effort: row.effort,
            initiator: row.initiator,
            turn: row.turnIndex,
            input: tk ? tk.input : null,
            output: tk ? tk.output : null,
            cacheRead: tk ? tk.cacheRead : null,
            cacheWrite: tk ? tk.cacheWrite : null,
            uncached: tk ? tk.input - tk.cacheRead - tk.cacheWrite : null,
            reasoning: row.reasoning,
            nano: row.nano === null ? null : row.nano.toString(),
            cumNano: cumNano.toString(),
            cum: { ...cum },
            durationMs: row.durationMs,
            ttftMs: row.ttftMs,
            finishReason: row.finishReason,
            apiEndpoint: row.apiEndpoint,
            multiplier: row.multiplier,
            contentFilter: row.contentFilter,
            detailMismatch,
        });
    });

    const matched = new Set();
    const agents = [...agentGroups.entries()].map(([key, group]) => agentView(key, group, subagents, matched));
    // 履歴に現れないサブエージェント (使用量の記録が無いもの) も、監査のため一覧に残す。
    for (const sub of subagents) {
        if (!matched.has(sub)) {
            agents.push(agentView(`sub:${sub.agentId ?? sub.toolCallId}`, newGroup(), [sub], matched));
        }
    }
    agents.sort((a, b) => kindOrder(a.kind) - kindOrder(b.kind) || (a.firstAt ?? Infinity) - (b.firstAt ?? Infinity));

    const appSource = appInput(appResult);
    const app = appView(appSource);
    return {
        schema: 1,
        readAt,
        session: {
            id: sessionId,
            isCurrent,
            repository: events.start?.repository ?? history.session?.repository ?? null,
            branch: events.start?.branch ?? history.session?.branch ?? null,
            selectedModel: events.start?.selectedModel ?? null,
            startTime: events.start?.startTime ?? null,
            copilotVersion: events.start?.copilotVersion ?? null,
            producer: events.start?.producer ?? null,
            reasoningEffort: events.start?.reasoningEffort ?? null,
            contextTier: events.start?.contextTier ?? null,
        },
        sources: {
            history: history.status,
            events: events.status,
            app: appSource.status,
        },
        totals: {
            // セッション合計は「取得済みの小計」と明示して出すため、値が 1 件も無くても 0 の小計を保つ (欠落件数を併記)。
            ...groupTotals(totals, { subtotal: true }),
            duplicateRowsExcluded: history.duplicateRowsExcluded,
            invalidTimestampRows: history.invalidTimestampRows,
        },
        requests,
        agents,
        models: [...modelGroups.entries()].map(([model, group]) => ({ model, ...groupTotals(group) }))
            .sort((a, b) => cmpNanoDesc(a.creditsNano, b.creditsNano)),
        tokenTypes: {
            rows: [...tokenTypes.values()].map((e) => ({ tokenType: e.tokenType, tokens: e.tokens, nano: e.nano.toString() }))
                .sort((a, b) => cmpNanoDesc(a.nano, b.nano)),
            rowsWithDetails: detailRows,
            rowsWithoutDetails: rows.length - detailRows,
            mismatchRows: detailMismatchRows,
            inexactEntries: detailInexactRows,
        },
        reconciliation: {
            cumulative: (events.cumulative ?? []).map((c) => cumulativeView(c, rows)),
            app: app.totals ? appReconciliation(app.totals, totals) : null,
        },
        context: contextView(rows, app, events),
        app,
        events: {
            eventCount: events.eventCount,
            parseErrors: events.parseErrors,
            firstEventAt: events.firstEventAt,
            lastEventAt: events.lastEventAt,
            turns: events.turns,
            userMessages: events.userMessages,
            aborts: events.aborts,
            resumes: events.resumes,
            branchChanges: events.branchChanges,
            modelChanges: events.modelChanges,
            compactions: (events.compactions ?? []).map((c) => ({ ...c, nano: c.nano === null ? null : c.nano.toString() })),
            shutdowns: (events.shutdowns ?? []).map((s) => ({
                ...s,
                nano: s.nano === null ? null : s.nano.toString(),
                modelMetrics: s.modelMetrics.map((m) => ({ ...m, nano: m.nano === null ? null : m.nano.toString() })),
            })),
            tools: events.tools,
            errors: events.errors,
        },
    };
}

// 履歴行のエージェント区分。agent_id が無い行はメインエージェント。
export function agentKeyOf(row) {
    if (row.initiator === COMPACTION) return COMPACTION;
    if (row.agentId) return `sub:${row.agentId}`;
    return MAIN;
}

function kindOrder(kind) {
    return kind === "main" ? 0 : kind === "subagent" ? 1 : 2;
}

function newGroup() {
    return {
        calls: 0, nano: 0n, creditsMissing: 0, tokenRowsInvalid: 0,
        tokens: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, uncachedInput: 0, reasoning: 0 },
        reasoningMissing: 0, durationMs: 0, ctxMax: null, ctxLast: null, firstAt: null, lastAt: null,
        models: new Set(), efforts: new Set(), parentToolCallIds: new Set(),
    };
}

function groupFor(map, key) {
    let group = map.get(key);
    if (!group) {
        group = newGroup();
        map.set(key, group);
    }
    return group;
}

// 1 行を集計に加える。credits の欠落は加算せず件数で数え、token 不正行は token 合計から外す。
function addRow(group, row) {
    group.calls += 1;
    if (row.nano === null) group.creditsMissing += 1;
    else group.nano += row.nano;
    if (row.tokens) {
        const tk = row.tokens;
        group.tokens.input += tk.input;
        group.tokens.output += tk.output;
        group.tokens.cacheRead += tk.cacheRead;
        group.tokens.cacheWrite += tk.cacheWrite;
        group.tokens.uncachedInput += tk.input - tk.cacheRead - tk.cacheWrite;
        group.ctxMax = Math.max(group.ctxMax ?? 0, tk.input);
        group.ctxLast = tk.input;
    } else {
        group.tokenRowsInvalid += 1;
    }
    if (row.reasoning === null) group.reasoningMissing += 1;
    else group.tokens.reasoning += row.reasoning;
    if (row.durationMs !== null) group.durationMs += row.durationMs;
    group.firstAt ??= row.t;
    group.lastAt = row.t;
    if (row.model) group.models.add(row.model);
    if (row.effort) group.efforts.add(row.effort);
    if (row.parentToolCallId) group.parentToolCallIds.add(row.parentToolCallId);
    if (row.agentId) group.agentId = row.agentId;
}

// 内訳 (エージェント・モデル) は、Credits / token の記録が 1 件も無ければ 0 ではなく null を返す。
function groupTotals(group, { subtotal = false } = {}) {
    const observedCredits = group.calls - group.creditsMissing;
    const validTokenRows = group.calls - group.tokenRowsInvalid;
    const tokens = subtotal || validTokenRows > 0
        ? { ...group.tokens }
        : Object.fromEntries(Object.keys(group.tokens).map((k) => [k, null]));
    return {
        requests: group.calls,
        creditsNano: subtotal || observedCredits > 0 ? group.nano.toString() : null,
        creditsMissing: group.creditsMissing,
        tokenRowsInvalid: group.tokenRowsInvalid,
        tokens,
        reasoningMissing: group.reasoningMissing,
        durationMs: group.durationMs,
        ctxMax: group.ctxMax,
        ctxLast: group.ctxLast,
        firstAt: group.firstAt,
        lastAt: group.lastAt,
        efforts: [...group.efforts],
    };
}

// 履歴の parent_tool_call_id と events の toolCallId、または agent 識別子同士で対応付ける。
function matchesSubagent(agentId, sub, parentIds) {
    if (sub.toolCallId && (sub.toolCallId === agentId || parentIds.has(sub.toolCallId))) return true;
    return Boolean(sub.agentId && sub.agentId === agentId);
}

// エージェント 1 件の表示用データ。名前は events.jsonl から、使用量は履歴から取る。
function agentView(key, group, subagents, matched) {
    const kind = key === MAIN ? "main" : key === COMPACTION ? "compaction" : "subagent";
    const agentId = kind === "subagent" ? key.slice(4) : null;
    const sub = kind === "subagent"
        ? subagents.find((s) => matchesSubagent(agentId, s, group.parentToolCallIds))
        : undefined;
    if (sub) matched.add(sub);
    const label = kind === "main" ? "Main agent"
        : kind === "compaction" ? "Compaction"
            : sub?.displayName ?? sub?.name ?? `Sub-agent ${shortId(agentId)}`;
    const models = [...group.models];
    if (models.length === 0 && sub?.model) models.push(sub.model);
    const efforts = [...group.efforts];
    if (efforts.length === 0 && sub?.effort) efforts.push(sub.effort);
    return {
        key,
        kind,
        label,
        name: sub?.name ?? null,
        agentId,
        shortId: agentId ? shortId(agentId) : null,
        named: kind !== "subagent" || Boolean(sub?.displayName ?? sub?.name),
        ...groupTotals(group),
        models,
        efforts,
        startedAt: sub?.startedAt ?? null,
        endedAt: sub?.endedAt ?? null,
        outcome: sub?.outcome ?? null,
    };
}

function shortId(id) {
    return id ? String(id).slice(0, 8) : "unknown";
}

// null (記録なし) は末尾。
function cmpNanoDesc(a, b) {
    if (a === null || b === null) return a === b ? 0 : a === null ? 1 : -1;
    const x = BigInt(a);
    const y = BigInt(b);
    return x === y ? 0 : x > y ? -1 : 1;
}

// 累積イベントは、そのイベント時刻以前の履歴だけと比べる。差は参考値で、補正には使わない。
function cumulativeView(event, rows) {
    const at = Date.parse(event.t);
    let nano = 0n;
    let missing = 0;
    let count = 0;
    for (const row of rows) {
        if (row.t > at) break;
        count += 1;
        if (row.nano === null) missing += 1;
        else nano += row.nano;
    }
    return {
        type: event.type,
        t: event.t,
        nano: event.nano.toString(),
        historyNanoAtT: nano.toString(),
        historyRowsAtT: count,
        historyMissingAtT: missing,
        diffNano: (event.nano - nano).toString(),
    };
}

function appInput(app) {
    return app ?? { status: { status: "missing" }, session: null, contextSeries: [] };
}

// App の参考値を表示用に整える。nano は BigInt 文字列へ。
function appView(app) {
    const s = app.session;
    const n = (name) => (s && s[name] !== undefined && s[name] !== null ? s[name] : null);
    const nanoStr = (name) => {
        const v = n(name);
        return typeof v === "number" || typeof v === "bigint" ? BigInt(v).toString() : null;
    };
    return {
        model: n("model"),
        reasoningEffort: n("reasoning_effort"),
        contextTier: n("context_tier"),
        totals: s ? {
            nano: nanoStr("total_nano_aiu"),
            agentMergeNano: nanoStr("total_agent_merge_nano_aiu"),
            input: n("total_input_tokens"),
            output: n("total_output_tokens"),
            cached: n("total_cached_tokens"),
            reasoning: n("total_reasoning_tokens"),
        } : null,
        context: s ? {
            current: n("context_current_tokens"),
            inputLimit: n("context_input_token_limit"),
            outputLimit: n("context_output_token_limit"),
            system: n("context_system_tokens"),
            conversation: n("context_conversation_tokens"),
            toolDefinitions: n("context_tool_definitions_tokens"),
            mcpTools: n("context_mcp_tools_tokens"),
            buffer: n("context_buffer_tokens"),
        } : null,
        contextSeries: app.contextSeries,
    };
}

// App が持つ累計値と履歴の取得済み小計を並べる。App の値の定義は公開されていないため参考比較に留める。
function appReconciliation(appTotals, history) {
    return {
        appNano: appTotals.nano,
        historyNano: history.nano.toString(),
        diffNano: appTotals.nano === null ? null : (BigInt(appTotals.nano) - history.nano).toString(),
        appInput: appTotals.input,
        historyInput: history.tokens.input,
        appOutput: appTotals.output,
        historyOutput: history.tokens.output,
        appCached: appTotals.cached,
        historyCached: history.tokens.cacheRead + history.tokens.cacheWrite,
        appAgentMergeNano: appTotals.agentMergeNano,
    };
}

// 最新のコンテキストサイズと上限。上限は記録がある所 (App、圧縮) だけから取る。
function contextView(rows, app, events) {
    const appLast = app.contextSeries.at(-1);
    const mainRows = rows.filter((r) => agentKeyOf(r) === MAIN && r.tokens);
    const lastMain = mainRows.at(-1);
    const compactionLimit = [...(events.compactions ?? [])].reverse().find((c) => c.tokenLimit !== null)?.tokenLimit ?? null;
    const limit = appLast?.limit ?? app.context?.inputLimit ?? compactionLimit ?? null;
    const limitSource = appLast?.limit != null || app.context?.inputLimit != null ? "app"
        : compactionLimit !== null ? "compaction" : null;
    return {
        latestMain: lastMain ? { tokens: lastMain.tokens.input, t: lastMain.t } : null,
        appCurrent: appLast ? { tokens: appLast.current, t: appLast.t } : app.context?.current != null ? { tokens: app.context.current, t: null } : null,
        limit,
        limitSource,
        compactionLimits: (events.compactions ?? []).filter((c) => c.tokenLimit !== null).map((c) => ({ t: c.t, limit: c.tokenLimit })),
    };
}
