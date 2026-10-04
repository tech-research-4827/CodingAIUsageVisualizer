from __future__ import annotations

import json
from collections.abc import Mapping


# Agent 可読な JSON report rendering を担当する。
class JsonReportService:
    """report_summary を装飾なしの構造化 JSON 文字列へ変換する。"""

    # JSON 文字列を生成する。
    def render(self, summary: Mapping[str, object], include_detail: bool) -> str:
        """report_summary を Agent 可読な JSON 文字列へ直列化する。

        include_detail=False のときは detail_rows key を出力から除外する
        （明細は opt-in）。出力は build_report_summary 由来の新 contract であり、
        既存 summary command の schema 互換は保証しない。
        """

        payload: dict[str, object] = {
            key: value for key, value in summary.items() if key != "detail_rows"
        }
        if include_detail and "detail_rows" in summary:
            payload["detail_rows"] = summary["detail_rows"]
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
