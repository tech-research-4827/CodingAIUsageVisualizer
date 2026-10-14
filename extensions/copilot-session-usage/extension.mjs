// Extension: copilot-session-usage
// 1 セッションの AI Credits・token・コンテキストサイズとエージェント別使用量を表示する canvas。
// 使用量の記録は読み取り専用で読み、解析結果は保存しない。追加 source の設定は利用者の環境へ保存する。
// stdout は JSON-RPC 専用のため console.log は使わない。
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { CanvasError, createCanvas, joinSession } from "@github/copilot-sdk/extension";
import { MultiSourceProvider } from "./lib/multi_provider.mjs";
import { SESSION_REF_PATTERN } from "./lib/paths.mjs";
import { startCanvasServer } from "./lib/server.mjs";
import { summarizeForAgent } from "./lib/summary.mjs";

const WEB_DIR = join(dirname(fileURLToPath(import.meta.url)), "web");
const SESSION_INPUT = {
    type: "object",
    properties: {
        sessionId: {
            type: "string",
            pattern: SESSION_REF_PATTERN,
            description: "Session to show: a Copilot CLI session id, or `<source id>~<session id>` for a session of an added source (ids appear in get_summary). Defaults to the session that opens the canvas.",
        },
    },
    additionalProperties: false,
};

const provider = new MultiSourceProvider();
let session;
let serverPromise;

// 現在のセッション。joinSession の完了前に callback が来ても ctx から分かるようにする。
function currentSessionId(ctx) {
    return ctx?.sessionId ?? session?.sessionId;
}

// server は最初の open で 1 つだけ起動し、全パネルで共有する。
function server(ctx) {
    provider.setCurrentSessionId(currentSessionId(ctx));
    serverPromise ??= startCanvasServer({ provider, webDir: WEB_DIR, currentSessionId: currentSessionId(ctx) });
    return serverPromise;
}

// action の対象セッション。明示指定 > パネルの選択 > 現在のセッション。
async function targetSession(ctx) {
    const explicit = ctx.input?.sessionId;
    if (explicit !== undefined) {
        if (!provider.isValidRef(explicit)) throw new CanvasError("invalid_session", "Invalid session id.");
        return explicit;
    }
    return (await server(ctx)).panelSession(ctx.instanceId) ?? currentSessionId(ctx);
}

function summaryOf(ctx, sessionId, force = false) {
    provider.setCurrentSessionId(currentSessionId(ctx));
    const { view } = provider.getView(sessionId, { currentSessionId: currentSessionId(ctx), force });
    return summarizeForAgent(view);
}

session = await joinSession({
    canvases: [
        createCanvas({
            id: "session-usage",
            displayName: "Session usage",
            description: "Shows AI Credits, token, and context-size trends and per-agent usage for one Copilot session.",
            inputSchema: SESSION_INPUT,
            actions: [
                {
                    name: "get_summary",
                    description: "Return totals, per-agent, per-model usage and reconciliation for the panel's session (or the given sessionId).",
                    inputSchema: SESSION_INPUT,
                    handler: async (ctx) => summaryOf(ctx, await targetSession(ctx)),
                },
                {
                    name: "select_session",
                    description: "Switch the panel to another Copilot CLI session and return its summary.",
                    inputSchema: { ...SESSION_INPUT, required: ["sessionId"] },
                    handler: async (ctx) => {
                        const sessionId = await targetSession(ctx);
                        (await server(ctx)).openPanel(ctx.instanceId, sessionId);
                        return summaryOf(ctx, sessionId);
                    },
                },
                {
                    name: "refresh",
                    description: "Re-read the session records now and return the summary.",
                    handler: async (ctx) => summaryOf(ctx, await targetSession({ ...ctx, input: undefined }), true),
                },
            ],
            open: async (ctx) => {
                const sessionId = ctx.input?.sessionId ?? currentSessionId(ctx);
                if (!provider.isValidRef(sessionId)) throw new CanvasError("invalid_session", "Invalid session id.");
                const url = (await server(ctx)).openPanel(ctx.instanceId, sessionId);
                return { title: "Session usage", url, status: provider.parseRef(sessionId).sessionId.slice(0, 8) };
            },
            onClose: async (ctx) => {
                const srv = await serverPromise;
                srv?.closePanel(ctx.instanceId);
            },
        }),
    ],
});
