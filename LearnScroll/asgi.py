"""
ASGI config for LearnScroll project.

It exposes the ASGI callable as a module-level variable named ``application``.
"""

import os

import django
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'LearnScroll.settings')
django.setup()  # <-- models import se pehle zaroori hai

# 🔥 IMPORTANT: apna REST_FRAMEWORK sirf JWTAuthentication use karta hai
# (settings.py me), koi SessionAuthentication nahi. Isliye websocket pe
# `channels.auth.AuthMiddlewareStack` kaam NAHI karega — wo sirf Django
# session-cookie padhta hai, aur tera mobile/frontend client cookie nahi,
# JWT access-token bhejta hai. AuthMiddlewareStack rakhne par scope['user']
# hamesha AnonymousUser milega aur consumer connect hi reject kar dega
# (jaisa humne likha tha `is_authenticated` check).
#
# 🔧 FIX (this pass, LearnScroll_project_documentation.md §7.3) — this
# file previously imported `message.Middleware.JWTAuthMiddleware`
# directly. That's the exact "two independent copies that can drift
# apart" problem `LearnScroll/ws_auth.py`'s own docstring already
# describes and was written to solve: it's the ONE shared copy both
# `message` and `liveclass` are meant to import their WS auth from, so
# there's a single implementation instead of two (or three, counting
# this file's previous choice of `message`'s copy over `liveclass`'s)
# that can go out of sync on a future change (token param name, error
# handling, etc.). It existed in the repo but nothing actually wired it
# into the app — a real dead-code risk exactly as flagged. Now imported
# and used below instead of reaching into `message.Middleware` directly.
from LearnScroll.ws_auth import JWTAuthMiddleware  # noqa: E402  (django.setup() ke baad import zaroori)
from message import routing as message_routing  # noqa: E402

# NOTE (fix — CRITICAL, production-breaking): this file only ever wired
# `message.routing.websocket_urlpatterns` into the URLRouter. liveclass
# has its OWN Channels layer — `liveclass/routing.py` registers
# `ws/liveclass/session/<id>/` -> `consumers.SessionConsumer` — built
# specifically for live-session chat, raise-hand, polls, and presence
# (see liveclass/consumers.py, realtime.py). That routing module was
# never imported here, so the URLRouter had no matching route for it:
# every liveclass WebSocket connection attempt would fail to resolve
# (Channels closes the connection — no route matches the path)
# regardless of how correct consumers.py's own logic is. All of that
# real-time functionality was unreachable in production. Fixed by
# importing liveclass's routing module and concatenating its
# `websocket_urlpatterns` with message's, same as any other Channels
# project mounting multiple apps' routes onto one URLRouter.
#
# Middleware: a single URLRouter can only sit behind one middleware
# instance, so both apps' routes are combined under the ONE shared
# `LearnScroll.ws_auth.JWTAuthMiddleware` above rather than either
# app's own (now-redundant) local copy.
#
# ⚠️ STILL OPEN, FLAGGED NOT DONE HERE: `message/Middleware.py` and
# `liveclass/ws_auth.py` themselves weren't part of this pass's upload,
# so their now-unused duplicate `JWTAuthMiddleware` classes haven't been
# deleted — this fix only stops `asgi.py` from reaching into either of
# them. They're genuinely dead code now (nothing imports them from this
# file anymore) and should be removed in a follow-up pass once those
# two files are actually available to edit, rather than guessed at
# blind here.
from liveclass import routing as liveclass_routing  # noqa: E402

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AllowedHostsOriginValidator(
        JWTAuthMiddleware(
            URLRouter(
                message_routing.websocket_urlpatterns
                + liveclass_routing.websocket_urlpatterns
            )
        )
    ),
})