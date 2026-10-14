from __future__ import annotations

import argparse
import multiprocessing
import sys
from pathlib import Path

from copilot_usage_visualizer.gui.settings import DashboardSettings
from copilot_usage_visualizer.gui.settings_store import DashboardSettingsStore, SettingsStoreError


# GUI 専用 CLI parser を構築する。
def build_parser() -> argparse.ArgumentParser:
    """copilot-usage-gui CLI の argument parser を構築する。

    GUI は UI から設定する前提のため、全引数を任意の初期値上書きとして扱う。
    """

    parser = argparse.ArgumentParser(
        prog="copilot-usage-gui",
        description="Open the Copilot usage Dear PyGui dashboard.",
    )
    parser.add_argument(
        "--db", type=Path,
        help="Override saved DB path (unsaved: per-user data/output/copilot-usage.db)."
    )
    parser.add_argument(
        "--cli-log-root",
        dest="cli_log_roots",
        action="append",
        type=Path,
        help="Copilot CLI session-state root (repeatable).",
    )
    parser.add_argument(
        "--chat-storage-root",
        dest="chat_storage_roots",
        action="append",
        type=Path,
        help="VS Code workspaceStorage root (repeatable).",
    )
    parser.add_argument("--output", type=Path, help="HTML report output path.")
    parser.add_argument("--from", dest="date_from", help="Initial start date YYYY-MM-DD.")
    parser.add_argument("--to", dest="date_to", help="Initial end date YYYY-MM-DD.")
    parser.add_argument("--usd-jpy-rate", type=float, help="Initial USD to JPY exchange rate.")
    return parser


# CLI 引数から settings を構築する。
def build_settings(
    args: argparse.Namespace, store: DashboardSettingsStore | None = None,
) -> DashboardSettings:
    """既定値 → 保存値 → 明示引数の順で起動設定を構築する。"""

    settings = DashboardSettings.from_defaults()
    resolved_store: DashboardSettingsStore = (
        store if store is not None else DashboardSettingsStore()
    )
    messages: list[str] = []
    try:
        saved: DashboardSettings | None = resolved_store.load()
        if saved is not None:
            settings = saved
            messages.append("Settings loaded.")
    except SettingsStoreError as exc:
        messages.append(str(exc))
    if args.db is not None:
        settings.db_path = args.db
    if args.cli_log_roots is not None:
        settings.cli_log_roots = tuple(args.cli_log_roots)
    if args.chat_storage_roots is not None:
        settings.chat_storage_roots = tuple(args.chat_storage_roots)
    if args.output is not None:
        settings.report_output = args.output
    if args.date_from is not None:
        settings.date_from = args.date_from
    if args.date_to is not None:
        settings.date_to = args.date_to
    if args.usd_jpy_rate is not None:
        settings.usd_jpy_rate = args.usd_jpy_rate
    try:
        settings.validate()
    except ValueError as exc:
        messages.append(str(exc))
    settings.startup_messages = tuple(messages)
    return settings


# settings から dashboard を起動する。
def launch(settings: DashboardSettings) -> int:
    """dearpygui dashboard を起動する（dearpygui は起動時のみ import）。"""

    from copilot_usage_visualizer.gui.dashboard import DashboardView
    from copilot_usage_visualizer.gui.data import DashboardDataProvider

    DashboardView(settings, DashboardDataProvider()).run()
    return 0


# GUI CLI entry point。
def main(argv: list[str] | None = None) -> int:
    """copilot-usage-gui を実行し exit code を返す。"""

    multiprocessing.freeze_support()
    args = build_parser().parse_args(argv)
    try:
        settings: DashboardSettings = build_settings(args)
    except ValueError as exc:
        # windowed実行体ではstderrがなく、PyInstallerの例外表示へ渡す必要がある。
        if sys.stderr is None:
            raise
        print(f"error: {exc}", file=sys.stderr)
        return 1
    # 描画中のValueErrorまで設定エラーとして握りつぶさない。
    return launch(settings)


if __name__ == "__main__":
    raise SystemExit(main())
