from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from copilot_usage_visualizer.db.repository import UsageRepository
from copilot_usage_visualizer.formatting import UsageValueFormatter
from copilot_usage_visualizer.normalization.confidence import ConfidenceMapper
from copilot_usage_visualizer.normalization.credits import DirectCreditsPolicy
from copilot_usage_visualizer.normalization.usage import (
    AiCreditsSource,
    NanoAiuAmount,
    RunKind,
    RunSelectionPolicy,
    UsageRow,
)


# token 合計値を保持する。
@dataclass
class SummaryTotals:
    """summary に出力する token / credit 合計を保持する。"""

    raw_input_tokens: int = 0
    fresh_input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    direct_nano_aiu: int = 0
    has_direct_nano_aiu: bool = False
    decimal_ai_credits: Decimal = Decimal(0)
    known_credits_count: int = 0
    missing_credits_count: int = 0
    reference_records_count: int = 0
    unattributed_nano_aiu: int = 0
    missing_tokens_count: int = 0
    known_tokens_count: int = 0
    missing_reasoning_tokens_count: int = 0
    known_reasoning_tokens_count: int = 0
    unresolved_history_count: int = 0
    # 未配賦額の内数であり合計へ再加算しない。
    estimated_primary_nano_aiu: int = 0

    @property
    def ai_credits(self) -> Decimal | None:
        """source別subtotalをAI Creditsへ換算した合計を返す。"""

        if not self.known_credits_count:
            return None
        direct_credits: Decimal = Decimal(self.direct_nano_aiu) / Decimal(1_000_000_000)
        return direct_credits + self.decimal_ai_credits

    # Row の値を加算する。
    def add_row(self, row: UsageRow) -> None:
        """usage row の token / credit 値を合計へ加算する。"""

        if row["inclusion_status"] == "unresolved_history":
            self.unresolved_history_count += 1
        if row["inclusion_status"] not in {"unresolved_history", "included"}:
            if row["tokens_valid"]:
                self.known_tokens_count += 1
            else:
                self.missing_tokens_count += 1
            if row["reasoning_tokens_valid"]:
                self.known_reasoning_tokens_count += 1
            else:
                self.missing_reasoning_tokens_count += 1
        self.raw_input_tokens += int(row["raw_input_tokens"])
        self.fresh_input_tokens += int(row["fresh_input_tokens"])
        self.cached_input_tokens += int(row["cached_input_tokens"])
        self.cache_write_tokens += int(row["cache_write_tokens"])
        self.output_tokens += int(row["output_tokens"])
        self.reasoning_tokens += int(row["reasoning_tokens"])
        nano_value: object = row["reported_nano_aiu"]
        if isinstance(nano_value, int):
            if row["model_key"] is None:
                self.unattributed_nano_aiu += nano_value
                if row["estimated_primary_model"] is not None:
                    self.estimated_primary_nano_aiu += nano_value
            self.direct_nano_aiu += nano_value
            self.has_direct_nano_aiu = True
            self.known_credits_count += 1
            return
        estimated_value: object = row["reported_ai_credits"]
        if isinstance(estimated_value, int | float):
            self.decimal_ai_credits += Decimal(str(estimated_value))
            self.known_credits_count += 1
        elif row["inclusion_status"] in {
            "included", "unresolved_history"
        } or row["ai_credits_source"] in {
            "copilot_chat_summary_reference",
            "copilot_cli_token_reference",
        }:
            self.reference_records_count += 1
        else:
            self.missing_credits_count += 1

    # JSON serializable dict へ変換する。
    def to_dict(self) -> dict[str, int | float | None]:
        """summary totals を JSON serializable dict に変換する。"""

        return {
            "raw_input_tokens": (self.raw_input_tokens
                if self.known_tokens_count or not self.missing_tokens_count else None),
            "fresh_input_tokens": (self.fresh_input_tokens
                if self.known_tokens_count or not self.missing_tokens_count else None),
            "cached_input_tokens": (self.cached_input_tokens
                if self.known_tokens_count or not self.missing_tokens_count else None),
            "cache_write_tokens": (self.cache_write_tokens
                if self.known_tokens_count or not self.missing_tokens_count else None),
            "output_tokens": (self.output_tokens
                if self.known_tokens_count or not self.missing_tokens_count else None),
            "reasoning_tokens": (self.reasoning_tokens
                if self.known_reasoning_tokens_count or not self.missing_reasoning_tokens_count
                else None),
            "ai_credits": float(self.ai_credits) if self.ai_credits is not None else None,
            "known_credits_count": self.known_credits_count,
            "missing_credits_count": self.missing_credits_count,
            "reference_records_count": self.reference_records_count,
            "missing_tokens_count": self.missing_tokens_count,
            "unresolved_history_count": self.unresolved_history_count,
            "missing_reasoning_tokens_count": self.missing_reasoning_tokens_count,
            "unattributed_ai_credits": float(
                Decimal(self.unattributed_nano_aiu) / Decimal(1_000_000_000)
            ),
            "estimated_primary_ai_credits": float(
                Decimal(self.estimated_primary_nano_aiu) / Decimal(1_000_000_000)
            ),
            "direct_nano_aiu": self.direct_nano_aiu if self.has_direct_nano_aiu else None,
        }


