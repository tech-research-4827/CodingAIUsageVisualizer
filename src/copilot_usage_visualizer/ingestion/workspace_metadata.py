from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse


# workspace metadata の path-like value を正規化する。
class WorkspaceMetadataNormalizer:
    """workspace metadata 由来の raw identity と表示 alias を正規化する。"""

    # path-like value を raw identity へ正規化する。
    @staticmethod
    def identity(value: str) -> str | None:
        """workspace metadata の path-like value を identity へ正規化する。"""

        if not value:
            return None
        stripped_value: str = value.strip()
        if (stripped_value.startswith('"') and stripped_value.endswith('"')) or (
            stripped_value.startswith("'") and stripped_value.endswith("'")
        ):
            stripped_value = stripped_value[1:-1]
        if not stripped_value:
            return None
        if stripped_value.startswith("file://"):
            parsed = urlparse(stripped_value)
            return unquote(parsed.path) or None
        return stripped_value

    # raw identity から表示 alias を作成する。
    @staticmethod
    def alias(workspace_identity: str | None) -> str | None:
        """raw path を保存せず basename 相当の alias を返す。"""

        if workspace_identity is None:
            return None
        return Path(workspace_identity).name
