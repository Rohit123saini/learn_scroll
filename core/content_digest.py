"""
core/content_digest.py - H18: the STUDY DIGEST ("Physics me 5 naye notes", "tumhare followed topic par 3 posts").

One bell row + one push per user per period (daily / weekly), instead of a buzz for every new note / post.

WHAT IS IN IT (both sections optional per user, never sent empty):
  * notes   for each subject the student studies (UserPreference.focus_subjects, set in onboarding / study
            profile): number of NEW public Notes / Resource posts (Post.kind = resource) with that subject
            (`subcategory` or hashtag - same matching as the Home subject chips, post/feed_chips.py).
  * topics  for each topic the student follows (post.TopicFollow, H13): number of NEW public posts carrying
            that hashtag.
  Posts by the user themself, blocked / muted accounts, hidden posts, private / minor / inactive authors,
  sensitive and unapproved posts are never counted (same "safe to show" rules as the feeds).

USER CONTROL  (NotificationPreference, GET/PATCH /core/notification-preferences/me/):
  content_digest_frequency  off | daily | weekly (default weekly)       content_digest_hour  0-23 local hour
  content_digest_notes / content_digest_topics   switch a section off   muted_types has "content_digest" => nothing
  timezone: the user's IANA zone (the app sends it); the untouched default "UTC" means "server time zone".

QUIET HOURS: the bell row is written at the chosen hour; the PUSH is only sent outside the user's quiet window /
DND / with push on and the type not muted. Inside the window the row stays `push_state=pending` and the next
hourly run sends it when the window ends - but only for PENDING_MAX_AGE (12 h), then it is marked `expired` (an
old digest is noise). Calls / emergencies are unaffected (they have their own bypass list).

IDEMPOTENT: `last_content_digest_at` + `content_digest_checked_on` (local date) make a re-run in the same local
day a no-op, so the hourly beat entry can overlap or repeat safely. An EMPTY digest only sets
`content_digest_checked_on` - `since` stays where it was, so what was missed yesterday still shows tomorrow.
"""
import logging
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

DEFAULTS = {
    "enabled": True,
    "weekly_weekday": 6,          # Monday=0 ... Sunday=6 (user's local weekday)
    "max_lookback_days": 14,      # never summarise further back than this
    "max_items_per_section": 3,
    "max_subjects": 6,            # subjects considered per user
    "max_topics": 12,             # followed topics considered per user
    "pending_max_age_hours": 12,
    "batch_size": 500,
}


def get_config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(getattr(settings, "CONTENT_DIGEST", None) or {})
    return cfg


# ------------------------------------------------------------------------------------------- time helpers
def user_tz(pref):
    """The user's zone; the untouched default 'UTC' (nobody ever sent a zone) -> the server time zone."""
    name = (getattr(pref, "timezone", "") or "").strip()
    if name and name.upper() != "UTC":
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            pass
    return timezone.get_current_timezone()


def _interval(frequency) -> timedelta:
    return timedelta(days=7) if frequency == "weekly" else timedelta(days=1)


def is_due(pref, now) -> bool:
    """Is it time to LOOK for this user's digest (not yet whether there is anything in it)?"""
    freq = pref.content_digest_frequency
    if freq not in ("daily", "weekly"):
        return False
    local = now.astimezone(user_tz(pref))
    if local.hour < int(pref.content_digest_hour):
        return False
    if pref.content_digest_checked_on == local.date():
        return False
    if freq == "weekly":
        if local.weekday() != int(get_config()["weekly_weekday"]):
            return False
    last = pref.last_content_digest_at
    if last is not None:
        # daily: not twice in 20 h; weekly: not twice in 6 d 20 h (lets the chosen hour drift by a few hours)
        if now - last < _interval(freq) - timedelta(hours=4):
            return False
    return True


def window_start(pref, now):
    cfg = get_config()
    floor = now - timedelta(days=int(cfg["max_lookback_days"]))
    since = pref.last_content_digest_at or (now - _interval(pref.content_digest_frequency))
    return max(since, floor)


# ------------------------------------------------------------------------------------------- content
def _safe_posts(user, since):
    """New posts a stranger may see, for `user`. Notes are filtered separately by kind."""
    from login.age import minor_cutoff_date
    from post.models import Post
    from post.services import exclude_hidden_and_muted

    qs = (
        Post.objects.filter(
            is_deleted=False, is_archived=False, moderation_status="approved", is_sensitive=False,
            visibility="public", is_scheduled=False, user__is_active=True, user__is_private=False,
            created_at__gt=since,
        )
        .exclude(post_type="repost")
        .exclude(user=user)
        .exclude(user__date_of_birth__gt=minor_cutoff_date())
    )
    return exclude_hidden_and_muted(qs, user)


