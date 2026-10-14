// GitHub Copilot App の data.db から、App セッションの累計値とコンテキスト使用量の推移を読む。
// title など本文由来の列は読まない。
import { openReadOnly, statusFromError, tableColumns, SqliteSourceError } from "./sqlite.mjs";

const SESSION_COLUMNS = [
    "model", "reasoning_effort", "total_input_tokens", "total_output_tokens", "total_cached_tokens",
    "total_reasoning_tokens", "total_nano_aiu", "context_current_tokens", "context_input_token_limit",
    "context_output_token_limit", "context_system_tokens", "context_conversation_tokens",
    "context_tool_definitions_tokens", "context_mcp_tools_tokens", "context_buffer_tokens",
    "total_agent_merge_nano_aiu", "context_tier",
];

/**
 * App 側の参考値。App が管理しないセッション (CLI 単体など) は status not_found。
 * @typedef {{
 *   status: import("./sqlite.mjs").SourceStatus,
 *   session: Record<string, string | number | bigint | null> | null,
 *   contextSeries: { t: number, current: number, limit: number }[],
 * }} AppResult
 */

// 1 セッション分の App 記録を返す。
export function readAppSession(dbPath, sessionId) {
    let db;
    try {
        db = openReadOnly(dbPath);
        const sessionCols = tableColumns(db, "sessions");
        if (!sessionCols.has("id")) {
            throw new SqliteSourceError({ status: "unsupported_schema", detail: "missing sessions.id" });
        }
        const picked = SESSION_COLUMNS.filter((name) => sessionCols.has(name));
        const stmt = db.prepare(`SELECT ${picked.length ? picked.join(", ") : "id"} FROM sessions WHERE id = ?`);
        stmt.setReadBigInts(true);
        const row = stmt.get(sessionId);
        let contextSeries = [];
        const contextCols = tableColumns(db, "session_context_usage");
        if (["session_id", "ts_ms", "current_tokens", "token_limit"].every((c) => contextCols.has(c))) {
            const points = db.prepare(`SELECT ts_ms, current_tokens, token_limit FROM session_context_usage
                                       WHERE session_id = ? ORDER BY id`).all(sessionId);
            contextSeries = points
                .map((p) => ({ t: Number(p.ts_ms), current: Number(p.current_tokens), limit: Number(p.token_limit) }))
                .filter((p) => [p.t, p.current, p.limit].every((v) => Number.isFinite(v) && v >= 0));
        }
        if (!row && contextSeries.length === 0) {
            return { status: { status: "not_found" }, session: null, contextSeries: [] };
        }
        const session = row ? Object.fromEntries(picked.map((name) => [name, normalize(row[name])])) : null;
        return { status: { status: "ok" }, session, contextSeries };
    } catch (error) {
        return { status: statusFromError(error), session: null, contextSeries: [] };
    } finally {
        db?.close();
    }
}

// 整数は安全範囲なら number、超えれば BigInt のまま返す。nano 系は呼び出し側で BigInt に揃える。
function normalize(value) {
    if (typeof value === "bigint") {
        return value <= BigInt(Number.MAX_SAFE_INTEGER) && value >= -BigInt(Number.MAX_SAFE_INTEGER) ? Number(value) : value;
    }
    return value ?? null;
}
