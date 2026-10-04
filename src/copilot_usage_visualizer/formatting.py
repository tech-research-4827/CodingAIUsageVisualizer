from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation


# 表示用の値整形を担当する。
class UsageValueFormatter:
    """HTML report と GUI dashboard で共有する表示整形を提供する。"""

    # 金額の確度とモデル帰属の推定を混同しない共通注記。
    PRIMARY_MODEL_NOTE: str = (
        "* includes estimated model attribution; Credits are direct values. "
        "Estimated Credits are part of the total, not an extra charge. "
        "Unattrib. means Credits without confirmed model attribution, including estimates."
    )
    # 内訳で金額とtokenが別行になる理由を明示する。
    BREAKDOWN_NOTE: str = (
        "Each record belongs to one breakdown row. Estimated Credits may have tokens on a "
        "separate reference row. Included compaction is already in the cumulative total; "
        "a dash is not a separately measured zero."
    )

    # モデルの確定度は名前を分裂させず印と推定額で示す。
    @staticmethod
    def aggregate_model_label(model: str | None, estimated_credits: object) -> str:
        """合計表・円グラフ用の共通モデル名を返す。"""

        if model is None:
            return "Unknown model"
        estimated: bool = isinstance(estimated_credits, int | float) and estimated_credits > 0
        return f"* {model}" if estimated else model

    # 推定マーカーを先頭へ置き、短縮した凡例でも識別可能にする。
    @staticmethod
    def model_label(
        model: str | None, estimated_primary: str | None, token_reference: bool = False
    ) -> str:
        """確定内訳・推定総額・ラベルなし総額を区別する。"""

        if model is not None:
            return f"Token reference: {model}" if token_reference else model
        if estimated_primary is not None:
            return f"Estimated primary: {estimated_primary}"
        return "Unattributed session total (no estimate)"

    # 日別グラフで0・欠落・参照専用を区別する。
    @staticmethod
    def daily_label(row: Mapping[str, object]) -> str:
        """棒の有無に依存しない軸ラベルを返す。"""

        label: str = str(row.get("date", ""))
        if row.get("ai_credits") is not None:
            return label
        missing: object = row.get("missing_credits_count", 0)
        reference: object = row.get("reference_records_count", 0)
        if isinstance(missing, int) and missing > 0:
            return label + " (unavailable)"
        if isinstance(reference, int) and reference > 0:
            return label + " (reference only)"
        return label + " (no data)"

    # integer を整形する。
    @staticmethod
    def integer(value: object) -> str:
        """integer value を comma-separated string に変換する。"""

        if isinstance(value, int):
            return f"{value:,}"
        return "-"

    # AI Credits を整形する。
    @staticmethod
    def credits(value: object) -> str:
        """AI Credits value を最大9桁、最低3桁の小数で整形する。"""

        if not isinstance(value, Decimal | int | float) or isinstance(value, bool):
            return "-"
        try:
            decimal_value: Decimal = Decimal(str(value))
        except InvalidOperation:
            return "-"
        if not decimal_value.is_finite():
            return "-"
        rendered: str = f"{decimal_value:,.9f}"
        integer_part, separator, fraction = rendered.partition(".")
        if separator != ".":
            return rendered
        trimmed_fraction: str = fraction.rstrip("0")
        padded_fraction: str = trimmed_fraction.ljust(3, "0")
        return f"{integer_part}.{padded_fraction}"

    # USD を整形する。
    @staticmethod
    def usd(value: object) -> str:
        """USD value を 2 桁小数で整形する。"""

        if isinstance(value, int | float):
            return f"${float(value):,.2f}"
        return "-"

    # JPY を整形する。
    @staticmethod
    def jpy(value: object) -> str:
        """JPY value を整数で整形する。"""

        if isinstance(value, int | float):
            return f"¥ {float(value):,.0f}"
        return "-"
