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
]