def _study_subjects(user):
    from post import feed_chips
    from user_profile.models import UserPreference

    pref = UserPreference.objects.filter(user=user).only("focus_subjects").first()
    out = []
    for raw in (pref.focus_subjects if pref else None) or []:
        s = " ".join(str(raw).lower().split())
        s = feed_chips._ALIAS_TO_CANONICAL.get(s, s)
        if s and s not in out:
            out.append(s)
    return out[: int(get_config()["max_subjects"])]


def _followed_topics(user):
    from post.models import TopicFollow

    return list(
        TopicFollow.objects.filter(user=user).order_by("-created_at").values_list("topic", flat=True)[
            : int(get_config()["max_topics"])
        ]
    )


def build_items(user, pref, since) -> list:
    """[{"kind": "notes", "subject": "physics", "count": 5}, {"kind": "topic", "topic": "kinematics", "count": 3}]
    biggest first inside each section, at most `max_items_per_section` each, notes before topics."""
    from post import feed_chips, feed_mix
    from post.models import Post

    cfg = get_config()
    cap = int(cfg["max_items_per_section"])
    items = []

    if pref.content_digest_notes:
        notes_qs = _safe_posts(user, since).filter(kind=Post.KIND_RESOURCE)
        rows = []
        for subject in _study_subjects(user):
            n = feed_chips.apply(notes_qs, feed_chips.FeedFilter(subject=subject)).count()
            if n:
                rows.append({"kind": "notes", "subject": subject, "count": n})
        items += sorted(rows, key=lambda r: -r["count"])[:cap]

    if pref.content_digest_topics:
        posts_qs = _safe_posts(user, since)
        rows = []
        for topic in _followed_topics(user):
            n = posts_qs.filter(feed_mix.hashtag_match_q(topic)).count()
            if n:
                rows.append({"kind": "topic", "topic": topic, "count": n})
        items += sorted(rows, key=lambda r: -r["count"])[:cap]
    return items


# ------------------------------------------------------------------------------------------- wording
_EN = {
    "title": "Your study digest",
    "notes": lambda i: f"{i['subject'].title()}: {i['count']} new note{'s' if i['count'] != 1 else ''}",
    "topic": lambda i: f"#{i['topic']}: {i['count']} new post{'s' if i['count'] != 1 else ''}",
}
_HI = {
    "title": "आपका स्टडी डाइजेस्ट",
    "notes": lambda i: f"{i['subject'].title()} में {i['count']} नए नोट्स",
    "topic": lambda i: f"#{i['topic']} पर {i['count']} नई पोस्ट",
}


def _language(user) -> str:
    try:
        from user_profile.models import UserPreference

        lang = UserPreference.objects.filter(user=user).values_list("language", flat=True).first()
        return "hi" if (lang or "").lower().startswith("hi") else "en"
    except Exception:  # noqa: BLE001
        return "en"


def compose(items, language="en"):
    """-> (title, message). Message joins every item with ' · ' ("Physics: 5 new notes · #kinematics: 3 new posts")."""
    words = _HI if language == "hi" else _EN
    return words["title"], " · ".join(words[i["kind"]](i) for i in items)


# ------------------------------------------------------------------------------------------- one user
def run_for_pref(pref, now):
    """Look for / create ONE user's digest. -> "empty" | "created" | "skipped". Never raises."""
    from .models import Notification
    from .services import create_notification

    try:
        user = pref.user
        local_date = now.astimezone(user_tz(pref)).date()
        ntype = Notification.NotifType.CONTENT_DIGEST
        if (ntype in (pref.muted_types or []) or not pref.in_app_allowed(ntype)
                or not (pref.content_digest_notes or pref.content_digest_topics)):
            pref.content_digest_checked_on = local_date
            pref.save(update_fields=["content_digest_checked_on"])
            return "skipped"

        since = window_start(pref, now)
        items = build_items(user, pref, since)
        if not items:
            pref.content_digest_checked_on = local_date  # nothing new: do not look again today
            pref.save(update_fields=["content_digest_checked_on"])
            return "empty"

        title, message = compose(items, _language(user))
        row = create_notification(
            user, ntype, title, message,
            data={
                "type": "content_digest",
                "period": pref.content_digest_frequency,
                "since": since.isoformat(),
                "items": items,
                "push_state": "pending",
            },
        )
        if row is None:
            # create_notification swallowed an error: do not retry every hour all day, try again tomorrow
            pref.content_digest_checked_on = local_date
            pref.save(update_fields=["content_digest_checked_on"])
            return "skipped"
        pref.last_content_digest_at = now
        pref.content_digest_checked_on = local_date
        pref.save(update_fields=["last_content_digest_at", "content_digest_checked_on"])
        push_pending_row(row, pref, now)
        return "created"
    except Exception:  # noqa: BLE001
        logger.exception("content_digest.run_for_pref failed (pref=%s)", getattr(pref, "pk", None))
        return "skipped"


