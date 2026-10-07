from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar, TypedDict


# usage record の粒度を表す。
class RunKind:
    """parser / repository 間で共有する run kind を保持する。"""

    CHAT_REQUEST_LEVEL: str = "chat_request_level"
    SHUTDOWN: str = "shutdown"
    CHECKPOINT: str = "usage_checkpoint"
    SESSION: str = "session"
    COMPACTION: str = "compaction"
    HISTORY: str = "request_history"


# run の選択規則を表す。
class RunSelectionPolicy:
    """parser / summary 間で共有する run selection policy を保持する。"""

    CHAT_REQUEST_LEVEL: str = "chat_request_level"
    LATEST_PER_SESSION: str = "latest_complete_session_candidate"
    RECONCILED_HISTORY: str = "reconciled_request_history"


# AI Credits最終値の由来を表す。
class AiCreditsSource:
    """usage recordのAI Credits provenance vocabularyを保持する。"""

    CLI_COMPACTION: str = "copilot_cli_compaction"
    CLI_HISTORY: str = "copilot_cli_request_history"
    UNAVAILABLE: str = "credits_unavailable"
    COPILOT_CLI_TOTAL_NANO_AIU: str = "copilot_cli_total_nano_aiu"
    CLI_SHUTDOWN_TOTAL: str = "copilot_cli_shutdown_total_nano_aiu"
    CLI_CHECKPOINT: str = "copilot_cli_usage_checkpoint"
    CLI_TOKEN_REFERENCE: str = "copilot_cli_token_reference"
    COPILOT_CHAT_CREDITS: str = "copilot_chat_copilot_credits"
    COPILOT_CHAT_SUMMARY_REFERENCE: str = "copilot_chat_summary_reference"


# Copilot CLI が記録する nano AIU 直接値を表す。
@dataclass(frozen=True)
class NanoAiuAmount:
    """SQLiteへ保存可能な非負のinteger nano AIUを保持する。"""

    SQLITE_MAX_INTEGER: ClassVar[int] = 9_223_372_036_854_775_807
    NANO_PER_AI_CREDIT: ClassVar[int] = 1_000_000_000
    value: int = 0

    def __post_init__(self) -> None:
        """直接constructされた値にも同じinteger境界を適用する。"""

        if (
            isinstance(self.value, bool)
            or not isinstance(self.value, int)
            or self.value < 0
            or self.value > self.SQLITE_MAX_INTEGER
        ):
            raise ValueError("nano AIU is outside the SQLite integer range.")

    @classmethod
    def from_json_value(cls, value: object) -> NanoAiuAmount:
        """JSON numberを検証し、整数nano AIUへ正規化する。"""

        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("nano AIU must be a number.")
        if isinstance(value, float) and (
            not math.isfinite(value) or not value.is_integer() or value > 2**53
        ):
            raise ValueError("floating-point nano AIU must be an exact finite integer.")
        integer_value: int = int(value)
        if integer_value < 0 or integer_value > cls.SQLITE_MAX_INTEGER:
            raise ValueError("nano AIU is outside the SQLite integer range.")
        return cls(value=integer_value)

    def to_ai_credits(self) -> Decimal:
        """integer nano AIUをexactなDecimal AI Creditsへ換算する。"""

        return Decimal(self.value) / Decimal(self.NANO_PER_AI_CREDIT)


# 正規化済み token bucket を表す。
@dataclass(frozen=True)
class TokenBuckets:
    """使用量計算に必要な token bucket を保持する。"""

    raw_input_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    output_tokens: int
    reasoning_tokens: int

    # fresh input token を算出する。
    @property
    def fresh_input_tokens(self) -> int:
        """raw input から cache 系 token を差し引いた fresh input token を返す。"""

        return max(0, self.raw_input_tokens - self.cached_input_tokens - self.cache_write_tokens)


