// 複数の Copilot home (source) の定義: 追加した source の保存・復元、表示名の生成、WSL の UNC パスの解釈。
// 設定ファイルは読み取り元の外 (利用者別の OS の設定 directory) に置く。パスは設定の表示と保存以外へ出さない。
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, posix, win32 } from "node:path";

export const PRIMARY_SOURCE_ID = "primary";
const SOURCE_ID_PATTERN = /^s[0-9a-f]{8}$/;
const MAX_SOURCES = 16;
const MAX_PATH_LENGTH = 1024;
const SETTINGS_VERSION = 1;

// 設定ファイルの置き場所。OS ごとの利用者の設定 directory の下に 1 ファイルだけ置く。
export function defaultSettingsPath({ platform = process.platform, env = process.env, home = homedir() } = {}) {
    let base;
    if (platform === "win32") base = env.APPDATA?.trim() || join(home, "AppData", "Roaming");
    else if (platform === "darwin") base = join(home, "Library", "Application Support");
    else base = env.XDG_CONFIG_HOME?.trim() || join(home, ".config");
    return join(base, "copilot-session-usage", "sources.json");
}

/**
 * Windows の WSL 共有 (`\\wsl.localhost\<distro>\…` / `\\wsl$\<distro>\…`) を解釈する。それ以外は null。
 * @returns {{ distribution: string, linuxPath: string } | null}
 */
export function parseWslLocation(path) {
    if (typeof path !== "string" || path.includes("\0")) return null;
    const match = /^\\\\(?:\?\\UNC\\|)(wsl\.localhost|wsl\$)\\([^\\/]+)((?:[\\/][^\\/]+)*)[\\/]*$/i.exec(path);
    if (!match) return null;
    const distribution = match[2];
    // distribution を option として解釈させない。
    if (distribution.startsWith("-")) return null;
    const parts = match[3].split(/[\\/]/).filter(Boolean);
    if (parts.includes("..") || parts.includes(".")) return null;
    return { distribution, linuxPath: `/${parts.join("/")}` };
}

// 設定に保存してよいパスか。絶対パスの文字列だけを検査し、到達できるかは見ない (停止中の WSL も追加できる)。
export function normalizeSourcePath(value) {
    if (typeof value !== "string") return null;
    const trimmed = value.trim().replace(/^"(.*)"$/, "$1").trim();
    if (!trimmed || trimmed.length > MAX_PATH_LENGTH || /[\0\r\n]/.test(trimmed)) return null;
    if (!win32.isAbsolute(trimmed) && !posix.isAbsolute(trimmed)) return null;
    // 末尾の区切りだけ除く。ルート (`C:\`、`/`) の区切りは残す (除くと絶対パスでなくなる)。
    const flavor = win32.isAbsolute(trimmed) ? win32 : posix;
    const root = flavor.parse(trimmed).root;
    const stripped = trimmed.replace(/[\\/]+$/, "");
    const normalized = stripped.length < root.length ? root : stripped;
    return win32.isAbsolute(normalized) || posix.isAbsolute(normalized) ? normalized : null;
}

// source の識別子。パスから決めるが、パスそのものは含まない。
// WSL では host と distribution 名だけ大文字小文字を区別せず、Linux 側のパスは区別する (Linux は区別するため)。
export function sourceIdOf(path) {
    const wsl = parseWslLocation(path);
    const key = wsl ? `wsl\0${wsl.distribution.toLowerCase()}\0${wsl.linuxPath}` : path;
    return `s${createHash("sha256").update(key).digest("hex").slice(0, 8)}`;
}

export function isValidSourceId(value) {
    return value === PRIMARY_SOURCE_ID || (typeof value === "string" && SOURCE_ID_PATTERN.test(value));
}

// 既定の source の表示名は OS 名。
export function primaryLabel(platform = process.platform) {
    return platform === "win32" ? "Windows" : platform === "darwin" ? "macOS" : "Linux";
}

