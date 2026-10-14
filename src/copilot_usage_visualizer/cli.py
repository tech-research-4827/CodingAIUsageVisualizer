from __future__ import annotations

import argparse
import json
import multiprocessing
import sys
from dataclasses import asdict
from pathlib import Path

from copilot_usage_visualizer import __version__
from copilot_usage_visualizer.app import UsageApplication
from copilot_usage_visualizer.defaults import AppDefaults


# CLI parser を構築する。
def build_parser() -> argparse.ArgumentParser:
    """copilot-usage CLI の argument parser を構築する。"""

    parser = argparse.ArgumentParser(prog="copilot-usage")
    parser.add_argument("--version", action="store_true", help="Print the package version.")
    subparsers = parser.add_subparsers(dest="command")

    merge_parser = subparsers.add_parser("merge", help="Combine derived DBs into a new DB.")
    merge_parser.add_argument(
        "--input", dest="inputs", action="append", required=True, type=Path,
        help="Read-only derived database (repeat for each input).",
    )
    merge_parser.add_argument("--output", type=Path, required=True, help="New database path.")

    scan_parser = subparsers.add_parser("scan", help="Scan Copilot log roots into SQLite.")
    scan_parser.add_argument(
        "--db", default=AppDefaults().db_path(), type=Path, help="SQLite DB path."
    )
    scan_parser.add_argument(
        "--cli-history-db", type=Path, help="Copilot request history SQLite input."
    )
    scan_parser.add_argument(
        "--cli-log-root",
        dest="cli_log_roots",
        action="append",
        type=Path,
        help="Copilot CLI session-state root (repeatable).",
    )
    scan_parser.add_argument(
        "--chat-storage-root",
        dest="chat_storage_roots",
        action="append",
        type=Path,
        help="VS Code workspaceStorage root (repeatable).",
    )
    scan_parser.add_argument(
        "--full",
        action="store_true",
        help="Rebuild from scratch: reset scan-derived data then scan.",
    )
    subparsers.add_parser("import-task-manifest", help="Import an A/B measurement task manifest.")

    summary_parser = subparsers.add_parser("summary", help="Print usage summary JSON.")
    summary_parser.add_argument(
        "--db", default=AppDefaults().db_path(), type=Path, help="SQLite DB path."
    )
    summary_parser.add_argument(
        "--from", dest="date_from", required=True, help="Start date YYYY-MM-DD."
    )
    summary_parser.add_argument("--to", dest="date_to", required=True, help="End date YYYY-MM-DD.")
    summary_parser.add_argument(
        "--format", choices=("json",), default="json", help="Output format."
    )

    report_parser = subparsers.add_parser(
        "report", help="Print an Agent-readable usage report (markdown or json)."
    )
    report_parser.add_argument(
        "--db", default=AppDefaults().db_path(), type=Path, help="SQLite DB path."
    )
    report_parser.add_argument(
        "--from", dest="date_from", required=True, help="Start date YYYY-MM-DD."
    )
    report_parser.add_argument("--to", dest="date_to", required=True, help="End date YYYY-MM-DD.")
    report_parser.add_argument(
        "--usd-jpy-rate", required=True, type=float, help="USD to JPY exchange rate."
    )
    report_parser.add_argument(
        "--format",
        dest="report_format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (default: markdown).",
    )
    report_parser.add_argument(
        "--detail", action="store_true", help="Include the per-run detail rows (opt-in)."
    )
    report_parser.add_argument(
        "--output", type=Path, help="Output file path (default: print to stdout)."
    )

    export_html_parser = subparsers.add_parser("export-html", help="Export an offline HTML report.")
    export_html_parser.add_argument(
        "--db", default=AppDefaults().db_path(), type=Path, help="SQLite DB path."
    )
    export_html_parser.add_argument(
        "--from", dest="date_from", required=True, help="Start date YYYY-MM-DD."
    )
    export_html_parser.add_argument(
        "--to", dest="date_to", required=True, help="End date YYYY-MM-DD."
    )
    export_html_parser.add_argument("--output", required=True, type=Path, help="HTML output path.")
    export_html_parser.add_argument(
        "--usd-jpy-rate", required=True, type=float, help="USD to JPY exchange rate."
    )
    gui_parser = subparsers.add_parser(
        "gui", help="Open the desktop dashboard (see also: copilot-usage-gui)."
    )
    gui_parser.add_argument(
        "--db", type=Path,
        help="Override saved DB path (unsaved: per-user data/output/copilot-usage.db)."
    )
    gui_parser.add_argument(
        "--cli-log-root",
        dest="cli_log_roots",
        action="append",
        type=Path,
        help="Copilot CLI session-state root (repeatable).",
    )
    gui_parser.add_argument(
        "--chat-storage-root",
        dest="chat_storage_roots",
        action="append",
        type=Path,
        help="VS Code workspaceStorage root (repeatable).",
    )
    gui_parser.add_argument("--output", type=Path, help="HTML report output path.")
    gui_parser.add_argument("--from", dest="date_from", help="Initial start date YYYY-MM-DD.")
    gui_parser.add_argument("--to", dest="date_to", help="Initial end date YYYY-MM-DD.")
    gui_parser.add_argument("--usd-jpy-rate", type=float, help="Initial USD to JPY exchange rate.")
    return parser


