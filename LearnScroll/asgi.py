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
# Isliye custom JWTAuthMiddleware use kar rahe hain jo query-string se
# ?token=<jwt> padh ke user verify karta hai.
from message.Middleware import JWTAuthMiddleware  # noqa: E402  (django.setup() ke baad import zaroori)
from message import routing as message_routing  # noqa: E402

# NOTE (fix — CRITICAL, production-breaking): this file only ever wired
# `message.routing.websocket_urlpatterns` into the URLRouter. liveclass
# has its OWN Channels layer — `liveclass/routing.py` registers
# `ws/liveclass/session/<id>/` -> `consumers.SessionConsumer` — built
# specifically for live-session chat, raise-hand, polls, and presence
# (see liveclass/consumers.py, realtime.py, ws_auth.py). That routing
# module was never imported here, so the URLRouter had no matching route
# for it: every liveclass WebSocket connection attempt would fail to
# resolve (Channels closes the connection — no route matches the path)
# regardless of how correct consumers.py/ws_auth.py's own logic is. All
# of that real-time functionality was unreachable in production. Fixed
# by importing liveclass's routing module and concatenating its
# `websocket_urlpatterns` with message's, same as any other Channels
# project mounting multiple apps' routes onto one URLRouter.
#
# Middleware: liveclass ships its own `liveclass.ws_auth.JWTAuthMiddleware`
# (same contract as `message.Middleware.JWTAuthMiddleware` — reads
# `?token=<jwt>` from the query string, validates it through
# rest_framework_simplejwt's own JWTAuthentication, sets scope["user"]).
# A single URLRouter can only sit behind one middleware instance, so
# rather than nesting two independent JWT implementations (which would
# only invite the two to drift apart over time), both apps' routes are
# combined under the ALREADY-PROVEN-IN-PRODUCTION `message.Middleware.
# JWTAuthMiddleware` below. TODO: since the two middlewares are meant to
# do the exact same thing, consider deleting `liveclass/ws_auth.py`'s
# copy and having liveclass import `message.Middleware.JWTAuthMiddleware`
# directly, so there's one implementation instead of two that can go out
# of sync on a future change (token param name, error handling, etc.).
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