def push_pending_row(row, pref, now) -> str:
    """pending -> sent | skipped (push off / muted) | stays pending (quiet hours / DND). Returns the new state."""
    from .tasks import _push_state_for, push_for_notification_row

    state = _push_state_for(pref, row.notif_type, now)
    if state == "defer":
        return "pending"
    if state == "send":
        push_for_notification_row(row)
        new_state = "sent"
    else:
        new_state = "skipped"
    row.data = {**(row.data or {}), "push_state": new_state}
    row.save(update_fields=["data"])
    return new_state


def flush_pending(now) -> dict:
    """Retry the push of digests that were held back by quiet hours; expire the ones that waited too long."""
    from .models import Notification, NotificationPreference

    out = {"sent": 0, "still_pending": 0, "expired": 0, "skipped": 0}
    max_age = timedelta(hours=int(get_config()["pending_max_age_hours"]))
    rows = list(Notification.objects.filter(
        notif_type=Notification.NotifType.CONTENT_DIGEST, created_at__gte=now - timedelta(days=2),
    ))
    rows = [r for r in rows if (r.data or {}).get("push_state") == "pending"]
    prefs = {p.user_id: p for p in NotificationPreference.objects.filter(user_id__in=[r.recipient_id for r in rows])}
    for row in rows:
        try:
            if now - row.created_at > max_age:
                row.data = {**(row.data or {}), "push_state": "expired"}
                row.save(update_fields=["data"])
                out["expired"] += 1
                continue
            state = push_pending_row(row, prefs.get(row.recipient_id), now)
            out["sent" if state == "sent" else "still_pending" if state == "pending" else "skipped"] += 1
        except Exception:  # noqa: BLE001
            logger.exception("content_digest.flush_pending failed (row=%s)", row.pk)
    return out


# ------------------------------------------------------------------------------------------- the run
def ensure_prefs(limit=1000) -> int:
    """NotificationPreference rows are created lazily (first time something needs them), so a student who follows a
    topic but never opened the settings has none yet - and would never get a digest. Create the missing rows (model
    defaults: weekly digest, push on) for users that have something to digest. At most `limit` per run."""
    from django.contrib.auth import get_user_model

    from user_profile.models import UserPreference

    from .models import NotificationPreference

    try:
        User = get_user_model()
        have = NotificationPreference.objects.values("user_id")
        ids = set(
            User.objects.filter(is_active=True, followed_topics__isnull=False).exclude(pk__in=have)
            .values_list("pk", flat=True).distinct()[:limit]
        )
        room = limit - len(ids)
        if room > 0:
            ids |= set(
                UserPreference.objects.exclude(focus_subjects=[]).filter(user__is_active=True)
                .exclude(user_id__in=have).values_list("user_id", flat=True)[:room]
            )
        NotificationPreference.objects.bulk_create(
            [NotificationPreference(user_id=uid) for uid in ids], ignore_conflicts=True,
        )
        return len(ids)
    except Exception:  # noqa: BLE001
        logger.exception("content_digest.ensure_prefs failed")
        return 0


def run(now=None) -> dict:
    """Hourly: push held-back digests, then create the digests that are due. Safe to run any number of times."""
    from .models import NotificationPreference

    cfg = get_config()
    now = now or timezone.now()
    summary = {"checked": 0, "created": 0, "empty": 0, "skipped": 0}
    if not cfg["enabled"]:
        return {**summary, "disabled": True}
    summary["flush"] = flush_pending(now)
    summary["prefs_created"] = ensure_prefs()
    qs = (
        NotificationPreference.objects.filter(
            content_digest_frequency__in=["daily", "weekly"], user__is_active=True,
        ).select_related("user")
    )
    for pref in qs.iterator(chunk_size=int(cfg["batch_size"])):
        if not is_due(pref, now):
            continue
        summary["checked"] += 1
        summary[run_for_pref(pref, now)] += 1
    return summary
