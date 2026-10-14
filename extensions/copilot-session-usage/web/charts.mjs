// 外部ライブラリを使わない SVG グラフ (折れ線・積み上げ棒)。色は CSS 変数名で受け取り、凡例は文字で示す。
// ポインタ・矢印キーで点を選ぶと、グラフ下の読み上げ欄 (aria-live) に値を出す。

const SVG_NS = "http://www.w3.org/2000/svg";
const MARGIN = { top: 10, right: 12, bottom: 24, left: 56 };

function svg(tag, attrs = {}) {
    const el = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs)) {
        if (value !== undefined && value !== null) el.setAttribute(key, String(value));
    }
    return el;
}

function html(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
}

// 目盛りに使うきりの良い値を返す。
function niceStep(range, count) {
    const raw = range / Math.max(count, 1);
    const power = 10 ** Math.floor(Math.log10(raw || 1));
    const unit = raw / power;
    const nice = unit >= 5 ? 10 : unit >= 2 ? 5 : unit >= 1 ? 2 : 1;
    return nice * power;
}

// y 軸のスケール。log は 1 未満を 1 に寄せた log10 (0 は軸の下端)。
function yScale(maxValue, height, log) {
    if (log) {
        const top = Math.max(1, Math.ceil(Math.log10(Math.max(maxValue, 10))));
        const map = (v) => height - (Math.log10(Math.max(v, 1)) / top) * height;
        const ticks = [];
        for (let p = 0; p <= top; p += Math.max(1, Math.ceil(top / 5))) ticks.push(10 ** p);
        return { map, ticks };
    }
    const max = maxValue > 0 ? maxValue : 1;
    const step = niceStep(max, 4);
    const top = Math.ceil(max / step) * step;
    const ticks = [];
    for (let v = 0; v <= top + step / 2; v += step) ticks.push(v);
    return { map: (v) => height - (v / top) * height, ticks };
}

function xScale(min, max, width) {
    const span = max - min || 1;
    return (v) => ((v - min) / span) * width;
}

// 軸・格子・枠を描き、描画領域の <g> を返す。
function frame(root, { width, height, yTicks, yMap, yFormat, xTicks, xMap, xFormat }) {
    const g = svg("g", { transform: `translate(${MARGIN.left},${MARGIN.top})` });
    root.append(g);
    for (const tick of yTicks) {
        const y = yMap(tick);
        g.append(svg("line", { x1: 0, x2: width, y1: y, y2: y, class: "grid" }));
        const label = svg("text", { x: -6, y: y + 4, class: "axis-label", "text-anchor": "end" });
        label.textContent = yFormat(tick);
        g.append(label);
    }
    xTicks.forEach((tick, i) => {
        const x = xMap(tick);
        // 端の目盛りは描画領域の外へはみ出さないよう寄せる。
        const anchor = xTicks.length > 1 && i === xTicks.length - 1 ? "end" : "middle";
        const label = svg("text", { x, y: height + 16, class: "axis-label", "text-anchor": anchor });
        label.textContent = xFormat(tick);
        g.append(label);
    });
    g.append(svg("line", { x1: 0, x2: width, y1: height, y2: height, class: "axis" }));
    return g;
}

function xTickValues(min, max, width) {
    const count = Math.max(2, Math.floor(width / 90));
    if (max <= min) return [min];
    const ticks = [];
    for (let i = 0; i <= count; i += 1) ticks.push(min + ((max - min) * i) / count);
    return ticks;
}

// 凡例 (色見本 + 文字)。色だけで区別させない。
function legend(items) {
    const list = html("ul", "legend");
    for (const item of items) {
        const li = html("li");
        const swatch = html("span", `swatch${item.dashed ? " dashed" : ""}${item.marker ? " marker" : ""}`);
        swatch.style.setProperty("--swatch", `var(${item.colorVar})`);
        li.append(swatch, document.createTextNode(item.label));
        list.append(li);
    }
    return list;
}

// 点の識別子。描き直し後に同じ点を選び直すために使う。
function pointKey(p) {
    return p.from ? `bar|${p.from.i ?? ""}` : `${p.series ?? ""}|${p.x}`;
}

