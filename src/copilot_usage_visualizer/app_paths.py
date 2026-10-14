from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path


# 起動元や実行体の配置先を、GUI の保存先に混ぜない。
class AppPaths:
    """ソース・配布版に共通する利用者別の設定・データ directory を解決する。"""

    NAMESPACE: str = "CopilotUsageVisualizer"

    # テスト時だけ OS・利用者・環境変数を注入して隔離する。
    def __init__(
        self, platform: str | None = None, home: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        """既定は現在の OS アカウントの環境を使用する。"""
        self._platform: str = sys.platform if platform is None else platform
        self._home: Path = Path.home() if home is None else home
        self._environment: Mapping[str, str] = os.environ if environ is None else environ

    # Windows は相対値を使って cwd に書くことを許さない。
    def _windows_base(self) -> Path:
        """LOCALAPPDATA がない、または絶対パスでない場合は固定エラーにする。"""
        value: str = self._environment.get("LOCALAPPDATA", "")
        if not value.strip() or not Path(value).is_absolute():
            raise ValueError(
                "Storage location unavailable: LOCALAPPDATA must be an absolute path. "
                "Restore the environment and restart."
            )
        return Path(value)

    # XDG の相対値は仕様上使わず、home 基準の既定にする。
    def _xdg_base(self, key: str, default: Path) -> Path:
        """絶対指定だけを採用し、空・相対値では home 下の既定を使う。"""
        value: Path = Path(self._environment.get(key, ""))
        return value if value.is_absolute() else self._home / default

    # 設定の従来の利用者 directory を維持する。
    def config_dir(self) -> Path:
        """明示保存する GUI JSON の directory を返す。"""
        if self._platform == "win32":
            base: Path = self._windows_base()
        elif self._platform == "darwin":
            base = self._home / "Library/Application Support"
        else:
            base = self._xdg_base("XDG_CONFIG_HOME", Path(".config"))
        return base / self.NAMESPACE

    # Linux のデータは config と分け、他 OS は同じ利用者領域を使う。
    def data_dir(self) -> Path:
        """GUI の既定 DB・HTML の基準 directory を返す。"""
        if self._platform in ("win32", "darwin"):
            return self.config_dir()
        return self._xdg_base("XDG_DATA_HOME", Path(".local/share")) / self.NAMESPACE