# 内訳の独立した軸と、非加算状態を保持する。
@dataclass(frozen=True)
class ModelBreakdownKey:
    """取得元・元モデル・用途・帰属・採用状態の集計キー。"""

    source: str
    raw_model: str | None
    estimated_model: str | None
    kind: str
    attribution: str
    inclusion_status: str | None

    # 履歴とイベントの両方から圧縮を識別する。
    @classmethod
    def from_row(cls, row: UsageRow) -> ModelBreakdownKey:
        """圧縮用途を優先し、それ以外の参照を分離する。"""

        compaction: bool = (
            row["run_kind"] == RunKind.COMPACTION or row["initiator"] == "compaction"
        )
        reference: bool = row["ai_credits_source"] in {
            AiCreditsSource.CLI_TOKEN_REFERENCE, "copilot_chat_summary_reference"
        } or row["inclusion_status"] in {"included", "unresolved_history"}
        kind: str = "compaction" if compaction else "reference" if reference else "regular"
        attribution: str = (
            "direct" if row["model_key"] is not None else
            "estimated" if row["estimated_primary_model"] is not None else "none"
        )
        return cls(str(row["source"]), row["model_key"], row["estimated_primary_model"],
                   kind, attribution, row["inclusion_status"])


# 通貨換算を担当する。
class CurrencyConverter:
    """AI Credits から USD / JPY の derived values を作成する。"""

    def __init__(self, usd_jpy_rate: float) -> None:
        """USD/JPY rate を保持する。"""

        self._usd_jpy_rate: float = usd_jpy_rate

    # AI Credits から USD を返す。
    def to_usd(self, ai_credits: float | None) -> float | None:
        """AI Credits から USD estimate を返す。"""

        if ai_credits is None:
            return None
        return ai_credits * DirectCreditsPolicy.AI_CREDIT_USD_RATE

    # AI Credits から JPY を返す。
    def to_jpy(self, ai_credits: float | None) -> float | None:
        """AI Credits から JPY estimate を返す。"""

        estimated_usd: float | None = self.to_usd(ai_credits)
        if estimated_usd is None:
            return None
        return estimated_usd * self._usd_jpy_rate


