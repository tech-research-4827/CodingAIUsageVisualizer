// Session usage canvas の画面。extension の loopback server から表示用データを取得して描画する。
import { renderLineChart, renderStackedBars } from "./charts.mjs";
import {
    compactText, creditsNumber, creditsRounded, creditsText, dateTimeText, durationText, intText,
    percentText, timeText, usdText,
} from "./format.mjs";

const AUTO_INTERVAL_MS = 5000;
const PAGE_SIZE = 100;
const AGENT_COLORS = ["--agent-1", "--agent-2", "--agent-3", "--agent-4", "--agent-5", "--agent-6"];
const STATUS_TEXT = {
    missing: "not found",
    unreadable: "could not be read",
    unsupported_schema: "has an unsupported format",
    not_found: "has no record of this session",
};

const params = new URLSearchParams(location.search);
const state = {
    token: params.get("t") ?? "",
    instance: params.get("instance") ?? "",
    sessionId: null,
    currentSessionId: null,
    signature: null,
    view: null,
    sessions: [],
    sessionsStatus: null,
    sources: [],
    settings: null,
    browser: { q: "", from: "", to: "", sort: "credits", dir: "desc", page: 0 },
    xMode: "time",
    logY: false,
    page: 0,
    open: new Set(),
    chartSelection: {},
    loading: false,
    reload: null,
    selectSeq: 0,
    selecting: 0,
    timer: null,
};

const $ = (id) => document.getElementById(id);

// 要素を作る小さな helper。文字列は textContent として入れ、HTML として解釈しない。
function h(tag, attrs = {}, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
        if (value === undefined || value === null || value === false) continue;
        if (key === "class") el.className = value;
        else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
        else el.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat(Infinity)) {
        if (child === null || child === undefined || child === false) continue;
        el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
}

function api(path, query = {}, method = "GET", body = undefined) {
    const url = new URL(path, location.href);
    url.searchParams.set("t", state.token);
    url.searchParams.set("instance", state.instance);
    for (const [key, value] of Object.entries(query)) {
        if (value !== null && value !== undefined) url.searchParams.set(key, value);
    }
    const init = { method, cache: "no-store" };
    if (body !== undefined) Object.assign(init, { body: JSON.stringify(body), headers: { "Content-Type": "application/json" } });
    return fetch(url, init).then(async (res) => {
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(body.error ?? `HTTP ${res.status}`);
        return body;
    });
}

function setStatus(text, isError = false) {
    const el = $("status");
    el.textContent = text;
    el.classList.toggle("error", isError);
}

// server が持つこのパネルの選択 (Agent の select_session で変わる) を画面へ反映する。変わったら true。
// 画面での選択を server へ保存している間・保存後に始まった同期の結果は使わない (古い選択へ戻さない)。
async function syncPanel() {
    const seq = state.selectSeq;
    const st = await api("api/state");
    state.currentSessionId = st.currentSessionId;
    if (seq !== state.selectSeq || state.selecting > 0 || !st.sessionId || st.sessionId === state.sessionId) return false;
    state.sessionId = st.sessionId;
    state.signature = null;
    renderSessionSelect();
    return true;
}

// 表示用データを取得する。記録が変わっていなければ server は本体を省く。
// 取得中に呼ばれたら終了後にもう一度読み、取得中に選択が変わった応答は捨てる。
async function load({ force = false } = {}) {
    if (state.loading) {
        state.reload = { force: force || Boolean(state.reload?.force) };
        return;
    }
    state.loading = true;
    $("refresh").disabled = true;
    $("content").setAttribute("aria-busy", "true");
    if (!state.view) setStatus("Loading…");
    try {
        await syncPanel();
        const id = state.sessionId;
        if (!id) {
            // 単独起動で記録が 1 件も無い (または一覧を読めない)。記録が現れれば次の自動更新で表示する。
            await loadSessions();
            renderNoSession();
            setStatus(`Checked ${timeText(Date.now())}`);
            return;
        }
        const sameSession = state.view?.session.id === id;
        const result = await api("api/view", {
            session: id,
            since: !force && sameSession ? state.signature : null,
            force: force ? "1" : null,
        });
        if (id !== state.sessionId) {
            state.reload ??= { force: false };
            return;
        }
        state.signature = result.signature;
        if (!result.unchanged) {
            if (!sameSession) {
                state.page = 0;
                state.chartSelection = {};
            }
            state.view = result.view;
            render();
            renderSessionSelect();
        }
        setStatus(`Updated ${timeText(Date.now())}${state.view ? ` · read ${timeText(Date.parse(state.view.readAt))}` : ""}`);
    } catch (error) {
        setStatus(`Could not load usage: ${error.message}. Retry with Refresh.`, true);
        if (!state.view) renderError(error.message);
    } finally {
        state.loading = false;
        $("refresh").disabled = false;
        $("content").setAttribute("aria-busy", "false");
        const again = state.reload;
        state.reload = null;
        if (again) load(again);
    }
}

async function loadSessions() {
    try {
        const result = await api("api/sessions");
        state.sessions = result.sessions ?? [];
        state.sessionsStatus = result.status ?? null;
        state.sources = result.sources ?? [];
    } catch (error) {
        state.sessions = [];
        state.sources = [];
        state.sessionsStatus = { status: "unreachable", detail: error.message };
    }
    renderSessionSelect();
    renderBrowser();
}

function projectText(entry) {
    if (!entry.repository) return null;
    return entry.branch ? `${entry.repository}@${entry.branch}` : entry.repository;
}

// 複数の source があるときだけ、一覧・選択肢に source 名を出す。
function multiSource() {
    return state.sources.length > 1;
}

function shortId(entry) {
    return (entry.sessionId ?? entry.id).slice(0, 8);
}

function sessionLabel(entry) {
    const parts = [shortId(entry)];
    if (multiSource() && entry.source) parts.unshift(entry.source);
    const project = projectText(entry);
    if (project) parts.push(project);
    if (entry.lastActivity) parts.push(dateTimeText(Date.parse(entry.lastActivity)));
    if (entry.creditsNano !== null && entry.creditsNano !== undefined) parts.push(`${creditsRounded(entry.creditsNano)} credits`);
    if (typeof entry.requests === "number") parts.push(`${intText(entry.requests)} req`);
    return parts.join(" · ");
}

