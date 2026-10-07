from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from copilot_usage_visualizer.session_usage.contract import (
    AgentView,
    CompactionView,
    CumulativeReconciliation,
    RequestView,
    SessionUsageView,
)
from copilot_usage_visualizer.session_usage.format import SessionUsageFormat
from copilot_usage_visualizer.session_usage.js_compat import JsValue

XMode = Literal["time", "seq"]
# 系列の色の役割。実際の色は GUI の theme (DashboardTheme) が持つ。
SeriesRole = Literal[
    "credits",
    "reference",
    "input",
    "cache_read",
    "cache_write",
    "output",
    "uncached",
    "limit",
    "agent_1",
    "agent_2",
    "agent_3",
    "agent_4",
    "agent_5",
    "agent_6",
]


# グラフ上の 1 点と、その点を選んだ時の説明。
@dataclass(frozen=True)
class ChartPoint:
    """x は時刻 (epoch 秒) か request 番号。"""

    x: float
    y: float
    describe: str


# 折れ線 1 本。
@dataclass(frozen=True)
class LineSeries:
    """dashed は破線、step は階段線で描く。"""

    label: str
    role: SeriesRole
    points: tuple[ChartPoint, ...]
    dashed: bool = False
    step: bool = False


# 参考値のマーカー (ひし形) の集まり。
@dataclass(frozen=True)
class MarkerSeries:
    """累積イベントや圧縮の時点。"""

    label: str
    role: SeriesRole
    points: tuple[ChartPoint, ...]


# 折れ線グラフ 1 枚。
@dataclass(frozen=True)
class LineChart:
    """点が 1 つも無ければ empty_text を表示する。"""

    title: str
    series: tuple[LineSeries, ...]
    markers: tuple[MarkerSeries, ...]
    log: bool
    empty_text: str
    note: str | None

    # ポインタで選べる点 (x の昇順)。
    def cursor_points(self) -> list[ChartPoint]:
        """系列とマーカーの全点。"""

        points: list[ChartPoint] = [point for line in self.series for point in line.points]
        points += [point for marker in self.markers for point in marker.points]
        points.sort(key=lambda point: point.x)
        return points

    # 描く点が無いかどうか。
    @property
    def is_empty(self) -> bool:
        """系列の点が無ければ空 (マーカーだけでは描かない)。"""

        return not any(line.points for line in self.series)


# 積み上げ棒の 1 本 (連続する request をまとめることがある)。
@dataclass(frozen=True)
class BarGroup:
    """values は parts と同じ順の合計。"""

    first: RequestView
    last: RequestView
    values: tuple[float, ...]
    count: int
    invalid: int
    describe: str