// 追加した source の表示名をパスから作る。WSL は `WSL: <distro>`、それ以外は `Folder: <親 directory 名>`。
export function labelForPath(path) {
    const wsl = parseWslLocation(path);
    if (wsl) return `WSL: ${wsl.distribution}`;
    // 入力の形式で選び、別 OS のパスでも親名を同じ規則で取り出す。
    const flavor = path.startsWith("/") && !path.startsWith("//") ? posix : win32;
    const parent = flavor.basename(flavor.dirname(flavor.normalize(path)));
    return `Folder: ${parent || flavor.parse(path).root || "root"}`;
}

// 表示名の重複に番号を付ける (最初のものはそのまま、次から `(2)` `(3)`)。
export function uniqueLabels(labels) {
    const seen = new Map();
    return labels.map((label) => {
        const n = (seen.get(label) ?? 0) + 1;
        seen.set(label, n);
        return n === 1 ? label : `${label} (${n})`;
    });
}

/**
 * 追加した source の保存先。読み込みの失敗は例外にせず状態コードで返し、読むだけでは書き換えない。
 * @typedef {{ status: "ok" | "unreadable" | "invalid", paths: string[] }} LoadedSources
 */
export class SourceSettings {
    constructor(settingsPath = defaultSettingsPath()) {
        this.settingsPath = settingsPath;
    }

    /** @returns {LoadedSources} */
    load() {
        let text;
        try {
            text = readFileSync(this.settingsPath, "utf8");
        } catch (error) {
            // ファイルが無いのは「まだ何も追加していない」だけで異常ではない。
            if (error?.code === "ENOENT") return { status: "ok", paths: [] };
            return { status: "unreadable", paths: [] };
        }
        try {
            const data = JSON.parse(text);
            if (!data || data.version !== SETTINGS_VERSION || !Array.isArray(data.sources)) throw new Error("shape");
            const paths = [];
            for (const entry of data.sources) {
                const path = normalizeSourcePath(entry?.path);
                if (path === null) throw new Error("path");
                if (!paths.includes(path)) paths.push(path);
            }
            return { status: "ok", paths: paths.slice(0, MAX_SOURCES) };
        } catch {
            return { status: "invalid", paths: [] };
        }
    }

    // 追加する。パスが不正・上限超え・既にある場合は理由コードを返す。
    add(rawPath, primaryPath) {
        const path = normalizeSourcePath(rawPath);
        if (path === null) return { ok: false, error: "invalid_path" };
        const loaded = this.load();
        if (loaded.status !== "ok") return { ok: false, error: "settings_unreadable" };
        const id = sourceIdOf(path);
        if (id === sourceIdOf(primaryPath) || loaded.paths.some((p) => sourceIdOf(p) === id)) {
            return { ok: false, error: "already_added" };
        }
        if (loaded.paths.length >= MAX_SOURCES) return { ok: false, error: "too_many_sources" };
        return this.save([...loaded.paths, path]);
    }

    remove(sourceId) {
        const loaded = this.load();
        if (loaded.status !== "ok") return { ok: false, error: "settings_unreadable" };
        const rest = loaded.paths.filter((p) => sourceIdOf(p) !== sourceId);
        if (rest.length === loaded.paths.length) return { ok: false, error: "not_found" };
        return this.save(rest);
    }

    // 同じ directory の一時ファイルへ書いてから置き換え、途中の切断で壊れたファイルを残さない。
    save(paths) {
        const temp = `${this.settingsPath}.${process.pid}.tmp`;
        try {
            mkdirSync(dirname(this.settingsPath), { recursive: true });
            writeFileSync(temp, `${JSON.stringify({ version: SETTINGS_VERSION, sources: paths.map((path) => ({ path })) }, null, 2)}\n`, "utf8");
            renameSync(temp, this.settingsPath);
            return { ok: true };
        } catch {
            rmSync(temp, { force: true });
            return { ok: false, error: "save_failed" };
        }
    }
}