function renderSessionSelect() {
    const select = $("session-select");
    const entries = [...state.sessions];
    if (state.sessionId && !entries.some((e) => e.id === state.sessionId)) {
        // 一覧 (最近の 100 件) に無いセッションは、読み込んだ表示用データから見出しを作る。
        const v = state.view?.session.id === state.sessionId ? state.view : null;
        entries.unshift({
            id: state.sessionId,
            sessionId: v?.session.sessionId,
            source: v?.session.source,
            repository: v?.session.repository ?? null,
            branch: v?.session.branch ?? null,
            lastActivity: v?.totals.lastAt ? new Date(v.totals.lastAt).toISOString() : null,
        });
    }
    if (state.currentSessionId && !entries.some((e) => e.id === state.currentSessionId)) {
        entries.unshift({ id: state.currentSessionId, repository: null, branch: null, lastActivity: null });
    }
    entries.sort((a, b) => (b.id === state.currentSessionId) - (a.id === state.currentSessionId));
    // 単独起動で記録が無い時は空欄のプルダウンにせず、選べるものが無いことを示す。
    select.disabled = entries.length === 0;
    if (entries.length === 0) {
        select.replaceChildren(h("option", { value: "", selected: true }, "No sessions"));
        return;
    }
    select.replaceChildren(...entries.map((entry) => h("option", {
        value: entry.id,
        selected: entry.id === state.sessionId,
    }, `${entry.id === state.currentSessionId ? "Current · " : ""}${sessionLabel(entry)}`)));
}

// ---- session list -----------------------------------------------------------

// 列順は狭い panel でも Credits が横スクロールなしで見えるよう、長い Project を最後に置く。

const SORTS = {
    id: { label: "Session", defaultDir: "asc", value: (e) => e.sessionId ?? e.id },
    source: { label: "Source", defaultDir: "asc", value: (e) => e.source?.toLowerCase() ?? null },
    credits: { label: "AI Credits", defaultDir: "desc", value: (e) => (e.creditsNano == null ? null : BigInt(e.creditsNano)), num: true },
    requests: { label: "Requests", defaultDir: "desc", value: (e) => e.requests ?? null, num: true },
    activity: { label: "Last activity", defaultDir: "desc", value: (e) => (e.lastActivity ? Date.parse(e.lastActivity) : null) },
    project: { label: "Project", defaultDir: "asc", value: (e) => projectText(e)?.toLowerCase() ?? null },
};

// 入力欄の日付 (YYYY-MM-DD) を、その日のローカル時刻の始まりのミリ秒へ。
function localDayStart(text) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
    return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).getTime() : null;
}

function filteredSessions() {
    const b = state.browser;
    const terms = b.q.toLowerCase().split(/\s+/).filter(Boolean);
    const from = localDayStart(b.from);
    const toStart = localDayStart(b.to);
    const to = toStart === null ? null : new Date(toStart).setDate(new Date(toStart).getDate() + 1);
    const list = state.sessions.filter((e) => {
        const hay = [e.sessionId ?? e.id, e.source ?? "", e.repository ?? "", e.branch ?? ""].join("\n").toLowerCase();
        if (!terms.every((term) => hay.includes(term))) return false;
        if (from === null && to === null) return true;
        const t = e.lastActivity ? Date.parse(e.lastActivity) : null;
        if (t === null) return false;
        return (from === null || t >= from) && (to === null || t < to);
    });
    const sort = SORTS[b.sort];
    const sign = b.dir === "asc" ? 1 : -1;
    // 空の値は昇順・降順どちらでも末尾。同順位は最終活動の新しい順。
    return list.sort((x, y) => {
        const a = sort.value(x);
        const c = sort.value(y);
        if (a === null || c === null) {
            if (a !== c) return a === null ? 1 : -1;
        } else if (a !== c) {
            return (a < c ? -1 : 1) * sign;
        }
        return (y.lastActivity ?? "").localeCompare(x.lastActivity ?? "");
    });
}

function setBrowserOpen(open) {
    $("session-browser").hidden = !open;
    $("sessions-toggle").setAttribute("aria-expanded", String(open));
    if (open) {
        renderBrowser();
        loadSessions();
        $("browser-q").focus();
    }
}

function sortHeader(key) {
    const sort = SORTS[key];
    const b = state.browser;
    const active = b.sort === key;
    return h("th", {
        class: sort.num ? "num" : null,
        scope: "col",
        "aria-sort": active ? (b.dir === "asc" ? "ascending" : "descending") : null,
    }, h("button", {
        type: "button",
        class: "sort",
        "data-focus": `sort:${key}`,
        onclick: () => {
            if (active) b.dir = b.dir === "asc" ? "desc" : "asc";
            else Object.assign(b, { sort: key, dir: sort.defaultDir });
            b.page = 0;
            renderBrowser();
        },
    }, sort.label, h("span", { class: "sort-mark", "aria-hidden": "true" }, active ? (b.dir === "asc" ? " ▲" : " ▼") : "")));
}

function renderBrowser() {
    if (!$("session-browser").hidden) keepFocus(renderBrowserBody);
}

function renderBrowserBody() {
    const box = $("browser-results");
    const st = state.sessionsStatus;
    if (st && st.status !== "ok") {
        box.replaceChildren(h("p", { class: "empty" },
            `The session list ${STATUS_TEXT[st.status] ?? "could not be read"}${st.detail ? ` (${st.detail})` : ""}. Retry with Refresh.`));
        return;
    }
    if (!st) {
        box.replaceChildren(h("p", { class: "loading" }, "Loading sessions…"));
        return;
    }
    const b = state.browser;
    const list = filteredSessions();
    const pages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
    b.page = Math.min(b.page, pages - 1);
    const shown = list.slice(b.page * PAGE_SIZE, (b.page + 1) * PAGE_SIZE);
    const rows = shown.map((e) => {
        const selected = e.id === state.sessionId;
        const choose = () => chooseFromBrowser(e.id);
        return h("tr", { class: selected ? "clickable selected" : "clickable", "aria-current": selected ? "true" : null, onclick: choose },
            h("td", { title: e.sessionId ?? e.id },
                h("button", { type: "button", class: "link", "data-focus": `pick:${e.id}`, "aria-label": `Show session ${e.sessionId ?? e.id}`, onclick: (event) => { event.stopPropagation(); choose(); } }, shortId(e)),
                e.id === state.currentSessionId ? h("span", { class: "tag" }, "current") : null),
            multiSource() ? h("td", { class: "name", title: e.source ?? "" }, e.source ?? "-") : null,
            creditsCell(e.creditsNano, e.creditsMissing),
            num(e.requests),
            h("td", {}, e.lastActivity ? dateTimeText(Date.parse(e.lastActivity)) : "-"),
            h("td", { class: "name", title: projectText(e) ?? "No repository recorded" }, projectText(e) ?? "-"),
        );
    });
    const pager = pages > 1 ? h("div", { class: "pager" },
        h("button", { type: "button", "data-focus": "browser-prev", disabled: b.page === 0, onclick: () => { b.page -= 1; renderBrowser(); } }, "Previous"),
        h("span", {}, `Page ${b.page + 1} of ${pages}`),
        h("button", { type: "button", "data-focus": "browser-next", disabled: b.page >= pages - 1, onclick: () => { b.page += 1; renderBrowser(); } }, "Next"),
    ) : null;
    box.replaceChildren(
        ...unreadableSourceNotes(),
        h("p", { class: "note", role: "status" }, `Showing ${intText(list.length)} of ${intText(state.sessions.length)} sessions. AI Credits and Requests follow the same rules as the session view; sessions without request history show -.`),
        list.length ? h("div", { class: "table-wrap" }, h("table", {},
            h("caption", { class: "visually-hidden" }, "Sessions. Choose a row to show it."),
            h("thead", {}, h("tr", {}, Object.keys(SORTS).filter((key) => key !== "source" || multiSource()).map(sortHeader))),
            h("tbody", {}, rows),
        )) : h("p", { class: "empty" }, "No sessions match the filter."),
        ...(pager ? [pager] : []),
    );
}

