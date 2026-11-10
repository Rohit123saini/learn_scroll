"""
spaced_repetition/services.py - P-F8 business logic (no HTTP). Every function takes an explicit `now` so tests never sleep.

A "card" is a post in the user's Revision deck (`PostSave(collection_name="revision")`, see post/study_tools.py).
  * NEW  card = deck item that has no RevisionCard yet. Introduced at most `SRS_NEW_PER_DAY` per local day.
  * DUE  card = RevisionCard with next_review_at <= now AND the post is still in the deck (taking a post out of the deck
               pauses its card, putting it back resumes it - the SM-2 state is kept).
Day-interval cards come due at LOCAL MIDNIGHT of their day (so "6 days" means "from the 6th morning", not "6x24h from
the minute you tapped Good"). "Again" comes back after `SRS_RELEARN_MINUTES`.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Dict, Iterable, List, Optional

from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef
from django.utils import timezone

from . import conf, sm2
from .models import RevisionCard, RevisionReview

logger = logging.getLogger(__name__)

MATURE_INTERVAL_DAYS = 21  # a card whose gap reached 3 weeks counts as "learned"


class SrsError(Exception):
    """Carries the machine `code` + HTTP status the view turns into {"detail", "code"}."""

    def __init__(self, code: str, detail: str, status: int = 400):
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


# ------------------------------------------------------------------------------------------------ time helpers
def user_tzinfo(user_id):
    """The user's timezone (NotificationPreference.timezone) or the server's current timezone."""
    try:
        from user_profile import learning_prefs

        tz = learning_prefs.user_tz(user_id)
    except Exception:  # noqa: BLE001
        tz = None
    return tz or timezone.get_current_timezone()


def local_day_start(now, tz):
    local = now.astimezone(tz)
    return datetime.combine(local.date(), time.min, tzinfo=tz)


def midnight_after_days(now, tz, days: int):
    """Aware datetime of local 00:00 of (today + days) in `tz`."""
    local_date = now.astimezone(tz).date() + timedelta(days=int(days))
    return datetime.combine(local_date, time.min, tzinfo=tz)


def schedule_next(now, tz, outcome: sm2.Outcome):
    if outcome.relearn:
        return now + timedelta(minutes=conf.relearn_minutes())
    return midnight_after_days(now, tz, outcome.state.interval_days)


# ------------------------------------------------------------------------------------------------ deck queries
def _deck_saves(user):
    from post.models import PostSave
    from post.study_tools import REVISION_COLLECTION

    return PostSave.objects.filter(user=user, collection_name=REVISION_COLLECTION, post__is_deleted=False)


def _in_deck_exists(user_field="user_id"):
    from post.models import PostSave
    from post.study_tools import REVISION_COLLECTION

    return Exists(PostSave.objects.filter(
        post_id=OuterRef("post_id"), user_id=OuterRef(user_field), collection_name=REVISION_COLLECTION,
        post__is_deleted=False,
    ))


def new_used_today(user, now, tz) -> int:
    return RevisionReview.objects.filter(user=user, was_new=True, reviewed_at__gte=local_day_start(now, tz)).count()


def new_remaining_today(user, now, tz) -> int:
    return max(0, conf.new_per_day() - new_used_today(user, now, tz))


def _due_cards_qs(user, now):
    return (
        RevisionCard.objects.filter(user=user, next_review_at__lte=now)
        .filter(_in_deck_exists())
        .select_related("post").prefetch_related("post__media")
        .order_by("next_review_at", "created_at")
    )


def _new_saves_qs(user, now):
    cutoff = now - timedelta(hours=conf.new_min_age_hours())
    has_card = RevisionCard.objects.filter(user=user, post_id=OuterRef("post_id"))
    return (
        _deck_saves(user).filter(created_at__lte=cutoff).filter(~Exists(has_card))
        .select_related("post").prefetch_related("post__media").order_by("created_at")
    )


def build_queue(user, now, limit: Optional[int] = None) -> dict:
    """{"due": [RevisionCard...], "new": [PostSave...]} - due first (oldest first), then up to the new-card allowance."""
    limit = max(1, min(int(limit or conf.max_queue()), conf.max_queue()))
    tz = user_tzinfo(user.pk)
    due = list(_due_cards_qs(user, now)[:limit])
    room = limit - len(due)
    new: list = []
    if room > 0:
        allowance = min(room, new_remaining_today(user, now, tz))
        if allowance > 0:
            new = list(_new_saves_qs(user, now)[:allowance])
    return {"due": due, "new": new}


def counts(user, now) -> dict:
    tz = user_tzinfo(user.pk)
    due = _due_cards_qs(user, now).count()
    unseen = _new_saves_qs(user, now).count()
    remaining = new_remaining_today(user, now, tz)
    reviewed_today = RevisionReview.objects.filter(user=user, reviewed_at__gte=local_day_start(now, tz)).count()
    return {
        "due": due,
        "new": min(unseen, remaining),
        "new_unseen": unseen,
        "total_in_deck": _deck_saves(user).count(),
        "reviewed_today": reviewed_today,
    }


# ------------------------------------------------------------------------------------------------ review
def _new_card(user, post_id, now) -> RevisionCard:
    return RevisionCard(user=user, post_id=post_id, next_review_at=now)


def submit_review(user, post_id, grade, now, duration_ms=None) -> dict:
    """Grade one card. Returns {"card": {...}, "counts": {...}}. Raises SrsError.

    Idempotent against double taps / offline replays: grading a card that is not due yet is 409 `not_due` (the first
    grade already moved it into the future) - except right after "Again", when the card may be graded again at once.
    """
    grade = str(grade or "").strip().lower()
    if not sm2.is_valid_grade(grade):
        raise SrsError("invalid_grade", "grade must be one of: again, hard, good, easy.")
    tz = user_tzinfo(user.pk)
    with transaction.atomic():
        if not _deck_saves(user).filter(post_id=post_id).exists():
            raise SrsError("not_in_deck", "This item is not in your Revision deck.", 404)
        card = RevisionCard.objects.select_for_update().filter(user=user, post_id=post_id).first()
        was_new = card is None
        if was_new:
            if new_remaining_today(user, now, tz) <= 0:
                raise SrsError("new_limit", "Today's new-card limit is reached. Come back tomorrow.", 409)
            card = _new_card(user, post_id, now)
            try:
                with transaction.atomic():
                    card.save()
            except IntegrityError:  # two taps raced: the other one created the card -> treat as a duplicate
                raise SrsError("not_due", "This card was already reviewed.", 409)
        elif card.next_review_at > now and not card.relearning:
            raise SrsError("not_due", "This card is not due yet.", 409)

        before = card.interval_days
        outcome = sm2.next_state(card.sm2_state(), grade, conf.max_interval_days())
        st = outcome.state
        card.ease, card.interval_days, card.repetitions, card.lapses = st.ease, st.interval_days, st.repetitions, st.lapses
        card.relearning = outcome.relearn
        card.next_review_at = schedule_next(now, tz, outcome)
        card.last_reviewed_at = now
        card.last_grade = grade
        card.save()
        RevisionReview.objects.create(
            card=card, user=user, grade=grade, reviewed_at=now, interval_before=before, interval_after=st.interval_days,
            ease_after=st.ease, was_new=was_new,
            duration_ms=max(0, min(int(duration_ms), 10 * 60 * 1000)) if isinstance(duration_ms, (int, float)) else None,
        )
    return {"card": serialize_card_state(card), "counts": counts(user, now)}


def serialize_card_state(card: RevisionCard) -> dict:
    return {
        "post_id": str(card.post_id),
        "ease": round(float(card.ease), 2),
        "interval_days": int(card.interval_days),
        "repetitions": int(card.repetitions),
        "lapses": int(card.lapses),
        "relearning": bool(card.relearning),
        "next_review_at": card.next_review_at.isoformat(),
        "last_grade": card.last_grade,
    }


def preview_for(card: Optional[RevisionCard]) -> Dict[str, dict]:
    state = card.sm2_state() if card is not None else sm2.CardState()
    return sm2.preview(state, conf.max_interval_days(), conf.relearn_minutes())


# ------------------------------------------------------------------------------------------------ stats
def stats(user, now, days: int = 7) -> dict:
    tz = user_tzinfo(user.pk)
    days = max(1, min(int(days), 30))
    since = local_day_start(now, tz) - timedelta(days=days - 1)
    reviews = list(RevisionReview.objects.filter(user=user, reviewed_at__gte=since).values_list("reviewed_at", "grade"))
    per_day: Dict[str, int] = {}
    for i in range(days):
        per_day[(since + timedelta(days=i)).astimezone(tz).date().isoformat()] = 0
    for ts, _g in reviews:
        key = ts.astimezone(tz).date().isoformat()
        if key in per_day:
            per_day[key] += 1

    month = RevisionReview.objects.filter(user=user, reviewed_at__gte=now - timedelta(days=30), was_new=False)
    total_m = month.count()
    kept = month.exclude(grade="again").count()
    retention = round(100.0 * kept / total_m, 1) if total_m >= 5 else None  # too few reviews = no number, not a fake one

    cards = RevisionCard.objects.filter(user=user).filter(_in_deck_exists())
    mature = cards.filter(interval_days__gte=MATURE_INTERVAL_DAYS).count()
    learning = cards.count() - mature
    nxt = cards.filter(next_review_at__gt=now).order_by("next_review_at").values_list("next_review_at", flat=True).first()

    active = {k for k, v in per_day.items() if v > 0}
    streak = 0
    day = now.astimezone(tz).date()
    if day.isoformat() not in active:  # today not done yet does not break yesterday's run
        day -= timedelta(days=1)
    while day.isoformat() in active:
        streak += 1
        day -= timedelta(days=1)

    return {
        **counts(user, now),
        "per_day": [{"date": k, "reviews": v} for k, v in per_day.items()],
        "retention_pct": retention,
        "mature": mature,
        "learning": learning,
        "next_due_at": nxt.isoformat() if nxt else None,
        "review_streak_days": streak,
    }


# ------------------------------------------------------------------------------------------------ batch (reminders)
def reminder_counts(user_ids: Iterable, now, min_new_age_days: int = 0) -> Dict[int, dict]:
    """{user_id: {"due": n, "new": n, "total": n}} for users who have at least one DUE or NEW card.
    `new` counts deck items with no card yet that were saved >= `min_new_age_days` ago, capped at SRS_NEW_PER_DAY."""
    from post.models import PostSave
    from post.study_tools import REVISION_COLLECTION

    user_ids = list(user_ids)
    out: Dict = {}
    due_rows = (
        RevisionCard.objects.filter(user_id__in=user_ids, next_review_at__lte=now).filter(_in_deck_exists())
        .values("user_id").annotate(n=Count("id"))
    )
    for r in due_rows:
        out.setdefault(r["user_id"], {"due": 0, "new": 0, "total": 0})["due"] = int(r["n"])
    cutoff = now - timedelta(days=max(0, int(min_new_age_days)))
    has_card = RevisionCard.objects.filter(user_id=OuterRef("user_id"), post_id=OuterRef("post_id"))
    new_rows = (
        PostSave.objects.filter(user_id__in=user_ids, collection_name=REVISION_COLLECTION, created_at__lte=cutoff,
                                post__is_deleted=False)
        .filter(~Exists(has_card)).values("user_id").annotate(n=Count("id"))
    )
    cap = conf.new_per_day()
    for r in new_rows:
        out.setdefault(r["user_id"], {"due": 0, "new": 0, "total": 0})["new"] = min(int(r["n"]), cap)
    for v in out.values():
        v["total"] = v["due"] + v["new"]
    return out


def users_with_cards() -> List[int]:
    """Users who ever started SRS - the reminder job uses this to decide who switches to the SRS cadence."""
    return list(RevisionCard.objects.values_list("user_id", flat=True).distinct())
