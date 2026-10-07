from __future__ import annotations

from datetime import date, timedelta
from typing import TypedDict

from copilot_usage_visualizer.defaults import AppDefaults


# dearpygui の date picker が受け渡す値。
class PickerDate(TypedDict):
    """dearpygui の date picker の値 (C の tm 構造体と同じ基準)。

    `month` は 0 始まり、`year` は 1900 年からの年数。
    """

    month_day: int
    month: int
    year: int


# 期間の一括指定を求める。
class DateRangePresets:
    """「先月」「今月」「今日」の期間を YYYY-MM-DD の組で返す。"""

    # 前月の 1 日から末日。
    @staticmethod
    def last_month(today: date) -> tuple[str, str]:
        """前月の月初・月末を返す (1 月は前年 12 月)。"""

        return AppDefaults.month_range(today.replace(day=1) - timedelta(days=1))

    # 今月の 1 日から末日。
    @staticmethod
    def this_month(today: date) -> tuple[str, str]:
        """起動時の既定値と同じ月初・月末を返す。"""

        return AppDefaults.month_range(today)

    # 今日だけ。
    @staticmethod
    def today(today: date) -> tuple[str, str]:
        """開始・終了とも今日にする。"""

        return today.isoformat(), today.isoformat()


# 入力欄の文字列と date picker の値を相互に変換する。
class DatePickerValue:
    """date picker は入力欄の補助であり、正本は YYYY-MM-DD の入力欄とする。"""

    # 入力欄の値から date picker の初期値を作る。
    @staticmethod
    def to_picker(text: str, today: date) -> PickerDate:
        """不正な日付の時は今日を開く (入力欄の値は変えない)。

        正規の YYYY-MM-DD だけを日付とみなす。`date.fromisoformat` は `20260901` や
        `2026-W36-2` も受け付けるが、保存時の検証 (`DashboardSettings.validate`) は
        拒否するため、それらも不正として扱う。
        """

        value: date = today
        try:
            parsed: date = date.fromisoformat(text)
        except ValueError:
            parsed = today
        if parsed.isoformat() == text:
            value = parsed
        return PickerDate(month_day=value.day, month=value.month - 1, year=value.year - 1900)

    # date picker の値を入力欄の文字列にする。
    @staticmethod
    def from_picker(value: PickerDate) -> str:
        """tm 基準の値を YYYY-MM-DD に戻す。"""

        return date(value["year"] + 1900, value["month"] + 1, value["month_day"]).isoformat()