# GUI dashboard を起動する。
def _launch_gui(args: argparse.Namespace) -> int:
    """CLI 引数で初期値を上書きした Dear PyGui dashboard を起動する。"""

    # GUI 起動ロジックは copilot-usage-gui と共有する（dearpygui は起動時のみ import）。
    from copilot_usage_visualizer.gui_cli import build_settings, launch

    return launch(build_settings(args))


# CLI entry point。
def main(argv: list[str] | None = None) -> int:
    """CLI command を実行し exit code を返す。"""

    parser = build_parser()
    multiprocessing.freeze_support()
    args = parser.parse_args(argv)
    application = UsageApplication()

    if args.version:
        print(__version__)
        return 0

    if args.command is None:
        parser.print_help()
        return 0

    try:
        if args.command == "merge":
            print(json.dumps(asdict(application.merge(args.inputs, args.output)), sort_keys=True))
            return 0
        if args.command == "scan":
            if (
                args.cli_log_roots is None
                and args.chat_storage_roots is None
                and args.cli_history_db is None
            ):
                print(
                    "scan requires --cli-log-root, --chat-storage-root or --cli-history-db.",
                    file=sys.stderr,
                )
                return 2
            scan_result = application.scan(
                args.db,
                args.cli_log_roots or (),
                args.chat_storage_roots or (),
                full=args.full,
                cli_history_db=args.cli_history_db,
            )
            print(
                json.dumps(
                    {
                        "scan_id": scan_result.scan_id,
                        "records_count": scan_result.records_count,
                        "diagnostics_count": scan_result.diagnostics_count,
                        "history_warnings": scan_result.history_warnings,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "summary":
            print(
                json.dumps(
                    application.summary(args.db, args.date_from, args.date_to), sort_keys=True
                )
            )
            return 0
        if args.command == "report":
            content = application.render_report(
                args.db,
                args.date_from,
                args.date_to,
                args.usd_jpy_rate,
                args.report_format,
                args.detail,
            )
            if args.output is not None:
                if args.output.parent and not args.output.parent.exists():
                    raise ValueError("output parent directory does not exist.")
                args.output.write_text(content, encoding="utf-8")
                print(json.dumps({"output": str(args.output)}, sort_keys=True))
            else:
                print(content)
            return 0
        if args.command == "export-html":
            output_path = application.export_html(
                args.db, args.date_from, args.date_to, args.output, args.usd_jpy_rate
            )
            print(json.dumps({"output": str(output_path)}, sort_keys=True))
            return 0
        if args.command == "gui":
            return _launch_gui(args)
        if args.command == "import-task-manifest":
            print(f"command not implemented in M1: {args.command}", file=sys.stderr)
            return 2
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
