// request 使用履歴 (session-store.db の assistant_usage_events) の読み取りと検証。
import { isValidSessionId } from "./paths.mjs";
import { openReadOnly, requireColumns, statusFromError, tableColumns } from "./sqlite.mjs";
import { readWslHistory } from "./wsl_history.mjs";

const TABLE = "assistant_usage_events";
const REQUIRED_COLUMNS = [
    "id", "session_id", "model", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "total_nano_aiu", "created_at",
];

/**
 * token_details_json から取り出した token 種別ごとの直接計算値。本文を含まない数値だけを持つ。
 * @typedef {{ tokenType: string, tokenCount: number, nano: bigint, exact: boolean }} TokenDetail
 */

/**
 * 検証済みの 1 request。nano は整数のまま保持し、表示境界で換算する。
 * @typedef {{
 *   rowId: number, createdAt: string, t: number, turnIndex: number | null,
 *   agentId: string | null, parentToolCallId: string | null, model: string | null,
 *   initiator: string | null, effort: string | null, apiEndpoint: string | null,
 *   finishReason: string | null, contentFilter: boolean | null, durationMs: number | null,
 *   ttftMs: number | null, multiplier: number | null, copilotUsageModel: string | null,
 *   nano: bigint | null,
 *   tokens: { input: number, output: number, cacheRead: number, cacheWrite: number } | null,
 *   reasoning: number | null,
 *   tokenDetails: TokenDetail[] | null,
 * }} HistoryRow
 */

/**
 * @typedef {{
 *   status: import("./sqlite.mjs").SourceStatus,
 *   rows: HistoryRow[],
 *   duplicateRowsExcluded: number,
 *   invalidTimestampRows: number,
 *   session: { repository: string | null, branch: string | null } | null,
 * }} HistoryResult
 */

// 1 セッション分の履歴を、保存重複を除いた時刻順で返す。
export function readHistory(dbPath, sessionId) {
    let db;
    try {
        db = openReadOnly(dbPath);
        const columns = requireColumns(db, TABLE, REQUIRED_COLUMNS);
        const stmt = db.prepare(`SELECT * FROM ${TABLE} WHERE session_id = ? ORDER BY created_at, id`);
        stmt.setReadBigInts(true);
        const raw = stmt.all(sessionId);
        return { ...normalizeHistoryRows(raw, columns), session: sessionMeta(db, sessionId), status: { status: "ok" } };
    } catch (error) {
        return { status: statusFromError(error), rows: [], duplicateRowsExcluded: 0, invalidTimestampRows: 0, session: null };
    } finally {
        db?.close();
    }
}

// WSL 側 reader が返した許可列の行から、readHistory と同じ結果を作る。検証・重複除外は同じ関数を通す。
export function readHistoryWsl(location, sessionId, options = {}) {
    const snapshot = readWslHistory(location, sessionId, options);
    if (snapshot.status.status !== "ok") {
        return { status: snapshot.status, rows: [], duplicateRowsExcluded: 0, invalidTimestampRows: 0, session: null };
    }
    const meta = snapshot.sessions.find((row) => row.id === sessionId);
    const session = meta ? { repository: text(meta.repository), branch: text(meta.branch) } : null;
    return { ...normalizeHistoryRows(sortRaw(snapshot.rows), new Set(snapshot.columns)), session, status: { status: "ok" } };
}

// SQL の ORDER BY created_at, id と同じ並びにそろえる (同時刻の行の重複判定の順序を DB 読み取りと一致させる)。
function sortRaw(rows) {
    return [...rows].sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)) || Number(a.id) - Number(b.id));
}

// sessions 表から repository / branch だけを読む (cwd・summary は読まない)。App のセッションは events.jsonl に無いため。
function sessionMeta(db, sessionId) {
    const columns = tableColumns(db, "sessions");
    if (!columns.has("id")) return null;
    const repo = columns.has("repository") ? "repository" : "NULL";
    const branch = columns.has("branch") ? "branch" : "NULL";
    const row = db.prepare(`SELECT ${repo} AS repository, ${branch} AS branch FROM sessions WHERE id = ?`).get(sessionId);
    return row ? { repository: text(row.repository), branch: text(row.branch) } : null;
}

