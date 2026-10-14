from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


# 進捗に本文や入力パスを混ぜず、処理段階だけを共有する。
class ScanPhase(StrEnum):
    """GUIとcoreで共有するscanの段階。"""

    DISCOVERING = "Discovering inputs"
    HISTORY = "Reading history"
    READING = "Reading session logs"
    NORMALIZING = "Normalizing usage"
    SAVING = "Saving database"
    REPORT = "Loading report"


# 件数は時間の予測ではなく、固定した入力の処理完了数を表す。
@dataclass(frozen=True)
class ScanProgress:
    """パスを含まない進捗通知。探索中だけtotalが未確定。"""

    phase: ScanPhase
    completed: int = 0
    total: int | None = None

    # 空入力でも0除算せず表示できる。
    @property
    def fraction(self) -> float:
        """読み込み対象の完了率を返す。全体の成功率ではない。"""

        return self.completed / self.total if self.total else 0.0


# 性能比較で段階別の負担を確認するための、メモリ内だけの記録。
@dataclass(frozen=True)
class ScanTiming:
    """一つの処理段階で費やした秒数。"""

    phase: ScanPhase
    seconds: float


# 保存前の中断を通常の入力失敗と区別する。
class ScanCancelled(Exception):
    """利用者の終了要求またはGUI通信切断による中断。"""


# 通知と協調終了は任意とし、CLIの既存呼び出しを同期のまま保つ。
class ScanControl:
    """scanの進捗・処理段階別時間・保存前の終了要求を管理する。"""

    # callbackは同じプロセス内でのみ使い、parser workerには渡さない。
    def __init__(
        self, notify: Callable[[ScanProgress], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        """進捗通知と終了要求の読み取りを受け取る。"""

        self._notify = notify
        self._cancelled = cancelled
        self.progress = ScanProgress(ScanPhase.DISCOVERING)
        self._started: float = time.perf_counter()
        self._timings: list[ScanTiming] = []

    # transaction中には呼ばず、境界でのみ終了要求を受ける。
    def checkpoint(self) -> None:
        """終了が要求されていれば保存前の処理を中断する。"""

        if self._cancelled is not None and self._cancelled():
            raise ScanCancelled("Scan stopped before the next phase.")

    # 段階の切り替えでだけ時間を区切り、件数更新の負荷を抑える。
    def update(
        self, phase: ScanPhase, completed: int = 0, total: int | None = None,
    ) -> None:
        """終了要求を確認して、段階と処理完了件数を通知する。"""

        self.checkpoint()
        if phase != self.progress.phase:
            now: float = time.perf_counter()
            self._timings.append(ScanTiming(self.progress.phase, now - self._started))
            self._started = now
        self.progress = ScanProgress(phase, completed, total)
        if self._notify is not None:
            self._notify(self.progress)

    # 最後の段階も計測に含める。
    def timings(self) -> tuple[ScanTiming, ...]:
        """現在までの段階別時間を返す。保存は行わない。"""

        return (*self._timings,
                ScanTiming(self.progress.phase, time.perf_counter() - self._started))