// 読めない source だけを示す (他の source のセッションは通常どおり並ぶ)。
function unreadableSourceNotes() {
    return state.sources.filter((src) => src.status.status !== "ok").map((src) => h("p", { class: "empty", role: "alert" },
        `${src.label}: request history ${STATUS_TEXT[src.status.status] ?? "could not be read"}${src.status.detail ? ` (${src.status.detail})` : ""}. Its sessions are not listed.`));
}

async function chooseFromBrowser(id) {
    setBrowserOpen(false);
    $("session-select").value = id;
    window.scrollTo({ top: 0 });
    await selectSession(id);
    $("sessions-toggle").focus();
}

async function selectSession(id) {
    state.selectSeq += 1;
    state.selecting += 1;
    state.sessionId = id;
    state.signature = null;
    let selectError = null;
    try {
        await api("api/select", { session: id }, "POST");
    } catch (error) {
        // 選択を server に保存できなくても表示は切り替える。パネル再読み込み時に元へ戻ることを知らせる。
        selectError = error;
    } finally {
        state.selecting -= 1;
    }
    await load();
    renderSessionSelect();
    if (selectError) setStatus(`Showing the selected session, but the choice was not saved (${selectError.message}).`, true);
}

// Auto refresh が on なら表示 (と開いている一覧) を読み直す。off でも Agent による選択の変更だけは反映する。
function scheduleAuto() {
    clearInterval(state.timer);
    state.timer = setInterval(async () => {
        if ($("auto").checked) {
            load();
            if (!$("session-browser").hidden) loadSessions();
        } else if (!state.loading) {
            try {
                if (await syncPanel()) load();
            } catch {
                // 次の tick か Refresh で再試行する。
            }
        }
    }, AUTO_INTERVAL_MS);
}

// ---- rendering -------------------------------------------------------------

function renderError(message) {
    $("content").replaceChildren(h("div", { class: "error-panel", role: "alert" },
        h("strong", {}, "Could not load session usage."),
        h("span", {}, `Reason: ${message}. If the canvas was closed or the launcher was stopped, reopen it; otherwise retry.`),
        h("button", { type: "button", class: "primary", onclick: () => load({ force: true }) }, "Retry"),
    ));
}

// 表示できるセッションが無い時の状態。一覧を読めないのか、記録がまだ無いのかを区別して示す。
function renderNoSession() {
    const st = state.sessionsStatus;
    if (st && st.status !== "ok") {
        $("content").replaceChildren(h("div", { class: "error-panel", role: "alert" },
            h("strong", {}, "No sessions to show."),
            h("span", {}, `Request history (session-store.db) ${STATUS_TEXT[st.status] ?? "could not be read"}${st.detail ? ` (${st.detail})` : ""}. Check the Copilot home that is being read.`),
            h("button", { type: "button", class: "primary", onclick: () => load({ force: true }) }, "Retry"),
        ));
        return;
    }
    $("content").replaceChildren(h("p", { class: "empty", role: "status" },
        "No sessions are recorded yet. Sessions appear here when Copilot CLI records them; with Auto refresh on, this page checks every 5 seconds."));
}

// 描き直しの前後で、data-focus を持つ要素のフォーカスを保つ (自動更新でキーボード操作を途切れさせない)。
function keepFocus(fn) {
    const key = document.activeElement?.dataset?.focus ?? null;
    fn();
    if (!key) return;
    const el = document.querySelector(`[data-focus="${CSS.escape(key)}"]`);
    if (el && document.activeElement !== el) el.focus({ preventScroll: true });
}

// グラフの選択点を描き直し後も保つための受け渡し。
function chartSelection(id) {
    return { key: state.chartSelection[id] ?? null, onChange: (key) => { state.chartSelection[id] = key; } };
}

function render() {
    const view = state.view;
    if (!view) return;
    // 開いている折りたたみの状態を描き直し後も保つ。
    document.querySelectorAll("details[data-key]").forEach((d) => {
        if (d.open) state.open.add(d.dataset.key);
        else state.open.delete(d.dataset.key);
    });
    keepFocus(() => {
        $("content").replaceChildren(
            renderAlerts(view),
            renderSummary(view),
            renderCharts(view),
            renderAgents(view),
            renderDetails(view),
        );
        drawCharts();
    });
}

function alertItem(kind, label, text) {
    return h("li", { class: kind }, h("strong", {}, `${label}: `), text);
}

// 部分表示・失敗の理由を、影響する値の近くで文字として示す。
function renderAlerts(view) {
    const items = [];
    const { history, events, app } = view.sources;
    if (history.status !== "ok") {
        items.push(alertItem("error", "Request history", `session-store.db ${STATUS_TEXT[history.status] ?? history.status}${history.detail ? ` (${history.detail})` : ""}. Totals, charts and agents are unavailable.`));
    } else if (view.totals.requests === 0) {
        items.push(alertItem("warning", "No request history", "This session has no rows in session-store.db. Cumulative events (if any) are shown under Reconciliation as reference only."));
    }
    if (events.status !== "ok") {
        items.push(alertItem("warning", "Session events", `events.jsonl ${STATUS_TEXT[events.status] ?? events.status}. Sub-agent names, compactions and cumulative totals are unavailable.`));
    }
    if (app.status !== "ok" && app.status !== "not_found" && app.status !== "missing") {
        items.push(alertItem("warning", "App data", `data.db ${STATUS_TEXT[app.status] ?? app.status}. Context limits from the App are unavailable.`));
    }
    if (view.totals.creditsMissing > 0) {
        items.push(alertItem("warning", "Credits missing", `${view.totals.creditsMissing} request(s) have no AI Credits value. Totals are an observed subtotal; missing values are not counted as 0.`));
    }
    if (view.totals.tokenRowsInvalid > 0) {
        items.push(alertItem("warning", "Invalid tokens", `${view.totals.tokenRowsInvalid} request(s) have inconsistent token counts and are excluded from token totals.`));
    }
    if (view.totals.invalidTimestampRows > 0) {
        items.push(alertItem("warning", "Invalid time", `${view.totals.invalidTimestampRows} request(s) without a valid timestamp are excluded.`));
    }
    if (view.tokenTypes.mismatchRows > 0) {
        items.push(alertItem("warning", "Cost detail mismatch", `${view.tokenTypes.mismatchRows} request(s) whose per-token-type cost does not add up to the recorded AI Credits.`));
    }
    if (view.agents.some((a) => !a.named)) {
        items.push(alertItem("info", "Unnamed sub-agent", "Some sub-agents have no name in events.jsonl and are shown by a short id."));
    }
    if (view.totals.duplicateRowsExcluded > 0) {
        items.push(alertItem("info", "Duplicates excluded", `${view.totals.duplicateRowsExcluded} identical history row(s) counted once (see Session and sources).`));
    }
    return items.length ? h("ul", { class: "alerts", "aria-label": "Data warnings" }, items) : h("div", { hidden: true });
}

