from __future__ import annotations

import hashlib
from os import stat_result
from pathlib import Path

from copilot_usage_visualizer.normalization.usage import FileScanState


# ファイル内容を読まず、既存の解析結果を再利用できるか判断する。
class FileChangeDetector:
    """解析規則と履歴の対応先も含めた読取前metadataを作る。"""

    # CLIの解析・正規化規則を変更した時に更新し、保存済み結果を再評価する。
    CONTRACT_VERSION: str = "cli-events-v1"

    # repositoryの既存path_hashと同じ匿名化を探索時にも使う。
    @staticmethod
    def path_hash(path: Path) -> str:
        """解決済みpathのhashだけを状態照合に使う。"""

        return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()

    # stat失敗は成功済みとみなさず、parserの既存エラー処理へ渡す。
    @classmethod
    def capture(cls, path: Path, history_scope: str | None) -> FileScanState | None:
        """解析前の状態を取得し、情報を得られない時は省略を許さない。"""

        try:
            metadata: stat_result = path.stat()
            try:
                workspace: stat_result | None = path.with_name("workspace.yaml").stat()
            except FileNotFoundError:
                workspace = None
        except OSError:
            return None
        # inodeのないfilesystemでは同名置換を区別できないため再解析する。
        if not metadata.st_ino or (workspace is not None and not workspace.st_ino):
            return None
        identity: tuple[object, ...] = (
            cls.CONTRACT_VERSION, cls._metadata_identity(metadata),
            cls._metadata_identity(workspace) if workspace is not None else None, history_scope,
        )
        fingerprint: str = "scan-v1:" + hashlib.sha256(repr(identity).encode("utf-8")).hexdigest()
        return FileScanState(metadata.st_mtime_ns, metadata.st_size, fingerprint)

    # イベントとworkspaceの両方で、内容を読まず同じ変更情報を照合する。
    @staticmethod
    def _metadata_identity(metadata: stat_result) -> tuple[int, int, int, int, int]:
        """本文・パスを含まない比較値を返す。Windowsのctimeは作成時刻。"""

        return (metadata.st_dev, metadata.st_ino, metadata.st_size,
                metadata.st_mtime_ns, metadata.st_ctime_ns)
