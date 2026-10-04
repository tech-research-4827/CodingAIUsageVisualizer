from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go
import plotly.io as pio
from jinja2 import Environment, FileSystemLoader, select_autoescape
from plotly.offline import get_plotlyjs

from copilot_usage_visualizer.formatting import UsageValueFormatter


# Plotly chart HTML を作成する。
class PlotlyChartBuilder:
    """Report summary から Plotly chart fragments を生成する。"""

    # model pie chart を作成する。
    def model_pie(self, summary: dict[str, object]) -> str:
        """model rows から pie chart fragment を返す。"""

        model_rows: list[dict[str, object]] = [
            row
            for row in self._rows(summary, "model_rows")
            if row.get("row_kind") == "model" and self._float_value(row, "ai_credits") > 0
        ]
        figure = go.Figure(
            data=[
                go.Pie(
                    # 実モデル名と推定/不明ラベルが同名でも別区分を保つ。
                    labels=[f"{index + 1}: {row['label']}"
                            for index, row in enumerate(model_rows)],
                    values=[self._float_value(row, "ai_credits") for row in model_rows],
                    hole=0.35,
                )
            ]
        )
        figure.update_layout(margin={"t": 20, "r": 20, "b": 20, "l": 20})
        return pio.to_html(
            figure,
            include_plotlyjs=False,
            full_html=False,
            config={"displaylogo": False, "plotlyServerURL": ""},
        )

    # daily bar chart を作成する。
    def daily_bar(self, summary: dict[str, object]) -> str:
        """daily rows から bar chart fragment を返す。"""

        daily_rows: list[dict[str, object]] = [
            row for row in self._rows(summary, "daily_rows") if row.get("row_kind") == "day"
        ]
        figure = go.Figure(
            data=[
                go.Bar(
                    x=[UsageValueFormatter.daily_label(row) for row in daily_rows],
                    name="Attributed credits",
                    y=[
                        (
                            self._float_value(row, "ai_credits")
                            - self._float_value(row, "unattributed_ai_credits")
                        )
                        if isinstance(row.get("ai_credits"), (int, float))
                        else None
                        for row in daily_rows
                    ],
                ),
                go.Bar(
                    name="Unattributed (incl. estimates)",
                    x=[UsageValueFormatter.daily_label(row) for row in daily_rows],
                    y=[
                        row.get("unattributed_ai_credits", 0)
                        if row.get("ai_credits") is not None
                        else None
                        for row in daily_rows
                    ],
                ),
            ]
        )
        figure.update_layout(
            margin={"t": 20, "r": 20, "b": 40, "l": 60},
            xaxis_title="Date",
            yaxis_title="Known AI Credits",
            barmode="stack",
        )
        return pio.to_html(
            figure,
            include_plotlyjs=False,
            full_html=False,
            config={"displaylogo": False, "plotlyServerURL": ""},
        )

    # summary から rows を取り出す。
    def _rows(self, summary: dict[str, object], key: str) -> list[dict[str, object]]:
        """summary の list[dict] field を返す。"""

        value: object = summary[key]
        if not isinstance(value, list):
            raise ValueError(f"{key} must be a list.")
        rows: list[dict[str, object]] = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError(f"{key} rows must be objects.")
            rows.append(item)
        return rows

    # numeric value を float に変換する。
    def _float_value(self, row: dict[str, object], key: str) -> float:
        """row の numeric field を float として返す。"""

        value: object = row.get(key)
        if isinstance(value, int | float):
            return float(value)
        return 0.0


# offline HTML report rendering を担当する。
class HtmlReportService:
    """Report summary を offline HTML に変換する。"""

    def __init__(self) -> None:
        """template environment と chart builder を初期化する。"""

        template_dir: Path = Path(__file__).parent / "templates"
        self._environment: Environment = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(("html", "j2")),
        )
        self._environment.filters["integer"] = UsageValueFormatter.integer
        self._environment.filters["credits"] = UsageValueFormatter.credits
        self._environment.filters["usd"] = UsageValueFormatter.usd
        self._environment.filters["jpy"] = UsageValueFormatter.jpy
        self._chart_builder: PlotlyChartBuilder = PlotlyChartBuilder()

    # HTML 文字列を生成する。
    def render(self, summary: dict[str, object]) -> str:
        """Report summary から HTML 文字列を返す。"""

        template = self._environment.get_template("offline_report.html.j2")
        return str(
            template.render(
                summary=summary,
                breakdown_note=UsageValueFormatter.BREAKDOWN_NOTE,
                plotly_js=self._offline_plotly_js(),
                third_party_licenses=Path(__file__).with_name(
                    "third_party_licenses.txt"
                ).read_text(encoding="utf-8"),
                model_chart=self._chart_builder.model_pie(summary),
                daily_chart=self._chart_builder.daily_bar(summary),
            )
        )

    # HTML file を生成する。
    def render_to_file(self, summary: dict[str, object], output_path: Path) -> Path:
        """Report summary を output_path へ書き出す。"""

        if output_path.parent and not output_path.parent.exists():
            raise ValueError("output parent directory does not exist.")
        output_path.write_text(self.render(summary), encoding="utf-8")
        return output_path

    # offline 用 Plotly JS を返す。
    def _offline_plotly_js(self) -> str:
        """Plotly bundle を改変せず inline 用 JS として返す。"""

        return str(get_plotlyjs())