# 積み上げ棒グラフ 1 枚。
@dataclass(frozen=True)
class StackedBarChart:
    """棒が描画幅に収まらない時は連続する request をまとめる (group_size 件ずつ)。"""

    title: str
    parts: tuple[tuple[str, SeriesRole], ...]
    groups: tuple[BarGroup, ...]
    group_size: int
    request_count: int
    empty_text: str

    # x 軸の目盛り (棒の位置と見出し)。
    def ticks(self, x_mode: XMode, width: int) -> list[tuple[str, float]]:
        """描画幅に応じた数だけ、棒の位置に時刻か request 番号を付ける。"""

        if not self.groups:
            return []
        count: int = max(2, width // 90)
        last: int = len(self.groups) - 1
        indexes: list[int] = []
        for step in range(count + 1):
            index: int = round(last * step / count) if last > 0 else 0
            if index not in indexes:
                indexes.append(index)
        return [(self._tick_label(self.groups[index], x_mode), float(index)) for index in indexes]

    # 目盛りの見出し。
    @staticmethod
    def _tick_label(group: BarGroup, x_mode: XMode) -> str:
        """時刻軸では最初の request の時刻、順番の軸では番号。"""

        if x_mode == "time":
            return SessionUsageFormat.time_text(group.first["t"])
        return f"#{group.first['i']}"


# 表示用データからグラフ 4 枚分のデータを作る。
class SessionCharts:
    """web/app.mjs の drawCharts と同じ系列・説明文・範囲の規則。"""

    AGENT_ROLES: tuple[SeriesRole, ...] = (
        "agent_1",
        "agent_2",
        "agent_3",
        "agent_4",
        "agent_5",
        "agent_6",
    )
    # 1 本あたりの最小幅 (px) から、描ける棒の数を決める。
    BAR_MIN_PIXELS: int = 3

    # 履歴が無い時の文言。
    @staticmethod
    def empty_text(view: SessionUsageView) -> str:
        """読めないのか、記録がまだ無いのかを区別する。"""

        if view["sources"]["history"].get("status") == "ok":
            return "No requests recorded for this session yet."
        return "Request history is unavailable."

    # エージェントごとの色の役割。
    @classmethod
    def agent_roles(cls, view: SessionUsageView) -> dict[str, SeriesRole]:
        """エージェントの並び順に 6 色を繰り返す。"""

        return {
            agent["key"]: cls.AGENT_ROLES[index % len(cls.AGENT_ROLES)]
            for index, agent in enumerate(view["agents"])
        }

    # request の x 座標。
    @staticmethod
    def x_of(request: RequestView, x_mode: XMode) -> float:
        """時刻軸は epoch 秒 (グラフの時刻軸の単位)、順番の軸は request 番号。"""

        return request["t"] / 1000 if x_mode == "time" else float(request["i"])

    # request の見出し。
    @classmethod
    def request_header(cls, view: SessionUsageView, request: RequestView) -> str:
        """#番号 時刻 · エージェント · モデル (effort)。"""

        effort: str = f" ({request['effort']})" if request["effort"] else ""
        return (
            f"#{request['i']} {SessionUsageFormat.time_text(request['t'])} · "
            f"{cls.agent_label(view, request['agent'])} · "
            f"{request['model'] or 'unknown model'}{effort}"
        )

    # エージェントの表示名。
    @staticmethod
    def agent_label(view: SessionUsageView, key: str) -> str:
        """一覧に無い key はそのまま。"""

        agent: AgentView | None = next(
            (item for item in view["agents"] if item["key"] == key), None
        )
        return agent["label"] if agent is not None else key

    # 時刻軸の範囲内の時刻かどうか。
    @staticmethod
    def _in_range(requests: Sequence[RequestView], millis: int | None) -> bool:
        """時間軸は request の範囲に揃える。範囲外の累積イベントは照合の表にだけ出す。"""

        return (
            bool(requests)
            and millis is not None
            and requests[0]["t"] <= millis <= requests[-1]["t"]
        )

    # 累積 AI Credits のグラフ。
    @classmethod
    def credits_chart(cls, view: SessionUsageView, x_mode: XMode) -> LineChart:
        """累積イベントは時刻軸の時だけ参考マーカーで重ねる。"""

        requests: list[RequestView] = view["requests"]
        points: tuple[ChartPoint, ...] = tuple(
            ChartPoint(
                cls.x_of(request, x_mode),
                SessionUsageFormat.credits_number(request["cumNano"]) or 0.0,
                f"{cls.request_header(view, request)} · request "
                f"{SessionUsageFormat.credits_text(request['nano'])} · cumulative "
                f"{SessionUsageFormat.credits_text(request['cumNano'])}",
            )
            for request in requests
        )
        cumulative: list[CumulativeReconciliation] = view["reconciliation"]["cumulative"]
        inside: list[CumulativeReconciliation] = [
            item for item in cumulative if cls._in_range(requests, JsValue.parse_time(item["t"]))
        ]
        markers: tuple[MarkerSeries, ...] = ()
        note: str | None = None
        if x_mode == "time":
            markers = (
                MarkerSeries(
                    "Session cumulative event (reference)",
                    "reference",
                    tuple(cls._cumulative_point(item) for item in inside),
                ),
            )
            outside: int = len(cumulative) - len(inside)
            if outside > 0:
                note = (
                    f"{outside} cumulative event(s) outside the request time range are listed "
                    "under Reconciliation."
                )
        series: LineSeries = LineSeries("AI Credits (request history)", "credits", points)
        return LineChart(
            "Cumulative AI Credits", (series,), markers, False, cls.empty_text(view), note
        )

    # 累積イベントの点。
    @staticmethod
    def _cumulative_point(item: CumulativeReconciliation) -> ChartPoint:
        """イベントの値と、その時点の履歴小計を説明に出す。"""

        millis: int = JsValue.parse_time(item["t"]) or 0
        return ChartPoint(
            millis / 1000,
            SessionUsageFormat.credits_number(item["nano"]) or 0.0,
            f"{item['type']} {SessionUsageFormat.time_text(millis)} · event "
            f"{SessionUsageFormat.credits_text(item['nano'])} · history at that time "
            f"{SessionUsageFormat.credits_text(item['historyNanoAtT'])}",
        )

    # 累積 token のグラフ。
    @classmethod
    def tokens_chart(cls, view: SessionUsageView, x_mode: XMode, log: bool) -> LineChart:
        """Input (cache を含む) は破線。対数目盛りでは 1 未満を 1 に寄せる。"""

        requests: list[RequestView] = view["requests"]
        series: list[LineSeries] = []
        kinds: tuple[tuple[str, SeriesRole, str, bool], ...] = (
            ("Input (incl. cache)", "input", "input", True),
            ("Cache read", "cache_read", "cacheRead", False),
            ("Cache write", "cache_write", "cacheWrite", False),
            ("Output", "output", "output", False),
        )
        for label, role, key, dashed in kinds:
            points: tuple[ChartPoint, ...] = tuple(
                ChartPoint(
                    cls.x_of(request, x_mode),
                    cls._scale_value(cls._cumulative(request, key), log),
                    f"{cls.request_header(view, request)} · cumulative input "
                    f"{SessionUsageFormat.int_text(request['cum']['input'])}, cache read "
                    f"{SessionUsageFormat.int_text(request['cum']['cacheRead'])}, cache write "
                    f"{SessionUsageFormat.int_text(request['cum']['cacheWrite'])}, output "
                    f"{SessionUsageFormat.int_text(request['cum']['output'])}",
                )
                for request in requests
            )
            series.append(LineSeries(label, role, points, dashed=dashed))
        return LineChart("Cumulative tokens", tuple(series), (), log, cls.empty_text(view), None)

    # 累積 token の 1 種別。
    @staticmethod
    def _cumulative(request: RequestView, key: str) -> int:
        """key は CumulativeTokens の項目名。"""

        cumulative = request["cum"]
        if key == "input":
            return cumulative["input"]
        if key == "cacheRead":
            return cumulative["cacheRead"]
        if key == "cacheWrite":
            return cumulative["cacheWrite"]
        return cumulative["output"]

    # 対数目盛りの値。
    @staticmethod
    def _scale_value(value: float, log: bool) -> float:
        """対数目盛りでは 0 を描けないため 1 に寄せる (canvas と同じく 0 は軸の下端)。"""

        return max(value, 1.0) if log else float(value)

    # request ごとのコンテキストサイズのグラフ。
    @classmethod
    def context_chart(cls, view: SessionUsageView, x_mode: XMode, log: bool) -> LineChart:
        """エージェントごとの線と、
        時刻軸の時だけ App の上限 (破線の階段) と圧縮マーカーを重ねる。
        """

        requests: list[RequestView] = view["requests"]
        roles: dict[str, SeriesRole] = cls.agent_roles(view)
        series: list[LineSeries] = []
        for agent in view["agents"]:
            if agent["requests"] <= 0 or agent["kind"] == "compaction":
                continue
            models: str = f" ({', '.join(agent['models'])})" if agent["models"] else ""
            points: tuple[ChartPoint, ...] = tuple(
                ChartPoint(
                    cls.x_of(request, x_mode),
                    cls._scale_value(request["input"], log),
                    f"{cls.request_header(view, request)} · context "
                    f"{SessionUsageFormat.int_text(request['input'])} tokens",
                )
                for request in requests
                if request["agent"] == agent["key"] and request["input"] is not None
            )
            series.append(LineSeries(f"{agent['label']}{models}", roles[agent["key"]], points))
        markers: tuple[MarkerSeries, ...] = ()
        if x_mode == "time" and requests:
            limits: list[ChartPoint] = [
                point
                for point in cls._limit_points(view)
                if cls._in_range(requests, int(point.x * 1000))
            ]
            if limits:
                series.append(
                    LineSeries(
                        "Context limit (main, recorded)",
                        "limit",
                        tuple(limits),
                        dashed=True,
                        step=True,
                    )
                )
            compactions: list[CompactionView] = view["events"]["compactions"]
            if compactions:
                markers = (
                    MarkerSeries(
                        "Compaction (pre-compaction size)",
                        "reference",
                        tuple(
                            cls._compaction_point(item, log)
                            for item in compactions
                            if item["preTokens"] is not None
                            and cls._in_range(requests, JsValue.parse_time(item["t"]))
                        ),
                    ),
                )
        return LineChart(
            "Context size per request", tuple(series), markers, log, cls.empty_text(view), None
        )

    # 上限の点。
    @staticmethod
    def _limit_points(view: SessionUsageView) -> list[ChartPoint]:
        """上限は App の記録 (メイン) と、それが無ければ現在の上限を request の範囲に引く。"""

        points: list[ChartPoint] = [
            ChartPoint(
                point["t"] / 1000,
                float(point["limit"]),
                f"Context limit {SessionUsageFormat.int_text(point['limit'])} at "
                f"{SessionUsageFormat.time_text(point['t'])}",
            )
            for point in view["app"]["contextSeries"]
        ]
        limit: object = view["context"]["limit"]
        requests: list[RequestView] = view["requests"]
        if (
            not points
            and isinstance(limit, int | float)
            and not isinstance(limit, bool)
            and limit
            and requests
        ):
            return [
                ChartPoint(
                    millis / 1000,
                    float(limit),
                    f"Context limit {SessionUsageFormat.int_text(limit)} at "
                    f"{SessionUsageFormat.time_text(millis)}",
                )
                for millis in (requests[0]["t"], requests[-1]["t"])
            ]
        return points

    # 圧縮マーカーの点。
    @classmethod
    def _compaction_point(cls, item: CompactionView, log: bool) -> ChartPoint:
        """圧縮前のサイズと上限を説明に出す。"""

        millis: int = JsValue.parse_time(item["t"]) or 0
        return ChartPoint(
            millis / 1000,
            cls._scale_value(item["preTokens"] or 0, log),
            f"Compaction {SessionUsageFormat.time_text(millis)} · before "
            f"{SessionUsageFormat.int_text(item['preTokens'])} · limit "
            f"{SessionUsageFormat.int_text(item['tokenLimit'])}",
        )

    # token 種別ごとの積み上げ棒のグラフ。
    @classmethod
    def requests_chart(cls, view: SessionUsageView, width: int) -> StackedBarChart:
        """width はグラフの描画幅 (px)。

        1 本が BAR_MIN_PIXELS を下回るなら連続する request をまとめる。
        """

        requests: list[RequestView] = view["requests"]
        parts: tuple[tuple[str, SeriesRole], ...] = (
            ("Uncached input", "uncached"),
            ("Cache read", "cache_read"),
            ("Cache write", "cache_write"),
            ("Output", "output"),
        )
        max_bars: int = max(10, width // cls.BAR_MIN_PIXELS)
        size: int = max(1, math.ceil(len(requests) / max_bars))
        groups: list[BarGroup] = []
        for start in range(0, len(requests), size):
            chunk: list[RequestView] = requests[start : start + size]
            values: tuple[float, ...] = tuple(
                float(sum(cls._part(request, index) or 0 for request in chunk))
                for index in range(4)
            )
            invalid: int = sum(1 for request in chunk if request["input"] is None)
            groups.append(
                BarGroup(
                    chunk[0],
                    chunk[-1],
                    values,
                    len(chunk),
                    invalid,
                    cls._bar_text(view, chunk, values, invalid),
                )
            )
        return StackedBarChart(
            "Tokens per request", parts, tuple(groups), size, len(requests), cls.empty_text(view)
        )

    # request の token 種別の値。
    @staticmethod
    def _part(request: RequestView, index: int) -> int | None:
        """cache なし input / cache read / cache write / output の順。"""

        return (
            request["uncached"],
            request["cacheRead"],
            request["cacheWrite"],
            request["output"],
        )[index]

    # 棒の説明。
    @classmethod
    def _bar_text(
        cls,
        view: SessionUsageView,
        chunk: list[RequestView],
        values: tuple[float, ...],
        invalid: int,
    ) -> str:
        """まとめた棒は番号の範囲と件数を出す。"""

        head: str = (
            cls.request_header(view, chunk[0])
            if len(chunk) == 1
            else f"#{chunk[0]['i']}–#{chunk[-1]['i']} ({len(chunk)} requests)"
        )
        uncached, cache_read, cache_write, output = (
            SessionUsageFormat.int_text(value) for value in values
        )
        invalid_text: str = f" · {invalid} invalid" if invalid else ""
        return (
            f"{head} · uncached {uncached}, cache read {cache_read}, cache write {cache_write}, "
            f"output {output}{invalid_text}"
        )