// ポインタ・キーボードで選んだ点を強調し、説明文を読み上げ欄へ出す。
// selection があれば選択を呼び出し側へ知らせ、描き直し後も同じ点を選び直す。
function attachCursor(container, svgRoot, plot, height, points, describe, selection) {
    const readout = html("div", "readout");
    readout.setAttribute("aria-live", "polite");
    readout.textContent = points.length ? "Point at the chart or use arrow keys to inspect values." : "";
    const cursor = svg("line", { y1: 0, y2: height, class: "cursor", visibility: "hidden" });
    plot.append(cursor);
    let index = -1;
    const select = (i) => {
        if (!points.length) return;
        index = Math.max(0, Math.min(points.length - 1, i));
        const p = points[index];
        cursor.setAttribute("x1", p.px);
        cursor.setAttribute("x2", p.px);
        cursor.setAttribute("visibility", "visible");
        readout.textContent = describe(p);
        selection?.onChange(pointKey(p));
    };
    svgRoot.addEventListener("pointermove", (event) => {
        if (!points.length) return;
        const rect = svgRoot.getBoundingClientRect();
        const scale = rect.width ? Number(svgRoot.getAttribute("width")) / rect.width : 1;
        const x = (event.clientX - rect.left) * scale - MARGIN.left;
        let best = 0;
        for (let i = 1; i < points.length; i += 1) {
            if (Math.abs(points[i].px - x) < Math.abs(points[best].px - x)) best = i;
        }
        select(best);
    });
    svgRoot.addEventListener("keydown", (event) => {
        const step = event.shiftKey ? 10 : 1;
        if (event.key === "ArrowRight") select(index < 0 ? 0 : index + step);
        else if (event.key === "ArrowLeft") select(index < 0 ? points.length - 1 : index - step);
        else if (event.key === "Home") select(0);
        else if (event.key === "End") select(points.length - 1);
        else return;
        event.preventDefault();
    });
    container.append(readout);
    if (selection?.key) {
        const i = points.findIndex((p) => pointKey(p) === selection.key);
        if (i >= 0) select(i);
    }
}

/**
 * 折れ線グラフ。
 * @param {HTMLElement} container
 * @param {{
 *   title: string, width: number, height?: number, log?: boolean,
 *   xFormat: (x: number) => string, yFormat: (y: number) => string,
 *   series: { label: string, colorVar: string, dashed?: boolean, step?: boolean,
 *             points: { x: number, y: number, ref?: any }[] }[],
 *   markers?: { label: string, colorVar: string, points: { x: number, y: number, ref?: any }[] }[],
 *   describe: (point: { x: number, y: number, ref?: any, series: string }) => string,
 *   emptyText: string, note?: string | null,
 *   selection?: { key: string | null, onChange: (key: string) => void },
 * }} options
 */
export function renderLineChart(container, options) {
    container.replaceChildren();
    const all = options.series.flatMap((s) => s.points);
    const markerPoints = (options.markers ?? []).flatMap((m) => m.points);
    if (all.length === 0) {
        container.append(html("p", "empty", options.emptyText));
        return;
    }
    const width = Math.max(200, options.width - MARGIN.left - MARGIN.right);
    const height = options.height ?? 160;
    // x の範囲は系列の点で決める (marker は呼び出し側で範囲内に絞る)。
    const xs = all.map((p) => p.x);
    const xMin = Math.min(...xs);
    const xMax = Math.max(...xs);
    const yMax = Math.max(...[...all, ...markerPoints].map((p) => p.y));
    const ys = yScale(yMax, height, options.log);
    const xMap = xScale(xMin, xMax, width);
    const root = svg("svg", {
        width: width + MARGIN.left + MARGIN.right,
        height: height + MARGIN.top + MARGIN.bottom,
        role: "img",
        tabindex: 0,
        "data-focus": `chart:${container.id}`,
        "aria-label": options.title,
        class: "chart",
    });
    const plot = frame(root, {
        width, height, yTicks: ys.ticks, yMap: ys.map, yFormat: options.yFormat,
        xTicks: xTickValues(xMin, xMax, width), xMap, xFormat: options.xFormat,
    });
    const cursorPoints = [];
    for (const series of options.series) {
        if (series.points.length === 0) continue;
        let d = "";
        series.points.forEach((p, i) => {
            const x = xMap(p.x);
            const y = ys.map(p.y);
            if (i === 0) d += `M${x},${y}`;
            else if (series.step) d += `H${x}V${y}`;
            else d += `L${x},${y}`;
            cursorPoints.push({ ...p, px: x, series: series.label });
        });
        const path = svg("path", { d, class: `line${series.dashed ? " dashed" : ""}` });
        path.style.setProperty("--stroke", `var(${series.colorVar})`);
        plot.append(path);
        if (series.points.length === 1) {
            const dot = svg("circle", { cx: xMap(series.points[0].x), cy: ys.map(series.points[0].y), r: 3, class: "dot" });
            dot.style.setProperty("--fill", `var(${series.colorVar})`);
            plot.append(dot);
        }
    }
    for (const marker of options.markers ?? []) {
        for (const p of marker.points) {
            const x = xMap(p.x);
            const y = ys.map(p.y);
            const shape = svg("path", { d: `M${x},${y - 5}L${x + 5},${y}L${x},${y + 5}L${x - 5},${y}Z`, class: "marker" });
            shape.style.setProperty("--fill", `var(${marker.colorVar})`);
            plot.append(shape);
            cursorPoints.push({ ...p, px: x, series: marker.label });
        }
    }
    cursorPoints.sort((a, b) => a.px - b.px);
    container.append(root);
    if (options.note) container.append(html("p", "note", options.note));
    container.append(legend([
        ...options.series.map((s) => ({ label: s.label, colorVar: s.colorVar, dashed: s.dashed })),
        ...(options.markers ?? []).map((m) => ({ label: m.label, colorVar: m.colorVar, marker: true })),
    ]));
    attachCursor(container, root, plot, height, cursorPoints, options.describe, options.selection);
}

