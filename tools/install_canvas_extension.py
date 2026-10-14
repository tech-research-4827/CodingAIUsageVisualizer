"""Session usage canvas extension を Copilot の user extension 置き場へ複製する。"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# 複製元と配置名。CLI は extensions/<name>/extension.mjs を探す。
EXTENSION_NAME = "copilot-session-usage"
SOURCE_DIR = Path(__file__).resolve().parent.parent / "extensions" / EXTENSION_NAME
# 本 script が配置したことを示す印。印の無い既存ディレクトリは上書きしない。
MARKER = ".installed-by-coding-ai-usage-visualizer"
# 実行時に必要なものだけを配る (テストと fixture は配らない)。
# standalone.mjs は App なしで画面をブラウザに出す単独起動用。
INCLUDED = ("extension.mjs", "standalone.mjs", "lib", "web")


# 配置の失敗を CLI で一貫して扱う。
class InstallError(RuntimeError):
    """配置先が想定外の状態であることを示す。"""


# 配置計画。dry-run と実行で同じ内容を表示する。
@dataclass(frozen=True)
class InstallPlan:
    """複製元・配置先・複製するファイルと、既存配置を置き換えるか。"""

    source: Path
    destination: Path
    files: tuple[Path, ...]
    replaces_existing: bool


# COPILOT_HOME 未設定時は CLI と同じ ~/.copilot を使う。
def default_copilot_home() -> Path:
    """環境変数か既定値から Copilot の home を返す。"""
    configured = os.environ.get("COPILOT_HOME", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".copilot"


# 複製するファイルの一覧を作る。
def source_files(source: Path) -> tuple[Path, ...]:
    """INCLUDED 配下の通常ファイルを相対パスで返す。"""
    files: list[Path] = []
    for name in INCLUDED:
        entry = source / name
        if entry.is_file():
            files.append(Path(name))
        elif entry.is_dir():
            files.extend(
                path.relative_to(source) for path in sorted(entry.rglob("*"))
                if path.is_file() and "__pycache__" not in path.parts
            )
        else:
            raise InstallError(f"Missing source entry: {name}")
    return tuple(files)


# 配置先を検査して計画を作る。
def plan_install(copilot_home: Path, source: Path = SOURCE_DIR) -> InstallPlan:
    """印の無い既存ディレクトリやシンボリックリンクは置き換えない。"""
    destination = copilot_home / "extensions" / EXTENSION_NAME
    replaces = False
    if destination.is_symlink():
        raise InstallError(f"Refusing to replace a symbolic link: {destination}")
    if destination.exists():
        if not (destination / MARKER).is_file():
            raise InstallError(
                f"{destination} exists and was not installed by this script; "
                "remove or rename it first."
            )
        replaces = True
    return InstallPlan(source, destination, source_files(source), replaces)


# 計画どおりに複製する。既存の配置は丸ごと置き換え、古いファイルを残さない。
def install(plan: InstallPlan) -> None:
    """印付きの既存配置を消してから複製し、最後に印を書く。"""
    if plan.replaces_existing:
        shutil.rmtree(plan.destination)
    for relative in plan.files:
        target = plan.destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plan.source / relative, target)
    (plan.destination / MARKER).write_text(f"source: {EXTENSION_NAME}\n", encoding="utf-8")


# 計画を人が確認できる形で表示する。
def describe(plan: InstallPlan) -> str:
    """配置先・置き換えの有無・ファイル一覧の文字列。"""
    action = "replace existing install at" if plan.replaces_existing else "install to"
    lines = [f"{action} {plan.destination}", f"{len(plan.files)} file(s):"]
    lines.extend(f"  {path.as_posix()}" for path in plan.files)
    return "\n".join(lines)


# CLI entry point。
def main(argv: list[str] | None = None) -> int:
    """引数を解釈して配置する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--copilot-home", type=Path, default=None,
        help="Copilot home directory (default: $COPILOT_HOME or ~/.copilot)",
    )
    parser.add_argument("--dry-run", action="store_true", help="show the plan without copying")
    args = parser.parse_args(argv)
    home = args.copilot_home.expanduser() if args.copilot_home else default_copilot_home()
    try:
        plan = plan_install(home)
    except InstallError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(describe(plan))
    if args.dry_run:
        print("dry run: nothing copied")
        return 0
    install(plan)
    print("installed. Reload extensions in the Copilot App (or restart the session) to load it.")
    print(f"standalone: node {plan.destination / 'standalone.mjs'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
