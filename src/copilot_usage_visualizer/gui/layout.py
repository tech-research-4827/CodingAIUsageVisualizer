from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import ClassVar

from copilot_usage_visualizer.formatting import UsageValueFormatter


# dict 値を文字列化する。
def _text(key: str) -> Callable[[dict[str, object]], str]:
    """row[key] を文字列で返す render を作る。"""

    return lambda row: "" if row.get(key) is None else str(row.get(key))


# dict 値を整形関数で文字列化する。
def _fmt(key: str, formatter: Callable[[object], str]) -> Callable[[dict[str, object]], str]:
    """row[key] に formatter を適用する render を作る。"""

    return lambda row: formatter(row.get(key))


# dict 値を文字列化し、None は "-" にする。
def _text_dash(key: str) -> Callable[[dict[str, object]], str]:
    """row[key] を文字列で返し、None の場合は "-" を返す render を作る。"""

    return lambda row: "-" if row.get(key) is None else str(row.get(key))


# table column の表示仕様を保持する。
@dataclass(frozen=True)
class ColumnSpec:
    """table column の header、row 値の整形関数、数値列かどうかを保持する。"""

    header: str
    render: Callable[[dict[str, object]], str]
    numeric: bool = False


# table の静的設定を保持する。
@dataclass(frozen=True)
class TableSpec:
    """1 つの table に対する dearpygui 設定と data source を保持する。"""

    tag: str
    columns: tuple[ColumnSpec, ...]
    source: str
    freeze_columns: int = 1
    clipper: bool = False
    expects_total: bool = False
    restores_body_style: bool = True
    height: int = 240
    residual: bool = False

    # 数値列の index を返す。
    def numeric_column_indices(self) -> tuple[int, ...]:
        """`numeric=True` の column index を宣言順で返す。"""

        return tuple(index for index, column in enumerate(self.columns) if column.numeric)


# タブの高さ配分を保持する。
@dataclass(frozen=True)
class TabLayoutSpec:
    """タブの固定高見積りと、残余領域を消費する widget を保持する。

    残余消費 widget は table に限らないため、tag だけで表現する
    （設定タブは Log の入力欄が消費者になる）。
    """

    # tab bar と window / child padding の合計見積り。
    CHROME_HEIGHT: ClassVar[int] = 88
    # 残余消費 widget に保証する最低実用高。
    MIN_RESIDUAL_HEIGHT: ClassVar[int] = 160
    # 本文 font の設計値 (`DashboardTheme.FONT_SIZE_BODY`)。
    # layout は dearpygui へ依存しないため複製し、テストで一致を保つ。
    BODY_FONT_PX: ClassVar[int] = 16

    label: str
    fixed_height: int
    residual_widget_tag: str
    # fixed_height のうち文字の行高で決まる分を、本文 font の行数で表す (文字の大きさで伸びる)。
    font_lines: float = 0.0

    # 残余高を求める。
    def residual_height(
        self, viewport_height: int, scale: float = 1.0, font_factor: float = 1.0
    ) -> int:
        """viewport 高 (実寸 px) から残余消費 widget の高さ (実寸 px) を求める。

        固定高・chrome・最低実用高は 100% の設計値なので、表示倍率を掛けてから引く。
        文字の大きさを上げると `font_lines` 行ぶんの高さが本文 font の増分だけ伸びる。
        可用高が最低実用高を下回る場合でも最低実用高を返す。
        その場合は固定 content 合計が可視領域を超えるため、
        タブ本体 child window の縦 scroll で全体を辿れる。
        """

        font_extra: float = self.font_lines * self.BODY_FONT_PX * (font_factor - 1.0)
        available: int = viewport_height - round(
            (self.CHROME_HEIGHT + self.fixed_height + font_extra) * scale
        )
        return max(round(self.MIN_RESIDUAL_HEIGHT * scale), available)

    # 全タブの残余高を求める。
    @classmethod
    def residual_heights(
        cls,
        specs: Sequence[TabLayoutSpec],
        viewport_height: int,
        scale: float = 1.0,
        font_factor: float = 1.0,
    ) -> dict[str, int]:
        """残余消費 widget tag から高さへの対応を返す。

        初期表示と viewport resize の双方がこの 1 つの計算を共有する。
        """

        return {
            spec.residual_widget_tag: spec.residual_height(viewport_height, scale, font_factor)
            for spec in specs
        }