// SQL 境界の生の行を検証済みの行へ変換する。テストから直接呼べるよう DB と分ける。
export function normalizeHistoryRows(rawRows, columns = undefined) {
    const seen = new Set();
    const rows = [];
    let duplicateRowsExcluded = 0;
    let invalidTimestampRows = 0;
    for (const raw of rawRows) {
        // ローカル id 以外の全列が一致する行は保存重複として 1 回だけ数える。
        const identity = identityOf(raw, columns);
        if (seen.has(identity)) {
            duplicateRowsExcluded += 1;
            continue;
        }
        seen.add(identity);
        const t = parseUtcTime(raw.created_at);
        if (t === null) {
            invalidTimestampRows += 1;
            continue;
        }
        rows.push(toHistoryRow(raw, t));
    }
    rows.sort((a, b) => a.t - b.t || a.rowId - b.rowId);
    return { rows, duplicateRowsExcluded, invalidTimestampRows };
}

// 行の同一性キー。BigInt を文字列化し、列順に依存しないよう列名で並べる。
function identityOf(raw, columns) {
    const names = (columns ? [...columns] : Object.keys(raw)).filter((name) => name !== "id").sort();
    return JSON.stringify(names.map((name) => {
        const value = raw[name];
        return typeof value === "bigint" ? `n:${value}` : value ?? null;
    }));
}

// timezone の無い時刻は推測しない (ISO 形式で Z またはオフセット付きのみ受け付ける)。
export function parseUtcTime(value) {
    if (typeof value !== "string" || !/(Z|[+-]\d{2}:?\d{2})$/.test(value)) return null;
    const t = Date.parse(value);
    return Number.isFinite(t) ? t : null;
}

// 外部値を検証し、欠落を 0 に置き換えずに null で表す。
function toHistoryRow(raw, t) {
    const input = safeInt(raw.input_tokens);
    const output = safeInt(raw.output_tokens);
    const cacheRead = safeInt(raw.cache_read_tokens);
    const cacheWrite = safeInt(raw.cache_write_tokens);
    // input は cache を含む総 prompt token。cache の合計を下回る行は不正として token 合計から外す。
    const tokensValid = [input, output, cacheRead, cacheWrite].every((v) => v !== null)
        && input >= cacheRead + cacheWrite;
    return {
        rowId: Number(raw.id),
        createdAt: new Date(t).toISOString(),
        t,
        turnIndex: safeInt(raw.turn_index),
        agentId: text(raw.agent_id),
        parentToolCallId: text(raw.parent_tool_call_id),
        model: text(raw.model),
        initiator: text(raw.initiator),
        effort: text(raw.reasoning_effort),
        apiEndpoint: text(raw.api_endpoint),
        finishReason: text(raw.finish_reason),
        contentFilter: raw.content_filter_triggered == null ? null : Number(raw.content_filter_triggered) !== 0,
        durationMs: safeInt(raw.duration_ms),
        ttftMs: finite(raw.time_to_first_token_ms),
        multiplier: finite(raw.request_multiplier),
        copilotUsageModel: text(raw.copilot_usage_model),
        nano: nanoValue(raw.total_nano_aiu),
        tokens: tokensValid ? { input, output, cacheRead, cacheWrite } : null,
        reasoning: safeInt(raw.reasoning_tokens),
        tokenDetails: parseTokenDetails(raw.token_details_json),
    };
}

// 非負の整数だけを number で返す。範囲外・欠落は null。
function safeInt(value) {
    if (typeof value === "bigint") {
        return value >= 0n && value <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(value) : null;
    }
    if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) return value;
    return null;
}

// nano AIU は BigInt のまま保持する。負数・非整数は欠落として扱う。
function nanoValue(value) {
    if (typeof value === "bigint") return value >= 0n ? value : null;
    if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) return BigInt(value);
    return null;
}

