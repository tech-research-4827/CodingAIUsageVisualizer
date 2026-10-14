// node:sqlite を読み取り専用で開く共通処理。CLI / App が書き込み中でも WAL の読み取りとして共存する。
import { existsSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";

/**
 * 読み取り元の状態。`detail` は固定コードだけで、DB の内容やパスを含めない。
 * @typedef {{ status: "ok" | "missing" | "unreadable" | "unsupported_schema" | "not_found", detail?: string }} SourceStatus
 */

// 読み取り失敗を例外ではなく状態として返し、画面側で部分表示できるようにする。
export class SqliteSourceError extends Error {
    /** @param {SourceStatus} status */
    constructor(status) {
        super(`${status.status}${status.detail ? `: ${status.detail}` : ""}`);
        this.sourceStatus = status;
    }
}

// 存在しない DB を作らないよう、開く前に存在を確かめる。
export function openReadOnly(path) {
    if (!existsSync(path)) {
        throw new SqliteSourceError({ status: "missing" });
    }
    try {
        const db = new DatabaseSync(path, { readOnly: true, timeout: 2000 });
        return db;
    } catch (error) {
        throw new SqliteSourceError({ status: "unreadable", detail: errorCode(error) });
    }
}

// 表の列名を集める。表が無ければ空集合。
export function tableColumns(db, table) {
    const rows = db.prepare(`SELECT name FROM pragma_table_info(?)`).all(table);
    return new Set(rows.map((row) => String(row.name)));
}

// 必須列が欠けた表は形式非対応として扱い、推測で読まない。
export function requireColumns(db, table, required) {
    const columns = tableColumns(db, table);
    if (columns.size === 0) {
        throw new SqliteSourceError({ status: "unsupported_schema", detail: `missing table ${table}` });
    }
    const missing = required.filter((name) => !columns.has(name));
    if (missing.length > 0) {
        throw new SqliteSourceError({ status: "unsupported_schema", detail: `missing ${missing.join(", ")}` });
    }
    return columns;
}

// SQLite のエラーは固定コードだけを残し、メッセージ中のパスを外へ出さない。
export function errorCode(error) {
    if (error && typeof error === "object") {
        if (typeof error.errcode === "number") return `sqlite_${error.errcode}`;
        if (typeof error.code === "string") return error.code;
    }
    return "error";
}

// 例外を読み取り元の状態へ変換する。想定外の例外は状態 unreadable とする。
export function statusFromError(error) {
    if (error instanceof SqliteSourceError) return error.sourceStatus;
    return { status: "unreadable", detail: errorCode(error) };
}