# table 1 つ分の描画計画を保持する。
@dataclass(frozen=True)
class TableRenderPlan:
    """dearpygui へ渡す table 設定と cell 文字列を確定した状態で保持する。

    dearpygui へ触れずに構築できるため、描画設定を headless で検証できる。
    """

    tag: str
    headers: tuple[str, ...]
    cells: tuple[tuple[str, ...], ...]
    numeric_columns: tuple[int, ...]
    total_row_indices: tuple[int, ...]
    freeze_rows: int
    freeze_columns: int
    clipper: bool
    row_background: bool
    restores_body_style: bool
    numeric_alignment: bool
    height: int
    residual: bool

    # 等幅 font を bind する列を返す。
    @property
    def numeric_font_columns(self) -> tuple[int, ...]:
        """等幅 font を bind する列 index。degraded mode では空になる。"""

        return self.numeric_columns if self.numeric_alignment else ()

    # rows から描画計画を構築する。
    @classmethod
    def build(
        cls,
        spec: TableSpec,
        rows: Sequence[dict[str, object]],
        numeric_alignment: bool,
    ) -> TableRenderPlan:
        """Total 行契約を検証し、cell 文字列と dearpygui 設定を確定する。

        `numeric_alignment` は等幅 font の解決結果であり、bool として注入することで
        本 module が dearpygui / theme へ依存しない状態を保つ。
        """

        total_indices: tuple[int, ...] = cls._total_row_indices(rows)
        cls._validate_total_rows(spec, total_indices)
        headers: tuple[str, ...] = tuple(column.header for column in spec.columns)
        cells: tuple[tuple[str, ...], ...] = tuple(
            tuple(column.render(row) for column in spec.columns) for row in rows
        )
        numeric_columns: tuple[int, ...] = spec.numeric_column_indices()
        if numeric_alignment:
            cells = cls._align_numeric_cells(headers, cells, numeric_columns)
        return cls(
            tag=spec.tag,
            headers=headers,
            cells=cells,
            numeric_columns=numeric_columns,
            total_row_indices=tuple(
                index for index, row in enumerate(rows)
                if row.get("row_kind") in {"total", "day"}
            ),
            # header に加え、Total 行があればそれも固定して常時可視にする。
            freeze_rows=2 if spec.expects_total else 1,
            freeze_columns=spec.freeze_columns,
            clipper=spec.clipper,
            row_background=True,
            restores_body_style=spec.restores_body_style,
            numeric_alignment=numeric_alignment,
            height=spec.height,
            residual=spec.residual,
        )

    # Total 行の index を求める。
    @staticmethod
    def _total_row_indices(rows: Sequence[dict[str, object]]) -> tuple[int, ...]:
        """`row_kind == "total"` の行 index を返す。"""

        return tuple(index for index, row in enumerate(rows) if row.get("row_kind") == "total")

    # Total 行契約を検証する。
    @staticmethod
    def _validate_total_rows(spec: TableSpec, total_indices: tuple[int, ...]) -> None:
        """analytics 層が保証する Total 行契約を検証する。

        契約違反は project 内で閉じる仕様不一致であり、描画側で吸収せず
        `ValueError` で顕在化させる。呼び出し元の既存 catch が Log へ出す。
        """

        if spec.expects_total:
            if total_indices != (0,):
                raise ValueError(
                    f"{spec.tag}: expected exactly one leading total row, "
                    f"got indices {total_indices}"
                )
            return
        if total_indices:
            raise ValueError(f"{spec.tag}: unexpected total row at indices {total_indices}")

    # 数値 cell を右端一致させる。
    @classmethod
    def _align_numeric_cells(
        cls,
        headers: tuple[str, ...],
        cells: tuple[tuple[str, ...], ...],
        numeric_columns: tuple[int, ...],
    ) -> tuple[tuple[str, ...], ...]:
        """数値列の文字数を列内で揃え、値の右端 x 座標を一致させる。

        等幅 font を bind した cell に対して行うため、文字数の一致が
        そのまま右端 x 座標の一致になる。header が値より長い場合も、
        余りは列内全行で同一になるため値どうしの右端は揃う。
        """

        if not numeric_columns or not cells:
            return cells
        widths: dict[int, int] = {
            index: cls._column_width(headers[index], cells, index) for index in numeric_columns
        }
        aligned: list[tuple[str, ...]] = []
        for row in cells:
            values: list[str] = list(row)
            for index in numeric_columns:
                values[index] = values[index].rjust(widths[index])
            aligned.append(tuple(values))
        return tuple(aligned)

    # 列の padding 幅を求める。
    @staticmethod
    def _column_width(header: str, cells: tuple[tuple[str, ...], ...], index: int) -> int:
        """header と全値の最大文字数を返す。"""

        return max([len(header), *[len(row[index]) for row in cells]])


