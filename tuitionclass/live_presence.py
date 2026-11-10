"""
tuitionclass/live_presence.py

LV1 — "Live aaye to story ring me dikhe". One place that defines what
"a session is live RIGHT NOW" means for the story tray, the home live-now row
and the stale sweeper, so they can never disagree.

A session is live-now when
    status == LIVE   AND   live_heartbeat_at is newer than the TTL.

The host's app pings ClassSessionViewSet.heartbeat() every ~20s (and /join/
+ /token/ stamp it too). If the host's app dies / loses network, the ring
disappears on its own once the TTL passes; tasks.sweep_stale_live_sessions
then completes the session if it stays silent for LIVE_STALE_COMPLETE_MINUTES.
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

# Heartbeat every ~20s from the client; 90s = 4 missed beats before the ring drops.
LIVE_HEARTBEAT_TTL_SECONDS = int(getattr(settings, "LIVE_HEARTBEAT_TTL_SECONDS", 90))
# A heartbeat-silent LIVE session is force-completed after this long (sweep task).
LIVE_STALE_COMPLETE_MINUTES = int(getattr(settings, "LIVE_STALE_COMPLETE_MINUTES", 10))


def fresh_cutoff():
    return timezone.now() - timedelta(seconds=LIVE_HEARTBEAT_TTL_SECONDS)


def touch_heartbeat(session_id) -> int:
    """Stamp the heartbeat on a LIVE session. Uses .update() on purpose: no
    post_save signal (signals.py reacts to status changes, not to this)."""
    from .models import ClassSession

    return ClassSession.objects.filter(
        pk=session_id, status=ClassSession.Status.LIVE
    ).update(live_heartbeat_at=timezone.now())
