from __future__ import annotations

from collections.abc import Mapping
from html import escape

from copilot_usage_visualizer.formatting import UsageValueFormatter


# Agent 可読な Markdown report rendering を担当する。
class MarkdownReportService:
    """report_summary を装飾なしの Markdown レポートへ変換する。

    HTML report と同じデータ source（build_report_summary）を扱い、Plotly / CSS を
    伴わない見出し + table 表現を返す。数値整形は HTML と同じ UsageValueFormatter を
    再利用する。
    """

    # Markdown 文字列を生成する。
    def render(self, summary: Mapping[str, object], include_detail: bool) -> str:
        """report_summary から Markdown レポート文字列を返す。"""

        sections: list[str] = [
            "# Copilot Usage Report",
            self._meta_section(summary),
        ]
        sections.append(str(summary.get("coverage", "")))
        sections.append(str(summary.get("data_status", "")))
        totals = summary.get("totals", {})
        if isinstance(totals, dict):
            sections.append(
                "Known direct subtotal; missing credits records: "
                f"{totals.get('missing_credits_count', 0)}"
            )
        sections.extend(
            [
                self._totals_section(summary),
                self._model_section(summary),
                self._daily_section(summary),
            ]
        )
        if include_detail:
            sections.append(self._detail_section(summary))
        sections.append(self._model_section(summary, breakdown=True))
        sections.append(self._confidence_section(summary))
        sections.append(self._diagnostics_section(summary))
        return "\n\n".join(sections) + "\n"

    # meta セクションを作成する。
    def _meta_section(self, summary: Mapping[str, object]) -> str:
        """日付範囲・rate・policy を bullet list で返す。"""

        currency: Mapping[str, object] = self._mapping(summary, "currency")
        policies: Mapping[str, object] = self._mapping(summary, "run_selection_policies")
        lines: list[str] = [
            "## Meta",
            "",
            f"- Date range: {self._text(summary, 'date_from')} to {self._text(summary, 'date_to')}",
            f"- Generated at: {self._text(summary, 'generated_at')}",
            f"- AI Credit USD rate: {self._text(currency, 'ai_credit_usd_rate')}",
            f"- USD/JPY rate: {self._text(currency, 'usd_jpy_rate')}",
            f"- Copilot CLI policy: {self._text(policies, 'copilot_cli')}",
            f"- Copilot Chat policy: {self._text(policies, 'copilot_chat')}",
            f"- {summary.get('cli_attribution', '')}",
            f"- {self._cell(str(summary.get('primary_model_attribution', '')))}",
            "- Copilot Chat: request-level attribution.",
        ]
        return "\n".join(lines)

    # totals セクションを作成する。
    def _totals_section(self, summary: Mapping[str, object]) -> str:
        """totals を 2-column table で返す（acceptance の独立 totals section）。"""

        totals: Mapping[str, object] = self._mapping(summary, "totals")
        rows: list[list[str]] = [
            ["JP¥", UsageValueFormatter.jpy(totals.get("estimated_jpy"))],
            ["US$", UsageValueFormatter.usd(totals.get("estimated_usd"))],
            ["AI Credits", UsageValueFormatter.credits(totals.get("ai_credits"))],
            ["Direct Nano AIU", UsageValueFormatter.integer(totals.get("direct_nano_aiu"))],
            ["Raw Input", UsageValueFormatter.integer(totals.get("raw_input_tokens"))],
            ["Fresh Input", UsageValueFormatter.integer(totals.get("fresh_input_tokens"))],
            ["Cached Input", UsageValueFormatter.integer(totals.get("cached_input_tokens"))],
            ["Cache Write", UsageValueFormatter.integer(totals.get("cache_write_tokens"))],
            ["Output", UsageValueFormatter.integer(totals.get("output_tokens"))],
            ["Reasoning", UsageValueFormatter.integer(totals.get("reasoning_tokens"))],
        ]
        return "## Totals\n\n" + self._render_table(["Metric", "Value"], rows)

    # model セクションを作成する。
    def _model_section(self, summary: Mapping[str, object], breakdown: bool = False) -> str:
        """model 別 usage table を返す（先頭 total row は HTML parity の冗長表示）。"""

        identity_headers: list[str] = (
            ["Model", "Source", "Kind", "Attribution", "Raw Model", "Estimated Model", "Status"]
            if breakdown else ["Model"]
        )
        headers: list[str] = identity_headers + [
            "JP¥", "US$", "AI Credits", "Estimated Credits", "Missing credits",
            "Direct Nano AIU", "Raw Input", "Fresh Input", "Cached Input", "Cache Write",
            "Output", "Reasoning", "Confidence",
        ]
        keys: list[str] = (
            ["display_model", "source", "kind", "attribution", "raw_model_key",
             "estimated_primary_model", "inclusion_status"] if breakdown else ["display_model"]
        )
        rows: list[list[str]] = [
            [*[self._text(row, key) for key in keys], *self._currency_cells(row),
             UsageValueFormatter.credits(row.get("estimated_primary_ai_credits")),
             UsageValueFormatter.integer(row.get("missing_credits_count")),
             UsageValueFormatter.integer(row.get("direct_nano_aiu")),
             *self._token_cells(row), self._text(row, "confidence")]
            for row in self._rows(summary, "model_breakdown" if breakdown else "model_rows")
        ]
        title: str = "Model Breakdown" if breakdown else "Model Summary"
        note: str = (UsageValueFormatter.BREAKDOWN_NOTE if breakdown
                     else UsageValueFormatter.PRIMARY_MODEL_NOTE)
        return f"## {title}\n\n{self._cell(note)}\n\n" + self._render_table(headers, rows)

    # daily セクションを作成する。
    def _daily_section(self, summary: Mapping[str, object]) -> str:
        """日次 usage table を返す。"""

        headers: list[str] = [
            "Date",
            "Model",
            "JP¥",
            "US$",
            "AI Credits",
            "Missing credits",
            "Unattrib.",
            "Direct Nano AIU",
            "Raw Input",
            "Fresh Input",
            "Cached Input",
            "Cache Write",
            "Output",
            "Reasoning",
        ]
        rows: list[list[str]] = [
            [
                self._text(row, "date"),
                self._text(row, "display_model"),
                *self._currency_cells(row),
                UsageValueFormatter.integer(row.get("missing_credits_count")),
                UsageValueFormatter.credits(row.get("unattributed_ai_credits")),
                UsageValueFormatter.integer(row.get("direct_nano_aiu")),
                *self._token_cells(row),
            ]
            for row in self._rows(summary, "daily_rows")
        ]
        return ("## Daily Usage\n\n" + self._cell(UsageValueFormatter.PRIMARY_MODEL_NOTE)
                + "\n\n" + self._render_table(headers, rows))

    # detail セクションを作成する。
    def _detail_section(self, summary: Mapping[str, object]) -> str:
        """workspace / session / run / model 単位の明細 table を返す。"""

        headers: list[str] = [
            "Workspace",
            "Timestamp",
            "Session",
            "Model",
            "Source",
            "JP¥",
            "US$",
            "AI Credits",
            "Nano AIU",
            "Raw Input",
            "Fresh Input",
            "Cached Input",
            "Cache Write",
            "Output",
            "Reasoning",
            "Run",
            "Run Kind",
            "Policy",
            "Credits Source",
            "Confidence",
        ]
        rows: list[list[str]] = [
            [
                self._text(row, "workspace_alias"),
                self._text(row, "timestamp"),
                self._text(row, "session_id"),
                self._text(row, "display_model"),
                self._text(row, "source"),
                *self._currency_cells(row),
                UsageValueFormatter.integer(row.get("reported_nano_aiu")),
                *self._token_cells(row),
                self._text(row, "run_id"),
                self._text(row, "run_kind"),
                self._text(row, "run_selection_policy"),
                self._text(row, "ai_credits_source"),
                self._text(row, "confidence"),
            ]
            for row in self._rows(summary, "detail_rows")
        ]
        return "## Detail\n\n" + self._render_table(headers, rows)

    # confidence セクションを作成する。
    def _confidence_section(self, summary: Mapping[str, object]) -> str:
        """confidence 別 breakdown table を返す。"""

        headers: list[str] = [
            "Confidence",
            "Coarse",
            "Records",
            "AI Credits",
            "Direct Nano AIU",
            "Missing credits",
        ]
        rows: list[list[str]] = [
            [
                self._text(row, "confidence"),
                self._text(row, "coarse_confidence"),
                UsageValueFormatter.integer(row.get("records_count")),
                UsageValueFormatter.credits(row.get("ai_credits")),
                UsageValueFormatter.integer(row.get("direct_nano_aiu")),
                UsageValueFormatter.integer(row.get("missing_credits_count")),
            ]
            for row in self._rows(summary, "confidence_breakdown")
        ]
        return "## Confidence Breakdown\n\n" + self._render_table(headers, rows)

    # diagnostics セクションを作成する。
    def _diagnostics_section(self, summary: Mapping[str, object]) -> str:
        """diagnostic 件数 summary を返す。"""

        diagnostics: Mapping[str, object] = self._mapping(summary, "diagnostics")
        by_code: list[Mapping[str, object]] = self._rows(diagnostics, "by_code")
        rows: list[list[str]] = [
            [
                self._text(row, "code"),
                UsageValueFormatter.integer(row.get("count")),
            ]
            for row in by_code
        ]
        warnings: str = self._text(diagnostics, "warnings_count")
        table: str = self._render_table(["Code", "Count"], rows)
        return f"## Diagnostics\n\nWarnings: {warnings}\n\n{table}"

    # currency cells を作成する。
    def _currency_cells(self, row: Mapping[str, object]) -> list[str]:
        """JP¥ / US$ / AI Credits の整形済みセルを返す。"""

        return [
            UsageValueFormatter.jpy(row.get("estimated_jpy")),
            UsageValueFormatter.usd(row.get("estimated_usd")),
            UsageValueFormatter.credits(row.get("ai_credits")),
        ]

    # token cells を作成する。
    def _token_cells(self, row: Mapping[str, object]) -> list[str]:
        """raw / fresh / cached / cache write / output / reasoning の整形済みセルを返す。"""

        return [
            UsageValueFormatter.integer(row.get("raw_input_tokens")),
            UsageValueFormatter.integer(row.get("fresh_input_tokens")),
            UsageValueFormatter.integer(row.get("cached_input_tokens")),
            UsageValueFormatter.integer(row.get("cache_write_tokens")),
            UsageValueFormatter.integer(row.get("output_tokens")),
            UsageValueFormatter.integer(row.get("reasoning_tokens")),
        ]

    # Markdown table を作成する。
    def _render_table(self, headers: list[str], rows: list[list[str]]) -> str:
        """header / 区切り / data 行からなる Markdown table 文字列を返す。"""

        header_line: str = "| " + " | ".join(self._cell(value) for value in headers) + " |"
        separator: str = "| " + " | ".join("---" for _ in headers) + " |"
        body_lines: list[str] = [
            "| " + " | ".join(self._cell(value) for value in row) + " |" for row in rows
        ]
        return "\n".join([header_line, separator, *body_lines])

    # table cell を整形する。
    def _cell(self, value: str) -> str:
        """外部モデル名をHTMLタグやMarkdown構文として解釈させない。"""

        text: str = escape(value, quote=False).replace("\n", " ").replace("\r", " ")
        for marker in "\\`*[]|":
            text = text.replace(marker, "\\" + marker)
        return text

    # mapping field の文字列を返す。
    def _text(self, mapping: Mapping[str, object], key: str) -> str:
        """mapping の値を文字列化し、None は '-' で返す。"""

        value: object = mapping.get(key)
        if value is None:
            return "-"
        return str(value)

    # mapping field を取り出す。
    def _mapping(self, summary: Mapping[str, object], key: str) -> Mapping[str, object]:
        """summary の Mapping field を返す。"""

        value: object = summary[key]
        if not isinstance(value, Mapping):
            raise ValueError(f"{key} must be a mapping.")
        return value

    # list[Mapping] field を取り出す。
    def _rows(self, summary: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
        """summary の list[Mapping] field を返す。"""

        value: object = summary[key]
        if not isinstance(value, list):
            raise ValueError(f"{key} must be a list.")
        rows: list[Mapping[str, object]] = []
        for item in value:
            if not isinstance(item, Mapping):
                raise ValueError(f"{key} rows must be mappings.")
            rows.append(item)
        return rows
