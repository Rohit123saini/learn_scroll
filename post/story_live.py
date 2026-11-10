"""
post/story_live.py

LV1 — "Live aaye to story ring me dikhe".

Single source of truth for "who is live right now, and may THIS viewer see it".
Used by:
  * StorySerializer.live          -> every story item carries `live` (or null)
  * StoryLiveAPIView              -> GET /post/stories/live/ (live users who have
                                     no active story still need a ring)

Shape of one live entry (same everywhere):
    {"session_id": int, "classroom_id": int, "title": str,
     "started_at": ISO-8601 | None, "viewer_count": int, "can_join": bool}

Who is "live": the classroom TEACHER of a ClassSession that is status=LIVE AND
has a fresh host heartbeat (tuitionclass/live_presence.py) — so the moment the
teacher ends the class (status != LIVE) or their app goes silent (heartbeat
older than the TTL) the entry disappears, and with it the ring/badge.

Visibility (decided per viewer):
  * the teacher themself, class members (teacher/staff/active pass)  -> can_join = True
  * accepted followers of the teacher                                 -> visible, can_join = False
    (the client sends them to the classroom page: pass / trial flow)
  * anyone else -> not visible. Blocked (either direction) and muted
    teachers are hidden, same as the story tray itself.
`can_join` is a hint only; POST sessions/<id>/join/ stays the real gate.

Cost: the platform-wide list of live sessions is tiny, so it is built once and
cached for LIVE_SNAPSHOT_SECONDS (2s); per viewer there are 4-5 small indexed
queries. The home screen polls every ~5s.
"""
import logging

from django.core.cache import cache
from django.db.models import Count, Q

from .models import MutedAccount

logger = logging.getLogger(__name__)

LIVE_SNAPSHOT_KEY = "post:story_live:snapshot:v1"
LIVE_SNAPSHOT_SECONDS = 2
LIVE_SNAPSHOT_MAX_ROWS = 200


def _thumb(session):
    try:
        return session.live_thumbnail.url if session.live_standalone and session.live_thumbnail else ""
    except Exception:
        return ""


def _live_snapshot():
    """All fresh live sessions on the platform (newest first), as plain dicts."""
    rows = cache.get(LIVE_SNAPSHOT_KEY)
    if rows is not None:
        return rows

    from tuitionclass.live_presence import fresh_cutoff
    from tuitionclass.models import ClassSession, SessionParticipant

    qs = (
        ClassSession.objects.filter(
            status=ClassSession.Status.LIVE,
            live_heartbeat_at__gte=fresh_cutoff(),
            classroom__is_active=True,
            classroom__is_deleted=False,
        )
        .select_related("classroom")
        .annotate(
            viewer_count=Count(
                "participants",
                filter=Q(
                    participants__left_at__isnull=True,
                    participants__role=SessionParticipant.Role.STUDENT,
                ),
            )
        )
        .order_by("-actual_start", "-id")[:LIVE_SNAPSHOT_MAX_ROWS]
    )
    rows = []
    for s in qs:
        started = s.actual_start or s.scheduled_start
        rows.append({
            "session_id": s.id,
            "classroom_id": s.classroom_id,
            "teacher_id": s.classroom.teacher_id,
            # X12 L-C: a standalone live carries its own title / audience / thumbnail
            "title": (s.live_title or "").strip() or s.classroom.title,
            "started_at": started.isoformat() if started else None,
            "viewer_count": int(s.viewer_count or 0),
            "standalone": bool(s.live_standalone),
            "audience": s.live_audience if s.live_standalone else "",
            "thumbnail": _thumb(s),
        })
    cache.set(LIVE_SNAPSHOT_KEY, rows, LIVE_SNAPSHOT_SECONDS)
    return rows


