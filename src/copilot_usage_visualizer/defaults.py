from __future__ import annotations

import calendar
import sys
from datetime import date
from pathlib import Path


# GUI / CLI 既定値を解決する。
class AppDefaults:
    """GUI dashboard と CLI が共有する既定 path / 期間 / rate を解決する。"""

    # 既定 USD/JPY 変換レート。
    DEFAULT_USD_JPY_RATE: float = 160.0

    # output 配下の既定 file 名。
    OUTPUT_DIR_NAME: str = "output"
    DB_FILE_NAME: str = "copilot-usage.db"
    REPORT_FILE_NAME: str = "copilot-usage-report.html"

    def __init__(self, base_dir: Path | None = None) -> None:
        """output dir の基準ディレクトリを保持する。"""

        self._base_dir: Path = base_dir if base_dir is not None else Path.cwd()

    # output dir を返す。
    def output_dir(self) -> Path:
        """既定 output ディレクトリを返す。"""

        return self._base_dir / self.OUTPUT_DIR_NAME

    # 既定 DB path を返す。
    def db_path(self) -> Path:
        """既定 SQLite DB path を返す。"""

        return self.output_dir() / self.DB_FILE_NAME

    # 既定 report 出力先を返す。
    def report_output(self) -> Path:
        """既定 HTML report 出力先 path を返す。"""

        return self.output_dir() / self.REPORT_FILE_NAME

    # 既定 Copilot CLI log root を返す。
    def cli_log_root(self, home: Path | None = None) -> Path:
        """Copilot CLI session-state の既定 root を返す。"""

        resolved_home: Path = home if home is not None else Path.home()
        return resolved_home / ".copilot" / "session-state"

    # 既定 VS Code Chat storage root を返す。
    def chat_storage_root(self, home: Path | None = None, platform: str | None = None) -> Path:
        """OS 別の VS Code workspaceStorage 既定 root を返す。"""

        resolved_home: Path = home if home is not None else Path.home()
        resolved_platform: str = platform if platform is not None else sys.platform
        code_user: Path
        if resolved_platform == "darwin":
            code_user = resolved_home / "Library" / "Application Support" / "Code" / "User"
        elif resolved_platform == "win32":
            code_user = resolved_home / "AppData" / "Roaming" / "Code" / "User"
        else:
            code_user = resolved_home / ".config" / "Code" / "User"
        return code_user / "workspaceStorage"

    # 既定 report 期間を返す。
    @staticmethod
    def month_range(today: date) -> tuple[str, str]:
        """指定日の月初・月末を YYYY-MM-DD で返す。"""

        last_day: int = calendar.monthrange(today.year, today.month)[1]
        month_start: date = today.replace(day=1)
        month_end: date = today.replace(day=last_day)
        return month_start.isoformat(), month_end.isoformat()
