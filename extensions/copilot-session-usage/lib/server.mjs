// canvas の iframe へ画面と表示用データを配信する loopback HTTP server。
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { join } from "node:path";
import { isValidSessionId } from "./paths.mjs";

const MAX_BODY_BYTES = 8 * 1024;

// 配信するファイルは固定の一覧だけ。任意パスを読まない。
const STATIC_FILES = {
    "/": ["index.html", "text/html; charset=utf-8"],
    "/app.mjs": ["app.mjs", "text/javascript; charset=utf-8"],
    "/charts.mjs": ["charts.mjs", "text/javascript; charset=utf-8"],
    "/format.mjs": ["format.mjs", "text/javascript; charset=utf-8"],
    "/app.css": ["app.css", "text/css; charset=utf-8"],
};
const INSTANCE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

/**
 * canvas パネルごとの状態。パネルを閉じれば消える一時的な UI 状態だけを持つ。
 * 単独起動で未選択の間は null。
 * @typedef {{ sessionId: string | null }} PanelState
 */

// server を起動する。`provider` は SessionUsageProvider と同じ getView / listSessions を持つ。
// `currentSessionId` は canvas を開いたセッション。単独起動では null (現在のセッションは無い)。
export async function startCanvasServer({ provider, webDir, currentSessionId = null }) {
    // セッションの指定 (ref) の検査。複数 source の provider は source 付きの形も受け付ける。
    const isValidRef = (value) => (provider.isValidRef ? provider.isValidRef(value) : isValidSessionId(value));
    // 他の local process やブラウザのページから使用量を読まれないよう、起動ごとの token と Host を確かめる。
    const token = randomBytes(18).toString("base64url");
    /** @type {Map<string, PanelState>} */
    const panels = new Map();
    let port = 0;

    const server = createServer((req, res) => {
        try {
            handle(req, res);
        } catch (error) {
            send(res, 500, { error: "internal_error" });
            process.stderr.write(`[copilot-session-usage] request failed: ${error?.stack ?? error}\n`);
        }
    });

    function handle(req, res) {
        if (req.headers.host !== `127.0.0.1:${port}`) {
            send(res, 421, { error: "bad_host" });
            return;
        }
        const url = new URL(req.url ?? "/", `http://127.0.0.1:${port}`);
        const file = STATIC_FILES[url.pathname];
        if (file && req.method === "GET") {
            res.writeHead(200, {
                "Content-Type": file[1],
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            });
            res.end(readFileSync(join(webDir, file[0])));
            return;
        }
        if (!url.pathname.startsWith("/api/")) {
            send(res, 404, { error: "not_found" });
            return;
        }
        if (url.searchParams.get("t") !== token) {
            send(res, 403, { error: "forbidden" });
            return;
        }
        const instance = url.searchParams.get("instance") ?? "";
        const panel = INSTANCE_PATTERN.test(instance) ? panels.get(instance) : undefined;
        if (url.pathname === "/api/state" && req.method === "GET") {
            send(res, 200, { sessionId: panelSessionOrLatest(panel), currentSessionId });
            return;
        }
        if (url.pathname === "/api/sessions" && req.method === "GET") {
            send(res, 200, provider.listSessions());
            return;
        }
        if (url.pathname === "/api/view" && req.method === "GET") {
            const sessionId = url.searchParams.get("session") ?? "";
            if (!isValidRef(sessionId)) {
                send(res, 400, { error: "invalid_session" });
                return;
            }
            const result = provider.getView(sessionId, {
                currentSessionId,
                since: url.searchParams.get("since"),
                force: url.searchParams.get("force") === "1",
            });
            send(res, 200, result);
            return;
        }
        if (url.pathname === "/api/select" && req.method === "POST") {
            const sessionId = url.searchParams.get("session") ?? "";
            if (!isValidRef(sessionId) || !panel) {
                send(res, 400, { error: "invalid_request" });
                return;
            }
            panel.sessionId = sessionId;
            send(res, 200, { sessionId });
            return;
        }
        // 設定 (読み取り元の追加・削除)。パスを返す・受け取るのはこの 3 つだけ。
        if (provider.getSettings && url.pathname === "/api/settings" && req.method === "GET") {
            send(res, 200, provider.getSettings());
            return;
        }
        if (provider.addSource && url.pathname === "/api/sources" && req.method === "POST") {
            readJsonBody(req).then((body) => {
                const result = provider.addSource(body?.path);
                send(res, result.ok ? 200 : 400, result.ok ? { ok: true } : { error: result.error });
            }).catch(() => send(res, 400, { error: "invalid_request" }));
            return;
        }
        if (provider.removeSource && url.pathname === "/api/sources/remove" && req.method === "POST") {
            const result = provider.removeSource(url.searchParams.get("id") ?? "");
            send(res, result.ok ? 200 : 400, result.ok ? { ok: true } : { error: result.error });
            return;
        }
        send(res, 404, { error: "not_found" });
    }

    // 表示するセッション。現在のセッションが無く未選択なら、最終活動が最新のセッションを選んでパネルに固定する
    // (後から別のセッションが新しくなっても表示を勝手に切り替えない)。記録が無ければ null。
    function panelSessionOrLatest(panel) {
        // 選んでいた source が設定から外れた後の指定は無効として扱い、最新のセッションへ切り替える。
        const selected = [panel?.sessionId, currentSessionId].find((ref) => ref && isValidRef(ref));
        if (selected) return selected;
        const latest = provider.listSessions().sessions[0]?.id ?? null;
        if (panel && latest) panel.sessionId = latest;
        return latest;
    }

    await new Promise((resolve, reject) => {
        server.once("error", reject);
        server.listen(0, "127.0.0.1", resolve);
    });
    port = server.address().port;

    return {
        port,
        // パネルを登録し、その iframe 用 URL を返す。単独起動では sessionId が null (最初の表示で最新を選ぶ)。
        openPanel(instanceId, sessionId) {
            const existing = panels.get(instanceId);
            if (existing) existing.sessionId = sessionId;
            else panels.set(instanceId, { sessionId });
            return `http://127.0.0.1:${port}/?instance=${encodeURIComponent(instanceId)}&t=${token}`;
        },
        panelSession(instanceId) {
            return panels.get(instanceId)?.sessionId ?? null;
        },
        closePanel(instanceId) {
            panels.delete(instanceId);
        },
        panelCount() {
            return panels.size;
        },
        close() {
            return new Promise((resolve) => {
                server.closeAllConnections?.();
                server.close(() => resolve());
            });
        },
    };
}

// 小さな JSON の本文を読む。大きすぎる・JSON でない本文は拒否する。
function readJsonBody(req) {
    return new Promise((resolve, reject) => {
        const chunks = [];
        let size = 0;
        req.on("data", (chunk) => {
            size += chunk.length;
            if (size > MAX_BODY_BYTES) {
                reject(new Error("too_large"));
                req.destroy();
                return;
            }
            chunks.push(chunk);
        });
        req.on("end", () => {
            try {
                resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
            } catch (error) {
                reject(error);
            }
        });
        req.on("error", reject);
    });
}

// BigInt を含まない JSON を返す (表示用データは集計時に文字列化済み)。
function send(res, status, body) {
    res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
    res.end(JSON.stringify(body));
}
