from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import TypeAlias

# Node 版と同じ入力で同じ出力にするため、JavaScript の値の扱い (Number.isSafeInteger、Date.parse、
# toISOString、String.prototype.trim、localeCompare) のうち集計規則が依存する部分だけを再現する。

# JSON 値 (json.loads の結果) の型。I/O 境界の値なので型を絞らない。
JsonValue: TypeAlias = "None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]"


# JavaScript の値の判定と変換を集める。
class JsValue:
    """Node 版の判定 (lib/history.mjs, lib/events.mjs) と同じ結果を返す変換。"""

    MAX_SAFE_INTEGER: int = 2**53 - 1
    # String.prototype.trim が除く空白と行終端 (ECMAScript の WhiteSpace と LineTerminator)。
    WHITESPACE: str = "\t\n\v\f\r                  　﻿"
    # timezone の明示 (Z または ±HH:MM / ±HHMM) で終わる時刻だけを受け付ける。
    ZONE_SUFFIX: re.Pattern[str] = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")
    # Date.parse が受け付ける ISO 形式のうち、記録に現れる形 (日付と時刻、任意の秒・小数秒)。
    ISO_TIME: re.Pattern[str] = re.compile(
        r"^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d+))?)?"
        r"(Z|z|[+-]\d{2}:?\d{2})$"
    )
    # Number(string) が数値にする 10 進表記。
    DECIMAL_TEXT: re.Pattern[str] = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
    EPOCH: datetime = datetime(1970, 1, 1, tzinfo=UTC)

    # 安全な非負整数 (bool を除く int、または整数値の float) を int で返す。
    @classmethod
    def safe_int(cls, value: object) -> int | None:
        """Number.isSafeInteger(value) && value >= 0 に当たる値を int で返す。"""

        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if 0 <= value <= cls.MAX_SAFE_INTEGER else None
        if isinstance(value, float) and value.is_integer() and 0 <= value <= cls.MAX_SAFE_INTEGER:
            return int(value)
        return None

    # SQLite の値から非負の nano AIU を int で返す。
    @classmethod
    def nano_from_sqlite(cls, value: object) -> int | None:
        """INTEGER は範囲を問わず非負なら採用し、
        REAL は安全な整数だけを採用する (Node の BigInt 読みと同じ)。
        """

        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value >= 0 else None
        return cls.safe_int(value)

    # 有限の数値を返す。
    @staticmethod
    def finite(value: object) -> int | float | None:
        """INTEGER はそのまま、REAL は有限の時だけ返す。"""

        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and math.isfinite(value):
            return value
        return None

    # 空白だけでない文字列を返す。
    @classmethod
    def text(cls, value: object) -> str | None:
        """typeof value === "string" && value.trim() の時だけ元の文字列を返す。"""

        if isinstance(value, str) and value.strip(cls.WHITESPACE):
            return value
        return None

    # JavaScript の Number(value) を求める。
    @classmethod
    def to_number(cls, value: object) -> float:
        """null は 0、数値文字列は数値、それ以外の文字列やバイト列は NaN。"""

        if value is None:
            return 0.0
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            text: str = value.strip(cls.WHITESPACE)
            if not text:
                return 0.0
            if text in ("Infinity", "+Infinity"):
                return math.inf
            if text == "-Infinity":
                return -math.inf
            lowered: str = text.lower()
            for prefix, base in (("0x", 16), ("0o", 8), ("0b", 2)):
                if lowered.startswith(prefix):
                    try:
                        return float(int(text[2:], base))
                    except ValueError:
                        return math.nan
            if cls.DECIMAL_TEXT.match(text):
                return float(text)
        return math.nan

    # timezone 付きの時刻を epoch ミリ秒にする。
    @classmethod
    def parse_time(cls, value: object) -> int | None:
        """timezone の明示が無い時刻は推測せず None を返す
         (Date.parse の小数秒はミリ秒に切り捨て)。
        """

        if not isinstance(value, str) or not cls.ZONE_SUFFIX.search(value):
            return None
        match: re.Match[str] | None = cls.ISO_TIME.match(value)
        if match is None:
            return None
        year, month, day, hour, minute = (int(match.group(i)) for i in range(1, 6))
        second: int = int(match.group(6) or 0)
        millis: int = int((match.group(7) or "0")[:3].ljust(3, "0"))
        zone: str = match.group(8)
        offset: timedelta = timedelta(0)
        if zone not in ("Z", "z"):
            digits: str = zone[1:].replace(":", "")
            offset = timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
            if zone[0] == "-":
                offset = -offset
        try:
            moment: datetime = datetime(
                year, month, day, hour, minute, second, millis * 1000, tzinfo=timezone(offset)
            )
        except ValueError:
            return None
        return (moment - cls.EPOCH) // timedelta(milliseconds=1)

    # epoch ミリ秒を toISOString と同じ文字列にする。
    @classmethod
    def iso_string(cls, millis: int) -> str:
        """YYYY-MM-DDTHH:MM:SS.sssZ (UTC)。"""

        moment: datetime = cls.EPOCH + timedelta(milliseconds=millis)
        return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"

    # timezone 付きの時刻を toISOString の形に揃える。
    @classmethod
    def iso_time(cls, value: object) -> str | None:
        """parse_time できる時刻だけを UTC の ISO 文字列で返す。"""

        millis: int | None = cls.parse_time(value)
        return None if millis is None else cls.iso_string(millis)

    # JSON.parse と同じ範囲の JSON を読む。
    @staticmethod
    def parse_json(text: str) -> JsonValue:
        """NaN / Infinity の表記は JSON.parse と同じく受け付けない (ValueError)。"""

        # parse_constant は NaN / Infinity / -Infinity の表記でだけ呼ばれる。
        def reject(constant: str) -> JsonValue:
            raise ValueError(f"invalid JSON constant {constant}")

        parsed: JsonValue = json.loads(text, parse_constant=reject)
        return parsed


# localeCompare (ICU の root 照合) の順を ASCII の範囲で再現する。
class LocaleOrder:
    """記号 < 数字 < 英字 (大文字小文字を区別しない) の順に比べ、同じなら小文字を先にする。"""

    # ICU root 照合での ASCII の空白・記号の順。
    PUNCTUATION: str = "\t\n\v\f\r _-,;:!?.'\"()[]{}@*/\\&#%`^+<=>|~$"

    # 並べ替えの key を返す。
    @classmethod
    def key(cls, value: str) -> tuple[tuple[tuple[int, int], ...], tuple[int, ...]]:
        """第 1 段は文字の種類と大文字小文字を同一視した文字、第 2 段は大文字かどうか。"""

        primary: list[tuple[int, int]] = []
        case: list[int] = []
        for char in value:
            position: int = cls.PUNCTUATION.find(char)
            if position >= 0:
                primary.append((0, position))
            elif char.isascii() and char.isdigit():
                primary.append((1, ord(char)))
            elif char.isascii() and char.isalpha():
                primary.append((2, ord(char.lower())))
            else:
                # ASCII 外は ICU の順を再現せず、英字の後に code point 順で置く。
                primary.append((3, ord(char)))
            case.append(1 if char.isupper() else 0)
        return tuple(primary), tuple(case)
