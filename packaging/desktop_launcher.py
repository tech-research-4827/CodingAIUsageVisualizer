"""Linux の実際の配置先を指す desktop entry を生成する。"""

from __future__ import annotations

import argparse
from pathlib import Path


# desktop entry は shell script ではないので、独自の引用規則を適用する。
class DesktopLauncher:
    """利用者が選んだ実行体とアイコンへ固定したランチャーを作る。"""

    FILE_NAME: str = "io.github.tech-research-4827.CodingAIUsageVisualizer.desktop"

    # field code 展開と desktop entry の行分割をパスから発生させない。
    @staticmethod
    def _path(path: Path) -> str:
        """実在するファイルの絶対パスを返し、未対応の文字は明示拒否する。"""
        resolved: Path = path.resolve(strict=True)
        value: str = str(resolved)
        if not resolved.is_file() or any(char in value for char in "\r\n\x00%="):
            raise ValueError("Launcher paths must be files and must not contain newline, %, or =.")
        return value

    # 一般stringエスケープの前に Exec 引用を行う。
    @staticmethod
    def _exec(value: str) -> str:
        """引用内の予約文字を処理し、desktop entry のstringとして符号化する。"""
        quoted: str = '"' + "".join(
            "\\" + char if char in '\\"`$' else char for char in value
        ) + '"'
        return quoted.replace("\\", "\\\\")

    # 登録や移動はせず、利用者が指定した出力だけへ生成する。
    @classmethod
    def create(cls, executable: Path, icon: Path, output: Path) -> None:
        """配置先を解決し、指定されたランチャーを生成する。"""
        binary: str = cls._path(executable)
        image: str = cls._path(icon).replace("\\", "\\\\").replace(" ", "\\s")
        content: str = (
            "[Desktop Entry]\nType=Application\n"
            "Name=Coding AI Usage Visualizer\n"
            "Comment=View local Copilot usage\n"
            f"Exec={cls._exec(binary)}\nIcon={image}\n"
            "Terminal=false\nCategories=Development;Utility;\n"
        )
        with output.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        output.chmod(0o755)

    # 公開sourceでもアプリを導入せずに生成できる単独CLI。
    @classmethod
    def main(cls) -> int:
        """指定した配置先のランチャーを作り、失敗は非0で知らせる。"""
        parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--executable", type=Path, required=True)
        parser.add_argument("--icon", type=Path, required=True)
        parser.add_argument("--output", type=Path, required=True)
        args: argparse.Namespace = parser.parse_args()
        try:
            cls.create(args.executable, args.icon, args.output)
        except (OSError, ValueError) as exc:
            parser.exit(1, f"Launcher could not be created: {exc}\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(DesktopLauncher.main())