# JSON summary 作成を担当する。
class SummaryService:
    """UsageRepository の query result から JSON summary を生成する。"""

    def __init__(self, repository: UsageRepository) -> None:
        """repository dependency を保持する。"""

        self._repository: UsageRepository = repository

    # summary を作成する。
    def build_summary(self, date_from: str, date_to: str) -> dict[str, object]:
        """指定日付範囲の usage summary を返す。"""

        rows: list[UsageRow] = self._repository.summary_records(date_from, date_to)
        excluded_sessions: int = self._repository.excluded_cli_sessions(date_from, date_to)
        totals: SummaryTotals = SummaryTotals()
        confidence_totals: defaultdict[str, SummaryTotals] = defaultdict(SummaryTotals)
        confidence_counts: defaultdict[str, int] = defaultdict(int)

        for row in rows:
            totals.add_row(row)
            confidence: str = str(row["confidence"])
            confidence_totals[confidence].add_row(row)
            confidence_counts[confidence] += 1

        return {
            "cli_attribution": DirectCreditsPolicy.CLI_ATTRIBUTION,
            "primary_model_attribution": UsageValueFormatter.PRIMARY_MODEL_NOTE,
            "coverage": DirectCreditsPolicy.coverage(date_from, date_to) + self._history_note(rows),
            "history_coverage": self._history_coverage(rows),
            "date_from": date_from,
            "date_to": date_to,
            "run_selection_policies": {
                "copilot_cli": RunSelectionPolicy.RECONCILED_HISTORY,
                "copilot_chat": RunSelectionPolicy.CHAT_REQUEST_LEVEL,
            },
            "data_status": self._data_status(totals, excluded_sessions),
            "totals": {**totals.to_dict(), "excluded_cli_sessions": excluded_sessions},
            "by_model": self._model_rows(rows),
            "model_breakdown": self._model_breakdown(rows),
            "confidence_breakdown": self._confidence_rows(confidence_totals, confidence_counts),
            "diagnostics": self._diagnostic_summary(),
        }

    # report summary を作成する。
    def build_report_summary(
        self, date_from: str, date_to: str, usd_jpy_rate: float
    ) -> dict[str, object]:
        """HTML report 用の usage summary を返す。"""

        rows: list[UsageRow] = self._repository.report_records(date_from, date_to)
        converter: CurrencyConverter = CurrencyConverter(usd_jpy_rate)
        excluded_sessions: int = self._repository.excluded_cli_sessions(date_from, date_to)
        totals: SummaryTotals = SummaryTotals()
        by_day_totals: defaultdict[str, SummaryTotals] = defaultdict(SummaryTotals)
        confidence_totals: defaultdict[str, SummaryTotals] = defaultdict(SummaryTotals)
        confidence_counts: defaultdict[str, int] = defaultdict(int)

        for row in rows:
            totals.add_row(row)
            day_key: str = str(row["timestamp"])[:10]
            by_day_totals[day_key].add_row(row)
            confidence: str = str(row["confidence"])
            confidence_totals[confidence].add_row(row)
            confidence_counts[confidence] += 1

        return {
            "cli_attribution": DirectCreditsPolicy.CLI_ATTRIBUTION,
            "primary_model_attribution": UsageValueFormatter.PRIMARY_MODEL_NOTE,
            "coverage": DirectCreditsPolicy.coverage(date_from, date_to) + self._history_note(rows),
            "history_coverage": self._history_coverage(rows),
            "date_from": date_from,
            "date_to": date_to,
            "generated_at": self._generated_at(),
            "currency": {
                "ai_credit_usd_rate": DirectCreditsPolicy.AI_CREDIT_USD_RATE,
                "usd_jpy_rate": usd_jpy_rate,
            },
            "run_selection_policies": {
                "copilot_cli": RunSelectionPolicy.RECONCILED_HISTORY,
                "copilot_chat": RunSelectionPolicy.CHAT_REQUEST_LEVEL,
            },
            "attribution_notes": {
                "copilot_cli": (DirectCreditsPolicy.CLI_ATTRIBUTION),
                "copilot_chat": "request-level attribution",
            },
            "data_status": self._data_status(totals, excluded_sessions),
            "totals": self._with_currency(
                {**totals.to_dict(), "excluded_cli_sessions": excluded_sessions}, converter
            ),
            "model_rows": [
                {"row_kind": "total", "display_model": "Total", "label": "Total",
                 **self._with_currency(totals.to_dict(), converter)},
                *[self._with_currency(row, converter) for row in self._model_rows(rows)],
            ],
            "model_breakdown": [
                self._with_currency(row, converter) for row in self._model_breakdown(rows)
            ],
            "daily_rows": self._report_daily_rows(rows, by_day_totals, totals, converter),
            "detail_rows": self._report_detail_rows(rows, converter),
            "confidence_breakdown": self._confidence_rows(confidence_totals, confidence_counts),
            "diagnostics": self._diagnostic_summary(),
        }

    # coverageは全frontendが同じ説明を表示するため共通summaryに置く。
    @staticmethod
    def _history_coverage(rows: list[UsageRow]) -> dict[str, int]:
        """期間内の採用状態別件数を返す。"""
        counts: dict[str, int] = {}
        for row in rows:
            if row['source'] == 'copilot_cli':
                counts[row['coverage']] = counts.get(row['coverage'], 0) + 1
        return counts

    # 履歴単独の既知小計を完全なsession総額と誤認させない。
    @classmethod
    def _history_note(cls, rows: list[UsageRow]) -> str:
        """既存のcoverage表示欄で状態を失わず伝える。"""
        counts = cls._history_coverage(rows)
        if not any('history' in key for key in counts):
            return ''
        note = '; CLI coverage: ' + ', '.join(f'{k}={v}' for k, v in sorted(counts.items()))
        if counts.get('history_only'):
            note += '; history-only values are observed subtotals, completeness unknown'
        if any(not row['tokens_valid']
               and row['inclusion_status'] not in {'unresolved_history', 'included'}
               for row in rows):
            note += '; some token metadata is unavailable'
        return note

    # 空・全欠落・部分小計を区別する。
    @staticmethod
    def _data_status(totals: SummaryTotals, excluded_sessions: int = 0) -> str:
        """利用者が集計の範囲を誤解しない状態文を返す。"""

        if excluded_sessions:
            status = (
                "Known subtotal (incomplete)"
                if totals.known_credits_count
                else "Credits unavailable"
            )
            return f"{status}; excluded unresolved CLI sessions: {excluded_sessions}"
        if totals.unresolved_history_count:
            return ("Known subtotal (request history overlap unresolved)"
                    if totals.known_credits_count else "Credits unavailable (unresolved history)")
        if totals.missing_credits_count:
            return (
                "Known subtotal (incomplete)"
                if totals.known_credits_count
                else "Credits unavailable"
            )
        if not totals.known_credits_count:
            return "Reference tokens only" if totals.reference_records_count else "No data"
        return "Known direct credits"

    # 永続化した名前は変えず、承認されたChat名前空間だけを除く。
    @staticmethod
    def _effective_model(row: UsageRow) -> str | None:
        """表示用モデルIDを求め、不明は文字列ではなくNoneで区別する。"""

        model: str | None = row["model_key"]
        if model is None:
            model = row["estimated_primary_model"]
        if row["source"] == "copilot_chat" and model and model.startswith("copilot/"):
            remainder: str = model.removeprefix("copilot/")
            if remainder:
                return remainder
        return model

    # 全frontendで同じ採用行と精度のまま集約する。
    def _aggregate_rows(self, rows: list[UsageRow]) -> dict[str, object]:
        """金額・tokenと確度を共通集約する。"""

        totals: SummaryTotals = SummaryTotals()
        confidences: set[str] = set()
        for row in rows:
            totals.add_row(row)
            confidences.add(str(row["confidence"]))
        confidence: str = self._combined_confidence(confidences)
        return {**totals.to_dict(), "confidence": confidence,
                "coarse_confidence": ConfidenceMapper.to_coarse(confidence)}

    # sourceや推定の違いを合計表の行数へ持ち込まない。
    def _model_rows(self, rows: list[UsageRow]) -> list[dict[str, object]]:
        """モデル単位の合計行を返す。"""

        groups: defaultdict[str | None, list[UsageRow]] = defaultdict(list)
        for row in rows:
            groups[self._effective_model(row)].append(row)
        result: list[dict[str, object]] = []
        for model in sorted(groups, key=lambda name: (name is None, name or "")):
            values: dict[str, object] = self._aggregate_rows(groups[model])
            label: str = UsageValueFormatter.aggregate_model_label(
                model, values["estimated_primary_ai_credits"]
            )
            result.append({"row_kind": "model", "model_key": model,
                           "display_model": label, "label": label, **values})
        return result

    # 推定帰属と圧縮用途を直交させ、参照・内包状態も失わない。
    def _model_breakdown(self, rows: list[UsageRow]) -> list[dict[str, object]]:
        """各採用レコードを1つの内訳だけに割り当てる。"""

        groups: defaultdict[ModelBreakdownKey, list[UsageRow]] = defaultdict(list)
        for row in rows:
            key: ModelBreakdownKey = ModelBreakdownKey.from_row(row)
            groups[key].append(row)
        result: list[dict[str, object]] = []
        for key, members in groups.items():
            model: str | None = self._effective_model(members[0])
            values: dict[str, object] = self._aggregate_rows(members)
            result.append({
                "model_key": model, "raw_model_key": key.raw_model,
                "estimated_primary_model": key.estimated_model,
                "display_model": UsageValueFormatter.aggregate_model_label(
                    model, values["estimated_primary_ai_credits"]
                ),
                "source": key.source, "kind": key.kind, "attribution": key.attribution,
                "inclusion_status": key.inclusion_status, **values,
            })
        return sorted(result, key=lambda row: (
            row["model_key"] is None, str(row["model_key"] or ""), str(row["source"]),
            str(row["kind"]), str(row["attribution"]), str(row["raw_model_key"] or ""),
            str(row["estimated_primary_model"] or ""), str(row["inclusion_status"]),
        ))

    # confidence 別 rows を作成する。
    def _confidence_rows(
        self,
        confidence_totals: defaultdict[str, SummaryTotals],
        confidence_counts: defaultdict[str, int],
    ) -> list[dict[str, object]]:
        """confidence 別 summary rows を返す。"""

        rows: list[dict[str, object]] = []
        for confidence in sorted(confidence_totals):
            rows.append(
                {
                    "confidence": confidence,
                    "coarse_confidence": ConfidenceMapper.to_coarse(confidence),
                    "records_count": confidence_counts[confidence],
                    **confidence_totals[confidence].to_dict(),
                    "direct_nano_aiu": (
                        confidence_totals[confidence].direct_nano_aiu
                        if confidence_totals[confidence].has_direct_nano_aiu
                        else None
                    ),
                }
            )
        return rows

    # diagnostic summary を作成する。
    def _diagnostic_summary(self) -> dict[str, object]:
        """diagnostic 件数 summary を返す。"""

        by_code: list[dict[str, object]] = [
            {
                "code": str(row["code"]),
                "count": int(row["count"]),
            }
            for row in self._repository.diagnostic_counts()
        ]
        warnings_count: int = sum(row["count"] for row in by_code if isinstance(row["count"], int))
        return {"warnings_count": warnings_count, "by_code": by_code}

    # model内に複数confidenceがあればmixedへ集約する。
    def _combined_confidence(self, confidences: set[str]) -> str:
        """model row用の単一confidence labelを返す。"""

        if len(confidences) == 1:
            return next(iter(confidences))
        return "mixed"

    # report daily rows を作成する。
    def _report_daily_rows(
        self,
        records: list[UsageRow],
        by_day_totals: defaultdict[str, SummaryTotals],
        totals: SummaryTotals,
        converter: CurrencyConverter,
    ) -> list[dict[str, object]]:
        """total row first の daily rows を返す。"""

        rows: list[dict[str, object]] = [
            {
                "row_kind": "total",
                "date": "Total",
                "display_model": "Total",
                **self._with_currency(totals.to_dict(), converter),
            }
        ]
        by_day_records: defaultdict[str, list[UsageRow]] = defaultdict(list)
        for row in records:
            by_day_records[str(row["timestamp"])[:10]].append(row)
        for day_key in sorted(by_day_totals):
            rows.append(
                {
                    "row_kind": "day",
                    "date": day_key,
                    "display_model": "Total",
                    **self._with_currency(by_day_totals[day_key].to_dict(), converter),
                }
            )
            rows.extend(
                {**self._with_currency(row, converter), "row_kind": "day_model", "date": day_key}
                for row in self._model_rows(by_day_records[day_key])
            )
        return rows

    # detail rows を作成する。
    def _report_detail_rows(
        self, rows: list[UsageRow], converter: CurrencyConverter
    ) -> list[dict[str, object]]:
        """workspace / session / run / model 単位の detail rows を返す。"""

        detail_rows: list[dict[str, object]] = []
        for row in rows:
            detail_rows.append(
                {
                    "source": str(row["source"]),
                    "workspace_alias": str(row["workspace_alias"]),
                    "session_id": str(row["session_id"]),
                    "run_id": int(row["run_id"]),
                    "run_kind": str(row["run_kind"]),
                    "run_selection_policy": str(row["run_selection_policy"]),
                    "timestamp": str(row["timestamp"]),
                    "model_key": row["model_key"],
                    "estimated_primary_model": row["estimated_primary_model"],
                    "token_reference": (
                        row["ai_credits_source"] == AiCreditsSource.CLI_TOKEN_REFERENCE
                    ),
                    "display_model": UsageValueFormatter.model_label(
                        row["model_key"],
                        row["estimated_primary_model"],
                        row["ai_credits_source"] == AiCreditsSource.CLI_TOKEN_REFERENCE,
                    ),
                    "attribution_scope": row["attribution_scope"],
                    "event_order": row["event_order"],
                    "ai_credits_source": str(row["ai_credits_source"]),
                    "reported_nano_aiu": row["reported_nano_aiu"],
                    "observed_nano_aiu": row["observed_nano_aiu"],
                    "inclusion_status": row["inclusion_status"],
                    "coverage": row["coverage"],
                    "initiator": row["initiator"],
                    "tokens_valid": bool(row["tokens_valid"]),
                    "reasoning_tokens_valid": bool(row["reasoning_tokens_valid"]),
                    "confidence": str(row["confidence"]),
                    **self._with_currency(self._row_totals(row), converter),
                }
            )
        return detail_rows

    # token / credit dict に currency fields を追加する。
    def _with_currency(
        self, totals: Mapping[str, object], converter: CurrencyConverter
    ) -> dict[str, object]:
        """token / credit dict に USD / JPY estimate を追加する。"""

        estimated_value: object = totals["ai_credits"]
        ai_credits: float | None = (
            float(estimated_value) if isinstance(estimated_value, int | float) else None
        )
        unattributed = totals.get("unattributed_ai_credits", 0)
        return {
            **totals,
            "estimated_usd": converter.to_usd(ai_credits),
            "estimated_jpy": converter.to_jpy(ai_credits),
            "unattributed_jpy": converter.to_jpy(
                float(unattributed) if isinstance(unattributed, int | float) else 0.0
            ),
        }

    # usage row から totals dict を作成する。
    def _row_totals(self, row: UsageRow) -> dict[str, object]:
        """usage row の token / credit fields を dict に変換する。"""

        return {
            "raw_input_tokens": row["raw_input_tokens"] if row["tokens_valid"] else None,
            "fresh_input_tokens": row["fresh_input_tokens"] if row["tokens_valid"] else None,
            "cached_input_tokens": row["cached_input_tokens"] if row["tokens_valid"] else None,
            "cache_write_tokens": row["cache_write_tokens"] if row["tokens_valid"] else None,
            "output_tokens": row["output_tokens"] if row["tokens_valid"] else None,
            "reasoning_tokens": (
                row["reasoning_tokens"] if row["reasoning_tokens_valid"] else None
            ),
            "ai_credits": self._effective_row_credits(row),
        }

    # usage rowのsource別正本からeffective creditsを返す。
    def _effective_row_credits(self, row: UsageRow) -> float | None:
        """nano AIU直接値を優先し、output境界でだけfloatへ変換する。"""

        nano_value: object = row["reported_nano_aiu"]
        if isinstance(nano_value, int):
            return float(NanoAiuAmount(nano_value).to_ai_credits())
        estimated_value: object = row["reported_ai_credits"]
        if isinstance(estimated_value, int | float):
            return float(Decimal(str(estimated_value)))
        return None

    # 生成時刻を返す。
    def _generated_at(self) -> str:
        """UTC ISO 8601 の generated_at を返す。"""

        return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