def live_map_for(viewer):
    """{teacher_user_id: live-dict} for everything `viewer` may see right now.
    Never raises (a live-lookup problem must not break the story tray)."""
    try:
        rows = _live_snapshot()
        if not rows or viewer is None or not getattr(viewer, "is_authenticated", False):
            return {}

        from tuitionclass.views.access import _accessible_classroom_ids
        from user_profile.models import Follow
        from user_profile.services import blocked_user_ids

        teacher_ids = {r["teacher_id"] for r in rows if r["teacher_id"] is not None}
        following = set(
            Follow.objects.filter(
                follower=viewer, status=Follow.Status.ACCEPTED, following_id__in=teacher_ids
            ).values_list("following_id", flat=True)
        )
        member_classrooms = _accessible_classroom_ids(viewer)
        from .minor_privacy import minor_ids_among
        minor_hosts = minor_ids_among(teacher_ids)  # H23: a minor's live is for their class only
        hidden = set(blocked_user_ids(viewer))
        hidden |= set(
            MutedAccount.objects.filter(user=viewer, mute_stories=True, muted_user_id__in=teacher_ids)
            .values_list("muted_user_id", flat=True)
        )

        out = {}
        for r in rows:  # newest first -> a teacher's newest visible session wins
            tid = r["teacher_id"]
            if tid is None or tid in out or (tid in hidden and tid != viewer.id):
                continue
            is_member = tid == viewer.id or r["classroom_id"] in member_classrooms
            # X12 L-C: a standalone live of a public / followers audience needs no pass - the viewer may join it.
            open_audience = r.get("audience") if r.get("standalone") else ""
            open_join = (
                (open_audience == "public" or (open_audience == "followers" and tid in following))
                and tid not in minor_hosts
            )
            if not (is_member or tid in following or open_join):
                continue
            if tid in minor_hosts and not is_member:
                continue
            if r.get("standalone") and open_audience in ("class", "pass") and not is_member:
                continue  # members-only standalone live: a plain follower does not even see a ring
            out[tid] = {
                "session_id": r["session_id"],
                "classroom_id": r["classroom_id"],
                "title": r["title"],
                "started_at": r["started_at"],
                "viewer_count": r["viewer_count"],
                "can_join": bool(is_member or open_join),
                "standalone": bool(r.get("standalone")),
                "audience": r.get("audience", ""),
                "thumbnail": r.get("thumbnail", ""),
            }
        return out
    except Exception:
        logger.exception("story_live.live_map_for failed (viewer=%s)", getattr(viewer, "id", None))
        return {}


# ---------------------------------------------------------------------------------------------------
# Live push (replaces the 5s REST poll on Home): a payload-free SIGNAL on the shared socket group
# below. Every connected InboxConsumer joins it (message/consumers.py); the app answers with ONE
# GET /post/stories/live/ so per-viewer visibility (followers / class members / blocks / mutes)
# stays decided server-side - the signal itself carries no data and leaks nothing.
# ---------------------------------------------------------------------------------------------------
LIVE_STORIES_GROUP = "live_stories"
LIVE_SIGNAL_DEBOUNCE_SECONDS = 2


def publish_live_stories_changed():
    """A class went LIVE / ended / was cancelled -> tell every connected client to refetch the live
    list. Sent after commit, debounced (one signal per 2s however many sessions flip), and never
    raises (no channel layer / Redis down = clients still have their slow fallback poll)."""
    def _send():
        try:
            if not cache.add("post:story_live:signal:v1", 1, LIVE_SIGNAL_DEBOUNCE_SECONDS):
                return  # one was sent a moment ago; clients refetch the freshest state anyway
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer

            layer = get_channel_layer()
            if layer is None:
                return
            async_to_sync(layer.group_send)(LIVE_STORIES_GROUP, {"type": "live_stories_changed"})
        except Exception:
            logger.exception("publish_live_stories_changed failed")

    try:
        from django.db import transaction

        transaction.on_commit(_send)
    except Exception:  # pragma: no cover
        logger.exception("publish_live_stories_changed could not schedule")
