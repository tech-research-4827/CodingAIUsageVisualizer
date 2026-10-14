// Windows から WSL の session-store.db を読む。共有 (UNC) 越しの SQLite はロックで開けないため、
// その distribution の python3 が WSL 内の native transaction で許可列だけを読み、JSON で返す。
// 本文を持つ表・列は読まず、DB 全体・stderr・WSL 内のパスは持ち帰らない。
import { spawnSync } from "node:child_process";

export const WSL_TIMEOUT_MS = 30_000;
const MAX_OUTPUT_BYTES = 256 * 1024 * 1024;

// 読む列の許可リスト。ここに無い列 (将来の追加を含む) は読まない。
export const HISTORY_COLUMNS = [
    "id", "session_id", "turn_index", "agent_id", "parent_tool_call_id", "model", "copilot_usage_model",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
    "total_nano_aiu", "request_multiplier", "duration_ms", "time_to_first_token_ms", "output_ttft_ms",
    "inter_token_latency_ms", "initiator", "api_endpoint", "reasoning_effort", "finish_reason",
    "content_filter_triggered", "token_details_json", "created_at",
];
const REQUIRED_COLUMNS = [
    "id", "session_id", "model", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "total_nano_aiu", "created_at",
];
const SESSION_COLUMNS = ["id", "repository", "branch", "updated_at"];

// WSL 内で実行する標準ライブラリだけの script。要求は末尾の run() の引数 (JSON) で渡す。
const SCRIPT = `import base64
import json
import sqlite3
from pathlib import Path


def cell(value):
    if isinstance(value, bool):
        return {"i": str(int(value))}
    if isinstance(value, int):
        return {"i": str(value)}
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, bytes):
        return {"b": base64.b64encode(value).decode("ascii")}
    return value


class HistoryReader:
    @staticmethod
    def run(request_json):
        request = json.loads(request_json)
        result = {"status": "ok", "detail": None, "columns": [], "rows": [], "sessionColumns": [], "sessions": []}
        connection = None
        try:
            path = Path(request["path"])
            if not path.is_file():
                result["status"] = "missing"
            else:
                connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
                connection.execute("BEGIN")
                columns = {str(r[1]) for r in connection.execute("PRAGMA table_info(assistant_usage_events)")}
                missing = [c for c in request["required"] if c not in columns]
                if not columns:
                    result["status"] = "unsupported_schema"
                    result["detail"] = "missing table assistant_usage_events"
                elif missing:
                    result["status"] = "unsupported_schema"
                    result["detail"] = "missing " + ", ".join(missing)
                else:
                    selected = [c for c in request["allowed"] if c in columns]
                    sql = "SELECT " + ", ".join(selected) + " FROM assistant_usage_events"
                    params = ()
                    if request["session_id"] is not None:
                        sql += " WHERE session_id = ?"
                        params = (request["session_id"],)
                    result["columns"] = selected
                    result["rows"] = [[cell(c) for c in row] for row in connection.execute(sql, params)]
                    session_columns = {str(r[1]) for r in connection.execute("PRAGMA table_info(sessions)")}
                    if "id" in session_columns:
                        picked = [c for c in request["session_allowed"] if c in session_columns]
                        sql = "SELECT " + ", ".join(picked) + " FROM sessions"
                        params = ()
                        if request["session_id"] is not None:
                            sql += " WHERE id = ?"
                            params = (request["session_id"],)
                        result["sessionColumns"] = picked
                        result["sessions"] = [[cell(c) for c in row] for row in connection.execute(sql, params)]
        except sqlite3.Error:
            result = {"status": "unreadable", "detail": "sqlite_error", "columns": [], "rows": [], "sessionColumns": [], "sessions": []}
        finally:
            if connection is not None:
                connection.close()
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
`;

