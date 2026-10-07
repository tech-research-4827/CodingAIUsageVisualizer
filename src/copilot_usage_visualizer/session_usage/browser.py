from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from copilot_usage_visualizer.session_usage.contract import SessionListEntry
from copilot_usage_visualizer.session_usage.format import SessionUsageFormat
from copilot_usage_visualizer.session_usage.js_compat import JsValue, LocaleOrder

SortKey = Literal["id", "credits", "requests", "activity", "project"]
SortDirection = Literal["asc", "desc"]
SortValue = str | int | None


# 並べ替えの列。
@dataclass(frozen=True)
class SortColumn:
    """見出し・既定の向き・比べる値。"""

    key: SortKey
    label: str
    default_direction: SortDirection
    numeric: bool
    value: Callable[[SessionListEntry], SortValue]


# 一覧の絞り込み条件。
@dataclass(frozen=True)
class BrowserFilter:
    """文字 (ID・repository・branch の部分一致、空白区切りは全て含む)
     と最終活動のローカル日付の範囲。
    """

    text: str = ""
    date_from: str = ""
    date_to: str = ""
    sort: SortKey = "credits"
    direction: SortDirection = "desc"


# セッション一覧の絞り込み・並べ替えと見出し。
class SessionBrowser:
    """web/app.mjs の一覧と同じ規則。

    空の値は昇順・降順どちらでも末尾、同順位は最終活動の新しい順。
    """

    LOCAL_DATE: re.Pattern[str] = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

    # Project 欄の文字。
    @staticmethod
    def project_text(entry: SessionListEntry) -> str | None:
        """repository@branch。repository が無ければ None。"""

        repository: str | None = entry["repository"]
        if not repository:
            return None
        branch: str | None = entry["branch"]
        return f"{repository}@{branch}" if branch else repository

    # 並べ替えの列の一覧。
    @classmethod
    def columns(cls) -> tuple[SortColumn, ...]:
        """列順は長い Project を最後に置く。"""

        return (
            SortColumn("id", "Session", "asc", False, lambda entry: entry["id"]),
            SortColumn(
                "credits",
                "AI Credits",
                "desc",
                True,
                lambda entry: None if entry["creditsNano"] is None else int(entry["creditsNano"]),
            ),
            SortColumn("requests", "Requests", "desc", True, lambda entry: entry["requests"]),
            SortColumn(
                "activity",
                "Last activity",
                "desc",
                False,
                lambda entry: JsValue.parse_time(entry["lastActivity"]),
            ),
            SortColumn(
                "project",
                "Project",
                "asc",
                False,
                lambda entry: (cls.project_text(entry) or "").lower() or None,
            ),
        )

    # 列を key で探す。
    @classmethod
    def column(cls, key: SortKey) -> SortColumn:
        """未知の key は無い (SortKey の範囲)。"""

        return next(column for column in cls.columns() if column.key == key)

    # 見出しを押した時の新しい並べ替え条件。
    @classmethod
    def toggle_sort(cls, current: BrowserFilter, key: SortKey) -> BrowserFilter:
        """同じ列なら向きを反転し、別の列ならその列の既定の向きにする。"""

        if current.sort == key:
            direction: SortDirection = "asc" if current.direction == "desc" else "desc"
            return BrowserFilter(current.text, current.date_from, current.date_to, key, direction)
        return BrowserFilter(
            current.text, current.date_from, current.date_to, key, cls.column(key).default_direction
        )

    # 絞り込んで並べ替えた一覧。
    @classmethod
    def filtered(
        cls, sessions: Sequence[SessionListEntry], condition: BrowserFilter
    ) -> list[SessionListEntry]:
        """日付の範囲は From の日の始まりから To の翌日の始まりまで (ローカル時刻)。"""

        terms: list[str] = condition.text.lower().split()
        start: int | None = cls._local_day_start(condition.date_from)
        to_start: int | None = cls._local_day_start(condition.date_to, days_after=1)
        matched: list[SessionListEntry] = []
        for entry in sessions:
            haystack: str = "\n".join(
                (entry["id"], entry["repository"] or "", entry["branch"] or "")
            ).lower()
            if not all(term in haystack for term in terms):
                continue
            if start is not None or to_start is not None:
                t: int | None = JsValue.parse_time(entry["lastActivity"])
                if t is None:
                    continue
                if start is not None and t < start:
                    continue
                if to_start is not None and t >= to_start:
                    continue
            matched.append(entry)
        column: SortColumn = cls.column(condition.sort)
        # 同順位は最終活動の新しい順 (先に並べ、安定な並べ替えで保つ)。
        matched.sort(key=lambda entry: LocaleOrder.key(entry["lastActivity"] or ""), reverse=True)
        present: list[SessionListEntry] = [
            entry for entry in matched if column.value(entry) is not None
        ]
        empty: list[SessionListEntry] = [entry for entry in matched if column.value(entry) is None]
        present.sort(
            key=lambda entry: cls._comparable(column.value(entry)),
            reverse=condition.direction == "desc",
        )
        return present + empty

    # コンボボックスなどに出す 1 行の見出し。
    @classmethod
    def label(cls, entry: SessionListEntry) -> str:
        """ID の先頭 8 文字・Project・最終活動・Credits・Requests。"""

        parts: list[str] = [entry["id"][:8]]
        project: str | None = cls.project_text(entry)
        if project:
            parts.append(project)
        t: int | None = JsValue.parse_time(entry["lastActivity"])
        if t is not None:
            parts.append(SessionUsageFormat.date_time_text(t))
        if entry["creditsNano"] is not None:
            parts.append(f"{SessionUsageFormat.credits_rounded(entry['creditsNano'])} credits")
        parts.append(f"{SessionUsageFormat.int_text(entry['requests'])} req")
        return " · ".join(parts)

    # 入力欄の日付をローカル時刻のその日の始まりへ。
    @classmethod
    def _local_day_start(cls, text: str, days_after: int = 0) -> int | None:
        """YYYY-MM-DD 以外 (空欄・不正な日付) は条件なし。"""

        match: re.Match[str] | None = cls.LOCAL_DATE.fullmatch(text.strip())
        if match is None:
            return None
        try:
            day: datetime = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
        return int((day + timedelta(days=days_after)).timestamp() * 1000)

    # 並べ替えの値を比べられる形にする。
    @staticmethod
    def _comparable(value: SortValue) -> tuple[int, str]:
        """数値は桁を揃えた文字列にせず、型ごとに比べる (同じ列の値は同じ型)。"""

        if isinstance(value, int):
            return (value, "")
        return (0, value or "")
