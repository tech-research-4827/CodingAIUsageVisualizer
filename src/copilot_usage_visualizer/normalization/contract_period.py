from __future__ import annotations

from dataclasses import dataclass
from datetime import date


# timestamp から集計期間判定用の日付を取得する。
@dataclass(frozen=True)
class UsageDateResolver:
    """usage timestamp から集計期間判定用の wall-clock date を抽出する。"""

    # timestamp から date 部分を取り出す。
    def resolve(self, timestamp: str) -> date:
        """ISO timestamp 文字列の先頭から日付を抽出する。"""

        if len(timestamp) < 10:
            raise ValueError("timestamp must start with YYYY-MM-DD.")
        value: str = timestamp[:10]
        if value[4] != "-" or value[7] != "-":
            raise ValueError("timestamp must start with YYYY-MM-DD.")
        return date.fromisoformat(value)