// 既定の実行: シェルを経由せず wsl.exe で python3 を起動する。script は stdin で渡す。
export function runWslProcess(distribution, program, timeoutMs = WSL_TIMEOUT_MS) {
    return spawnSync("wsl.exe", ["--distribution", distribution, "--exec", "python3", "-I", "-"], {
        input: Buffer.from(program, "utf8"),
        timeout: timeoutMs,
        windowsHide: true,
        maxBuffer: MAX_OUTPUT_BYTES,
    });
}

// JSON を ASCII だけにして、Python の文字列リテラルとしてそのまま埋め込めるようにする。
function asciiJson(value) {
    return JSON.stringify(value).replace(/[\u007f-￿]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`);
}

/**
 * 1 セッション (sessionId) または全セッション (null) の履歴を WSL 側から取得する。
 * 失敗は例外にせず、固定の状態コード (detail) の status で返す。
 * @param {{ distribution: string, linuxPath: string }} location WSL の `.copilot` (Linux 側のパス)
 * @returns {{ status: import("./sqlite.mjs").SourceStatus, columns: string[], rows: Record<string, unknown>[], sessions: Record<string, unknown>[] }}
 */
export function readWslHistory(location, sessionId, { run = runWslProcess } = {}) {
    const empty = (status) => ({ status, columns: [], rows: [], sessions: [] });
    const request = asciiJson({
        path: `${location.linuxPath}/session-store.db`,
        allowed: HISTORY_COLUMNS,
        required: REQUIRED_COLUMNS,
        session_allowed: SESSION_COLUMNS,
        session_id: sessionId,
    });
    const program = `${SCRIPT}\nHistoryReader.run(${JSON.stringify(request)})\n`;
    let process;
    try {
        process = run(location.distribution, program);
    } catch {
        return empty({ status: "unreadable", detail: "wsl_process_failed" });
    }
    if (process?.error?.code === "ETIMEDOUT") return empty({ status: "unreadable", detail: "wsl_timeout" });
    if (!process || process.error || process.status !== 0) return empty({ status: "unreadable", detail: "wsl_process_failed" });
    try {
        return decodeResponse(process.stdout);
    } catch {
        return empty({ status: "unreadable", detail: "wsl_invalid_response" });
    }
}

const STATUSES = new Set(["ok", "missing", "unreadable", "unsupported_schema"]);

// 応答の形・列名・値の型を検証し、整数は BigInt に戻す (SQLite の読み取りと同じ型にそろえる)。
export function decodeResponse(output) {
    const payload = JSON.parse(Buffer.from(output).toString("utf8"));
    if (!payload || typeof payload !== "object" || !STATUSES.has(payload.status)) throw new Error("status");
    const detail = typeof payload.detail === "string" && /^[A-Za-z0-9_ ,.]{1,120}$/.test(payload.detail) ? payload.detail : undefined;
    const status = payload.status === "ok" ? { status: "ok" } : { status: payload.status, ...(detail ? { detail } : {}) };
    return {
        status,
        columns: validColumns(payload.columns, HISTORY_COLUMNS),
        rows: decodeRows(payload.columns, payload.rows, HISTORY_COLUMNS),
        sessions: decodeRows(payload.sessionColumns, payload.sessions, SESSION_COLUMNS),
    };
}

function validColumns(columns, allowed) {
    if (!Array.isArray(columns) || columns.some((c) => !allowed.includes(c))) throw new Error("columns");
    return columns;
}

function decodeRows(columns, rows, allowed) {
    const names = validColumns(columns, allowed);
    if (!Array.isArray(rows)) throw new Error("rows");
    return rows.map((row) => {
        if (!Array.isArray(row) || row.length !== names.length) throw new Error("row");
        return Object.fromEntries(names.map((name, i) => [name, decodeCell(row[i])]));
    });
}

function decodeCell(cell) {
    if (cell === null || typeof cell === "string") return cell;
    if (typeof cell === "number") return cell;
    if (cell && typeof cell === "object") {
        if (typeof cell.i === "string" && /^-?\d{1,40}$/.test(cell.i)) return BigInt(cell.i);
        if (typeof cell.b === "string") return Buffer.from(cell.b, "base64");
    }
    throw new Error("value");
}
