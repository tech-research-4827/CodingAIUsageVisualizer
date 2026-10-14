// 記録の読み取り・変更検知・表示用データの生成をまとめる。canvas と HTTP server の両方から使う。
import { statSync } from "node:fs";
import { readAppSession } from "./app.mjs";
import { buildSessionView } from "./aggregate.mjs";
import { EventsReader } from "./events.mjs";
import { listSessions, listSessionsWsl, readHistory, readHistoryWsl } from "./history.mjs";
import { isValidSessionId, resolveCopilotHome, sourcePaths } from "./paths.mjs";
import { parseWslLocation } from "./sources.mjs";

// 変更検知の対象。SQLite は WAL に追記されると本体の mtime が動かないため -wal も見る。
// WSL の source は App の data.db を持たない (App は Windows 側で動く)。
function watchedFiles(paths, isWsl) {
    const history = [paths.historyDb, `${paths.historyDb}-wal`, paths.eventsFile];
    return isWsl ? history : [...history, paths.appDb, `${paths.appDb}-wal`];
}

const NO_APP_RESULT = { status: { status: "missing" }, session: null, contextSeries: [] };

// ファイル群の (mtime, size) から変更検知用の署名を作る。存在しないファイルも状態として含める。
export function sourceSignature(files) {
    return files.map((file) => {
        try {
            const stat = statSync(file);
            return `${stat.mtimeMs}:${stat.size}`;
        } catch {
            return "-";
        }
    }).join("|");
}

// セッションごとに最新の表示用データを保持し、記録が変わった時だけ作り直す。
export class SessionUsageProvider {
    /** @param {{ copilotHome?: string, now?: () => Date, runWsl?: Function }} [options] */
    constructor(options = {}) {
        this.copilotHome = options.copilotHome ?? resolveCopilotHome();
        // WSL 共有 (wsl.localhost / wsl$) 上の home は、共有越しの SQLite ロックを避けて WSL 側 reader で履歴を読む。
        this.wsl = parseWslLocation(this.copilotHome);
        this.wslOptions = options.runWsl ? { run: options.runWsl } : {};
        this.now = options.now ?? (() => new Date());
        this.events = new EventsReader();
        /** @type {Map<string, { signature: string, view: any }>} */
        this.cache = new Map();
        /** @type {{ signature: string, result: any } | undefined} */
        this.sessionList = undefined;
    }

    // 表示用データを返す。`since` が現在の署名と同じなら本体を省く。
    getView(sessionId, { currentSessionId = null, force = false, since = null } = {}) {
        if (!isValidSessionId(sessionId)) {
            throw new Error("Invalid session id.");
        }
        const paths = sourcePaths(this.copilotHome, sessionId);
        const signature = sourceSignature(watchedFiles(paths, this.wsl !== null));
        const cached = this.cache.get(sessionId);
        if (!force && cached && cached.signature === signature) {
            if (since === signature) return { signature, unchanged: true };
            return { signature, view: { ...cached.view, session: { ...cached.view.session, isCurrent: sessionId === currentSessionId } } };
        }
        const view = buildSessionView({
            sessionId,
            isCurrent: sessionId === currentSessionId,
            history: this.wsl ? readHistoryWsl(this.wsl, sessionId, this.wslOptions) : readHistory(paths.historyDb, sessionId),
            events: this.events.read(paths.eventsFile, { force }),
            app: this.wsl ? NO_APP_RESULT : readAppSession(paths.appDb, sessionId),
            readAt: this.now().toISOString(),
        });
        this.cache.set(sessionId, { signature, view });
        return { signature, view };
    }

    // この provider が扱うセッション指定として正しいか (単一 home では ID そのもの)。
    isValidRef(ref) {
        return isValidSessionId(ref);
    }

    // セッション選択用の一覧。全履歴を読むため、履歴 DB (と WAL) が変わった時だけ作り直す。
    listSessions() {
        const { historyDb } = sourcePaths(this.copilotHome, "list");
        const signature = sourceSignature([historyDb, `${historyDb}-wal`]);
        if (this.sessionList?.signature !== signature) {
            this.sessionList = { signature, result: this.wsl ? listSessionsWsl(this.wsl, this.wslOptions) : listSessions(historyDb) };
        }
        return this.sessionList.result;
    }
}
