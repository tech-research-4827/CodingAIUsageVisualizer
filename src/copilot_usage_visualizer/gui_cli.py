from __future__ import annotations

import argparse
from pathlib import Path

from copilot_usage_visualizer.gui.settings import DashboardSettings


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
        "--db", type=Path, help="SQLite DB path (default: output/copilot-usage.db)."
    )
    parser.add_argument("--cli-log-root", type=Path, help="Copilot CLI session-state root.")
    parser.add_argument("--chat-storage-root", type=Path, help="VS Code workspaceStorage root.")
    parser.add_argument("--output", type=Path, help="HTML report output path.")
    parser.add_argument("--from", dest="date_from", help="Initial start date YYYY-MM-DD.")
    parser.add_argument("--to", dest="date_to", help="Initial end date YYYY-MM-DD.")
    parser.add_argument("--usd-jpy-rate", type=float, help="Initial USD to JPY exchange rate.")
    return parser


# CLI 引数から settings を構築する。
def build_settings(args: argparse.Namespace) -> DashboardSettings:
    """既定値に任意の CLI 引数を上書きした DashboardSettings を構築する。"""

    settings = DashboardSettings.from_defaults()
    if args.db is not None:
        settings.db_path = args.db
    if args.cli_log_root is not None:
        settings.cli_log_root = args.cli_log_root
    if args.chat_storage_root is not None:
        settings.chat_storage_root = args.chat_storage_root
    if args.output is not None:
        settings.report_output = args.output
    if args.date_from is not None:
        settings.date_from = args.date_from
    if args.date_to is not None:
        settings.date_to = args.date_to
    if args.usd_jpy_rate is not None:
        settings.usd_jpy_rate = args.usd_jpy_rate
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

    args = build_parser().parse_args(argv)
    return launch(build_settings(args))


if __name__ == "__main__":
    raise SystemExit(main())