function card(label, value, sub, title, primary = false) {
    return h("div", { class: `card${primary ? " primary-card" : ""}`, title },
        h("div", { class: "label" }, label),
        h("div", { class: "value" }, value),
        sub ? h("div", { class: "sub" }, sub) : null,
    );
}

function renderSummary(view) {
    const t = view.totals;
    const tk = t.tokens;
    const noHistory = view.sources.history.status !== "ok";
    const creditsSub = [`≈ ${usdText(t.creditsNano)}`, t.creditsMissing ? `${t.creditsMissing} missing` : "observed subtotal"].join(" · ");
    const ctx = view.context;
    const ctxValue = ctx.appCurrent?.tokens ?? ctx.latestMain?.tokens ?? null;
    const ctxSub = ctx.limit ? `of ${compactText(ctx.limit)} (${percentText(ctxValue, ctx.limit)}) · ${ctx.limitSource === "app" ? "App" : "compaction"} limit` : "limit not recorded";
    const span = t.firstAt !== null ? `${timeText(t.firstAt)}–${timeText(t.lastAt)}` : "no requests";
    // 履歴を読めない時は実測 0 と区別するため、履歴由来の値をすべて "-" にする。
    const hv = (text) => (noHistory ? "-" : text);
    return h("section", { "aria-labelledby": "summary-title" },
        h("div", { class: "section-head" },
            h("h2", { id: "summary-title" }, "Summary"),
            h("span", { class: "note" }, sessionLine(view)),
        ),
        h("div", { class: "cards" },
            card("AI Credits", hv(creditsRounded(t.creditsNano)), hv(creditsSub), hv(`Exact: ${creditsText(t.creditsNano)} AI Credits`), true),
            card("Requests", hv(intText(t.requests)), hv(span), hv(`API time ${durationText(t.durationMs)}`)),
            card("Input tokens", hv(compactText(tk.input)), hv(`uncached ${compactText(tk.uncachedInput)}`), hv(`${intText(tk.input)} (includes cache read and cache write)`)),
            card("Output tokens", hv(compactText(tk.output)), hv(`reasoning ${compactText(tk.reasoning)}`), hv(intText(tk.output))),
            card("Cache read", hv(compactText(tk.cacheRead)), hv(`${percentText(tk.cacheRead, tk.input)} of input`), hv(intText(tk.cacheRead))),
            card("Cache write", hv(compactText(tk.cacheWrite)), hv(`${percentText(tk.cacheWrite, tk.input)} of input`), hv(intText(tk.cacheWrite))),
            card("Context (main)", compactText(ctxValue), ctxSub, ctxValue === null ? "No main-agent request yet" : intText(ctxValue)),
        ),
    );
}

function sessionLine(view) {
    const s = view.session;
    const repo = s.repository ? `${s.repository}${s.branch ? `@${s.branch}` : ""}` : null;
    return [s.isCurrent ? "Current session" : "Session", multiSource() ? s.source : null, (s.sessionId ?? s.id).slice(0, 8), repo].filter(Boolean).join(" · ");
}

function segmented(label, options, value, onChange) {
    return h("span", { class: "chart-controls", role: "group", "aria-label": label },
        h("span", {}, label),
        h("span", { class: "segmented" }, options.map(([key, text]) => h("button", {
            type: "button",
            "aria-pressed": String(value === key),
            "data-focus": `seg:${label}:${key}`,
            onclick: () => onChange(key),
        }, text))),
    );
}

function renderCharts(view) {
    const section = h("section", { "aria-labelledby": "charts-title" },
        h("div", { class: "section-head" },
            h("h2", { id: "charts-title" }, "Trends"),
            h("span", { class: "chart-controls" },
                segmented("X axis", [["time", "Time"], ["seq", "Sequence"]], state.xMode, (v) => { state.xMode = v; render(); }),
                segmented("Token scale", [["linear", "Linear"], ["log", "Log"]], state.logY ? "log" : "linear", (v) => { state.logY = v === "log"; render(); }),
            ),
        ),
    );
    for (const [id, title] of [
        ["chart-credits", "Cumulative AI Credits"],
        ["chart-tokens", "Cumulative tokens"],
        ["chart-context", "Context size per request"],
        ["chart-requests", "Tokens per request"],
    ]) {
        section.append(h("div", { class: "chart-block" }, h("h2", {}, title), h("div", { id })));
    }
    return section;
}

function agentColors(view) {
    const map = new Map();
    view.agents.forEach((a, i) => map.set(a.key, AGENT_COLORS[i % AGENT_COLORS.length]));
    return map;
}

function agentLabel(view, key) {
    return view.agents.find((a) => a.key === key)?.label ?? key;
}

function xOf(req) {
    return state.xMode === "time" ? req.t : req.i;
}

// 1 日を超える範囲では時刻だけだと順序が読めないため、日付も付ける。
let multiDay = false;

function xFormat(x) {
    if (state.xMode !== "time") return `#${Math.round(x)}`;
    if (!multiDay) return timeText(x);
    const d = new Date(x);
    return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${timeText(x).slice(0, 5)}`;
}

function requestHeader(view, req) {
    return `#${req.i} ${timeText(req.t)} · ${agentLabel(view, req.agent)} · ${req.model ?? "unknown model"}${req.effort ? ` (${req.effort})` : ""}`;
}

