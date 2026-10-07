from __future__ import annotations

import math
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from copilot_usage_visualizer.formatting import UsageValueFormatter


# セッション使用量の表示用の数値整形。
class SessionUsageFormat:
    """web/format.mjs と同じ表記。nano AIU は整数のまま受け取り、表示の境界で換算する。"""

    NANO_PER_CREDIT: int = 1_000_000_000
    # 参考換算: 1 AI Credit = 0.01 USD (web/format.mjs、Python 側 AI_CREDIT_USD_RATE と同じ値)。
    USD_CENTS_PER_CREDIT: int = 1

    # nano AIU を AI Credits の 10 進文字列へ。
    @classmethod
    def credits_text(cls, nano: str | int | None) -> str:
        """小数は最大 9 桁、末尾の 0 を削って最低 3 桁 (既存 report と同じ規則)。記録なしは `-`。"""

        if nano is None:
            return "-"
        return UsageValueFormatter.credits(Decimal(int(nano)).scaleb(-9))

    # 小数 2 桁に四捨五入した AI Credits。
    @classmethod
    def credits_rounded(cls, nano: str | int | None, digits: int = 2) -> str:
        """カード用。完全な値は credits_text で別に示す。"""

        if nano is None:
            return "-"
        value: Decimal = Decimal(int(nano)).scaleb(-9)
        rounded: Decimal = abs(value).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
        sign: str = "-" if value < 0 else ""
        return f"{sign}{rounded:,.{digits}f}"

    # 参考 USD (セント単位で丸め)。
    @classmethod
    def usd_text(cls, nano: str | int | None) -> str:
        """1 AI Credit = 1 セント。"""

        if nano is None:
            return "-"
        cents: int = (
            int(nano) * cls.USD_CENTS_PER_CREDIT + cls.NANO_PER_CREDIT // 2
        ) // cls.NANO_PER_CREDIT
        sign: str = "-" if cents < 0 else ""
        whole: int = abs(cents)
        return f"{sign}${whole // 100:,}.{whole % 100:02d}"

    # グラフ用の近似値。
    @classmethod
    def credits_number(cls, nano: str | int | None) -> float | None:
        """合計の表示には使わない。"""

        return None if nano is None else int(nano) / cls.NANO_PER_CREDIT

    # 整数の桁区切り。
    @staticmethod
    def int_text(value: object) -> str:
        """None は `-` (欠落を 0 と表示しない)。小数は四捨五入する。"""

        if isinstance(value, bool) or not isinstance(value, int | float):
            return "-"
        if isinstance(value, float):
            if not math.isfinite(value):
                return "-"
            value = math.floor(value + 0.5)
        return UsageValueFormatter.integer(value)

    # 1.2k / 3.4M 形式。
    @classmethod
    def compact_text(cls, value: object) -> str:
        """完全な値は表やツールチップで併記する。"""

        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            return "-"
        magnitude: float = abs(value)
        for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
            if magnitude >= size:
                scaled: float = value / size
                digits: int = 0 if abs(scaled) >= 100 else 1 if abs(scaled) >= 10 else 2
                return f"{cls._fixed(scaled, digits)}{suffix}"
        return str(math.floor(value + 0.5))

    # 時刻 (ローカル時刻 HH:MM:SS)。
    @staticmethod
    def time_text(millis: int | float | None) -> str:
        """epoch ミリ秒をローカル時刻にする。"""

        if millis is None:
            return "-"
        return datetime.fromtimestamp(millis / 1000).strftime("%H:%M:%S")

    # 日時 (ローカル時刻 YYYY-MM-DD HH:MM:SS)。
    @staticmethod
    def date_time_text(millis: int | float | None) -> str:
        """epoch ミリ秒をローカル日時にする。"""

        if millis is None:
            return "-"
        return datetime.fromtimestamp(millis / 1000).strftime("%Y-%m-%d %H:%M:%S")

    # 所要時間。
    @classmethod
    def duration_text(cls, millis: object) -> str:
        """ "1h 02m" / "3m 04s" / "5.2s" / "830ms"。"""

        if (
            isinstance(millis, bool)
            or not isinstance(millis, int | float)
            or not math.isfinite(millis)
        ):
            return "-"
        if millis < 1000:
            return f"{math.floor(millis + 0.5)}ms"
        seconds: float = millis / 1000
        if seconds < 60:
            return f"{cls._fixed(seconds, 1)}s"
        minutes: int = math.floor(seconds / 60)
        if minutes < 60:
            return f"{minutes}m {math.floor(seconds % 60):02d}s"
        return f"{minutes // 60}h {minutes % 60:02d}m"

    # 比率 (%)。
    @classmethod
    def percent_text(cls, part: object, whole: object) -> str:
        """分母が無ければ `-`。"""

        if (
            isinstance(part, bool)
            or isinstance(whole, bool)
            or not isinstance(part, int | float)
            or not isinstance(whole, int | float)
            or whole <= 0
        ):
            return "-"
        return f"{cls._fixed(part / whole * 100, 1)}%"

    # Number.prototype.toFixed と同じ丸め (2 進の値に最も近い桁、中間は大きい方)。
    @staticmethod
    def _fixed(value: float, digits: int) -> str:
        """Python の書式指定は偶数丸めのため Decimal で丸める。"""

        quantum: Decimal = Decimal(1).scaleb(-digits)
        rounded: Decimal = Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP)
        return f"{rounded:.{digits}f}"
