from __future__ import annotations


# confidence label の粗い分類を担当する。
class ConfidenceMapper:
    """詳細 confidence label を粗い confidence vocabulary へ変換する。"""

    # 詳細 label から粗い label へ変換する。
    @staticmethod
    def to_coarse(confidence: str) -> str:
        """confidence label に対応する coarse confidence を返す。"""

        if confidence in {
            "measured_from_total_nano_aiu",
            "measured_from_shutdown_total_nano_aiu",
            "measured_from_usage_checkpoint",
            "measured_from_compaction_separate",
            "measured_from_request_history",
            "measured_from_request_history_only",
        }:
            return "exact"
        if confidence == "reported_from_copilot_chat":
            return "reported"
        if confidence in {
            "reference_only_chat_summary_tokens", "reference_only_cli_tokens",
            "compaction_included_in_cumulative",
            "unresolved_request_history",
        }:
            return "reference_only"
        if confidence == "mixed":
            return "mixed"
        if confidence == "credits_unavailable":
            return "unavailable"
        return "unknown"