// グラフは要素の幅が決まってから描く (幅が変われば描き直す)。
function drawCharts() {
    const view = state.view;
    if (!view) return;
    const reqs = view.requests;
    const empty = view.sources.history.status === "ok" ? "No requests recorded for this session yet." : "Request history is unavailable.";
    const width = (id) => $(id)?.clientWidth || 320;
    // 時間軸は request の範囲に揃える。範囲外の累積イベント (再開後の shutdown など) は Reconciliation の表にだけ出す。
    const tMin = reqs.length ? reqs[0].t : 0;
    const tMax = reqs.length ? reqs.at(-1).t : 0;
    const inRange = (t) => reqs.length > 0 && t >= tMin && t <= tMax;
    multiDay = tMax - tMin > 20 * 3600 * 1000;
    const cumulativeInRange = view.reconciliation.cumulative.filter((c) => inRange(Date.parse(c.t)));
    const cumulativeOutside = view.reconciliation.cumulative.length - cumulativeInRange.length;

    renderLineChart($("chart-credits"), {
        selection: chartSelection("chart-credits"),
        title: "Cumulative AI Credits over time",
        width: width("chart-credits"),
        xFormat,
        yFormat: (y) => compactText(y),
        series: [{
            label: "AI Credits (request history)",
            colorVar: "--series-credits",
            points: reqs.map((r) => ({ x: xOf(r), y: creditsNumber(r.cumNano), ref: r })),
        }],
        markers: state.xMode === "time" ? [{
            label: "Session cumulative event (reference)",
            colorVar: "--series-reference",
            points: cumulativeInRange.map((c) => ({ x: Date.parse(c.t), y: creditsNumber(c.nano), ref: c })),
        }] : [],
        note: state.xMode === "time" && cumulativeOutside > 0
            ? `${cumulativeOutside} cumulative event(s) outside the request time range are listed under Reconciliation.` : null,
        describe: (p) => (p.ref?.type
            ? `${p.ref.type} ${timeText(Date.parse(p.ref.t))} · event ${creditsText(p.ref.nano)} · history at that time ${creditsText(p.ref.historyNanoAtT)}`
            : `${requestHeader(view, p.ref)} · request ${p.ref.nano === null ? "-" : creditsText(p.ref.nano)} · cumulative ${creditsText(p.ref.cumNano)}`),
        emptyText: empty,
    });

    const tokenSeries = [
        ["Input (incl. cache)", "--series-input", "input", true],
        ["Cache read", "--series-cache-read", "cacheRead"],
        ["Cache write", "--series-cache-write", "cacheWrite"],
        ["Output", "--series-output", "output"],
    ];
    renderLineChart($("chart-tokens"), {
        selection: chartSelection("chart-tokens"),
        title: "Cumulative tokens over time",
        width: width("chart-tokens"),
        log: state.logY,
        xFormat,
        yFormat: (y) => compactText(y),
        series: tokenSeries.map(([label, colorVar, key, dashed]) => ({
            label, colorVar, dashed,
            points: reqs.map((r) => ({ x: xOf(r), y: r.cum[key], ref: r })),
        })),
        describe: (p) => `${requestHeader(view, p.ref)} · cumulative input ${intText(p.ref.cum.input)}, cache read ${intText(p.ref.cum.cacheRead)}, cache write ${intText(p.ref.cum.cacheWrite)}, output ${intText(p.ref.cum.output)}`,
        emptyText: empty,
    });

    const colors = agentColors(view);
    const contextSeries = view.agents.filter((a) => a.requests > 0 && a.kind !== "compaction").map((a) => ({
        label: `${a.label}${a.models.length ? ` (${a.models.join(", ")})` : ""}`,
        colorVar: colors.get(a.key),
        points: reqs.filter((r) => r.agent === a.key && r.input !== null).map((r) => ({ x: xOf(r), y: r.input, ref: r })),
    }));
    const limitPoints = state.xMode === "time" ? limitSeries(view).filter((p) => inRange(p.x)) : [];
    if (limitPoints.length && reqs.length) {
        contextSeries.push({ label: "Context limit (main, recorded)", colorVar: "--series-limit", dashed: true, step: true, points: limitPoints });
    }
    renderLineChart($("chart-context"), {
        selection: chartSelection("chart-context"),
        title: "Context size (prompt tokens) per request",
        width: width("chart-context"),
        log: state.logY,
        xFormat,
        yFormat: (y) => compactText(y),
        series: contextSeries,
        markers: state.xMode === "time" && view.events.compactions.length && reqs.length ? [{
            label: "Compaction (pre-compaction size)",
            colorVar: "--series-reference",
            points: view.events.compactions.filter((c) => c.preTokens !== null && inRange(Date.parse(c.t)))
                .map((c) => ({ x: Date.parse(c.t), y: c.preTokens, ref: { compaction: c } })),
        }] : [],
        describe: (p) => (p.ref?.compaction
            ? `Compaction ${timeText(Date.parse(p.ref.compaction.t))} · before ${intText(p.ref.compaction.preTokens)} · limit ${intText(p.ref.compaction.tokenLimit)}`
            : p.ref?.limit !== undefined
                ? `Context limit ${intText(p.ref.limit)} at ${timeText(p.x)}`
                : `${requestHeader(view, p.ref)} · context ${intText(p.ref.input)} tokens`),
        emptyText: empty,
    });

    renderStackedBars($("chart-requests"), {
        selection: chartSelection("chart-requests"),
        title: "Tokens per request, stacked by type",
        width: width("chart-requests"),
        parts: [
            { label: "Uncached input", colorVar: "--series-uncached" },
            { label: "Cache read", colorVar: "--series-cache-read" },
            { label: "Cache write", colorVar: "--series-cache-write" },
            { label: "Output", colorVar: "--series-output" },
        ],
        bars: reqs.map((r) => ({ values: [r.uncached, r.cacheRead, r.cacheWrite, r.output], ref: r })),
        yFormat: (y) => compactText(y),
        xFormat: (bar) => (state.xMode === "time" ? timeText(bar.from.t) : `#${bar.from.i}`),
        describe: (bar) => {
            const head = bar.count === 1 ? requestHeader(view, bar.from) : `#${bar.from.i}–#${bar.to.i} (${bar.count} requests)`;
            const [u, r, w, o] = bar.values;
            const invalid = bar.invalid ? ` · ${bar.invalid} invalid` : "";
            return `${head} · uncached ${intText(u)}, cache read ${intText(r)}, cache write ${intText(w)}, output ${intText(o)}${invalid}`;
        },
        emptyText: empty,
    });
}

// 上限は App の記録 (main) と圧縮時の tokenLimit だけを使う。
function limitSeries(view) {
    const points = view.app.contextSeries.map((p) => ({ x: p.t, y: p.limit, ref: { limit: p.limit } }));
    if (points.length === 0 && view.context.limit && view.requests.length) {
        const first = view.requests[0].t;
        const last = view.requests.at(-1).t;
        return [first, last].map((x) => ({ x, y: view.context.limit, ref: { limit: view.context.limit } }));
    }
    return points;
}

function num(value, formatter = intText) {
    return h("td", { class: "num" }, formatter(value));
}

function creditsCell(nano, missing = 0) {
    return h("td", { class: "num", title: nano === null ? "No AI Credits recorded" : `${creditsText(nano)} AI Credits` },
        nano === null ? "-" : creditsText(nano), missing ? h("span", { class: "flag" }, ` (${missing} missing)`) : null);
}

