from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

from copilot_usage_visualizer.normalization.contract_period import UsageDateResolver
from copilot_usage_visualizer.normalization.usage import (
    AiCreditsSource,
    DiagnosticRecord,
    ParsedUsageRecord,
    RunKind,
    UsageRow,
    UsageSelection,
)


# 直接値と集計対象期間の共通契約。
class DirectCreditsPolicy:
    """解析済み usage の期間と欠落状態を統一する。"""

    CLI_ATTRIBUTION: str = (
        "CLI: reconciled request history uses UTC request dates and direct models. "
        "Remaining session cumulative credits use the selected event date (timestamp date prefix); "
        "not request-level daily usage or account billing totals. "
        "Separately reconciled compaction credits use their own event date. "
        "Tokens use the last valid shutdown date and may be reference-only. "
        "Unfinished sessions may omit later usage; selected credits are already included."
    )
    BASELINE: str = "2026-08-01"
    AI_CREDIT_USD_RATE: float = 0.01

    # ライフサイクル解析後に期間を適用する。
    @classmethod
    def normalize(
        cls, records: list[ParsedUsageRecord], diagnostics: list[DiagnosticRecord]
    ) -> list[ParsedUsageRecord]:
        """対象期間の record を返し、直接値欠落の警告を追加する。"""

        result: list[ParsedUsageRecord] = []
        for record in records:
            if UsageDateResolver().resolve(record.timestamp) < date.fromisoformat(cls.BASELINE):
                continue
            if (
                record.reported_nano_aiu is None
                and record.reported_ai_credits is None
                and record.ai_credits_source
                not in {
                    AiCreditsSource.COPILOT_CHAT_SUMMARY_REFERENCE,
                    AiCreditsSource.CLI_TOKEN_REFERENCE,
                    AiCreditsSource.CLI_COMPACTION,
                    AiCreditsSource.CLI_HISTORY,
                }
            ):
                record = replace(
                    record,
                    confidence="credits_unavailable",
                    ai_credits_source=AiCreditsSource.UNAVAILABLE,
                )
                if record.source == "copilot_cli":
                    result.append(record)
                    continue
                diagnostics.append(
                    DiagnosticRecord(
                        source=record.source,
                        source_file_path=record.source_file_path,
                        source_file_display_alias=record.source_file_display_alias,
                        code=f"{record.source}_credits_unavailable",
                        message="Direct credits unavailable; token metadata retained.",
                    )
                )
            result.append(record)
        return result

    # 出力期間の有効範囲を明示する。
    @classmethod
    def coverage(cls, date_from: str, date_to: str) -> str:
        """旧期間をデータなしと混同しない表示文を返す。"""

        if date_to < cls.BASELINE:
            return f"Outside supported coverage (from {cls.BASELINE})"
        if date_from < cls.BASELINE:
            return f"Effective coverage: {cls.BASELINE} to {date_to}"
        return f"Supported coverage: {date_from} to {date_to}"

