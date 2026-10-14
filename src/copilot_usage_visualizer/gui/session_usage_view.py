from __future__ import annotations

import bisect
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import dearpygui.dearpygui as dpg

from copilot_usage_visualizer.gui.dpg_support import DpgContext, DpgSupport
from copilot_usage_visualizer.gui.layout import ColumnSpec, TableRenderPlan, TableSpec
from copilot_usage_visualizer.gui.theme import DashboardTheme, DashboardThemeAssets, DpgItem
from copilot_usage_visualizer.session_usage.browser import (
    BrowserFilter,
    SessionBrowser,
    SortDirection,
    SortKey,
)
from copilot_usage_visualizer.session_usage.charts import (
    ChartPoint,
    LineChart,
    LineSeries,
    SeriesRole,
    SessionCharts,
    StackedBarChart,
    XMode,
)
from copilot_usage_visualizer.session_usage.contract import (
    RequestView,
    SessionListEntry,
    SessionListResult,
    SessionUsageView,
)
from copilot_usage_visualizer.session_usage.format import SessionUsageFormat as Fmt
from copilot_usage_visualizer.session_usage.js_compat import JsValue
from copilot_usage_visualizer.session_usage.paths import CopilotHomePaths
from copilot_usage_visualizer.session_usage.presentation import (
    SessionPresentation,
    SummaryCard,
    TableData,
)
from copilot_usage_visualizer.session_usage.provider import SessionUsageProvider, ViewResult

Rgb = tuple[int, int, int]


# 1 枚のグラフのポインタ選択の状態。
@dataclass
class _ChartCursor:
    """選べる点 (x 昇順)、選んだ点の位置、カーソル線と読み上げ欄の item。"""

    plot: DpgItem
    cursor_line: DpgItem
    readout: DpgItem
    points: list[ChartPoint]
    xs: list[float]
    index: int = -1
    # 最後に選び直した時のマウスの x。マウスが動いていない間は矢印キーで選んだ点を保つ。
    mouse_x: float | None = None


# セッション使用量タブの表示状態。
@dataclass
class _TabState:
    """描き直しても保つ状態 (選択中のセッション・軸・絞り込み・開いている折りたたみなど)。"""

    roots: tuple[Path, ...] = ()
    root_index: int = 0
    session_id: str | None = None
    sessions: SessionListResult | None = None
    view: ViewResult | None = None
    x_mode: XMode = "time"
    log_y: bool = False
    browser_open: bool = False
    browser: BrowserFilter = field(default_factory=BrowserFilter)
    browser_page: int = 0
    request_page: int = 0
    open_sections: set[str] = field(default_factory=set)
    chart_selection: dict[str, float] = field(default_factory=dict)
    last_check: float = 0.0
    chart_width: int = 0


