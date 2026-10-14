// Copilot CLI の記録の置き場所と、外部から受け取るセッション ID の検証。
import { homedir } from "node:os";
import { join } from "node:path";

// セッション ID はパスの一部になるため、区切り文字や `..` を含む値を拒否する。
export const SESSION_ID_PATTERN = "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$";
// 追加した source のセッションは `<source id>~<session id>` で指す (source id は sources.mjs の sourceIdOf)。
export const SESSION_REF_PATTERN = "^(s[0-9a-f]{8}~)?[A-Za-z0-9][A-Za-z0-9._-]{0,127}$";
const SESSION_ID_REGEX = new RegExp(SESSION_ID_PATTERN);

// パス走査に使えない ID だけを受け付ける。
export function isValidSessionId(value) {
    return typeof value === "string" && SESSION_ID_REGEX.test(value) && !value.includes("..");
}

// COPILOT_HOME が未設定なら CLI と同じ既定 (~/.copilot) を使う。
export function resolveCopilotHome(env = process.env) {
    const configured = env.COPILOT_HOME;
    return configured && configured.trim() ? configured : join(homedir(), ".copilot");
}

/**
 * 1 セッション分の読み取り元。絶対パスは extension 内だけで使い、画面へは出さない。
 * @typedef {{ historyDb: string, eventsFile: string, appDb: string }} SourcePaths
 */

// 3 つの読み取り元のパスを組み立てる。
export function sourcePaths(copilotHome, sessionId) {
    if (!isValidSessionId(sessionId)) {
        throw new Error("Invalid session id.");
    }
    return {
        historyDb: join(copilotHome, "session-store.db"),
        eventsFile: join(copilotHome, "session-state", sessionId, "events.jsonl"),
        appDb: join(copilotHome, "data.db"),
    };
}