function table(headers, rows, caption) {
    return h("div", { class: "table-wrap" }, h("table", {},
        caption ? h("caption", { class: "visually-hidden" }, caption) : null,
        h("thead", {}, h("tr", {}, headers.map(([text, isNum]) => h("th", { class: isNum ? "num" : null, scope: "col" }, text)))),
        h("tbody", {}, rows),
    ));
}

function renderAgents(view) {
    const rows = view.agents.map((a) => (a.requests === 0 ? h("tr", {},
        h("td", { class: "name", title: `${a.label}${a.agentId ? ` · id ${a.agentId}` : ""}` }, a.label, h("span", { class: "tag" }, "sub-agent")),
        h("td", { class: "name", title: a.models.join(", ") }, a.models.join(", ") || "-"),
        h("td", {}, a.efforts.join(", ") || "-"),
        h("td", { class: "num", colspan: 8 }, "no usage rows in request history"),
        h("td", {}, a.outcome ?? "-"),
    ) : h("tr", {},
        h("td", { class: "name", title: `${a.label}${a.agentId ? ` · id ${a.agentId}` : ""}` }, a.label,
            a.kind !== "main" ? h("span", { class: "tag" }, a.kind === "subagent" ? "sub-agent" : a.kind) : null),
        h("td", { class: "name", title: a.models.join(", ") }, a.models.join(", ") || "-"),
        h("td", {}, a.efforts.join(", ") || "-"),
        num(a.requests),
        creditsCell(a.creditsNano, a.creditsMissing),
        num(a.tokens.input), num(a.tokens.output), num(a.tokens.cacheRead), num(a.tokens.cacheWrite),
        num(a.ctxMax), num(a.ctxLast),
        h("td", {}, a.outcome ?? (a.kind === "subagent" ? "running/unknown" : "-")),
    )));
    return h("section", { "aria-labelledby": "agents-title" },
        h("h2", { id: "agents-title" }, "Agents"),
        view.agents.length ? table([
            ["Agent"], ["Model"], ["Effort"], ["Requests", true], ["AI Credits", true], ["Input", true], ["Output", true],
            ["Cache read", true], ["Cache write", true], ["Context max", true], ["Context last", true], ["Outcome"],
        ], rows, "Usage per agent") : h("p", { class: "empty" }, "No agent usage recorded."),
        h("p", { class: "note" }, "Context = prompt tokens sent in a request (input including cache). Sub-agent context limits are not recorded."),
    );
}

function details(key, title, ...body) {
    return h("details", { "data-key": key, open: state.open.has(key) },
        h("summary", { "data-focus": `summary:${key}` }, title),
        h("div", { class: "content" }, body),
    );
}

function facts(entries) {
    return h("dl", { class: "facts" }, entries.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v ?? "-")]));
}

function renderDetails(view) {
    return h("section", { "aria-labelledby": "details-title" },
        h("h2", { id: "details-title" }, "Audit details"),
        renderRequests(view),
        details("models", `Models (${view.models.length})`, view.models.length ? table(
            [["Model"], ["Effort"], ["Requests", true], ["AI Credits", true], ["Input", true], ["Output", true], ["Cache read", true], ["Cache write", true]],
            view.models.map((m) => h("tr", {}, h("td", { class: "name", title: m.model }, m.model), h("td", {}, m.efforts.join(", ") || "-"),
                num(m.requests), creditsCell(m.creditsNano, m.creditsMissing), num(m.tokens.input), num(m.tokens.output),
                num(m.tokens.cacheRead), num(m.tokens.cacheWrite))),
        ) : h("p", { class: "empty" }, "No models recorded.")),
        renderTokenTypes(view),
        renderReconciliation(view),
        renderCompactions(view),
        details("model-changes", `Model changes (${view.events.modelChanges.length})`, view.events.modelChanges.length ? table(
            [["Time"], ["From"], ["To"], ["Effort"]],
            view.events.modelChanges.map((c) => h("tr", {}, h("td", {}, dateTimeText(Date.parse(c.t))), h("td", {}, c.previousModel ?? "-"),
                h("td", {}, c.newModel ?? "-"), h("td", {}, [c.previousReasoningEffort, c.reasoningEffort].filter(Boolean).join(" → ") || "-"))),
        ) : h("p", { class: "empty" }, "No model changes recorded.")),
        renderSubagents(view),
        details("tools", `Tools (${view.events.tools.length})`, view.events.tools.length ? table(
            [["Tool"], ["Calls", true], ["Failures", true]],
            view.events.tools.map((t) => h("tr", {}, h("td", { class: "name", title: t.name }, t.name), num(t.calls), num(t.failures))),
        ) : h("p", { class: "empty" }, "No tool calls recorded.")),
        details("errors", "Errors and interruptions", facts([
            ["Errors", view.events.errors.length ? view.events.errors.map((e) => `${e.errorType} ×${e.count}`).join(", ") : "none"],
            ["Aborts", intText(view.events.aborts)],
            ["Resumes", view.events.resumes.length ? view.events.resumes.map((t) => dateTimeText(Date.parse(t))).join(", ") : "none"],
            ["Unreadable event lines", intText(view.events.parseErrors)],
        ])),
        renderSessionFacts(view),
    );
}

function renderRequests(view) {
    const reqs = view.requests;
    const pages = Math.max(1, Math.ceil(reqs.length / PAGE_SIZE));
    state.page = Math.min(state.page, pages - 1);
    const slice = reqs.slice(state.page * PAGE_SIZE, (state.page + 1) * PAGE_SIZE);
    const pager = h("div", { class: "pager" },
        h("button", { type: "button", disabled: state.page === 0, onclick: () => { state.page = 0; render(); } }, "First"),
        h("button", { type: "button", "data-focus": "requests-prev", disabled: state.page === 0, onclick: () => { state.page -= 1; render(); } }, "Previous"),
        h("span", {}, `Page ${state.page + 1} of ${pages} · ${reqs.length} requests`),
        h("button", { type: "button", "data-focus": "requests-next", disabled: state.page >= pages - 1, onclick: () => { state.page += 1; render(); } }, "Next"),
        h("button", { type: "button", disabled: state.page >= pages - 1, onclick: () => { state.page = pages - 1; render(); } }, "Last"),
    );
    const rows = slice.map((r) => h("tr", {},
        num(r.i, (v) => `#${v}`),
        h("td", {}, timeText(r.t)),
        h("td", { class: "name", title: agentLabel(view, r.agent) }, agentLabel(view, r.agent)),
        h("td", { class: "name", title: r.model ?? "" }, r.model ?? "-"),
        h("td", {}, r.effort ?? "-"),
        h("td", {}, r.initiator ?? "-"),
        creditsCell(r.nano),
        r.input === null ? h("td", { class: "num flag", colspan: 4 }, "invalid token counts") : [num(r.input), num(r.output), num(r.cacheRead), num(r.cacheWrite)],
        num(r.reasoning),
        num(r.durationMs, durationText),
        num(r.ttftMs, durationText),
        h("td", {}, r.finishReason ?? "-", r.detailMismatch ? h("span", { class: "flag", title: "Per-token-type cost does not add up" }, " ≠") : null),
        h("td", {}, r.apiEndpoint ?? "-"),
    ));
    return details("requests", `Requests (${reqs.length})`, reqs.length ? [pager, table([
        ["#", true], ["Time"], ["Agent"], ["Model"], ["Effort"], ["Initiator"], ["AI Credits", true], ["Input", true], ["Output", true],
        ["Cache read", true], ["Cache write", true], ["Reasoning", true], ["Duration", true], ["TTFT", true], ["Finish"], ["Endpoint"],
    ], rows, "Requests")] : h("p", { class: "empty" }, "No requests recorded."));
}