/**
 * 積み上げ棒グラフ。棒が描画幅に収まらない時は連続する request をまとめて 1 本にする。
 * @param {HTMLElement} container
 * @param {{
 *   title: string, width: number, height?: number,
 *   parts: { label: string, colorVar: string }[],
 *   bars: { values: (number | null)[], ref: any }[],
 *   yFormat: (y: number) => string, xFormat: (bar: { from: any, to: any }) => string,
 *   describe: (bar: { from: any, to: any, values: number[], count: number, invalid: number }) => string,
 *   emptyText: string,
 *   selection?: { key: string | null, onChange: (key: string) => void },
 * }} options
 */
export function renderStackedBars(container, options) {
    container.replaceChildren();
    if (options.bars.length === 0) {
        container.append(html("p", "empty", options.emptyText));
        return;
    }
    const width = Math.max(200, options.width - MARGIN.left - MARGIN.right);
    const height = options.height ?? 160;
    const maxBars = Math.max(10, Math.floor(width / 3));
    const size = Math.ceil(options.bars.length / maxBars);
    const groups = [];
    for (let i = 0; i < options.bars.length; i += size) {
        const slice = options.bars.slice(i, i + size);
        const values = options.parts.map((_, k) => slice.reduce((sum, bar) => sum + (bar.values[k] ?? 0), 0));
        const invalid = slice.filter((bar) => bar.values.some((v) => v === null)).length;
        groups.push({ from: slice[0].ref, to: slice.at(-1).ref, values, count: slice.length, invalid });
    }
    const totals = groups.map((g) => g.values.reduce((a, b) => a + b, 0));
    const ys = yScale(Math.max(...totals), height, false);
    const band = width / groups.length;
    const root = svg("svg", {
        width: width + MARGIN.left + MARGIN.right,
        height: height + MARGIN.top + MARGIN.bottom,
        role: "img",
        tabindex: 0,
        "data-focus": `chart:${container.id}`,
        "aria-label": options.title,
        class: "chart",
    });
    const tickIndexes = xTickValues(0, groups.length - 1, width).map((v) => Math.round(v));
    const plot = frame(root, {
        width, height, yTicks: ys.ticks, yMap: ys.map, yFormat: options.yFormat,
        xTicks: [...new Set(tickIndexes)], xMap: (i) => i * band + band / 2,
        xFormat: (i) => options.xFormat(groups[i]),
    });
    const cursorPoints = [];
    groups.forEach((group, i) => {
        let base = height;
        const x = i * band + Math.max(0, band * 0.1);
        const w = Math.max(1, band * 0.8);
        group.values.forEach((value, k) => {
            if (value <= 0) return;
            const h = height - ys.map(value);
            const rect = svg("rect", { x, y: base - h, width: w, height: h, class: "bar" });
            rect.style.setProperty("--fill", `var(${options.parts[k].colorVar})`);
            plot.append(rect);
            base -= h;
        });
        cursorPoints.push({ ...group, px: i * band + band / 2 });
    });
    container.append(root);
    if (size > 1) {
        container.append(html("p", "note", `Each bar sums ${size} consecutive requests (${options.bars.length} requests).`));
    }
    container.append(legend(options.parts));
    attachCursor(container, root, plot, height, cursorPoints, options.describe, options.selection);
}