# 保存した観測値から、二重計上しない採用射影を作る。
class CliHistorySelection:
    """requestと圧縮を同一snapshotで照合し、期間フィルタの前に選択する。"""

    # SQLite取得結果を変更せず、出力用の行だけを書き換える。
    @classmethod
    def select(cls, observations: list[UsageRow]) -> UsageSelection:
        """session単位の照合結果と固定コードの診断を返す。"""
        sessions: dict[str, list[UsageRow]] = {}
        selected: list[UsageRow] = []
        issues: list[tuple[int, str]] = []
        for original in observations:
            row = original.copy()
            row['coverage'] = 'event_only' if row['source'] == 'copilot_cli' else 'request'
            if row['source'] != 'copilot_cli':
                selected.append(row)
            else:
                sessions.setdefault(row['session_id'], []).append(row)
        for rows in sessions.values():
            effective, reasons = cls._session(rows)
            selected.extend(effective)
            issues.extend(reasons)
        return UsageSelection(selected, issues)

    # UTCの比較を共通化し、timezone欠落を推測しない。
    @staticmethod
    def _time(value: str) -> datetime:
        """時刻メタデータを比較可能なUTCへ変換する。"""
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('Timestamp has no timezone.')
        return parsed.astimezone(UTC)

    # 履歴の不採用行は直接観測額を残し、tokenも二重加算しない。
    @staticmethod
    def _unresolved(row: UsageRow) -> UsageRow:
        """観測と加算を混同しない参照射影を返す。"""
        value = row.copy()
        value.update(reported_nano_aiu=None, inclusion_status='unresolved_history', tokens_valid=0,
                     reasoning_tokens_valid=0, confidence='unresolved_request_history',
                     coverage='unresolved_history',
                     raw_input_tokens=0, fresh_input_tokens=0, cached_input_tokens=0,
                     cache_write_tokens=0, output_tokens=0, reasoning_tokens=0)
        return value

    # 同一sessionでも別homeや異なる入力の履歴を無条件に合算しない。
    @classmethod
    def _session(cls, rows: list[UsageRow]) -> tuple[list[UsageRow], list[tuple[int, str]]]:
        """履歴の採用、累積の維持、圧縮の未確定を一か所で決める。"""
        history = [r for r in rows if r['run_kind'] == RunKind.HISTORY]
        events = [r for r in rows if r['run_kind'] != RunKind.HISTORY]
        scopes = {r['history_scope'] for r in events} if events else {
            r['history_scope'] for r in history
        }
        scope = next(iter(scopes)) if len(scopes) == 1 else None
        matched = [r for r in history if scope is not None and r['history_scope'] == scope]
        unmatched = [cls._unresolved(r) for r in history if r not in matched]
        candidates = [r for r in events if r['run_kind'] != RunKind.COMPACTION
                      and r['reported_nano_aiu'] is not None]
        compactions = [r for r in events if r['run_kind'] == RunKind.COMPACTION]
        model_observations = [
            r for r in events
            if r['ai_credits_source'] == AiCreditsSource.CLI_TOKEN_REFERENCE
            and r['event_order'] == candidates[0]['event_order']
            and r['observed_nano_aiu'] is not None
        ] if candidates else []
        issues: list[tuple[int, str]] = []
        if not candidates and matched:
            accepted: list[UsageRow] = []
            for row in matched:
                if (row['evidence_error'] or row['history_error']
                        or row['reported_nano_aiu'] is None):
                    accepted.append(cls._unresolved(row))
                    continue
                row.update(coverage='history_only', inclusion_status='history_selected',
                           confidence='measured_from_request_history_only')
                accepted.append(row)
            # 履歴と一致しない圧縮は未確定のまま見えるようにする。
            for compaction in compactions:
                if (compaction['evidence_error'] or sum(
                    r['match_key'] == compaction['match_key'] and r['reported_nano_aiu'] is not None
                    for r in accepted
                ) != 1):
                    compaction.update(
                        reported_nano_aiu=None, coverage='unresolved_compaction',
                        inclusion_status='unresolved', confidence='credits_unavailable',
                    )
                    accepted.append(compaction)
                    issues.append((compaction['source_file_id'],
                                   'cli_compaction_unresolved_missing_candidate'))
            return [*accepted, *unmatched], issues
        reason: str = 'history_empty'
        if candidates and matched:
            reason = cls._relation(candidates, compactions, matched, model_observations)
            if reason in {'included', 'separate'}:
                cutoff = cls._time(candidates[0]['timestamp'])
                adopted: list[UsageRow] = []
                for row in matched:
                    if row['evidence_error'] or row['history_error']:
                        adopted.append(cls._unresolved(row))
                    else:
                        row.update(
                            coverage=('reconciled_history' if cls._time(row['timestamp']) <= cutoff
                                      else 'reconciled_history_tail'),
                            inclusion_status='history_selected',
                        )
                        adopted.append(row)
                return [*adopted, *unmatched], issues
            if reason in {'model_totals_mismatch:included',
                          'model_totals_mismatch:separate'}:
                return cls._model_mismatch_fallback(
                    events, candidates, compactions, matched, reason.rsplit(':', 1)[1]
                )
        elif events:
            reason = events[0]['history_error'] or (
                'ambiguous_scope' if len(scopes) != 1 or unmatched else 'history_empty'
            )
        elif history:
            reason = 'ambiguous_scope'
        if history:
            issues.append((history[0]['source_file_id'], f'cli_history_selection_{reason}'))
        for row in events:
            if row['run_kind'] == RunKind.COMPACTION:
                row.update(reported_nano_aiu=None, confidence='credits_unavailable',
                           inclusion_status='unresolved', coverage='unresolved_compaction')
                issues.append((row['source_file_id'], f'cli_compaction_unresolved_{reason}'))
            elif history:
                row['coverage'] = 'cumulative_with_unresolved_history'
        return [*events, *(cls._unresolved(r) for r in history)], issues

    # 包含は総額・期間・識別・圧縮の一対一対応を併せて判定する。
    @classmethod
    def _relation(
        cls, candidates: list[UsageRow], compactions: list[UsageRow], history: list[UsageRow],
        model_observations: list[UsageRow],
    ) -> str:
        """不完全な履歴や金額だけの一致は候補の置換を正当化しない。"""
        try:
            cutoff = cls._time(candidates[0]['timestamp'])
            if any(cls._time(e['timestamp']) > cutoff for e in compactions):
                return 'later_event'
            if any(e['evidence_error'] for e in compactions):
                return next(str(e['evidence_error']) for e in compactions if e['evidence_error'])
            prefix = [r for r in history if cls._time(r['timestamp']) <= cutoff]
        except ValueError:
            return 'invalid_timestamp'
        if not prefix:
            return 'history_empty'
        for row in prefix:
            if row['history_error'] or row['evidence_error']:
                return str(row['history_error'] or row['evidence_error'])
            if row['reported_nano_aiu'] is None or not row['tokens_valid']:
                return 'invalid_history_metadata'
        # 同じ粗いキーを持つ別履歴行や別イベントを誤って縮約しない。
        expected = [r['match_key'] for r in compactions]
        actual = [r['match_key'] for r in prefix if r['initiator'] == 'compaction'
                  or (r['initiator'] is None and r['match_key'] in expected)]
        if compactions and (
            None in expected or len(set(expected)) != len(expected)
            or len(actual) != len(expected) or any(actual.count(k) != 1 for k in expected)
        ):
            return 'event_match_mismatch'
        total = sum(r['reported_nano_aiu'] or 0 for r in candidates)
        history_total = sum(r['reported_nano_aiu'] or 0 for r in prefix)
        if total == history_total:
            return cls._model_relation(candidates, prefix, 'included', model_observations)
        if any(r['initiator'] not in {'user', 'agent', 'sub-agent', 'compaction'} for r in prefix):
            return 'unsupported_initiator'
        ordinary = sum(r['reported_nano_aiu'] or 0 for r in prefix
                       if r['initiator'] != 'compaction')
        if compactions and total == ordinary and len(actual) == len(expected):
            return cls._model_relation(
                candidates, [r for r in prefix if r['initiator'] != 'compaction'],
                'separate', model_observations,
            )
        return 'total_mismatch'


    # 部分的な直接モデル行を下回る履歴へ置換しない。
    @staticmethod
    def _model_relation(
        candidates: list[UsageRow], history: list[UsageRow], relation: str,
        model_observations: list[UsageRow],
    ) -> str:
        """全額累積行と非加算のモデル下限を分離して照合する。"""
        history_totals: dict[str, int] = {}
        for row in history:
            if row['model_key'] is not None:
                history_totals[row['model_key']] = (
                    history_totals.get(row['model_key'], 0) + (row['reported_nano_aiu'] or 0)
                )
        if any(r['model_key'] is None for r in candidates):
            observed: dict[str, int] = {}
            for row in model_observations:
                if row['model_key'] is None or row['observed_nano_aiu'] is None:
                    continue
                observed[row['model_key']] = (
                    observed.get(row['model_key'], 0) + row['observed_nano_aiu']
                )
            if any(history_totals.get(model, 0) < amount
                   for model, amount in observed.items()):
                return f'model_totals_mismatch:{relation}'
            full_total = sum(r['reported_nano_aiu'] or 0 for r in candidates)
            known_total = sum(observed.values())
            history_unallocated = sum(history_totals.values()) - known_total
            event_unallocated = full_total - known_total
            return (relation if event_unallocated >= 0 and history_unallocated == event_unallocated
                    else f'model_totals_mismatch:{relation}')
        expected = {str(r['model_key']): r['reported_nano_aiu'] or 0 for r in candidates}
        return relation if history_totals == expected else f'model_totals_mismatch:{relation}'

    # モデル内訳が矛盾する場合も、金額と圧縮の独立した証拠を保持する。
    @classmethod
    def _model_mismatch_fallback(
        cls, rows: list[UsageRow], candidates: list[UsageRow], compactions: list[UsageRow],
        history: list[UsageRow], relation: str,
    ) -> tuple[list[UsageRow], list[tuple[int, str]]]:
        """元の累積を使い、included/separateの圧縮根拠だけを反映する。"""
        cutoff = cls._time(candidates[0]['timestamp'])
        prefix = [r for r in history if cls._time(r['timestamp']) <= cutoff]
        expected = [r['match_key'] for r in compactions]
        matched_compactions = [
            r for r in prefix
            if r['initiator'] == 'compaction' and r['match_key'] in expected
            and not r['history_error'] and not r['evidence_error']
            and r['reported_nano_aiu'] is not None and r['tokens_valid']
        ]
        proved_separate = (
            relation == 'separate' and len(expected) == len(matched_compactions)
            and all(matched_compactions.count(r) == 1 for r in matched_compactions)
        )
        expected_set = set(expected) if proved_separate else set()
        selected: list[UsageRow] = []
        for row in prefix:
            if proved_separate and row['match_key'] in expected_set:
                row.update(
                    coverage='reconciled_history', inclusion_status='history_selected',
                    tokens_valid=0, reasoning_tokens_valid=0,
                    raw_input_tokens=0, fresh_input_tokens=0, cached_input_tokens=0,
                    cache_write_tokens=0, output_tokens=0, reasoning_tokens=0,
                )
                selected.append(row)
            else:
                selected.append(cls._unresolved(row))
        retained_events = [r for r in rows if r['run_kind'] != RunKind.COMPACTION]
        for row in retained_events:
            row['coverage'] = 'cumulative_with_unresolved_history'
        if relation == 'included':
            for row in compactions:
                row.update(
                    reported_nano_aiu=None, inclusion_status='included',
                    coverage='included_compaction', confidence='compaction_included_in_cumulative',
                    tokens_valid=0, reasoning_tokens_valid=0,
                )
                retained_events.append(row)
        issues = [(history[0]['source_file_id'], 'cli_history_selection_model_totals_mismatch')]
        tail = [cls._unresolved(r) for r in history if cls._time(r['timestamp']) > cutoff]
        return [*retained_events, *selected, *tail], issues