# copilot-usage-gui のセッション使用量タブ。
class SessionUsageTab:
    """Copilot CLI の記録を直接読み、canvas と同じ集計・同じ構成で 1 セッションを表示する。

    読み取りは描画ループの中で同期に行い、記録が変わった時だけ読み直す (SessionUsageProvider)。
    """

    TAB_LABEL: str = "セッション使用量"
    BODY_TAG: str = "su_body"
    AUTO_INTERVAL_SECONDS: float = 5.0
    # 1 ページの行数。表は縦にスクロールさせず全行を並べるため、ページのスクロールで追える量に抑える
    # (canvas の 100 行より少ない。2026-10-03 のユーザー確認)。
    PAGE_SIZE: int = 25
    # hover handler は載っている間ずっと通知する。通知は別スレッドから遅れて届くため、
    # 最後の通知からこの秒数までを「載っている」とみなす。
    HOVER_SECONDS: float = 0.25
    # 描き直し後にスクロール位置を戻し続けるフレーム数 (表の枠の高さが決まるまで)。
    SCROLL_RESTORE_FRAMES: int = 6
    # グラフの y 軸の目盛りと余白を除いた描画幅の見積り。
    CHART_SIDE_MARGIN: int = 120
    # 上段の選択欄の幅 (設計値)。セッション選択欄は窓幅に合わせて下限まで縮む。
    ROOT_COMBO_WIDTH: int = 220
    SESSION_COMBO_WIDTH: int = 420
    SESSION_COMBO_MIN_WIDTH: int = 200
    # Sessions・Refresh・Auto refresh と部品の間の余白の合計の見積り (設計値)。
    TOP_ROW_OTHERS_WIDTH: int = 215
    SERIES_COLORS: dict[str, Rgb] = {
        "credits": DashboardTheme.SERIES_CREDITS,
        "reference": DashboardTheme.SERIES_REFERENCE,
        "input": DashboardTheme.SERIES_INPUT,
        "uncached": DashboardTheme.SERIES_UNCACHED,
        "cache_read": DashboardTheme.SERIES_CACHE_READ,
        "cache_write": DashboardTheme.SERIES_CACHE_WRITE,
        "output": DashboardTheme.SERIES_OUTPUT,
        "limit": DashboardTheme.SERIES_LIMIT,
    }

    # 依存を受け取る。
    def __init__(
        self,
        roots: Callable[[], tuple[Path, ...]],
        log: Callable[[str], None],
        theme: Callable[[], DashboardThemeAssets],
    ) -> None:
        """roots は設定欄の現在の CLI log root、log は実行 Log への追記。

        theme は構築済みの theme を返す (タブの構築は theme の構築後)。
        """

        self._reads_paused: bool = False
        # 再開時は「セッションなし」など、停止前の無効状態も復元する。
        self._paused_enabled: dict[str, bool] = {}
        self._roots_source: Callable[[], tuple[Path, ...]] = roots
        self._log: Callable[[str], None] = log
        self._theme: Callable[[], DashboardThemeAssets] = theme
        self._state: _TabState = _TabState()
        self._providers: dict[Path, SessionUsageProvider] = {}
        self._cursors: dict[str, _ChartCursor] = {}
        self._handler_registries: list[DpgItem] = []
        self._series_themes: dict[tuple[Rgb, bool], DpgItem] = {}
        self._sections: dict[str, DpgItem] = {}
        self._session_labels: dict[str, str] = {}
        self._tab: DpgItem | None = None
        # 描き直し後に戻すスクロール位置と、戻し続ける残りフレーム数。
        self._scroll_target: tuple[float, int] | None = None
        # マウスが載っているグラフと、それを最後に通知された時刻 (time.monotonic)。
        self._hovered: tuple[str, float] | None = None

    # ---- 構築と描画ループ ------------------------------------------------------

    # タブを構築する。
    def build(self) -> None:
        """tab bar の中で呼ぶ。上段の操作は固定し、本体 (BODY_TAG) を縦にスクロールする。"""

        with DpgSupport.context(dpg.tab(label=self.TAB_LABEL)) as tab:
            self._tab = tab
            with DpgSupport.context(dpg.group(horizontal=True)):
                dpg.add_combo(
                    [], tag="su_root", width=self._fpx(self.ROOT_COMBO_WIDTH), show=False,
                    callback=self._on_root,
                )
                dpg.add_combo(
                    [], tag="su_session", width=self._fpx(self.SESSION_COMBO_WIDTH),
                    callback=self._on_session,
                )
                dpg.add_button(label="Sessions", callback=self._on_toggle_browser)
                dpg.add_button(label="Refresh", tag="su_refresh", callback=self._on_refresh)
                dpg.bind_item_theme("su_refresh", self._theme().primary_button_theme)
                # 既定は OFF。GUI では主に過去のセッションを見るため (ユーザーの選択 2026-10-03)。
                dpg.add_checkbox(label="Auto refresh", tag="su_auto", default_value=False)
            # 状態の文は長くなる (読み取り失敗の理由など) ため、最小幅 1200px でも見切れないよう
            # 操作の行とは別の行に置く。
            dpg.add_text("Open this tab to load sessions.", tag="su_status")
            # 高さは DashboardLayout.TAB_LAYOUTS の残余として viewport に合わせて決まる。
            with DpgSupport.context(dpg.child_window(tag=self.BODY_TAG, width=-1, border=False)):
                # 子の無い group をスクロールする入れ物に置くと、Dear PyGui はスクロールした分だけ
                # 中身の高さを増やして計算し、ページが動かなくなる (非表示でも同じ)。どの group にも
                # 常に子を置く。
                with DpgSupport.context(dpg.group(tag="su_notice", show=False)):
                    dpg.add_text("", tag="su_notice_text")
                with DpgSupport.context(dpg.group(tag="su_browser", show=False)):
                    self._build_browser_frame()
                with DpgSupport.context(dpg.group(tag="su_content")):
                    dpg.add_text("Loading session usage…")
        with DpgSupport.context(dpg.handler_registry()):
            for key in (dpg.mvKey_Left, dpg.mvKey_Right, dpg.mvKey_Home, dpg.mvKey_End):
                dpg.add_key_press_handler(key, callback=self._on_key, user_data=key)

    # タブが選ばれた時。
    def on_tab_selected(self) -> None:
        """設定欄の CLI log root を読み直して表示する (root の変更はここと Refresh で反映)。"""

        if self._reads_paused:
            dpg.set_value("su_status", "Scan running; session refresh deferred.")
            return
        self._reload_roots()
        self._load(force=False, sessions=True)

    # 描画ループの毎フレーム。
    def on_frame(self) -> None:
        """描き直し後のスクロール位置の復元と、自動更新 (5 秒ごと、タブが前面の時だけ)。"""

        self._restore_scroll()
        if self._reads_paused or not self.is_active() or not dpg.get_value("su_auto"):
            return
        if time.monotonic() - self._state.last_check < self.AUTO_INTERVAL_SECONDS:
            return
        state: _TabState = self._state
        self._load(force=False, sessions=state.browser_open or state.session_id is None)

    # scanの間は別経路の同期読み取りを保留し、描画を止めない。
    def set_reads_paused(self, paused: bool) -> None:
        """表示を維持したまま、セッション読み取り操作を一時停止する。"""

        if paused == self._reads_paused:
            return
        self._reads_paused = paused
        if paused:
            self._paused_enabled = {
                tag: bool(dpg.get_item_configuration(tag)["enabled"])
                for tag in ("su_refresh", "su_root", "su_session")
            }
            for tag in self._paused_enabled:
                dpg.configure_item(tag, enabled=False)
            dpg.set_value("su_status", "Scan running; session refresh deferred.")
        else:
            for tag, enabled in self._paused_enabled.items():
                dpg.configure_item(tag, enabled=enabled)
            self._paused_enabled.clear()
            dpg.set_value("su_status", "Scan finished. Refresh to update sessions.")

    # viewport の大きさが変わった時。
    def on_resize(self) -> None:
        """上段の選択欄を幅に合わせ、グラフの描画幅が変わったら棒のまとめ方と目盛りを描き直す。"""

        self._fit_top_row()
        if self._state.view is not None and self._chart_width() != self._state.chart_width:
            self._render_content()

    # 上段の操作を最小幅でも右端まで収める。
    def _fit_top_row(self) -> None:
        """文字の大きさで広がる操作欄が窓幅を超えないよう、セッション選択欄を縮める。

        root 選択欄 (複数 root の時だけ表示)・Sessions・Refresh・Auto refresh の幅を引いた残りを、
        上限 (設計値 420) までセッション選択欄に充てる。縦 scroll では右端へ届かない。
        """

        theme: DashboardThemeAssets = self._theme()
        root_shown: bool = bool(dpg.get_item_configuration("su_root")["show"])
        used: int = theme.fpx(self.TOP_ROW_OTHERS_WIDTH)
        if root_shown:
            used += theme.fpx(self.ROOT_COMBO_WIDTH) + theme.px(DashboardTheme.ITEM_SPACING[0])
        margin: int = theme.px(
            2 * DashboardTheme.WINDOW_PADDING[0] + DashboardTheme.SCROLLBAR_SIZE
        )
        available: int = dpg.get_viewport_client_width() - used - margin
        width: int = max(theme.fpx(self.SESSION_COMBO_MIN_WIDTH),
                         min(theme.fpx(self.SESSION_COMBO_WIDTH), available))
        dpg.configure_item("su_session", width=width)

    # ---- 読み取り -----------------------------------------------------------

    # 設定欄の root を読み直す。
    def _reload_roots(self) -> None:
        """root が複数ある時だけ選択欄を出す。root が変わったら最新のセッションから表示し直す。"""

        roots: tuple[Path, ...] = self._roots_source()
        state: _TabState = self._state
        if roots != state.roots:
            state.roots = roots
            self._reset_root(0)
        labels: list[str] = [f"{index + 1}: {root}" for index, root in enumerate(roots)]
        dpg.configure_item("su_root", items=labels, show=len(roots) > 1)
        self._fit_top_row()
        if labels:
            dpg.set_value("su_root", labels[state.root_index])

    # root を切り替える。
    def _reset_root(self, index: int) -> None:
        """選択と読み取り結果を捨てる。"""

        state: _TabState = self._state
        state.root_index = index
        state.session_id = None
        state.sessions = None
        state.view = None

    # 選んでいる root の Copilot home。
    def _home(self) -> Path | None:
        """root が無い、または Windows から見た WSL の共有なら None (理由は通知に出す)。"""

        state: _TabState = self._state
        if not state.roots:
            return None
        root: Path = state.roots[state.root_index]
        if CopilotHomePaths.is_wsl_share(root):
            return None
        return CopilotHomePaths.home_from_cli_log_root(root)

    # 一覧と表示用データを読み、変わっていれば描き直す。
    def _load(self, force: bool, sessions: bool) -> None:
        """force は記録の署名にかかわらず読み直す。読み取りの失敗は状態として表示する。"""

        if self._reads_paused:
            dpg.set_value("su_status", "Scan running; session refresh deferred.")
            return
        state: _TabState = self._state
        state.last_check = time.monotonic()
        self._render_notice()
        home: Path | None = self._home()
        if home is None:
            state.sessions = None
            state.view = None
            self._render_session_select()
            self._clear_content()
            dpg.add_text("No session usage to show for this root.", parent="su_content")
            # 一覧を開いていれば、前の root の一覧を残さない。
            self._render_browser_results()
            dpg.set_value("su_status", "No readable Copilot CLI log root.")
            return
        provider: SessionUsageProvider = self._providers.setdefault(
            home, SessionUsageProvider(home)
        )
        try:
            if sessions or force or state.sessions is None:
                state.sessions = provider.list_sessions(force=force)
                if state.browser_open:
                    self._render_browser()
            if state.session_id is None and state.sessions["sessions"]:
                # 最初の表示は最終活動が最新のセッション。以後の自動更新では選択を変えない。
                state.session_id = state.sessions["sessions"][0]["id"]
            self._render_session_select()
            if state.session_id is None:
                state.view = None
                self._render_no_session()
                dpg.set_value("su_status", f"Checked {Fmt.time_text(time.time() * 1000)}")
                return
            result: ViewResult = provider.get_view(state.session_id, force=force)
        except (OSError, ValueError) as error:
            # 例外の文言にはパスが入り得るため、種類だけを出す。
            name: str = type(error).__name__
            dpg.set_value("su_status", f"Could not load usage ({name}). Retry with Refresh.")
            self._log(f"session usage: load failed ({name})")
            return
        previous: ViewResult | None = state.view
        if previous is None or previous.view["session"]["id"] != state.session_id:
            state.request_page = 0
            state.chart_selection.clear()
        state.view = result
        if previous is None or not self._same_content(previous, result):
            self._render_content()
            self._render_session_select()
        read_at: int | None = JsValue.parse_time(result.view["readAt"])
        now: str = Fmt.time_text(time.time() * 1000)
        dpg.set_value("su_status", f"Updated {now} · read {Fmt.time_text(read_at)}")

    # タブが前面にあるかどうか。
    def is_active(self) -> bool:
        """tab bar の値は選択中のタブ。"""

        if self._tab is None:
            return False
        parent: DpgItem | None = dpg.get_item_parent(self._tab)
        return parent is not None and dpg.get_value(parent) == self._tab

    # ---- 操作 ---------------------------------------------------------------

    # root を選んだ時。
    def _on_root(self, _sender: DpgItem, value: str) -> None:
        """選んだ root の最新のセッションから表示し直す。"""

        index: int = int(value.split(":", 1)[0]) - 1
        if index != self._state.root_index:
            self._reset_root(index)
        self._load(force=False, sessions=True)

    # セッションを選んだ時。
    def _on_session(self, _sender: DpgItem, label: str) -> None:
        """コンボボックスの見出しからセッションを選ぶ。"""

        for session_id, text in self._session_labels.items():
            if text == label:
                self._select_session(session_id)
                return

    # セッションを切り替える。
    def _select_session(self, session_id: str) -> None:
        """表示用データを読み直す。"""

        self._state.session_id = session_id
        self._load(force=False, sessions=False)

    # 一覧の開閉。
    def _on_toggle_browser(self) -> None:
        """開く時は一覧を読み直す。"""

        state: _TabState = self._state
        state.browser_open = not state.browser_open
        dpg.configure_item("su_browser", show=state.browser_open)
        if state.browser_open:
            self._load(force=False, sessions=True)
            self._render_browser()

    # Refresh。
    def _on_refresh(self) -> None:
        """root を読み直し、記録の署名にかかわらず全部読み直す。"""

        self._reload_roots()
        self._load(force=True, sessions=True)

    # マウスが載っているグラフ。
    def _hovered_chart(self) -> str | None:
        """hover handler が HOVER_SECONDS 以内に通知したグラフ。

        is_item_hovered では判定できなかった (2026-10-03 のユーザー確認) ため、
        hover handler の通知を使う。
        """

        if self._hovered is None:
            return None
        chart_id, notified = self._hovered
        if time.monotonic() - notified > self.HOVER_SECONDS or chart_id not in self._cursors:
            return None
        return chart_id

    # 矢印キーで点を移る。
    def _on_key(self, _sender: DpgItem, _data: object, key: int) -> None:
        """マウスを載せているグラフの点を 1 つ (Shift で 10) ずつ、Home / End で端へ移る。"""

        chart_id: str | None = self._hovered_chart()
        if chart_id is None:
            return
        cursor: _ChartCursor = self._cursors[chart_id]
        if not cursor.points:
            return
        shift: bool = dpg.is_key_down(dpg.mvKey_LShift) or dpg.is_key_down(dpg.mvKey_RShift)
        step: int = 10 if shift else 1
        last: int = len(cursor.points) - 1
        index: int = cursor.index
        if key == dpg.mvKey_Right:
            index = 0 if index < 0 else index + step
        elif key == dpg.mvKey_Left:
            index = last if index < 0 else index - step
        elif key == dpg.mvKey_Home:
            index = 0
        else:
            index = last
        self._select_point(chart_id, cursor, max(0, min(last, index)))

    # マウスを載せた位置に x が最も近い点を選ぶ。
    def _on_hover(self, _sender: DpgItem, _data: object, chart_id: str) -> None:
        """点は x の昇順なので二分探索で隣の 2 点を比べる。"""

        self._hovered = (chart_id, time.monotonic())
        cursor: _ChartCursor | None = self._cursors.get(chart_id)
        if cursor is None or not cursor.points:
            return
        x: float = dpg.get_plot_mouse_pos()[0]
        # hover handler は載っている間ずっと通知する。マウスが動いた時だけ選び直す。
        if x == cursor.mouse_x:
            return
        cursor.mouse_x = x
        position: int = bisect.bisect_left(cursor.xs, x)
        candidates: list[int] = [i for i in (position - 1, position) if 0 <= i < len(cursor.xs)]
        best: int = min(candidates, key=lambda i: abs(cursor.xs[i] - x))
        if best != cursor.index:
            self._select_point(chart_id, cursor, best)

    # 点を選ぶ。
    def _select_point(self, chart_id: str, cursor: _ChartCursor, index: int) -> None:
        """カーソル線を動かして説明を読み上げ欄へ出す。

        描き直し後に同じ x を選び直せるよう覚える。
        """

        cursor.index = index
        point: ChartPoint = cursor.points[index]
        dpg.set_value(cursor.cursor_line, [[point.x]])
        dpg.configure_item(cursor.cursor_line, show=True)
        dpg.set_value(cursor.readout, point.describe)
        self._state.chart_selection[chart_id] = point.x

    # ---- 描画: 通知・選択欄・一覧 ----------------------------------------------

    # root の通知。
    def _render_notice(self) -> None:
        """root が無い時と、Windows から WSL の共有を指している時。"""

        state: _TabState = self._state
        text: str | None = None
        if not state.roots:
            text = (
                "No Copilot CLI log root is configured. Add one under 設定・アクション "
                "(Copilot CLI log roots), then press Refresh."
            )
        elif CopilotHomePaths.is_wsl_share(state.roots[state.root_index]):
            text = (
                "This root is a WSL share. Session usage is not read across the share because "
                "SQLite locking fails there. To view these sessions, run this GUI inside WSL "
                "with the WSL log root."
            )
        dpg.configure_item("su_notice_text", wrap=self._wrap_width())
        dpg.set_value("su_notice_text", text or "")
        dpg.configure_item("su_notice", show=text is not None)

    # セッションの選択欄。
    def _render_session_select(self) -> None:
        """一覧に無い選択中のセッションは、表示用データから見出しを作る。"""

        state: _TabState = self._state
        entries: list[SessionListEntry] = list(state.sessions["sessions"]) if state.sessions else []
        if state.session_id is not None and all(item["id"] != state.session_id for item in entries):
            entries.insert(0, self._entry_from_view(state.session_id))
        self._session_labels = {item["id"]: SessionBrowser.label(item) for item in entries}
        labels: list[str] = list(self._session_labels.values())
        dpg.configure_item("su_session", items=labels or ["No sessions"], enabled=bool(labels))
        selected: str | None = self._session_labels.get(state.session_id or "")
        dpg.set_value("su_session", selected or (labels[0] if labels else "No sessions"))

    # 一覧に無いセッションの見出し用の行。
    def _entry_from_view(self, session_id: str) -> SessionListEntry:
        """読み込んだ表示用データがあればその値を使う。"""

        result: ViewResult | None = self._state.view
        view: SessionUsageView | None = (
            result.view
            if result is not None and result.view["session"]["id"] == session_id
            else None
        )
        if view is None:
            return {
                "id": session_id,
                "repository": None,
                "branch": None,
                "lastActivity": None,
                "requests": 0,
                "creditsNano": None,
                "creditsMissing": 0,
            }
        last_at: int | None = view["totals"]["lastAt"]
        return {
            "id": session_id,
            "repository": view["session"]["repository"],
            "branch": view["session"]["branch"],
            "lastActivity": JsValue.iso_string(last_at) if last_at is not None else None,
            "requests": view["totals"]["requests"],
            "creditsNano": view["totals"]["creditsNano"],
            "creditsMissing": view["totals"]["creditsMissing"],
        }

    # セッション一覧の枠 (構築時に 1 度だけ作る)。
    def _build_browser_frame(self) -> None:
        """見出し・絞り込みの入力欄・結果の欄。結果の欄は描き直すたびに中身を入れ替える。"""

        self._heading("Sessions")
        with DpgSupport.context(dpg.group(horizontal=True)):
            dpg.add_input_text(
                tag="su_browser_q",
                hint="Filter: ID, repository, branch",
                width=self._fpx(280),
                callback=self._on_browser_filter,
            )
            for label, tag in (("From", "su_browser_from"), ("To", "su_browser_to")):
                dpg.add_text(label)
                dpg.add_input_text(
                    tag=tag, hint="YYYY-MM-DD", width=self._fpx(120),
                    callback=self._on_browser_filter,
                )
            dpg.add_button(label="Clear", callback=self._on_browser_clear)
        with DpgSupport.context(dpg.group(tag="su_browser_results")):
            dpg.add_text("Loading sessions…")
        dpg.add_separator()

    # セッション一覧の結果を描き直す。
    def _render_browser(self) -> None:
        """入力欄は保ったまま結果の部分だけを描き直す。"""

        self._render_browser_results()

    # 絞り込みの入力。
    def _on_browser_filter(self) -> None:
        """入力のたびに結果を描き直す。並べ替えは保つ。"""

        state: _TabState = self._state
        state.browser = BrowserFilter(
            str(dpg.get_value("su_browser_q")),
            str(dpg.get_value("su_browser_from")),
            str(dpg.get_value("su_browser_to")),
            state.browser.sort,
            state.browser.direction,
        )
        state.browser_page = 0
        self._render_browser_results()

    # 絞り込みを消す。
    def _on_browser_clear(self) -> None:
        """文字と日付を空にする。"""

        for tag in ("su_browser_q", "su_browser_from", "su_browser_to"):
            dpg.set_value(tag, "")
        self._on_browser_filter()

    # 一覧の結果。
    def _render_browser_results(self) -> None:
        """読めない時は理由、0 件の時はその旨を出す。行を押すとそのセッションを表示する。"""

        parent: str = "su_browser_results"
        dpg.delete_item(parent, children_only=True)
        state: _TabState = self._state
        result: SessionListResult | None = state.sessions
        if result is None:
            text: str = (
                "No readable Copilot CLI log root (see the notice above)."
                if self._home() is None
                else "Loading sessions…"
            )
            dpg.add_text(text, parent=parent)
            return
        if result["status"]["status"] != "ok":
            reason: str = SessionPresentation.status_text(result["status"])
            dpg.add_text(f"The session list {reason}. Retry with Refresh.", parent=parent)
            return
        matched: list[SessionListEntry] = SessionBrowser.filtered(result["sessions"], state.browser)
        pages: int = max(1, -(-len(matched) // self.PAGE_SIZE))
        state.browser_page = min(state.browser_page, pages - 1)
        start: int = state.browser_page * self.PAGE_SIZE
        shown: list[SessionListEntry] = matched[start : start + self.PAGE_SIZE]
        dpg.add_text(
            f"Showing {Fmt.int_text(len(matched))} of {Fmt.int_text(len(result['sessions']))} "
            "sessions. AI Credits and Requests follow the same rules as the session view; sessions "
            "without request history show -. Click a row to show it.",
            parent=parent,
            wrap=self._wrap_width(),
        )
        if not matched:
            dpg.add_text("No sessions match the filter.", parent=parent)
            return
        if pages > 1:
            self._pager(
                parent, state.browser_page, pages, f"{len(matched)} sessions", self._browser_page
            )
        cells: list[tuple[str, ...]] = [
            (
                item["id"][:8],
                SessionPresentation.credits_cell(item["creditsNano"], item["creditsMissing"]),
                Fmt.int_text(item["requests"]),
                Fmt.date_time_text(JsValue.parse_time(item["lastActivity"])),
                SessionBrowser.project_text(item) or "-",
            )
            for item in shown
        ]
        headers: list[str] = [column.label for column in SessionBrowser.columns()]
        with (
            self._table_frame(parent),
            DpgSupport.context(
                dpg.table(
                    header_row=True,
                    sortable=True,
                    callback=self._on_browser_sort,
                    row_background=True,
                    borders_innerH=True,
                    resizable=True,
                    policy=dpg.mvTable_SizingFixedFit,
                    width=self._table_width(headers, cells),
                )
            ),
        ):
            for column in SessionBrowser.columns():
                active: bool = column.key == state.browser.sort
                direction: str = state.browser.direction if active else column.default_direction
                dpg.add_table_column(
                    label=column.label,
                    user_data=column.key,
                    default_sort=active,
                    prefer_sort_descending=direction == "desc",
                )
            for item, row in zip(shown, cells, strict=True):
                with DpgSupport.context(dpg.table_row()):
                    dpg.add_selectable(
                        label=row[0],
                        span_columns=True,
                        default_value=item["id"] == state.session_id,
                        callback=self._on_browser_pick,
                        user_data=item["id"],
                    )
                    for value in row[1:]:
                        dpg.add_text(value)

    # 一覧の並べ替え (見出しを押した時)。
    def _on_browser_sort(self, _sender: DpgItem, specs: object) -> None:
        """Dear PyGui が渡す [(列, 向き)] から並べ替え条件を作る。"""

        if not isinstance(specs, list) or not specs:
            return
        column_item, direction = specs[0]
        keys: dict[object, SortKey] = {
            column.key: column.key for column in SessionBrowser.columns()
        }
        sort_key: SortKey | None = keys.get(dpg.get_item_user_data(column_item))
        if sort_key is None:
            return
        state: _TabState = self._state
        sort_direction: SortDirection = "asc" if direction > 0 else "desc"
        # Dear PyGui は表を作った最初のフレームにも現在の並べ替えを通知する。変わっていない時に
        # 描き直すと、作り直した表がまた通知して毎フレーム作り直し続ける (列幅も測れない)。
        if (sort_key, sort_direction) == (state.browser.sort, state.browser.direction):
            return
        state.browser = BrowserFilter(
            state.browser.text,
            state.browser.date_from,
            state.browser.date_to,
            sort_key,
            sort_direction,
        )
        self._render_browser_results()

    # 一覧の行を選んだ時。
    def _on_browser_pick(self, _sender: DpgItem, _value: object, session_id: str) -> None:
        """一覧を閉じてそのセッションを表示し、ページの先頭へ戻す。"""

        self._state.browser_open = False
        dpg.configure_item("su_browser", show=False)
        dpg.set_y_scroll(self.BODY_TAG, 0.0)
        self._select_session(session_id)

    # 一覧のページ。
    def _browser_page(self, page: int) -> None:
        """ページを変えて結果を描き直す。"""

        self._state.browser_page = page
        self._render_browser_results()

    # ---- 描画: 本体 -------------------------------------------------------------

    # 本体を空にする。
    def _clear_content(self) -> None:
        """折りたたみの開閉を覚えてから、グラフの handler と一緒に消す。"""

        self._remember_open_sections()
        dpg.delete_item("su_content", children_only=True)
        for registry in self._handler_registries:
            if dpg.does_item_exist(registry):
                dpg.delete_item(registry)
        self._handler_registries.clear()
        self._cursors.clear()

    # 表示できるセッションが無い時。
    def _render_no_session(self) -> None:
        """一覧を読めないのか、記録がまだ無いのかを区別して示す。"""

        self._clear_content()
        result: SessionListResult | None = self._state.sessions
        if result is not None and result["status"]["status"] != "ok":
            text: str = (
                "No sessions to show. Request history (session-store.db) "
                f"{SessionPresentation.status_text(result['status'])}. "
                "Check the Copilot CLI log root under 設定・アクション."
            )
        else:
            text = (
                "No sessions are recorded yet. Sessions appear here when Copilot CLI records them; "
                "with Auto refresh on, this tab checks every 5 seconds."
            )
        dpg.add_text(text, parent="su_content", wrap=self._wrap_width())

    # 本体を描き直す。
    def _render_content(self) -> None:
        """通知・要約・グラフ・エージェント・監査詳細。スクロール位置と開いている折りたたみを保つ。"""

        state: _TabState = self._state
        if state.view is None:
            return
        view: SessionUsageView = state.view.view
        scroll: float = dpg.get_y_scroll(self.BODY_TAG)
        self._clear_content()
        state.chart_width = self._chart_width()
        dpg.push_container_stack("su_content")
        try:
            for text in SessionPresentation.alerts(view):
                dpg.add_text(text, wrap=self._wrap_width())
            self._render_summary(view)
            self._render_charts(view)
            self._render_agents(view)
            self._render_details(view)
        finally:
            dpg.pop_container_stack()
        dpg.set_y_scroll(self.BODY_TAG, scroll)
        self._scroll_target = (scroll, self.SCROLL_RESTORE_FRAMES)

    # 描き直し後のスクロール位置を戻す。
    def _restore_scroll(self) -> None:
        """描き直した直後は中身に合わせて高さを決める表の枠が 1 フレームだけ低くなり、
        Dear ImGui がスクロール位置を切り詰める。数フレームの間、元の位置へ戻し続ける。
        """

        if self._scroll_target is None or not dpg.does_item_exist(self.BODY_TAG):
            return
        scroll, frames = self._scroll_target
        if dpg.get_y_scroll(self.BODY_TAG) != scroll:
            dpg.set_y_scroll(self.BODY_TAG, scroll)
        self._scroll_target = (scroll, frames - 1) if frames > 1 else None

    # 表示する中身が同じかどうか。
    @staticmethod
    def _same_content(previous: ViewResult, current: ViewResult) -> bool:
        """読み取り時刻を除いて同じなら描き直さない。

        履歴 DB はどのセッションの記録でも更新されるため、見ているセッションが変わっていなくても
        署名は変わる。同じ中身で描き直すとスクロールや選択が乱れる。
        """

        if previous is current:
            return True
        return {**previous.view, "readAt": ""} == {**current.view, "readAt": ""}

    # 折りたたみの開閉を覚える。
    def _remember_open_sections(self) -> None:
        """描き直しの前に、今開いている折りたたみの key を集める。"""

        for key, item in self._sections.items():
            if not dpg.does_item_exist(item):
                continue
            if dpg.get_value(item):
                self._state.open_sections.add(key)
            else:
                self._state.open_sections.discard(key)
        self._sections.clear()

    # 要約のカード。
    def _render_summary(self, view: SessionUsageView) -> None:
        """見出し・値・補足の 3 段。値にマウスを載せると正確な値を出す。"""

        dpg.add_spacer(height=self._px(DashboardTheme.GROUP_SPACER_HEIGHT))
        with DpgSupport.context(dpg.group(horizontal=True)):
            self._heading("Summary")
            dpg.add_text(SessionPresentation.session_line(view))
        cards: list[SummaryCard] = SessionPresentation.summary_cards(view)
        heading_font: DpgItem | None = self._theme().heading_font
        with DpgSupport.context(
            dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchSame, borders_innerV=True)
        ):
            for _ in cards:
                dpg.add_table_column()
            for row in range(3):
                with DpgSupport.context(dpg.table_row()):
                    for card in cards:
                        text: DpgItem = dpg.add_text((card.label, card.value, card.sub)[row])
                        if row != 1:
                            continue
                        if heading_font is not None:
                            dpg.bind_item_font(text, heading_font)
                        with DpgSupport.context(dpg.tooltip(text)):
                            dpg.add_text(card.title)
        dpg.add_spacer(height=self._px(DashboardTheme.GROUP_SPACER_HEIGHT))

    # グラフ 4 枚。
    def _render_charts(self, view: SessionUsageView) -> None:
        """X 軸 (時刻 / 順番) と token の目盛り (線形 / 対数) を切り替えると描き直す。"""

        state: _TabState = self._state
        with DpgSupport.context(dpg.group(horizontal=True)):
            self._heading("Trends")
            dpg.add_text("X axis")
            dpg.add_radio_button(
                ["Time", "Sequence"],
                horizontal=True,
                default_value="Time" if state.x_mode == "time" else "Sequence",
                callback=self._on_x_mode,
            )
            dpg.add_spacer(width=self._px(16))
            dpg.add_text("Token scale")
            dpg.add_radio_button(
                ["Linear", "Log"],
                horizontal=True,
                default_value="Log" if state.log_y else "Linear",
                callback=self._on_log_scale,
            )
        dpg.add_text(
            "Point at a chart to read values (arrow keys step while pointing). The wheel scrolls "
            "the page; Ctrl + wheel zooms a chart and double-click restores it.",
            wrap=self._wrap_width(),
        )
        self._line_chart("credits", SessionCharts.credits_chart(view, state.x_mode))
        self._line_chart("tokens", SessionCharts.tokens_chart(view, state.x_mode, state.log_y))
        self._line_chart("context", SessionCharts.context_chart(view, state.x_mode, state.log_y))
        self._bar_chart("requests", SessionCharts.requests_chart(view, state.chart_width))

    # X 軸の切り替え。
    def _on_x_mode(self, _sender: DpgItem, value: str) -> None:
        """時刻軸では参考マーカーと上限線も出す。"""

        self._state.x_mode = "time" if value == "Time" else "seq"
        self._state.chart_selection.clear()
        self._render_content()

    # token の目盛りの切り替え。
    def _on_log_scale(self, _sender: DpgItem, value: str) -> None:
        """累積 token とコンテキストのグラフに効く。"""

        self._state.log_y = value == "Log"
        self._render_content()

    # 折れ線グラフ 1 枚。
    def _line_chart(self, chart_id: str, chart: LineChart) -> None:
        """点が無ければ文言だけを出す。凡例は文字で系列を示す (色だけに頼らない)。"""

        self._heading(chart.title)
        if chart.is_empty:
            dpg.add_text(chart.empty_text)
            return
        time_axis: bool = self._state.x_mode == "time"
        with self._plot() as plot:
            dpg.add_plot_legend(location=dpg.mvPlot_Location_NorthWest)
            x_scale: int = dpg.mvPlotScale_Time if time_axis else dpg.mvPlotScale_Linear
            x_axis: DpgItem = dpg.add_plot_axis(dpg.mvXAxis, scale=x_scale)
            y_scale: int = dpg.mvPlotScale_Log10 if chart.log else dpg.mvPlotScale_Linear
            with DpgSupport.context(dpg.plot_axis(dpg.mvYAxis, scale=y_scale)) as y_axis:
                for series in chart.series:
                    self._add_line_series(y_axis, series)
                for marker in chart.markers:
                    if not marker.points:
                        continue
                    item: DpgItem = dpg.add_scatter_series(
                        [point.x for point in marker.points],
                        [point.y for point in marker.points],
                        label=marker.label,
                    )
                    dpg.bind_item_theme(item, self._series_theme(self._color(marker.role), True))
            cursor_line: DpgItem = dpg.add_inf_line_series([0.0], parent=x_axis, show=False)
        if chart.note:
            dpg.add_text(chart.note, wrap=self._wrap_width())
        self._attach_cursor(chart_id, plot, cursor_line, chart.cursor_points())

    # 折れ線 1 本を足す。
    def _add_line_series(self, y_axis: DpgItem, series: LineSeries) -> None:
        """Dear PyGui (ImPlot) に線種が無いため、破線は短い線分の並びで描く。"""

        if not series.points:
            return
        xs: list[float] = [point.x for point in series.points]
        ys: list[float] = [point.y for point in series.points]
        item: DpgItem
        if series.dashed:
            if series.step:
                xs, ys = self._step_vertices(xs, ys)
            dash_xs, dash_ys = self._dashes(xs, ys)
            item = dpg.add_line_series(
                dash_xs, dash_ys, label=series.label, parent=y_axis, segments=True
            )
        elif series.step:
            item = dpg.add_stair_series(xs, ys, label=series.label, parent=y_axis)
        else:
            item = dpg.add_line_series(xs, ys, label=series.label, parent=y_axis)
        # 1 点だけの系列は線にならないため点を打つ。
        marker: bool = len(series.points) == 1
        dpg.bind_item_theme(item, self._series_theme(self._color(series.role), marker))

    # グラフの枠。
    def _plot(self) -> DpgContext[DpgItem]:
        """時刻軸はローカル時刻の 24 時間表記。

        拡大縮小は Ctrl + ホイールだけにし、ホイールはページのスクロールに使う。
        ImPlot の既定では入力を無視する修飾キーも Ctrl なので Alt へ移す。
        """

        return DpgSupport.context(
            dpg.plot(
                height=self._px(DashboardTheme.CHART_HEIGHT),
                width=-1,
                no_menus=True,
                use_local_time=True,
                use_24hour_clock=True,
                zoom_mod=dpg.mvKey_ModCtrl,
                override_mod=dpg.mvKey_ModAlt,
                query_toggle_mod=dpg.mvKey_ModAlt,
            )
        )

    # 階段線の頂点。
    @staticmethod
    def _step_vertices(xs: list[float], ys: list[float]) -> tuple[list[float], list[float]]:
        """各点で水平に進んでから垂直に上下する (canvas の H/V と同じ)。"""

        step_xs: list[float] = [xs[0]]
        step_ys: list[float] = [ys[0]]
        for x, y in zip(xs[1:], ys[1:], strict=True):
            step_xs += [x, x]
            step_ys += [step_ys[-1], y]
        return step_xs, step_ys

    # 破線の線分。
    @staticmethod
    def _dashes(xs: list[float], ys: list[float]) -> tuple[list[float], list[float]]:
        """x 範囲の DASH_FRACTION ごとに区切り、1 区間おきに線分 (始点と終点の組) を残す。"""

        span: float = (max(xs) - min(xs)) or 1.0
        step: float = span * DashboardTheme.DASH_FRACTION
        dash_xs: list[float] = []
        dash_ys: list[float] = []
        for index in range(len(xs) - 1):
            x0, y0, x1, y1 = xs[index], ys[index], xs[index + 1], ys[index + 1]
            if x1 == x0:
                # 垂直の区間 (階段の段差) は 1 本の線分にする。
                dash_xs += [x0, x1]
                dash_ys += [y0, y1]
                continue
            pieces: int = max(1, int(abs(x1 - x0) / step))
            for k in range(0, pieces, 2):
                start: float = k / pieces
                end: float = min(1.0, (k + 1) / pieces)
                dash_xs += [x0 + (x1 - x0) * start, x0 + (x1 - x0) * end]
                dash_ys += [y0 + (y1 - y0) * start, y0 + (y1 - y0) * end]
        return dash_xs, dash_ys

    # 積み上げ棒グラフ 1 枚。
    def _bar_chart(self, chart_id: str, chart: StackedBarChart) -> None:
        """棒は request の順番に並べ、目盛りに時刻か番号を付ける。"""

        self._heading(chart.title)
        if not chart.groups:
            dpg.add_text(chart.empty_text)
            return
        with self._plot() as plot:
            dpg.add_plot_legend(location=dpg.mvPlot_Location_NorthWest)
            x_axis: DpgItem = dpg.add_plot_axis(dpg.mvXAxis)
            with DpgSupport.context(dpg.plot_axis(dpg.mvYAxis)) as y_axis:
                self._add_stacked_bars(y_axis, chart)
            ticks: list[tuple[str, float]] = chart.ticks(
                self._state.x_mode, self._state.chart_width
            )
            dpg.set_axis_ticks(x_axis, tuple(ticks))
            cursor_line: DpgItem = dpg.add_inf_line_series([0.0], parent=x_axis, show=False)
        if chart.group_size > 1:
            dpg.add_text(
                f"Each bar sums {chart.group_size} consecutive requests "
                f"({chart.request_count} requests)."
            )
        points: list[ChartPoint] = [
            ChartPoint(float(index), sum(group.values), group.describe)
            for index, group in enumerate(chart.groups)
        ]
        self._attach_cursor(chart_id, plot, cursor_line, points)

    # 積み上げ棒を種別ごとの棒で描く。
    def _add_stacked_bars(self, y_axis: DpgItem, chart: StackedBarChart) -> None:
        """種別ごとに、下の種別までの累積の高さの棒を、上の種別から順に重ねて描く。

        積み上げの部品 (bar group) は色を colormap から順に割り当て、描き直すと割り当てが
        ずれることがあったため使わない。後に描いた棒が前に出るので、累積の大きい上の種別から描く。
        """

        xs: list[float] = [float(index) for index in range(len(chart.groups))]
        for part in reversed(range(len(chart.parts))):
            label, role = chart.parts[part]
            heights: list[float] = [sum(group.values[: part + 1]) for group in chart.groups]
            item: DpgItem = dpg.add_bar_series(xs, heights, label=label, weight=0.8, parent=y_axis)
            dpg.bind_item_theme(item, self._series_theme(self._color(role), False))

    # ポインタ選択と読み上げ欄を付ける。
    def _attach_cursor(
        self, chart_id: str, plot: DpgItem, cursor_line: DpgItem, points: list[ChartPoint]
    ) -> None:
        """前回選んでいた x の点があれば選び直す。"""

        readout: DpgItem = dpg.add_text(
            "Point at the chart to inspect values." if points else "", wrap=self._wrap_width()
        )
        xs: list[float] = [point.x for point in points]
        cursor: _ChartCursor = _ChartCursor(plot, cursor_line, readout, points, xs)
        self._cursors[chart_id] = cursor
        with DpgSupport.context(dpg.item_handler_registry()) as registry:
            dpg.add_item_hover_handler(callback=self._on_hover, user_data=chart_id)
        dpg.bind_item_handler_registry(plot, registry)
        self._handler_registries.append(registry)
        selected: float | None = self._state.chart_selection.get(chart_id)
        if selected is not None and selected in xs:
            self._select_point(chart_id, cursor, xs.index(selected))
        dpg.add_spacer(height=self._px(DashboardTheme.GROUP_SPACER_HEIGHT))

    # 系列の theme。
    def _series_theme(self, color: Rgb, marker: bool) -> DpgItem:
        """色とマーカーの有無ごとに 1 度だけ作って使い回す。"""

        cached: DpgItem | None = self._series_themes.get((color, marker))
        if cached is not None:
            return cached
        plots: int = dpg.mvThemeCat_Plots
        with DpgSupport.context(dpg.theme()) as theme:
            with DpgSupport.context(dpg.theme_component(dpg.mvAll)):
                for target in (
                    dpg.mvPlotCol_Line,
                    dpg.mvPlotCol_Fill,
                    dpg.mvPlotCol_MarkerFill,
                    dpg.mvPlotCol_MarkerOutline,
                ):
                    dpg.add_theme_color(target, color, category=plots)
                weight: float = DashboardTheme.SERIES_LINE_WEIGHT * self._theme().scale
                dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, weight, category=plots)
                if marker:
                    size: float = DashboardTheme.SERIES_MARKER_SIZE * self._theme().scale
                    shape: int = dpg.mvPlotMarker_Diamond
                    dpg.add_theme_style(dpg.mvPlotStyleVar_Marker, shape, category=plots)
                    dpg.add_theme_style(dpg.mvPlotStyleVar_MarkerSize, size, category=plots)
        self._series_themes[(color, marker)] = theme
        return theme

    # 色の役割から色へ。
    @classmethod
    def _color(cls, role: SeriesRole) -> Rgb:
        """固定の役割は SERIES_COLORS、エージェント (agent_<n>) は AGENT_SERIES の順。"""

        fixed: Rgb | None = cls.SERIES_COLORS.get(role)
        if fixed is not None:
            return fixed
        index: int = int(role.rsplit("_", 1)[1]) - 1
        return DashboardTheme.AGENT_SERIES[index % len(DashboardTheme.AGENT_SERIES)]

    # エージェントの表。
    def _render_agents(self, view: SessionUsageView) -> None:
        """使用量の記録が無いサブエージェントも監査のため残す。"""

        self._heading("Agents")
        if view["agents"]:
            self._table(SessionPresentation.agents_table(view))
        else:
            dpg.add_text("No agent usage recorded.")
        dpg.add_text(
            "Context = prompt tokens sent in a request (input including cache). "
            "Sub-agent context limits are not recorded.",
            wrap=self._wrap_width(),
        )
        dpg.add_spacer(height=self._px(DashboardTheme.GROUP_SPACER_HEIGHT))

    # 監査詳細。
    def _render_details(self, view: SessionUsageView) -> None:
        """折りたたみに request・モデル・token 種別・照合・圧縮・モデル変更・サブエージェント・
        tool・エラー・読み取り元を並べる。
        """

        events = view["events"]
        self._heading("Audit details")
        self._render_requests(view)
        self._table_section(
            "models",
            f"Models ({len(view['models'])})",
            SessionPresentation.models_table(view),
            "No models recorded.",
        )
        token_table, token_note = SessionPresentation.token_types(view)
        with self._section("token-types", "AI Credits by token type"):
            if token_table.rows:
                self._table(token_table)
                dpg.add_text(token_note, wrap=self._wrap_width())
            else:
                dpg.add_text("No per-token-type rate details recorded.")
        with self._section("reconciliation", "Reconciliation (reference)"):
            dpg.add_text(SessionPresentation.reconciliation_note(view), wrap=self._wrap_width())
            cumulative: TableData = SessionPresentation.cumulative_table(view)
            if cumulative.rows:
                self._table(cumulative)
            else:
                dpg.add_text("No cumulative events (checkpoint or shutdown) recorded.")
            app_table: TableData | None = SessionPresentation.app_table(view)
            if app_table is not None:
                self._table(app_table)
            else:
                dpg.add_text("The App has no totals for this session.")
        self._table_section(
            "compactions",
            f"Compactions ({len(events['compactions'])})",
            SessionPresentation.compactions_table(view),
            "No compactions recorded.",
        )
        self._table_section(
            "model-changes",
            f"Model changes ({len(events['modelChanges'])})",
            SessionPresentation.model_changes_table(view),
            "No model changes recorded.",
        )
        subagents: TableData = SessionPresentation.subagents_table(view)
        self._table_section(
            "subagents",
            f"Sub-agents ({len(subagents.rows)})",
            subagents,
            "No sub-agents in this session.",
        )
        self._table_section(
            "tools",
            f"Tools ({len(events['tools'])})",
            SessionPresentation.tools_table(view),
            "No tool calls recorded.",
        )
        with self._section("errors", "Errors and interruptions"):
            self._facts(SessionPresentation.error_facts(view))
        with self._section("session", "Session and sources"):
            self._facts(SessionPresentation.session_facts(view))

    # request ごとの表 (PAGE_SIZE 件ずつ)。
    def _render_requests(self, view: SessionUsageView) -> None:
        """ページ送りで PAGE_SIZE 件ずつ描く。"""

        requests: list[RequestView] = view["requests"]
        state: _TabState = self._state
        with self._section("requests", f"Requests ({len(requests)})"):
            if not requests:
                dpg.add_text("No requests recorded.")
                return
            pages: int = max(1, -(-len(requests) // self.PAGE_SIZE))
            state.request_page = min(state.request_page, pages - 1)
            summary: str = f"{len(requests)} requests"
            self._pager(None, state.request_page, pages, summary, self._request_page)
            start: int = state.request_page * self.PAGE_SIZE
            shown: list[RequestView] = requests[start : start + self.PAGE_SIZE]
            self._table(SessionPresentation.requests_table(view, shown))
            dpg.add_text("≠ = per-token-type cost does not add up to the recorded AI Credits.")

    # request の表のページ。
    def _request_page(self, page: int) -> None:
        """ページを変えて描き直す。"""

        self._state.request_page = page
        self._render_content()

    # ---- 部品 ---------------------------------------------------------------

    # 見出し。
    def _heading(self, text: str) -> None:
        """既存の見出しの font と色。"""

        theme: DashboardThemeAssets = self._theme()
        item: DpgItem = dpg.add_text(text)
        dpg.bind_item_theme(item, theme.heading_theme)
        if theme.heading_font is not None:
            dpg.bind_item_font(item, theme.heading_font)

    # 折りたたみ。
    def _section(self, key: str, title: str) -> DpgContext[DpgItem]:
        """開閉の状態を描き直し後も保つ (item は描き直しのたびに新しい ID)。"""

        item: DpgItem = dpg.generate_uuid()
        self._sections[key] = item
        return DpgSupport.context(
            dpg.collapsing_header(
                label=title, tag=item, default_open=key in self._state.open_sections
            )
        )

    # 表だけの折りたたみ。
    def _table_section(self, key: str, title: str, table: TableData, empty: str) -> None:
        """行が無ければ文言を出す。"""

        with self._section(key, title):
            if table.rows:
                self._table(table)
            else:
                dpg.add_text(empty)

    # 名前と値の 2 列。
    def _facts(self, entries: Sequence[tuple[str, str]]) -> None:
        """長い値は折り返す。"""

        wrap: int = max(self._px(200), int(self._wrap_width() * 0.7))
        with DpgSupport.context(dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp)):
            dpg.add_table_column(init_width_or_weight=0.25)
            dpg.add_table_column(init_width_or_weight=0.75)
            for name, value in entries:
                with DpgSupport.context(dpg.table_row()):
                    dpg.add_text(name)
                    dpg.add_text(value, wrap=wrap)

    # 表。
    def _table(self, data: TableData) -> None:
        """既存の TableRenderPlan で数値列を右端で揃え、等幅 font を当てる。"""

        theme: DashboardThemeAssets = self._theme()
        spec: TableSpec = TableSpec(
            tag="su_table",
            columns=tuple(
                ColumnSpec(header, self._cell_reader(index), numeric)
                for index, (header, numeric) in enumerate(data.columns)
            ),
            source="session_usage",
        )
        rows: list[dict[str, object]] = [
            {f"c{index}": value for index, value in enumerate(row)} for row in data.rows
        ]
        plan: TableRenderPlan = TableRenderPlan.build(spec, rows, theme.numeric_alignment)
        with (
            self._table_frame(),
            DpgSupport.context(
                dpg.table(
                    header_row=True,
                    row_background=True,
                    borders_innerH=True,
                    resizable=True,
                    policy=dpg.mvTable_SizingFixedFit,
                    width=self._table_width(list(plan.headers), plan.cells),
                )
            ),
        ):
            for header in plan.headers:
                dpg.add_table_column(label=header)
            for row in plan.cells:
                with DpgSupport.context(dpg.table_row()):
                    for index, value in enumerate(row):
                        cell: DpgItem = dpg.add_text(value)
                        if index in plan.numeric_font_columns and theme.mono_font is not None:
                            dpg.bind_item_font(cell, theme.mono_font)

    # 表を入れる枠。
    @staticmethod
    def _table_frame(parent: str | None = None) -> DpgContext[DpgItem]:
        """高さは中身に合わせ、横にはみ出した列は横スクロールバーで見せ、ホイールは受けない枠。

        表自体にスクロールを付けると、縦にスクロールできない表でも Dear ImGui が縦のホイールを
        横スクロールに使い、表の上でページがスクロールしなくなる (2026-10-03 のユーザー確認)。
        高さを行の高さの見積りで決めると、等幅 font の行が見積りより高く下の行が見切れたため、
        中身に合わせる (auto_resize_y)。
        """

        if parent is not None:
            return DpgSupport.context(
                dpg.child_window(
                    parent=parent,
                    width=-1,
                    auto_resize_y=True,
                    border=False,
                    horizontal_scrollbar=True,
                    no_scroll_with_mouse=True,
                )
            )
        return DpgSupport.context(
            dpg.child_window(
                width=-1,
                auto_resize_y=True,
                border=False,
                horizontal_scrollbar=True,
                no_scroll_with_mouse=True,
            )
        )

    # 表の幅の見積り。
    def _table_width(self, headers: Sequence[str], rows: Sequence[Sequence[str]]) -> int:
        """列ごとの最長の文字列から表の幅を見積もる。

        スクロールの無い表は Dear ImGui が親の幅に収まるよう列を詰めるため、列の内容が
        収まる幅を明示し、枠の横スクロールで見せる。全角文字は 2 文字分、見出しは並べ替えの
        印の分を足す。文字幅は等幅 font の約 0.65 倍で、やや広めに見積もる。
        """

        theme: DashboardThemeAssets = self._theme()
        char: float = DashboardTheme.FONT_SIZE_MONO * theme.scale * theme.font_factor * 0.65
        padding: int = self._px(2 * DashboardTheme.CELL_PADDING[0] + 4)
        total: int = 0
        for index, header in enumerate(headers):
            widths: list[int] = [self._text_columns(header) + 2]
            widths += [self._text_columns(row[index]) for row in rows]
            total += int(max(widths) * char) + padding
        return total

    # 文字列の表示上の桁数。
    @staticmethod
    def _text_columns(text: str) -> int:
        """全角 (East Asian Wide / Fullwidth) は 2 桁。"""

        return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in text)

    # 列の値を取り出す関数。
    @staticmethod
    def _cell_reader(index: int) -> Callable[[dict[str, object]], str]:
        """TableSpec の行 (c0, c1, ...) から文字列を取り出す。"""

        key: str = f"c{index}"
        return lambda row: str(row[key])

    # ページ送り。
    def _pager(
        self, parent: str | None, page: int, pages: int, summary: str, go: Callable[[int], None]
    ) -> None:
        """First / Previous / Next / Last と現在のページ。"""

        group: DpgItem = (
            dpg.add_group(horizontal=True, parent=parent)
            if parent
            else dpg.add_group(horizontal=True)
        )
        first: bool = page == 0
        last: bool = page >= pages - 1
        dpg.add_button(label="First", parent=group, enabled=not first, callback=lambda: go(0))
        dpg.add_button(
            label="Previous", parent=group, enabled=not first, callback=lambda: go(page - 1)
        )
        dpg.add_text(f"Page {page + 1} of {pages} · {summary}", parent=group)
        dpg.add_button(label="Next", parent=group, enabled=not last, callback=lambda: go(page + 1))
        dpg.add_button(label="Last", parent=group, enabled=not last, callback=lambda: go(pages - 1))

    # 文の折り返し幅。
    def _wrap_width(self) -> int:
        """本体の幅から余白を引く。"""

        return max(
            self._px(400),
            dpg.get_viewport_client_width() - self._px(6 * DashboardTheme.WINDOW_PADDING[0]),
        )

    # グラフの描画幅。
    def _chart_width(self) -> int:
        """棒のまとめ方と目盛りの数に使う (y 軸の目盛りと余白を除く)。

        棒の最小幅は文字と同じく 100% の設計値なので、表示倍率で割った幅を返す。
        """

        scale: float = self._theme().scale
        actual: int = dpg.get_viewport_client_width() - self._px(self.CHART_SIDE_MARGIN)
        return max(200, round(actual / scale))

    # 文字の大きさに連動する設計値の px を実寸にする。
    def _fpx(self, value: float) -> int:
        """文字数で決める幅の設計値に、表示倍率と文字の大きさを掛ける。"""

        return self._theme().fpx(value)

    # 設計値の px を表示倍率の実寸にする。
    def _px(self, value: float) -> int:
        """幅・高さの設計値に表示倍率を掛ける。"""

        return self._theme().px(value)
