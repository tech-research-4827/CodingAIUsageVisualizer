// Copilot App なしで Session usage の画面をブラウザに表示する単独起動スクリプト。
// 使い方: node standalone.mjs [--home <copilot home>] [--session <id>] [--no-browser]
// 記録は canvas と同じく読み取り専用で読み、何も保存しない。終了は Ctrl+C (自分の server を閉じる)。
// 古い Node でも必要な版の案内を出せるよう、静的 import は古い版にもある node: の組み込みだけにし、
// node:sqlite を使う lib/ は版の確認後に読み込む。
import { spawn } from "node:child_process";
import { realpathSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const REQUIRED_NODE_MAJOR = 24;
const INSTANCE_ID = "standalone";
const HERE = dirname(fileURLToPath(import.meta.url));
const USAGE = `Usage: node standalone.mjs [--home <dir>] [--session <id>] [--no-browser]

  --home <dir>     Read only this Copilot home (default: COPILOT_HOME, else ~/.copilot, plus the sources added on the page)
  --session <id>   Session to show first (default: the session with the latest activity)
  --no-browser     Only print the URL; do not open a browser
`;

// 実行中の Node が必要な版に満たなければ案内文を、満たせば null を返す。
export function nodeVersionProblem(version = process.versions.node) {
    const major = Number.parseInt(String(version).split(".")[0], 10);
    if (Number.isInteger(major) && major >= REQUIRED_NODE_MAJOR) return null;
    return `Node.js ${REQUIRED_NODE_MAJOR} or later is required (this is v${version}). Install a current Node.js LTS and run again.`;
}

// 引数を解釈する。不正な引数は error に理由を入れて返す。
export function parseLauncherArgs(argv) {
    const options = { home: null, session: null, openBrowser: true, help: false, error: null };
    for (let i = 0; i < argv.length; i += 1) {
        const arg = argv[i];
        if (arg === "--no-browser") options.openBrowser = false;
        else if (arg === "--help" || arg === "-h") options.help = true;
        else if (arg === "--home" || arg === "--session") {
            const value = argv[i + 1];
            if (value === undefined || value.startsWith("--")) return { ...options, error: `${arg} needs a value` };
            options[arg.slice(2)] = value;
            i += 1;
        } else {
            return { ...options, error: unknownArgumentMessage(arg) };
        }
    }
    return options;
}

// 不明な引数の案内。値 (`--home=<path>` のパスなど) は出さず、option 名の形の部分だけを示す。
function unknownArgumentMessage(arg) {
    const name = arg.split("=")[0];
    return /^--?[A-Za-z][A-Za-z-]*$/.test(name) ? `unknown option: ${name}` : "unexpected argument (value not shown)";
}

// OS の既定ブラウザで URL を開くコマンド。WSL の中では Windows 側の既定ブラウザを使う。開けない環境は null。
export function browserCommand(platform = process.platform, env = process.env) {
    if (platform === "darwin") return { command: "open", args: [] };
    // cmd を経由しないため、URL の & を shell が解釈しない。
    if (platform === "win32") return { command: "rundll32", args: ["url.dll,FileProtocolHandler"] };
    if (platform === "linux" && env.WSL_DISTRO_NAME) return { command: "rundll32.exe", args: ["url.dll,FileProtocolHandler"] };
    if (platform === "linux") return { command: "xdg-open", args: [] };
    return null;
}

// ブラウザを開く。起動できなかった時だけ onFailure を呼ぶ (URL は既に表示済み)。
export function openInBrowser(url, { platform = process.platform, env = process.env, spawnFn = spawn, onFailure } = {}) {
    const entry = browserCommand(platform, env);
    if (!entry) {
        onFailure("no browser command for this platform");
        return;
    }
    let reported = false;
    const fail = (reason) => {
        if (!reported) onFailure(reason);
        reported = true;
    };
    try {
        const child = spawnFn(entry.command, [...entry.args, url], { stdio: "ignore", detached: true, windowsHide: true });
        child.on("error", (error) => fail(error?.code ?? "error"));
        child.on("exit", (code) => {
            if (code !== 0 && code !== null) fail(`${entry.command} exited with ${code}`);
        });
        child.unref();
    } catch (error) {
        fail(error?.code ?? "error");
    }
}

// server を起動し、表示用 URL と終了処理を返す。Copilot home の絶対パスは返さない。
export async function startStandalone({ home = null, session = null, env = process.env, settingsPath = null } = {}) {
    const { resolveCopilotHome } = await import("./lib/paths.mjs");
    const { MultiSourceProvider } = await import("./lib/multi_provider.mjs");
    const { defaultSettingsPath, SourceSettings } = await import("./lib/sources.mjs");
    const { startCanvasServer } = await import("./lib/server.mjs");
    const homeSource = home !== null ? "--home option" : env.COPILOT_HOME?.trim() ? "COPILOT_HOME" : "default (~/.copilot)";
    const primaryHome = home !== null ? resolve(home) : resolveCopilotHome(env);
    // --home は従来どおりその 1 つだけを読む (設定は読まず、追加もできない)。
    const settings = home !== null ? null : new SourceSettings(settingsPath ?? defaultSettingsPath({ env }));
    const provider = new MultiSourceProvider({ primaryHome, settings });
    if (session !== null && !provider.isValidRef(session)) {
        throw new UsageError("--session is not a valid session id");
    }
    const server = await startCanvasServer({ provider, webDir: join(HERE, "web"), currentSessionId: null });
    const url = server.openPanel(INSTANCE_ID, session);
    return { url, homeSource, extraSources: settings !== null, close: () => server.close() };
}

// 利用者の入力の誤り。使い方を添えて終了コード 2 で終わる。
class UsageError extends Error {}

async function main() {
    const problem = nodeVersionProblem();
    if (problem) {
        process.stderr.write(`${problem}\n`);
        process.exit(1);
    }
    const options = parseLauncherArgs(process.argv.slice(2));
    if (options.help) {
        process.stdout.write(USAGE);
        return;
    }
    if (options.error) throw new UsageError(options.error);
    const app = await startStandalone(options);
    const out = (line) => process.stdout.write(`${line}\n`);
    out(`Session usage: ${app.url}`);
    out(`Reading the Copilot home from: ${app.homeSource}`);
    if (app.extraSources) out("More Copilot homes (for example WSL) can be added from the Sources panel on the page.");
    if (options.openBrowser) {
        out("Opening the default browser…");
        openInBrowser(app.url, { onFailure: (reason) => out(`Could not open a browser (${reason}). Open the URL above.`) });
    } else {
        out("Open the URL above in a browser.");
    }
    out("Press Ctrl+C to stop.");

    // 自分が起動した server だけを閉じて終わる。2 回目の Ctrl+C は待たずに終わる。
    let stopping = false;
    const stop = async () => {
        if (stopping) process.exit(1);
        stopping = true;
        await app.close();
        out("Stopped.");
        process.exit(0);
    };
    process.on("SIGINT", stop);
    process.on("SIGTERM", stop);
}

// このファイルを直接実行した時だけ起動する (テストは関数だけを読み込む)。
function isEntryPoint() {
    try {
        return realpathSync(process.argv[1] ?? "") === realpathSync(fileURLToPath(import.meta.url));
    } catch {
        return false;
    }
}

if (isEntryPoint()) {
    main().catch((error) => {
        if (error instanceof UsageError) {
            process.stderr.write(`${error.message}\n\n${USAGE}`);
            process.exit(2);
        }
        // 例外の文言にはパスが含まれ得るため、固定のコードだけを出す。
        process.stderr.write(`Could not start session usage (${error?.code ?? "error"}).\n`);
        process.exit(1);
    });
}
