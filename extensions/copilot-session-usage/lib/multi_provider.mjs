// 複数の Copilot home (source) を 1 つの provider として扱う。セッション一覧は全 source を混ぜ、
// セッションの指定 (ref) は主 source なら ID そのもの、追加した source なら `<source id>~<session id>`。
import { isValidSessionId, resolveCopilotHome } from "./paths.mjs";
import { SessionUsageProvider } from "./provider.mjs";
import {
    isValidSourceId, labelForPath, parseWslLocation, PRIMARY_SOURCE_ID, primaryLabel, sourceIdOf, SourceSettings, uniqueLabels,
} from "./sources.mjs";

const REF_SEPARATOR = "~";

// source 単位の状態コードを持つ一覧用の行。パスは含めない。
// 状態の優先は ok > それ以外 (どれか 1 つでも読めれば一覧全体は ok とし、読めない source は sources に残す)。
function overallStatus(sourceResults) {
    if (sourceResults.some((r) => r.status.status === "ok")) return { status: "ok" };
    return sourceResults[0]?.status ?? { status: "missing" };
}

export class MultiSourceProvider {
    /**
     * @param {{
     *   primaryHome?: string, settings?: SourceSettings | null, now?: () => Date,
     *   platform?: string, runWsl?: Function,
     * }} [options] `settings` が null なら追加・保存はできず、主 source だけを読む (`--home` 指定など)。
     */
    constructor(options = {}) {
        this.primaryHome = options.primaryHome ?? resolveCopilotHome();
        this.settings = options.settings === undefined ? new SourceSettings() : options.settings;
        this.now = options.now;
        this.platform = options.platform ?? process.platform;
        this.runWsl = options.runWsl;
        this.currentSessionId = null;
        /** @type {Map<string, SessionUsageProvider>} */
        this.providers = new Map();
        this.settingsStatus = { status: "ok" };
        this.entries = [];
        this.reload();
    }

    // 設定を読み直して source の一覧と provider を作る。変わらない source の provider (とその cache) は残す。
    reload() {
        const loaded = this.settings ? this.settings.load() : { status: "ok", paths: [] };
        this.settingsStatus = { status: loaded.status };
        const labels = uniqueLabels([primaryLabel(this.platform), ...loaded.paths.map(labelForPath)]);
        this.entries = [
            { id: PRIMARY_SOURCE_ID, label: labels[0], path: this.primaryHome, isPrimary: true },
            ...loaded.paths.map((path, i) => ({ id: sourceIdOf(path), label: labels[i + 1], path, isPrimary: false })),
        ];
        const next = new Map();
        for (const entry of this.entries) {
            const existing = this.providers.get(entry.id);
            next.set(entry.id, existing ?? new SessionUsageProvider({
                copilotHome: entry.path,
                now: this.now,
                runWsl: this.runWsl,
            }));
        }
        this.providers = next;
    }

    // canvas を開いたセッション。主 source のものとして扱う。
    setCurrentSessionId(id) {
        this.currentSessionId = id ?? null;
    }

    refOf(sourceId, sessionId) {
        return sourceId === PRIMARY_SOURCE_ID ? sessionId : `${sourceId}${REF_SEPARATOR}${sessionId}`;
    }

    /**
     * 形が不正・未知の source は null。設定は別のプロセス (canvas と単独起動) からも変わるため、
     * 検証のたびに設定を読み直す (削除された source を読み続けない。変わらない source の provider は残る)。
     * @returns {{ sourceId: string, sessionId: string } | null}
     */
    parseRef(ref) {
        if (typeof ref !== "string") return null;
        const at = ref.indexOf(REF_SEPARATOR);
        const sourceId = at < 0 ? PRIMARY_SOURCE_ID : ref.slice(0, at);
        const sessionId = at < 0 ? ref : ref.slice(at + 1);
        if (!isValidSourceId(sourceId) || !isValidSessionId(sessionId)) return null;
        this.reload();
        return this.providers.has(sourceId) ? { sourceId, sessionId } : null;
    }

    isValidRef(ref) {
        return this.parseRef(ref) !== null;
    }

    // 表示用データ。session.id は ref (選択・API の鍵)、sessionId は記録上の ID、source は表示名。
    getView(ref, { currentSessionId = this.currentSessionId, force = false, since = null } = {}) {
        const parsed = this.parseRef(ref);
        if (!parsed) throw new Error("Invalid session id.");
        const entry = this.entries.find((e) => e.id === parsed.sourceId);
        const result = this.providers.get(parsed.sourceId).getView(parsed.sessionId, {
            currentSessionId: entry.isPrimary ? currentSessionId : null,
            force,
            since,
        });
        if (result.unchanged) return result;
        const session = { ...result.view.session, id: ref, sessionId: parsed.sessionId, sourceId: entry.id, source: entry.label };
        return { signature: result.signature, view: { ...result.view, session } };
    }

    // 全 source のセッションを最終活動の新しい順に混ぜた一覧と、source ごとの状態。
    listSessions() {
        this.reload();
        const results = this.entries.map((entry) => ({ entry, result: this.providers.get(entry.id).listSessions() }));
        const sessions = results.flatMap(({ entry, result }) => result.sessions.map((s) => ({
            ...s,
            id: this.refOf(entry.id, s.id),
            sessionId: s.id,
            sourceId: entry.id,
            source: entry.label,
        })));
        sessions.sort((a, b) => (b.lastActivity ?? "").localeCompare(a.lastActivity ?? "") || a.id.localeCompare(b.id));
        return {
            status: overallStatus(results.map((r) => r.result)),
            sessions,
            sources: results.map(({ entry, result }) => ({ id: entry.id, label: entry.label, status: result.status })),
        };
    }

    // 設定画面用。パスはここ (設定の表示と通信) だけに出す。
    getSettings() {
        this.reload();
        return {
            editable: this.settings !== null,
            status: this.settingsStatus,
            sources: this.entries.map((e) => ({
                id: e.id,
                label: e.label,
                path: e.path,
                isPrimary: e.isPrimary,
                kind: parseWslLocation(e.path) ? "wsl" : "local",
            })),
        };
    }

    addSource(path) {
        if (!this.settings) return { ok: false, error: "not_editable" };
        const result = this.settings.add(path, this.primaryHome);
        if (result.ok) this.reload();
        return result;
    }

    removeSource(sourceId) {
        if (!this.settings) return { ok: false, error: "not_editable" };
        if (sourceId === PRIMARY_SOURCE_ID || !isValidSourceId(sourceId)) return { ok: false, error: "not_found" };
        const result = this.settings.remove(sourceId);
        if (result.ok) this.reload();
        return result;
    }
}
