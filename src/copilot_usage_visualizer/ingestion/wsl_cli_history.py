from __future__ import annotations

import base64
import binascii
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import PureWindowsPath

from copilot_usage_visualizer.normalization.usage import CliHistorySnapshot


# Windows の WSL 共有を特定し、Linux の入力位置を保持する。
@dataclass(frozen=True)
class WslHistoryLocation:
    """シェルを介さずに使用する distribution と絶対 Linux path。"""

    distribution: str
    linux_path: str

    # 共有名の別表記を扱い、通常の Windows 入力には介入しない。
    @classmethod
    def from_path(cls, path: str) -> WslHistoryLocation | None:
        """WSL の通常・拡張 UNC path を認識する。"""

        windows_path: PureWindowsPath = PureWindowsPath(path)
        drive: str = windows_path.drive
        if drive.casefold().startswith("\\\\?\\unc\\"):
            drive = "\\\\" + drive[8:]
        parts: list[str] = drive.split("\\")
        if len(parts) != 4 or parts[:2] != ["", ""]:
            return None
        if parts[2].casefold() not in {"wsl.localhost", "wsl$"} or not parts[3]:
            return None
        # distribution をオプションとして解釈させない。
        if parts[3].startswith("-") or "\x00" in path:
            return None
        relative: tuple[str, ...] = windows_path.parts[1:]
        if ".." in relative:
            return None
        return cls(parts[3], "/" + "/".join(relative))


# プロセス境界では本文を持つ DB 全体を転送しない。
class WslCliHistoryTransport:
    """repository が作る許可列取得スクリプトを WSL 内で実行する。"""

    TIMEOUT_SECONDS: int = CliHistorySnapshot.WSL_TIMEOUT_SECONDS

    # stderr は内容を解釈せず、固定理由に置換する。
    @classmethod
    def read(
        cls, location: WslHistoryLocation, script: str,
        allowed: tuple[str, ...], required: set[str], session_id: str | None,
    ) -> CliHistorySnapshot:
        """UTF-8 の構造化された応答だけを取得し、失敗を snapshot にする。"""

        request: str = json.dumps({
            "path": location.linux_path, "allowed": allowed,
            "required": sorted(required), "session_id": session_id,
        }, ensure_ascii=True)
        # 引数の path と filter は stdin のデータにして、起動引数を固定する。
        program: str = script + "\nHistoryReader.run(" + repr(request) + ")\n"
        try:
            process: subprocess.CompletedProcess[bytes] = subprocess.run(
                ["wsl.exe", "--distribution", location.distribution,
                 "--exec", "python3", "-I", "-"],
                input=program.encode("utf-8"), capture_output=True, check=False,
                timeout=cls.TIMEOUT_SECONDS,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired:
            return CliHistorySnapshot(error="wsl_timeout", read_failed=True)
        except OSError:
            return CliHistorySnapshot(error="wsl_process_failed", read_failed=True)
        if process.returncode != 0:
            return CliHistorySnapshot(error="wsl_process_failed", read_failed=True)
        try:
            return cls.decode(process.stdout, allowed, required)
        except (UnicodeError, ValueError, TypeError, KeyError):
            return CliHistorySnapshot(error="wsl_invalid_response", read_failed=True)

    # 取得可能な列・値型・既知理由以外を内部の契約へ通さない。
    @staticmethod
    def decode(
        output: bytes, allowed: tuple[str, ...], required: set[str],
    ) -> CliHistorySnapshot:
        """許可列の JSON を検証し、SQLite の BLOB 型を復元する。"""

        payload = json.loads(output.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"columns", "rows", "error"}:
            raise ValueError("Invalid history response.")
        columns = payload["columns"]
        rows = payload["rows"]
        error = payload["error"]
        if error not in (None, "unsupported_history_schema", "history_unavailable"):
            raise ValueError("Invalid history status.")
        if not isinstance(columns, list) or any(type(c) is not str for c in columns):
            raise ValueError("Invalid history columns.")
        if columns != [column for column in allowed if column in columns]:
            raise ValueError("Unexpected history columns.")
        if not isinstance(rows, list):
            raise ValueError("Invalid history rows.")
        if error == "history_unavailable":
            if columns or rows:
                raise ValueError("Invalid failed history response.")
            return CliHistorySnapshot(error="wsl_history_unavailable", read_failed=True)
        if not required <= set(columns) and not (
            error == "unsupported_history_schema" and not columns and not rows
        ):
            raise ValueError("Missing history columns.")
        decoded: list[tuple[object, ...]] = []
        for row in rows:
            if not isinstance(row, list) or len(row) != len(columns):
                raise ValueError("Invalid history row.")
            values: list[object] = []
            for cell in row:
                if isinstance(cell, dict) and set(cell) == {"blob"}:
                    if type(cell["blob"]) is not str:
                        raise ValueError("Invalid history BLOB.")
                    try:
                        cell = base64.b64decode(cell["blob"], validate=True)
                    except binascii.Error as exc:
                        raise ValueError("Invalid history BLOB.") from exc
                elif cell is not None and type(cell) not in (int, float, str):
                    raise ValueError("Invalid history value.")
                values.append(cell)
            decoded.append(tuple(values))
        return CliHistorySnapshot(tuple(columns), tuple(decoded), error)
