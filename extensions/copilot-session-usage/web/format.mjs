// 表示用の数値整形。Node (Agent 向け要約) とブラウザ (canvas) の両方から使う。DOM に依存しない。

const NANO_PER_CREDIT = 1_000_000_000n;
// 参考換算: 1 AI Credit = 0.01 USD (Python 側 AI_CREDIT_USD_RATE と同じ値)。
const USD_CENTS_PER_CREDIT = 1n;

// nano AIU (整数文字列) を AI Credits の 10 進文字列へ。小数は最大 9 桁、末尾の 0 を削って最低 3 桁 (既存 report と同じ規則)。
export function creditsText(nano) {
    if (nano === null || nano === undefined) return null;
    const value = BigInt(nano);
    const sign = value < 0n ? "-" : "";
    const abs = value < 0n ? -value : value;
    const integer = (abs / NANO_PER_CREDIT).toLocaleString("en-US");
    const fraction = (abs % NANO_PER_CREDIT).toString().padStart(9, "0").replace(/0+$/, "").padEnd(3, "0");
    return `${sign}${integer}.${fraction}`;
}

// カード用に小数 2 桁へ四捨五入した AI Credits。完全な値は creditsText で別に示す。
export function creditsRounded(nano, digits = 2) {
    if (nano === null || nano === undefined) return "-";
    const value = BigInt(nano);
    const sign = value < 0n ? "-" : "";
    const abs = value < 0n ? -value : value;
    const unit = NANO_PER_CREDIT / 10n ** BigInt(digits);
    const rounded = (abs + unit / 2n) / unit;
    const scale = 10n ** BigInt(digits);
    const fraction = digits > 0 ? `.${(rounded % scale).toString().padStart(digits, "0")}` : "";
    return `${sign}${(rounded / scale).toLocaleString("en-US")}${fraction}`;
}

// 参考 USD (セント単位で丸め)。
export function usdText(nano) {
    if (nano === null || nano === undefined) return "-";
    const cents = (BigInt(nano) * USD_CENTS_PER_CREDIT + NANO_PER_CREDIT / 2n) / NANO_PER_CREDIT;
    const sign = cents < 0n ? "-" : "";
    const abs = cents < 0n ? -cents : cents;
    return `${sign}$${(abs / 100n).toLocaleString("en-US")}.${(abs % 100n).toString().padStart(2, "0")}`;
}

// グラフ用の近似値 (number)。合計の表示には使わない。
export function creditsNumber(nano) {
    return nano === null || nano === undefined ? null : Number(BigInt(nano)) / 1e9;
}

// 整数の桁区切り。null は "-" (欠落を 0 と表示しない)。
export function intText(value) {
    return typeof value === "number" && Number.isFinite(value) ? Math.round(value).toLocaleString("en-US") : "-";
}

// 1.2k / 3.4M 形式。完全な値は title などで併記する。
export function compactText(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) return "-";
    const abs = Math.abs(value);
    const units = [[1e9, "B"], [1e6, "M"], [1e3, "k"]];
    for (const [size, suffix] of units) {
        if (abs >= size) {
            const scaled = value / size;
            return `${scaled.toFixed(Math.abs(scaled) >= 100 ? 0 : Math.abs(scaled) >= 10 ? 1 : 2)}${suffix}`;
        }
    }
    return `${Math.round(value)}`;
}

function pad(n) {
    return String(n).padStart(2, "0");
}

// 時刻 (ローカル時刻 HH:MM:SS)。
export function timeText(t) {
    if (t === null || t === undefined) return "-";
    const d = new Date(t);
    return Number.isNaN(d.getTime()) ? "-" : `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

// 日時 (ローカル時刻 YYYY-MM-DD HH:MM:SS)。
export function dateTimeText(t) {
    if (t === null || t === undefined) return "-";
    const d = new Date(t);
    if (Number.isNaN(d.getTime())) return "-";
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${timeText(d.getTime())}`;
}

// 所要時間 (ms → "1h 02m" / "3m 04s" / "5.2s")。
export function durationText(ms) {
    if (typeof ms !== "number" || !Number.isFinite(ms)) return "-";
    if (ms < 1000) return `${Math.round(ms)}ms`;
    const s = ms / 1000;
    if (s < 60) return `${s.toFixed(1)}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ${pad(Math.floor(s % 60))}s`;
    return `${Math.floor(m / 60)}h ${pad(m % 60)}m`;
}

// 比率 (%)。分母が無ければ "-"。
export function percentText(part, whole) {
    if (typeof part !== "number" || typeof whole !== "number" || whole <= 0) return "-";
    return `${((part / whole) * 100).toFixed(1)}%`;
}
