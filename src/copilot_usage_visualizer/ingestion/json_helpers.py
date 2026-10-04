from __future__ import annotations


# JSON object から型付き値を取り出す境界 helper。
class JsonObjectReader:
    """parser 境界で raw JSON object から安全に値を抽出する。"""

    # 文字列値を取得する。
    @staticmethod
    def string_value(data: dict[str, object], keys: tuple[str, ...]) -> str | None:
        """指定 key のいずれかに存在する文字列を返す。"""

        for key in keys:
            value: object | None = data.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    # 整数値を取得する。
    @staticmethod
    def int_value(data: dict[str, object], keys: tuple[str, ...]) -> int:
        """指定 key のいずれかに存在する整数値を返す。"""

        for key in keys:
            value: object | None = data.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value)
        return 0

    # 辞書値を取得する。
    @staticmethod
    def object_value(data: dict[str, object], key: str) -> dict[str, object] | None:
        """指定 key の値が JSON object なら返す。"""

        value: object | None = data.get(key)
        if isinstance(value, dict):
            return value
        return None