function renderTokenTypes(view) {
    const tt = view.tokenTypes;
    const total = tt.rows.reduce((sum, r) => sum + BigInt(r.nano), 0n);
    return details("token-types", "AI Credits by token type", tt.rows.length ? [
        table([["Token type"], ["Tokens", true], ["AI Credits", true], ["Share", true]], tt.rows.map((r) => h("tr", {},
            h("td", {}, r.tokenType), num(r.tokens), creditsCell(r.nano),
            h("td", { class: "num" }, percentText(Number(BigInt(r.nano)), Number(total)))))),
        h("p", { class: "note" }, `Computed from each request's per-token-type rates. ${tt.rowsWithoutDetails} request(s) without rate details are not included here; ${tt.mismatchRows} request(s) do not add up to the recorded value.`),
    ] : h("p", { class: "empty" }, "No per-token-type rate details recorded."));
}

function renderReconciliation(view) {
    const rec = view.reconciliation;
    const app = rec.app;
    return details("reconciliation", "Reconciliation (reference)",
        h("p", { class: "note" }, `History read at ${dateTimeText(Date.parse(view.readAt))}. Each cumulative event is compared with history rows recorded at or before the event time; differences are references, not corrections.`),
        rec.cumulative.length ? table(
            [["Event"], ["Time"], ["Event AI Credits", true], ["History at event", true], ["Rows", true], ["Difference", true]],
            rec.cumulative.map((c) => h("tr", {}, h("td", {}, c.type), h("td", {}, dateTimeText(Date.parse(c.t))), creditsCell(c.nano),
                creditsCell(c.historyNanoAtT, c.historyMissingAtT), num(c.historyRowsAtT), creditsCell(c.diffNano))),
        ) : h("p", { class: "empty" }, "No cumulative events (checkpoint or shutdown) recorded."),
        app ? table(
            [["Value"], ["App (data.db)", true], ["Request history", true]],
            [
                h("tr", {}, h("td", {}, "AI Credits"), creditsCell(app.appNano), creditsCell(app.historyNano)),
                h("tr", {}, h("td", {}, "Input tokens"), num(app.appInput), num(app.historyInput)),
                h("tr", {}, h("td", {}, "Output tokens"), num(app.appOutput), num(app.historyOutput)),
                h("tr", {}, h("td", {}, "Cached tokens (read + write)"), num(app.appCached), num(app.historyCached)),
            ],
        ) : h("p", { class: "note" }, "The App has no totals for this session."),
    );
}

function renderCompactions(view) {
    const list = view.events.compactions;
    return details("compactions", `Compactions (${list.length})`, list.length ? table(
        [["Time"], ["Result"], ["Trigger"], ["Before", true], ["Limit", true], ["Model"], ["AI Credits", true], ["Input", true], ["Output", true], ["Duration", true]],
        list.map((c) => h("tr", {}, h("td", {}, dateTimeText(Date.parse(c.t))), h("td", {}, c.success === null ? "-" : c.success ? "success" : "failed"),
            h("td", {}, c.trigger ?? "-"), num(c.preTokens), num(c.tokenLimit), h("td", {}, c.model ?? "-"), creditsCell(c.nano),
            num(c.input), num(c.output), num(c.durationMs, durationText))),
    ) : h("p", { class: "empty" }, "No compactions recorded."));
}

function renderSubagents(view) {
    const subs = view.agents.filter((a) => a.kind === "subagent");
    return details("subagents", `Sub-agents (${subs.length})`, subs.length ? table(
        [["Agent"], ["Id"], ["Model"], ["Effort"], ["Started"], ["Ended"], ["Outcome"], ["Requests", true], ["AI Credits", true]],
        subs.map((a) => h("tr", {}, h("td", { class: "name", title: a.label }, a.label), h("td", {}, a.shortId ?? "-"),
            h("td", {}, a.models.join(", ") || "-"), h("td", {}, a.efforts.join(", ") || "-"),
            h("td", {}, a.startedAt ? timeText(Date.parse(a.startedAt)) : "-"), h("td", {}, a.endedAt ? timeText(Date.parse(a.endedAt)) : "-"),
            h("td", {}, a.outcome ?? "-"), num(a.requests), creditsCell(a.creditsNano, a.creditsMissing))),
    ) : h("p", { class: "empty" }, "No sub-agents in this session."));
}

function renderSessionFacts(view) {
    const s = view.session;
    const last = view.events.shutdowns.at(-1);
    const appCtx = view.app.context;
    const sourceText = (st) => (st.status === "ok" ? "ok" : `${STATUS_TEXT[st.status] ?? st.status}${st.detail ? ` (${st.detail})` : ""}`);
    return details("session", "Session and sources", facts([
        ["Session id", s.sessionId ?? s.id],
        ["Source", s.source ?? "-"],
        ["Repository", s.repository ? `${s.repository}${s.branch ? `@${s.branch}` : ""}` : "-"],
        ["CLI version", s.copilotVersion],
        ["Started", s.startTime ? dateTimeText(Date.parse(s.startTime)) : "-"],
        ["Initial model / effort / tier", [s.selectedModel, s.reasoningEffort, s.contextTier].filter(Boolean).join(" / ") || "-"],
        ["App model / effort", [view.app.model, view.app.reasoningEffort].filter(Boolean).join(" / ") || "-"],
        ["Turns / user messages", `${intText(view.events.turns)} / ${intText(view.events.userMessages)}`],
        ["Events", `${intText(view.events.eventCount)} (last ${view.events.lastEventAt ? dateTimeText(Date.parse(view.events.lastEventAt)) : "-"})`],
        ["App context now", appCtx ? `${intText(appCtx.current)} / ${intText(appCtx.inputLimit)} (system ${intText(appCtx.system)}, conversation ${intText(appCtx.conversation)}, tools ${intText(appCtx.toolDefinitions)}, MCP ${intText(appCtx.mcpTools)}, buffer ${intText(appCtx.buffer)})` : "-"],
        ["Last shutdown", last ? `${dateTimeText(Date.parse(last.t))} · ${last.shutdownType ?? "-"} · ${last.nano === null ? "-" : creditsText(last.nano)} AI Credits · +${intText(last.linesAdded)}/-${intText(last.linesRemoved)} lines in ${intText(last.filesModified)} file(s)` : "-"],
        ["Request history", sourceText(view.sources.history)],
        ["Session events", sourceText(view.sources.events)],
        ["App data", sourceText(view.sources.app)],
        ["Duplicates excluded", `${intText(view.totals.duplicateRowsExcluded)} — identical rows are counted once; distinct calls with identical recorded values cannot be told apart and are also counted once.`],
        ["Read at", dateTimeText(Date.parse(view.readAt))],
    ]));
}