# parser から DB へ渡す使用量 record を表す。
@dataclass(frozen=True)
class ParsedUsageRecord:
    """source parser が抽出した privacy-safe な使用量 record を保持する。"""

    source: str
    source_file_path: str
    source_file_display_alias: str
    workspace_identity: str | None
    workspace_display_alias: str | None
    session_id: str
    run_kind: str
    run_timestamp: str | None
    run_selection_policy: str
    request_id: str | None
    timestamp: str
    model_key: str | None
    token_buckets: TokenBuckets
    confidence: str
    producer_version: str | None = None
    reported_ai_credits: float | None = None
    reported_nano_aiu: int | None = None
    ai_credits_source: str = AiCreditsSource.UNAVAILABLE
    event_order: int = 0
    attribution_scope: str = "model"
    # 金額の直接値とは独立した、累積期間の主応答・終了内訳による推定名。
    estimated_primary_model: str | None = None
    # 圧縮の観測値と包含判定を加算値から分離する。
    observed_nano_aiu: int | None = None
    inclusion_status: str | None = None
    # 入力同士を結ぶ匿名化済みscopeと使用量の照合情報。
    history_scope: str | None = None
    match_key: str | None = None
    initiator: str | None = None
    evidence_error: str | None = None
    tokens_valid: bool = True
    reasoning_tokens_valid: bool = True


# scan 中に発生した privacy-safe diagnostic を表す。
@dataclass(frozen=True)
class DiagnosticRecord:
    """raw content を含まない scan diagnostic を保持する。"""

    source: str
    source_file_path: str
    source_file_display_alias: str
    code: str
    message: str
    line_number: int | None = None
    session_id: str | None = None
    history_scope: str | None = None


# 空ファイルを含む再解析対象を記録する。
@dataclass(frozen=True)
class ParsedSourceFile:
    """再解析が成功したファイルの置換範囲を保持する。"""

    source: str
    source_file_path: str
    source_file_display_alias: str


# 外部履歴DBから読み取った許可列だけを渡す。
@dataclass(frozen=True)
class CliHistorySnapshot:
    """SQL境界の列名と行、利用不能理由を保持する。"""

    # WSL 取得の上限を処理と診断文で共有する。
    WSL_TIMEOUT_SECONDS: ClassVar[int] = 30
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[object, ...], ...] = ()
    error: str | None = None
    read_failed: bool = False

    # 取得経路の外部出力を診断本文へ混ぜない。
    @staticmethod
    def failure_message(reason: str) -> str | None:
        """履歴の読み取り失敗にだけ、固定の確認手順を返す。"""

        messages: dict[str, str] = {
            "history_unavailable": "CLI history could not be read; check database access.",
            "wsl_timeout": (
                f"WSL history read timed out after {CliHistorySnapshot.WSL_TIMEOUT_SECONDS} "
                "seconds; check that WSL is responsive "
                "and scan again."
            ),
            "wsl_process_failed": (
                "WSL history reader did not return a successful response; check the selected "
                "distribution and python3 3.10+ with SQLite support."
            ),
            "wsl_history_unavailable": (
                "WSL history database could not be read; check its existence and read access "
                "inside the selected distribution."
            ),
            "wsl_invalid_response": (
                "WSL history reader returned an invalid response; check python3 3.10+ "
                "with SQLite support in the selected distribution."
            ),
        }
        message: str | None = messages.get(reason)
        if message is None:
            return None
        return message + " Totals may change until the next successful history read."


# 履歴入力の利用可否と、日付を割り当てられない行のあるsessionを保持する。
@dataclass(frozen=True)
class ParsedHistoryInput:
    """内容を保持しないscan単位の履歴状態。"""

    path: str
    error: str | None = None
    invalid_sessions: tuple[str, ...] = ()


# repositoryから選択処理・analyticsへ渡す明示的な射影契約。
class UsageRow(TypedDict):
    """SQLiteの列と出力時の採用状態を型で共有する。"""

    usage_id: int
    run_id: int
    source_file_id: int
    timestamp: str
    model_key: str | None
    event_order: int
    raw_input_tokens: int
    fresh_input_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    output_tokens: int
    reasoning_tokens: int
    reported_ai_credits: float | None
    reported_nano_aiu: int | None
    observed_nano_aiu: int | None
    ai_credits_source: str
    confidence: str
    attribution_scope: str
    estimated_primary_model: str | None
    inclusion_status: str | None
    request_id: str | None
    match_key: str | None
    history_scope: str | None
    initiator: str | None
    evidence_error: str | None
    tokens_valid: int
    reasoning_tokens_valid: int
    source: str
    session_id: str
    run_kind: str
    run_selection_policy: str
    workspace_id: int | None
    workspace_alias: str
    coverage: str
    history_error: str | None


# 累積値と履歴を照合した結果と、保存用の診断情報。
@dataclass(frozen=True)
class UsageSelection:
    """選択済み行とsession単位の理由を別々に渡す。"""

    rows: list[UsageRow]
    issues: list[tuple[int, str]]
