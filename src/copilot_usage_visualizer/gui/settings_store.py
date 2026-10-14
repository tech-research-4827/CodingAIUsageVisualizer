from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import TypedDict, cast

from copilot_usage_visualizer.app_paths import AppPaths
from copilot_usage_visualizer.gui.display_scale import FontSizeChoice
from copilot_usage_visualizer.gui.settings import DashboardSettings


# 設定ファイルだけに保存する項目。通知・usage データを混ぜない。
class SettingsDocument(TypedDict):
    """利用者が明示保存する GUI 設定の JSON 契約。"""

    db_path: str
    cli_log_roots: list[str]
    chat_storage_roots: list[str]
    report_output: str
    date_from: str
    date_to: str
    usd_jpy_rate: float
    # 表示倍率 (百分率)。null は自動。この項目がない旧ファイルは自動として読む。
    ui_scale_percent: int | None
    # 文字の大きさ (百分率)。この項目がない旧ファイルは 100 として読む。
    font_size_percent: int


# 生の IO エラーやファイル内容を GUI の診断へ流さない。
class SettingsStoreError(ValueError):
    """固定メッセージで通知できる設定の読込・保存失敗。"""


# usage DB とは独立した利用者設定を読み書きする。
class DashboardSettingsStore:
    """9 項目の JSON 設定を明示保存し、利用不能な root もそのまま読む。"""

    # 表示倍率・文字の大きさを追加する前の項目。この 2 項目だけは欠けていてもよい。
    _REQUIRED_KEYS: frozenset[str] = frozenset({
        "db_path", "cli_log_roots", "chat_storage_roots", "report_output",
        "date_from", "date_to", "usd_jpy_rate",
    })
    _UI_SCALE_KEY: str = "ui_scale_percent"
    _FONT_SIZE_KEY: str = "font_size_percent"
    _OPTIONAL_KEYS: frozenset[str] = frozenset({"ui_scale_percent", "font_size_percent"})
    FILE_NAME: str = "gui-settings.json"

    # 不正 JSON の内容や詳細な例外文字列は利用者設定にも転記しない。
    INVALID_DOCUMENT: str = "Saved settings are invalid. Edit the fields and save to replace them."

    def __init__(self, path: Path | None = None) -> None:
        """内部テストでのみ保存先を注入できるようにする。"""

        self._path: Path | None = path

    # 保存先は読み込み時にも使うが、ディレクトリは保存まで作らない。
    @property
    def path(self) -> Path:
        """注入された保存先、または既定の保存先を返す。"""

        return self._path if self._path is not None else self.default_path()

    # 配布版もソース起動も、実行体や cwd と独立した利用者領域へ保存する。
    @staticmethod
    def default_path(
        platform: str | None = None,
        home: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> Path:
        """OS 別の利用者 directory を使い、旧版の実行体横の設定は読まない。"""
        try:
            return AppPaths(platform, home, environ).config_dir() / DashboardSettingsStore.FILE_NAME
        except ValueError as exc:
            raise SettingsStoreError(str(exc)) from None

    # 存在しない初回だけは正常な未保存状態とする。
    def load(self) -> DashboardSettings | None:
        """全体が有効な保存値だけを返す。失敗してもファイルを変更しない。"""

        try:
            content: str = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except UnicodeError:
            raise SettingsStoreError(self.INVALID_DOCUMENT) from None
        except OSError:
            raise SettingsStoreError(
                "Saved settings could not be read. "
                "Check access to the settings location and restart."
            ) from None
        try:
            raw: object = json.loads(content)
            return self._decode(raw)
        except (ValueError, TypeError, OverflowError):
            raise SettingsStoreError(self.INVALID_DOCUMENT) from None

    # 書込途中で唯一の保存設定が切れないよう、完成した1ファイルだけを置き換える。
    def save(self, settings: DashboardSettings) -> DashboardSettings:
        """検証した設定を一括保存し、成功した絶対パスの設定を返す。"""

        settings.validate()
        try:
            saved: DashboardSettings = replace(
                settings,
                db_path=settings.db_path.absolute(),
                cli_log_roots=tuple(root.absolute() for root in settings.cli_log_roots),
                chat_storage_roots=tuple(root.absolute() for root in settings.chat_storage_roots),
                report_output=settings.report_output.absolute(),
                startup_messages=(),
            )
        except OSError:
            raise SettingsStoreError(
                "Settings paths could not be resolved. Check the working directory and retry."
            ) from None
        document: SettingsDocument = {
            "db_path": str(saved.db_path),
            "cli_log_roots": [str(root) for root in saved.cli_log_roots],
            "chat_storage_roots": [str(root) for root in saved.chat_storage_roots],
            "report_output": str(saved.report_output),
            "date_from": saved.date_from,
            "date_to": saved.date_to,
            "usd_jpy_rate": saved.usd_jpy_rate,
            "ui_scale_percent": saved.ui_scale_percent,
            "font_size_percent": saved.font_size_percent,
        }
        destination: Path = self.path
        temporary: Path | None = None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=destination.parent, delete=False,
                prefix=".gui-settings-", suffix=".tmp",
            ) as stream:
                temporary = Path(stream.name)
                json.dump(document, stream, ensure_ascii=False, allow_nan=False, indent=2)
                stream.write("\n")
            temporary.replace(destination)
        except OSError:
            raise SettingsStoreError(
                "Settings could not be saved. "
                "Check write access to the settings location and retry."
            ) from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    raise SettingsStoreError(
                        "Temporary settings could not be removed. "
                        "Check access to the settings location."
                    ) from None
        return saved

    # JSON 境界で型と項目を確定してから内部の holder を構築する。
    @classmethod
    def _decode(cls, raw: object) -> DashboardSettings:
        """未知項目・欠落・不正型を拒否し、本文などを設定として受け入れない。"""

        if not isinstance(raw, dict):
            raise SettingsStoreError(cls.INVALID_DOCUMENT)
        data: dict[str, object] = cast(dict[str, object], raw)
        keys: set[str] = set(data)
        if not cls._REQUIRED_KEYS <= keys or not keys <= cls._REQUIRED_KEYS | cls._OPTIONAL_KEYS:
            raise SettingsStoreError(cls.INVALID_DOCUMENT)
        rate: object = data["usd_jpy_rate"]
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            raise SettingsStoreError(cls.INVALID_DOCUMENT)
        scale: object = data.get(cls._UI_SCALE_KEY)
        if scale is not None and (isinstance(scale, bool) or not isinstance(scale, int)):
            raise SettingsStoreError(cls.INVALID_DOCUMENT)
        font_size: object = data.get(cls._FONT_SIZE_KEY, FontSizeChoice.DEFAULT_PERCENT)
        if isinstance(font_size, bool) or not isinstance(font_size, int):
            raise SettingsStoreError(cls.INVALID_DOCUMENT)
        settings: DashboardSettings = DashboardSettings(
            db_path=Path(cls._text(data["db_path"])),
            cli_log_roots=cls._roots(data["cli_log_roots"]),
            chat_storage_roots=cls._roots(data["chat_storage_roots"]),
            report_output=Path(cls._text(data["report_output"])),
            date_from=cls._text(data["date_from"]),
            date_to=cls._text(data["date_to"]),
            usd_jpy_rate=float(rate),
            ui_scale_percent=scale,
            font_size_percent=font_size,
        )
        settings.validate()
        return settings

    # 空文字を Path('.') にして入力の欠落を隠さない。
    @classmethod
    def _text(cls, raw: object) -> str:
        """JSON の必須文字列を型・空欄・NUL について検証する。"""

        if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
            raise SettingsStoreError(cls.INVALID_DOCUMENT)
        return raw

    # 空リストは取得元の無効化として有効にする。
    @classmethod
    def _roots(cls, raw: object) -> tuple[Path, ...]:
        """順序と利用不能なパスを保ち、リスト内の不正型は拒否する。"""

        if not isinstance(raw, list):
            raise SettingsStoreError(cls.INVALID_DOCUMENT)
        return tuple(Path(cls._text(value)) for value in raw)