// ---- sources (読み取り元の追加・削除。パスを表示するのはこの設定欄だけ) ------------------

const SOURCE_ERRORS = {
    invalid_path: "Enter an absolute path.",
    already_added: "That Copilot home is already in the list.",
    too_many_sources: "Too many sources are saved.",
    settings_unreadable: "The saved settings could not be read, so nothing was changed.",
    save_failed: "The settings could not be saved.",
    not_found: "That source is no longer in the list.",
    not_editable: "Sources cannot be changed in this mode.",
};

function setSourcesMessage(text, isError = false) {
    const el = $("sources-message");
    el.textContent = text;
    el.classList.toggle("error", isError);
}

async function loadSettings() {
    try {
        state.settings = await api("api/settings");
    } catch {
        state.settings = null;
    }
    $("sources-toggle").hidden = state.settings === null;
    renderSources();
}

function sourceStatusText(id) {
    const st = state.sources.find((src) => src.id === id)?.status;
    if (!st) return "not checked yet";
    return st.status === "ok" ? "readable" : `${STATUS_TEXT[st.status] ?? "could not be read"}${st.detail ? ` (${st.detail})` : ""}`;
}

function renderSources() {
    const settings = state.settings;
    if (!settings) return;
    $("source-form").hidden = !settings.editable || settings.status.status !== "ok";
    const rows = settings.sources.map((src) => h("li", { class: "source-row" },
        h("div", { class: "source-info" },
            h("strong", {}, src.label),
            h("span", { class: "note" }, ` · ${src.isPrimary ? "default" : src.kind === "wsl" ? "WSL" : "folder"} · ${sourceStatusText(src.id)}`),
            h("div", { class: "source-path", title: src.path }, src.path)),
        settings.editable && !src.isPrimary
            ? h("button", { type: "button", "data-focus": `remove:${src.id}`, "aria-label": `Remove ${src.label}`, onclick: () => removeSource(src) }, "Remove")
            : null));
    $("sources-list").replaceChildren(h("ul", { class: "source-list" }, rows));
    if (settings.status.status !== "ok") {
        setSourcesMessage(`The saved source list could not be used (${settings.status.status}); only the default source is read. Adding a source is disabled until the file is fixed.`, true);
    }
}

function setSourcesOpen(open) {
    $("sources-panel").hidden = !open;
    $("sources-toggle").setAttribute("aria-expanded", String(open));
    if (open) {
        loadSettings();
        $("source-path").focus();
    }
}

// 追加・削除のあとで一覧と選択を整える。選んでいたセッションの source が無くなったら、選択を server の既定へ戻す。
async function afterSourcesChanged() {
    await loadSessions();
    await loadSettings();
    if (state.sessionId?.includes("~")) {
        const sourceId = state.sessionId.split("~")[0];
        if (!state.sources.some((src) => src.id === sourceId)) {
            state.sessionId = null;
            state.view = null;
            state.signature = null;
        }
    }
    load({ force: true });
}

async function addSource(event) {
    event.preventDefault();
    const input = $("source-path");
    const path = input.value.trim();
    if (!path) {
        setSourcesMessage(SOURCE_ERRORS.invalid_path, true);
        return;
    }
    $("source-add").disabled = true;
    try {
        await api("api/sources", {}, "POST", { path });
        input.value = "";
        setSourcesMessage("Added. Sessions of the new source appear in the list; a source that cannot be reached is shown as unreadable.");
        await afterSourcesChanged();
    } catch (error) {
        setSourcesMessage(SOURCE_ERRORS[error.message] ?? `Could not add the source (${error.message}).`, true);
    } finally {
        $("source-add").disabled = false;
    }
}

async function removeSource(src) {
    try {
        await api("api/sources/remove", { id: src.id }, "POST");
        setSourcesMessage(`Removed ${src.label}.`);
        await afterSourcesChanged();
    } catch (error) {
        setSourcesMessage(SOURCE_ERRORS[error.message] ?? `Could not remove the source (${error.message}).`, true);
    }
}

// ---- startup ---------------------------------------------------------------

async function init() {
    $("refresh").addEventListener("click", () => {
        loadSessions();
        load({ force: true });
    });
    $("auto").addEventListener("change", scheduleAuto);
    $("session-select").addEventListener("change", (event) => selectSession(event.target.value));
    $("sessions-toggle").addEventListener("click", () => setBrowserOpen($("session-browser").hidden));
    $("sources-toggle").addEventListener("click", () => setSourcesOpen($("sources-panel").hidden));
    $("source-form").addEventListener("submit", addSource);
    $("sources-panel").addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            setSourcesOpen(false);
            $("sources-toggle").focus();
        }
    });
    const b = state.browser;
    $("browser-q").addEventListener("input", (event) => { b.q = event.target.value; b.page = 0; renderBrowser(); });
    $("browser-from").addEventListener("change", (event) => { b.from = event.target.value; b.page = 0; renderBrowser(); });
    $("browser-to").addEventListener("change", (event) => { b.to = event.target.value; b.page = 0; renderBrowser(); });
    $("browser-clear").addEventListener("click", () => {
        Object.assign(b, { q: "", from: "", to: "", page: 0 });
        $("browser-q").value = "";
        $("browser-from").value = "";
        $("browser-to").value = "";
        renderBrowser();
    });
    $("session-browser").addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            setBrowserOpen(false);
            $("sessions-toggle").focus();
        }
    });
    let lastWidth = 0;
    new ResizeObserver((entries) => {
        const w = Math.round(entries[0].contentRect.width);
        if (w !== lastWidth) {
            lastWidth = w;
            keepFocus(drawCharts);
        }
    }).observe($("content"));
    // 初回の選択は load の中で server から取得する。失敗しても Retry / Refresh / 定期処理で取り直せる。
    loadSessions();
    loadSettings();
    await load();
    scheduleAuto();
}

init();
