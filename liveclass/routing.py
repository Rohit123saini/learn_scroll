# liveclass/routing.py
"""
WebSocket URL patterns for this app — imported into the project-level
asgi.py's URLRouter (see ws_auth.py's docstring for the exact wiring).
Kept separate from urls.py (HTTP-only, wired into ROOT_URLCONF) since
Channels routes live in a completely different protocol router.
"""

from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r"^ws/liveclass/session/(?P<session_id>\d+)/$", consumers.SessionConsumer.as_asgi()),
    # Per-user realtime channel (Flutter Phase 1, items 2 & 3 / Phase 4
    # item 2): pairs with `UserConsumer` in consumers.py, now added
    # (realtime fix pass) — see realtime.py's broadcast_to_user()
    # docstring for what pushes through here. Naming mirrors the session
    # route above exactly.
    re_path(r"^ws/liveclass/user/$", consumers.UserConsumer.as_asgi()),
    # Per-CLASSROOM realtime channel (fix — classroom stats push): pairs
    # with `ClassroomConsumer` in consumers.py and `broadcast_to_classroom()`
    # in realtime.py — see models.py's `_broadcast_classroom_stats()`
    # docstring for what pushes through here (rating_avg/rating_count/
    # enrolled_count) and consumers.py's ClassroomConsumer docstring for why
    # this is public/auth-only rather than per-object-access-gated like
    # SessionConsumer. Naming mirrors the user route above exactly, just
    # keyed by classroom id — matches classroom_detail_screen.dart's
    # `LiveClassClassroomSocket`, which already targets this exact path.
    re_path(r"^ws/liveclass/classroom/(?P<classroom_id>\d+)/$", consumers.ClassroomConsumer.as_asgi()),
]