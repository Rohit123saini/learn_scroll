"""
spaced_repetition/sm2.py - the SM-2 scheduler (SuperMemo-2) as PURE functions: no Django, no clock, no database.

The user rates a card with one of four buttons; each maps to an SM-2 quality `q` (0-5):

    again -> 1    forgot it                    hard -> 3    remembered with effort
    good  -> 4    remembered                   easy -> 5    effortless

SM-2 rules (this module):
  * q < 3 ("again"): repetitions back to 0, interval 1 day, ease -0.2 (floor 1.3), lapses + 1. The card comes back
    after `relearn_minutes` (the caller decides the time) so a forgotten card is seen again in the same session.
  * q >= 3: repetitions 0 -> 1 day (easy: 3 days), 1 -> 6 days, then round(previous interval * ease). The gap is always
    at least one day longer than before and never above `max_interval`. repetitions + 1.
  * ease += 0.1 - (5-q) * (0.08 + (5-q) * 0.02)   (good: unchanged, easy: +0.10, hard: -0.14), floor 1.3.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

GRADES: Dict[str, int] = {"again": 1, "hard": 3, "good": 4, "easy": 5}
START_EASE = 2.5
MIN_EASE = 1.3
FIRST_INTERVAL = 1
SECOND_INTERVAL = 6
EASY_FIRST_INTERVAL = 3
LAPSE_EASE_PENALTY = 0.2


@dataclass(frozen=True)
class CardState:
    ease: float = START_EASE
    interval_days: int = 0
    repetitions: int = 0
    lapses: int = 0


@dataclass(frozen=True)
class Outcome:
    state: CardState
    relearn: bool  # True = "again": show it again soon (minutes), not on a day boundary


def is_valid_grade(grade) -> bool:
    return str(grade) in GRADES


def next_state(state: CardState, grade: str, max_interval: int = 365) -> Outcome:
    """Pure SM-2 step. Raises KeyError for an unknown grade (callers validate first with `is_valid_grade`)."""
    q = GRADES[grade]
    if q < 3:
        return Outcome(
            CardState(
                ease=round(max(MIN_EASE, state.ease - LAPSE_EASE_PENALTY), 4),
                interval_days=FIRST_INTERVAL,
                repetitions=0,
                lapses=state.lapses + 1,
            ),
            relearn=True,
        )

    if state.repetitions == 0:
        interval = EASY_FIRST_INTERVAL if q == 5 else FIRST_INTERVAL
    elif state.repetitions == 1:
        interval = SECOND_INTERVAL
    else:
        interval = int(round(state.interval_days * state.ease))
    interval = min(max_interval, max(interval, state.interval_days + 1 if state.repetitions >= 1 else interval))

    delta = 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)
    return Outcome(
        CardState(
            ease=round(max(MIN_EASE, state.ease + delta), 4),
            interval_days=max(1, interval),
            repetitions=state.repetitions + 1,
            lapses=state.lapses,
        ),
        relearn=False,
    )


def preview(state: CardState, max_interval: int = 365, relearn_minutes: int = 10) -> Dict[str, dict]:
    """What each button would do, for the labels under a card: {"again": {"minutes": 10}, "good": {"days": 6}, ...}."""
    out: Dict[str, dict] = {}
    for grade in GRADES:
        o = next_state(state, grade, max_interval)
        out[grade] = {"minutes": relearn_minutes} if o.relearn else {"days": o.interval_days}
    return out