# KPI panel の表示値を保持する。
@dataclass(frozen=True)
class KpiValues:
    """モデル概要タブの KPI panel に表示する文字列を保持する。"""

    period: str
    jpy: str
    usd: str
    credits: str

    # 未取得時の初期値を返す。
    @classmethod
    def placeholder(cls) -> KpiValues:
        """描画前および初期 load 失敗時に使う placeholder を返す。"""

        return cls(period="-", jpy="-", usd="-", credits="-")

    # Total 行から構築する。
    @classmethod
    def from_total_row(
        cls, total_row: dict[str, object], date_from: str, date_to: str
    ) -> KpiValues:
        """model_rows の Total 行と集計期間から表示値を作る。

        新しい集計は行わず、summary が持つ値をそのまま整形する。
        """

        missing: object = total_row.get("missing_credits_count", 0)
        credits: str = UsageValueFormatter.credits(total_row.get("ai_credits"))
        if isinstance(missing, int) and missing > 0:
            if total_row.get("known_credits_count") == 0:
                credits = f"unavailable ({missing} missing)"
            else:
                credits += f" (partial; {missing} missing)"

        return cls(
            period=f"{date_from} 〜 {date_to}",
            jpy=UsageValueFormatter.jpy(total_row.get("estimated_jpy")),
            usd=UsageValueFormatter.usd(total_row.get("estimated_usd")),
            credits=credits,
        )