function finite(value) {
    if (typeof value === "bigint") return Number(value);
    return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function text(value) {
    return typeof value === "string" && value.trim() ? value : null;
}

// token_details_json から token 種別・件数・単価だけを読み、その種別の直接計算値を出す。
export function parseTokenDetails(value) {
    if (typeof value !== "string" || !value.trim()) return null;
    let parsed;
    try {
        parsed = JSON.parse(value);
    } catch {
        return null;
    }
    if (!Array.isArray(parsed)) return null;
    const details = [];
    for (const item of parsed) {
        if (!item || typeof item !== "object") return null;
        const { tokenType, tokenCount, costPerBatch, batchSize } = item;
        if (typeof tokenType !== "string" || ![tokenCount, costPerBatch, batchSize].every(
            (v) => Number.isSafeInteger(v) && v >= 0,
        ) || batchSize === 0) {
            return null;
        }
        const product = BigInt(tokenCount) * BigInt(costPerBatch);
        details.push({
            tokenType,
            tokenCount,
            nano: product / BigInt(batchSize),
            exact: product % BigInt(batchSize) === 0n,
        });
    }
    return details;
}

/**
 * セッション選択用の一覧。本文・要約・作業ディレクトリは読まない。
 * credits / requests は表示画面と同じ規則 (normalizeHistoryRows) で数える。
 * @typedef {{
 *   id: string, repository: string | null, branch: string | null, lastActivity: string | null,
 *   requests: number, creditsNano: string | null, creditsMissing: number,
 * }} SessionListEntry
 */

// 全セッションの一覧を最終活動の新しい順で返す。
export function listSessions(dbPath) {
    let db;
    try {
        db = openReadOnly(dbPath);
        const columns = requireColumns(db, TABLE, REQUIRED_COLUMNS);
        const stmt = db.prepare(`SELECT * FROM ${TABLE} ORDER BY session_id, created_at, id`);
        stmt.setReadBigInts(true);
        return { status: { status: "ok" }, sessions: buildSessionList(sessionMetas(db), stmt.iterate(), columns) };
    } catch (error) {
        return { status: statusFromError(error), sessions: [] };
    } finally {
        db?.close();
    }
}

// WSL 側 reader の許可列から一覧を作る。
export function listSessionsWsl(location, options = {}) {
    const snapshot = readWslHistory(location, null, options);
    if (snapshot.status.status !== "ok") return { status: snapshot.status, sessions: [] };
    const metas = snapshot.sessions
        .filter((row) => typeof row.id === "string" && isValidSessionId(row.id))
        .map((row) => ({ id: row.id, repository: text(row.repository), branch: text(row.branch), updatedAt: row.updated_at }));
    return { status: { status: "ok" }, sessions: buildSessionList(metas, sortRaw(snapshot.rows), new Set(snapshot.columns)) };
}

// セッションの行を session_id ごとにまとめ、表示画面と同じ規則で数えた一覧にする。
function buildSessionList(metas, rawRows, columns) {
    const entries = new Map();
    for (const meta of metas) entries.set(meta.id, { ...meta, rows: [] });
    for (const raw of rawRows) {
        const id = typeof raw.session_id === "string" ? raw.session_id : null;
        if (!id || !isValidSessionId(id)) continue;
        let entry = entries.get(id);
        if (!entry) {
            entry = { id, repository: null, branch: null, updatedAt: null, rows: [] };
            entries.set(id, entry);
        }
        entry.rows.push(raw);
    }
    const sessions = [...entries.values()].map((entry) => {
        const { rows } = normalizeHistoryRows(entry.rows, columns);
        let nano = 0n;
        let missing = 0;
        let lastAt = null;
        for (const row of rows) {
            if (row.nano === null) missing += 1;
            else nano += row.nano;
            lastAt = lastAt === null ? row.t : Math.max(lastAt, row.t);
        }
        return {
            id: entry.id,
            repository: entry.repository,
            branch: entry.branch,
            lastActivity: lastAt !== null ? new Date(lastAt).toISOString() : activityTime(entry.updatedAt),
            requests: rows.length,
            creditsNano: rows.length ? nano.toString() : null,
            creditsMissing: missing,
        };
    });
    sessions.sort((a, b) => (b.lastActivity ?? "").localeCompare(a.lastActivity ?? "") || a.id.localeCompare(b.id));
    return sessions;
}

// sessions 表から一覧用の repository / branch / updated_at だけを読む。
function sessionMetas(db) {
    const columns = tableColumns(db, "sessions");
    if (!columns.has("id")) return [];
    const pick = (name) => (columns.has(name) ? name : "NULL");
    return db.prepare(`SELECT id, ${pick("repository")} AS repository, ${pick("branch")} AS branch,
                              ${pick("updated_at")} AS updated_at FROM sessions`).all()
        .filter((row) => typeof row.id === "string" && isValidSessionId(row.id))
        .map((row) => ({ id: row.id, repository: text(row.repository), branch: text(row.branch), updatedAt: row.updated_at }));
}

// sessions.updated_at は SQLite の datetime('now') (UTC、timezone 表記なし) なので UTC として読む。
function activityTime(value) {
    if (typeof value !== "string") return null;
    const t = parseUtcTime(value) ?? parseUtcTime(`${value.replace(" ", "T")}Z`);
    return t === null ? null : new Date(t).toISOString();
}
