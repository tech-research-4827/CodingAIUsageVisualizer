"""Shared helpers for the copilot-usage PyInstaller spec files.

CLI と GUI の spec が共通で必要とする data file 収集・hidden import 解決をまとめる。
spec から ``exec``/``import`` 経由で利用する前提で、PyInstaller 実行時のみ読み込まれる。
"""

from __future__ import annotations

import os
import tomllib

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs


# バンドルの版番号も source metadata と同じ正本から読む。
def package_version(spec_dir: str) -> str:
    """PyInstaller の macOS Info.plist に使う版番号を返す。"""
    with open(os.path.join(repo_root(spec_dir), "pyproject.toml"), "rb") as stream:
        return str(tomllib.load(stream)["project"]["version"])


# repo root（packaging/ の親）を返す。
def repo_root(spec_dir: str) -> str:
    """spec file が置かれた packaging/ から見た repository root を返す。"""

    return os.path.dirname(os.path.abspath(spec_dir))


# src/ package 配下の package path を返す。
def src_path(spec_dir: str) -> str:
    """copilot_usage_visualizer package を含む src ディレクトリを返す。"""

    return os.path.join(repo_root(spec_dir), "src")


# __file__ 相対で読み込まれる package data を収集する。
def package_datas(spec_dir: str) -> list[tuple[str, str]]:
    """schema.sql / template / 第三者通知を package 相対の位置へ同梱する。

    repository.py / html.py は ``Path(__file__).parent`` 相対で読み込むため、
    bundle 内でも同じ package 相対 path に置く必要がある。
    """

    root: str = src_path(spec_dir)
    return [
        (
            os.path.join(root, "copilot_usage_visualizer", "db", "schema.sql"),
            os.path.join("copilot_usage_visualizer", "db"),
        ),
        (
            os.path.join(root, "copilot_usage_visualizer", "report", "third_party_licenses.txt"),
            os.path.join("copilot_usage_visualizer", "report"),
        ),
        (
            os.path.join(
                root,
                "copilot_usage_visualizer",
                "report",
                "templates",
                "offline_report.html.j2",
            ),
            os.path.join("copilot_usage_visualizer", "report", "templates"),
        ),
    ]


# plotly が必要とする data file（plotly.min.js 等）を収集する。
def plotly_datas() -> list[tuple[str, str]]:
    """offline HTML report の get_plotlyjs / template package data を収集する。"""

    return collect_data_files("plotly")


# dearpygui の native library / data を収集する。
def dearpygui_binaries() -> list[tuple[str, str]]:
    """GUI bundle 用に dearpygui の compiled extension を収集する。"""

    return collect_dynamic_libs("dearpygui")