# 1 回分の全描画計画を保持する。
@dataclass(frozen=True)
class DashboardRenderPlan:
    """table / chart / KPI / diagnostics の描画内容を確定した状態で保持する。

    dearpygui item を変更する前にこの plan を構築し、構築が成功した場合だけ
    適用する。これにより検証失敗時に新旧表示が混在しない。
    """

    # 棒グラフ上限に与える余白比率。
    AXIS_HEADROOM: ClassVar[float] = 1.15
    # 棒の幅（X 軸の位置単位）。
    BAR_WEIGHT: ClassVar[float] = 0.5
    # 左右端の棒と plot 端の間に置く余白（X 軸の位置単位）。
    X_AXIS_PADDING: ClassVar[float] = 0.5

    tables: tuple[TableRenderPlan, ...]
    kpi: KpiValues
    diagnostics_summary: str
    daily_positions: tuple[float, ...]
    daily_values: tuple[float, ...]
    daily_unattributed_values: tuple[float, ...]
    daily_ticks: tuple[tuple[str, float], ...]
    daily_axis_upper: float
    daily_axis_x_bounds: tuple[float, float]
    pie_values: tuple[float, ...]
    pie_labels: tuple[str, ...]
    pie_model_names: str

    # 外側凡例が円グラフの幅を使い切らない上限。
    PIE_LABEL_MAX_CHARACTERS: ClassVar[int] = 24

    # report / reference から描画計画を構築する。
    @classmethod
    def build(
        cls,
        report: dict[str, object],
        table_specs: Sequence[TableSpec],
        numeric_alignment: bool,
        date_from: str,
        date_to: str,
    ) -> DashboardRenderPlan:
        """全 table plan と chart / KPI / diagnostics の表示値をまとめて構築する。"""

        tables: tuple[TableRenderPlan, ...] = tuple(
            TableRenderPlan.build(spec, cls._rows_for(spec, report), numeric_alignment)
            for spec in table_specs
        )
        model_rows: list[dict[str, object]] = cls._rows(report, "model_rows")
        daily_rows: list[dict[str, object]] = cls._rows(report, "daily_rows")
        day_rows: list[dict[str, object]] = [
            row for row in daily_rows if row.get("row_kind") == "day"
        ]
        daily_values: tuple[float, ...] = tuple(
            cls._float(row.get("estimated_jpy"))
            for row in day_rows
            if row.get("estimated_jpy") is not None
        )
        pie_rows: list[dict[str, object]] = [
            row
            for row in model_rows
            if row.get("row_kind") == "model" and cls._float(row.get("ai_credits")) > 0
        ]
        return cls(
            tables=tables,
            kpi=cls._kpi_values(model_rows, date_from, date_to),
            diagnostics_summary=cls._diagnostics_summary(report),
            daily_positions=tuple(
                float(index)
                for index, row in enumerate(day_rows)
                if row.get("estimated_jpy") is not None
            ),
            daily_values=daily_values,
            daily_unattributed_values=tuple(
                cls._float(row.get("unattributed_jpy"))
                for row in day_rows
                if row.get("estimated_jpy") is not None
            ),
            daily_ticks=tuple(
                (
                    UsageValueFormatter.daily_label(row),
                    float(index),
                )
                for index, row in enumerate(day_rows)
            ),
            daily_axis_upper=cls.axis_upper(daily_values),
            daily_axis_x_bounds=cls.axis_x_bounds(len(day_rows)),
            pie_values=tuple(cls._float(row.get("ai_credits")) for row in pie_rows),
            pie_labels=tuple(
                cls._pie_label(index, str(row.get("display_model")))
                for index, row in enumerate(pie_rows)
            ),
            pie_model_names="\n".join(
                f"{index + 1}: {row.get('display_model', '')}"
                for index, row in enumerate(pie_rows)
            )
            or "No positive measured credits.",
        )

    # 凡例の表示だけを短縮し、raw model の識別子には触れない。
    @classmethod
    def _pie_label(cls, index: int, model: str) -> str:
        """番号で短縮後の衝突を防ぎ、完全な名前は 展開欄と表で示す。"""

        label: str = f"{index + 1}: {model}"
        if len(label) <= cls.PIE_LABEL_MAX_CHARACTERS:
            return label
        return label[: cls.PIE_LABEL_MAX_CHARACTERS - 3] + "..."

    # 棒グラフの Y 軸上限を求める。
    @classmethod
    def axis_upper(cls, values: Sequence[float]) -> float:
        """非負データ用の Y 軸上限を返す。

        値が無い場合と全値が 0 以下の場合は 1.0 を返し、
        series の有無にかかわらず下限 0 の軸を設定できるようにする。
        """

        if not values:
            return 1.0
        largest: float = max(values)
        if largest <= 0.0:
            return 1.0
        return largest * cls.AXIS_HEADROOM

    # 棒グラフの X 軸表示範囲を求める。
    @classmethod
    def axis_x_bounds(cls, bar_count: int) -> tuple[float, float]:
        """棒 index 0..n-1 を収め、左右端の棒に余白を与える範囲を返す。

        描画のたびにこの範囲を適用することで、前回表示の pan / zoom 状態が
        残らず、指定期間の data が常に収まる。
        """

        if bar_count <= 0:
            return (0.0, 1.0)
        # 棒の半幅に固定余白を足した分だけ、両端を外へ広げる。
        margin: float = cls.BAR_WEIGHT / 2.0 + cls.X_AXIS_PADDING
        return (-margin, float(bar_count - 1) + margin)

    # table の row を取り出す。
    @classmethod
    def _rows_for(cls, spec: TableSpec, report: dict[str, object]) -> list[dict[str, object]]:
        """`TableSpec.source` に対応する row list を返す。"""

        # diagnostics だけ summary 直下ではなく diagnostics 配下に入る。
        if spec.source == "diagnostics_by_code":
            return cls._rows(cls._as_dict(report.get("diagnostics", {})), "by_code")
        return cls._rows(report, spec.source)

    # KPI 表示値を作る。
    @classmethod
    def _kpi_values(
        cls, model_rows: Sequence[dict[str, object]], date_from: str, date_to: str
    ) -> KpiValues:
        """model_rows の Total 行から KPI 表示値を作る。

        Total 行の存在と位置は `TableRenderPlan` の検証で保証されるため、
        ここでは先頭行をそのまま使う。
        """

        if not model_rows:
            return KpiValues.placeholder()
        return KpiValues.from_total_row(model_rows[0], date_from, date_to)

    # diagnostics の要約文を作る。
    @classmethod
    def _diagnostics_summary(cls, report: dict[str, object]) -> str:
        """diagnostics の warnings_count 表示文字列を返す。"""

        diagnostics: dict[str, object] = cls._as_dict(report.get("diagnostics", {}))
        totals = cls._as_dict(report.get("totals", {}))
        return (
            f"{report.get('coverage', '')} | warnings: {diagnostics.get('warnings_count', 0)} | "
            f"{report.get('data_status', 'Known subtotal')}; "
            f"missing records: {totals.get('missing_credits_count', 0)}. "
            "Pie: shares of known positive credits only. No positive values means no pie. "
            f"{report.get('cli_attribution', '')} "
            f"{report.get('primary_model_attribution', '')}"
        )

    # summary から list rows を取り出す。
    @staticmethod
    def _rows(summary: dict[str, object], key: str) -> list[dict[str, object]]:
        """summary の list[dict] field を返す。"""

        value: object = summary.get(key, [])
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    # dict へ変換する。
    @staticmethod
    def _as_dict(value: object) -> dict[str, object]:
        """value が dict ならそのまま、そうでなければ空 dict を返す。"""

        return value if isinstance(value, dict) else {}

    # numeric を float へ変換する。
    @staticmethod
    def _float(value: object) -> float:
        """numeric value を float として返す（非 numeric は 0.0）。"""

        if isinstance(value, int | float):
            return float(value)
        return 0.0


