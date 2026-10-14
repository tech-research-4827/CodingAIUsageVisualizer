// canvas action が Agent に返す要約。request 単位の明細は含めず、合計・エージェント・モデル・照合だけを返す。
import { creditsText } from "../web/format.mjs";

// 表示用データから Agent 向けの要約を作る。
export function summarizeForAgent(view) {
    // 履歴を読めない時は、履歴由来の数量を 0 ではなく null (取得できない) で返す。
    const historyOk = view.sources.history.status === "ok";
    const known = (value) => (historyOk ? value : null);
    return {
        sessionId: view.session.id,
        source: view.session.source ?? null,
        isCurrent: view.session.isCurrent,
        repository: view.session.repository,
        branch: view.session.branch,
        readAt: view.readAt,
        sources: view.sources,
        totals: {
            requests: known(view.totals.requests),
            aiCredits: known(creditsText(view.totals.creditsNano)),
            aiCreditsNote: !historyOk
                ? `request history unavailable (${view.sources.history.status})`
                : view.totals.creditsMissing > 0
                    ? `observed subtotal; ${view.totals.creditsMissing} request(s) without credits`
                    : "observed subtotal of request history",
            creditsMissing: known(view.totals.creditsMissing),
            tokens: known(view.totals.tokens),
            tokenRowsInvalid: known(view.totals.tokenRowsInvalid),
            duplicateRowsExcluded: known(view.totals.duplicateRowsExcluded),
            invalidTimestampRows: known(view.totals.invalidTimestampRows),
            firstAt: isoOrNull(view.totals.firstAt),
            lastAt: isoOrNull(view.totals.lastAt),
        },
        context: view.context,
        agents: view.agents.map((a) => ({
            label: a.label,
            kind: a.kind,
            agentId: a.shortId,
            models: a.models,
            efforts: a.efforts,
            requests: a.requests,
            aiCredits: creditsText(a.creditsNano),
            creditsMissing: a.creditsMissing,
            tokens: a.tokens,
            contextMax: a.ctxMax,
            contextLast: a.ctxLast,
            outcome: a.outcome,
        })),
        models: view.models.map((m) => ({
            model: m.model,
            requests: m.requests,
            aiCredits: creditsText(m.creditsNano),
            creditsMissing: m.creditsMissing,
            tokens: m.tokens,
        })),
        reconciliation: {
            cumulativeEvents: view.reconciliation.cumulative.map((c) => ({
                type: c.type,
                at: c.t,
                eventAiCredits: creditsText(c.nano),
                historyAiCreditsAtEvent: creditsText(c.historyNanoAtT),
                referenceDifference: creditsText(c.diffNano),
            })),
            app: view.reconciliation.app ? {
                appAiCredits: creditsText(view.reconciliation.app.appNano),
                historyAiCredits: creditsText(view.reconciliation.app.historyNano),
                referenceDifference: creditsText(view.reconciliation.app.diffNano),
            } : null,
        },
    };
}

function isoOrNull(t) {
    return typeof t === "number" ? new Date(t).toISOString() : null;
}