# dashboard が表示する table / タブの定義を保持する。
class DashboardLayout:
    """table spec とタブの高さ配分を集約する。

    描画層と test の双方がここを唯一の定義元として参照する。
    """

    MODEL_COLUMNS: tuple[ColumnSpec, ...] = (
        ColumnSpec("Model", _text("display_model")),
        ColumnSpec("JP¥", _fmt("estimated_jpy", UsageValueFormatter.jpy), numeric=True),
        ColumnSpec("US$", _fmt("estimated_usd", UsageValueFormatter.usd), numeric=True),
        ColumnSpec(
            "AI Credits",
            _fmt("ai_credits", UsageValueFormatter.credits),
            numeric=True,
        ),
        ColumnSpec("Estimated Credits",
                   _fmt("estimated_primary_ai_credits", UsageValueFormatter.credits), numeric=True),
        ColumnSpec(
            "Missing credits",
            _fmt("missing_credits_count", UsageValueFormatter.integer),
            numeric=True,
        ),
        ColumnSpec(
            "Direct Nano AIU",
            _fmt("direct_nano_aiu", UsageValueFormatter.integer),
            numeric=True,
        ),
        ColumnSpec(
            "Raw Input", _fmt("raw_input_tokens", UsageValueFormatter.integer), numeric=True
        ),
        ColumnSpec(
            "Fresh Input", _fmt("fresh_input_tokens", UsageValueFormatter.integer), numeric=True
        ),
        ColumnSpec(
            "Cached Input", _fmt("cached_input_tokens", UsageValueFormatter.integer), numeric=True
        ),
        ColumnSpec(
            "Cache Write", _fmt("cache_write_tokens", UsageValueFormatter.integer), numeric=True
        ),
        ColumnSpec("Output", _fmt("output_tokens", UsageValueFormatter.integer), numeric=True),
        ColumnSpec(
            "Reasoning", _fmt("reasoning_tokens", UsageValueFormatter.integer), numeric=True
        ),
        ColumnSpec("Confidence", _text("confidence")),
    )
    BREAKDOWN_COLUMNS: tuple[ColumnSpec, ...] = (
        MODEL_COLUMNS[0],
        ColumnSpec("Source", _text("source")),
        ColumnSpec("Kind", _text("kind")),
        ColumnSpec("Attribution", _text("attribution")),
        ColumnSpec("Raw Model", _text_dash("raw_model_key")),
        ColumnSpec("Estimated Model", _text_dash("estimated_primary_model")),
        ColumnSpec("Status", _text_dash("inclusion_status")),
        *MODEL_COLUMNS[1:],
    )
    DAILY_COLUMNS: tuple[ColumnSpec, ...] = (
        ColumnSpec("Date", _text("date")),
        ColumnSpec("Model", _text("display_model")),
        ColumnSpec("JP¥", _fmt("estimated_jpy", UsageValueFormatter.jpy), numeric=True),
        ColumnSpec("US$", _fmt("estimated_usd", UsageValueFormatter.usd), numeric=True),
        ColumnSpec(
            "AI Credits",
            _fmt("ai_credits", UsageValueFormatter.credits),
            numeric=True,
        ),
        ColumnSpec(
            "Missing credits",
            _fmt("missing_credits_count", UsageValueFormatter.integer),
            numeric=True,
        ),
        ColumnSpec("Unattrib.",
                   _fmt("unattributed_ai_credits", UsageValueFormatter.credits), numeric=True),
        ColumnSpec(
            "Direct Nano AIU",
            _fmt("direct_nano_aiu", UsageValueFormatter.integer),
            numeric=True,
        ),
        ColumnSpec(
            "Raw Input", _fmt("raw_input_tokens", UsageValueFormatter.integer), numeric=True
        ),
        ColumnSpec(
            "Fresh Input", _fmt("fresh_input_tokens", UsageValueFormatter.integer), numeric=True
        ),
        ColumnSpec(
            "Cached Input", _fmt("cached_input_tokens", UsageValueFormatter.integer), numeric=True
        ),
        ColumnSpec(
            "Cache Write", _fmt("cache_write_tokens", UsageValueFormatter.integer), numeric=True
        ),
        ColumnSpec("Output", _fmt("output_tokens", UsageValueFormatter.integer), numeric=True),
        ColumnSpec(
            "Reasoning", _fmt("reasoning_tokens", UsageValueFormatter.integer), numeric=True
        ),
    )
    DETAIL_COLUMNS: tuple[ColumnSpec, ...] = (
        ColumnSpec("Workspace", _text("workspace_alias")),
        ColumnSpec("Timestamp", _text("timestamp")),
        ColumnSpec("Session", _text("session_id")),
        ColumnSpec("Model", _text("display_model")),
        ColumnSpec("Source", _text("source")),
        ColumnSpec("JP¥", _fmt("estimated_jpy", UsageValueFormatter.jpy), numeric=True),
        ColumnSpec("US$", _fmt("estimated_usd", UsageValueFormatter.usd), numeric=True),
        ColumnSpec(
            "AI Credits",
            _fmt("ai_credits", UsageValueFormatter.credits),
            numeric=True,
        ),
        ColumnSpec(
            "Nano AIU", _fmt("reported_nano_aiu", UsageValueFormatter.integer), numeric=True
        ),
        ColumnSpec(
            "Raw Input", _fmt("raw_input_tokens", UsageValueFormatter.integer), numeric=True
        ),
        ColumnSpec(
            "Fresh Input", _fmt("fresh_input_tokens", UsageValueFormatter.integer), numeric=True
        ),
        ColumnSpec(
            "Cached Input", _fmt("cached_input_tokens", UsageValueFormatter.integer), numeric=True
        ),
        ColumnSpec(
            "Cache Write", _fmt("cache_write_tokens", UsageValueFormatter.integer), numeric=True
        ),
        ColumnSpec("Output", _fmt("output_tokens", UsageValueFormatter.integer), numeric=True),
        ColumnSpec(
            "Reasoning", _fmt("reasoning_tokens", UsageValueFormatter.integer), numeric=True
        ),
        ColumnSpec("Run", _text("run_id")),
        ColumnSpec("Run Kind", _text("run_kind")),
        ColumnSpec("Policy", _text("run_selection_policy")),
        ColumnSpec("Credits Source", _text("ai_credits_source")),
        ColumnSpec("Confidence", _text("confidence")),
    )
    CONFIDENCE_COLUMNS: tuple[ColumnSpec, ...] = (
        ColumnSpec("Confidence", _text("confidence")),
        ColumnSpec("Coarse", _text("coarse_confidence")),
        ColumnSpec("Records", _fmt("records_count", UsageValueFormatter.integer), numeric=True),
        ColumnSpec(
            "AI Credits",
            _fmt("ai_credits", UsageValueFormatter.credits),
            numeric=True,
        ),
        ColumnSpec(
            "Missing credits",
            _fmt("missing_credits_count", UsageValueFormatter.integer),
            numeric=True,
        ),
        ColumnSpec(
            "Direct Nano AIU",
            _fmt("direct_nano_aiu", UsageValueFormatter.integer),
            numeric=True,
        ),
    )
    DIAGNOSTICS_COLUMNS: tuple[ColumnSpec, ...] = (
        ColumnSpec("Code", _text("code")),
        ColumnSpec("Count", _fmt("count", UsageValueFormatter.integer), numeric=True),
    )
    TABLE_SPECS: tuple[TableSpec, ...] = (
        TableSpec(
            tag="diagnostics_table",
            columns=DIAGNOSTICS_COLUMNS,
            source="diagnostics_by_code",
            residual=True,
        ),
        TableSpec(
            tag="model_table",
            columns=MODEL_COLUMNS,
            source="model_rows",
            expects_total=True,
            height=200,
        ),
        TableSpec(
            tag="model_breakdown_table",
            columns=BREAKDOWN_COLUMNS,
            source="model_breakdown",
            height=200,
        ),
        TableSpec(
            tag="confidence_table",
            columns=CONFIDENCE_COLUMNS,
            source="confidence_breakdown",
            residual=True,
        ),
        TableSpec(
            tag="daily_table",
            columns=DAILY_COLUMNS,
            source="daily_rows",
            freeze_columns=2,
            expects_total=True,
            residual=True,
        ),
        TableSpec(
            tag="detail_table",
            columns=DETAIL_COLUMNS,
            source="detail_rows",
            freeze_columns=2,
            clipper=True,
            residual=True,
        ),
    )

    # 固定高見積り。設定 form 行 32px / text table 行 24px / 見出し 28px /
    # widget 行 28px を前提に置く（`DashboardTheme.form_row_height` /
    # `text_row_height` / `heading_height` に対応する）。
    TAB_LAYOUTS: tuple[TabLayoutSpec, ...] = (
        TabLayoutSpec(
            label="設定・アクション", fixed_height=628, residual_widget_tag="log_box",
            font_lines=22.5,
        ),
        TabLayoutSpec(
            label="Diagnostics", fixed_height=72, residual_widget_tag="diagnostics_table"
        ),
        TabLayoutSpec(
            label="モデルごと概要", fixed_height=900, residual_widget_tag="confidence_table"
        ),
        TabLayoutSpec(label="日付ごと概要", fixed_height=388, residual_widget_tag="daily_table"),
        TabLayoutSpec(
            label="セッションごとの詳細", fixed_height=28, residual_widget_tag="detail_table"
        ),
        # 上段の操作 1 行と状態の文 1 行 (button 24px + 文 + 行間) を固定し、残りを本体に充てる。
        TabLayoutSpec(
            label="セッション使用量", fixed_height=64, residual_widget_tag="su_body", font_lines=3,
        ),
    )

    # 設定タブの固定高のうち文字の行高で決まる分 (本文 font の行数)。
    @classmethod
    def settings_font_lines(cls) -> float:
        """window の最小・既定の高さを文字の大きさに合わせて増やす時の行数を返す。"""

        return cls.TAB_LAYOUTS[0].font_lines

    # tag から table spec を引く。
    @classmethod
    def table_spec(cls, tag: str) -> TableSpec:
        """指定 tag の `TableSpec` を返す（未定義は `ValueError`）。"""

        for spec in cls.TABLE_SPECS:
            if spec.tag == tag:
                return spec
        raise ValueError(f"unknown table tag: {tag}")
