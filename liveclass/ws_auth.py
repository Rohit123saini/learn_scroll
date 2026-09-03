# liveclass/ws_auth.py
"""
Channels' built-in AuthMiddlewareStack authenticates off the Django
session cookie — this project authenticates the REST API with
djangorestframework-simplejwt (see settings.py DEFAULT_AUTHENTICATION_
CLASSES), and a mobile app (Flutter, per the audit doc) has no cookie jar
to send anyway. This middleware does the WebSocket-handshake equivalent
of JWTAuthentication: pull the token out of the connection, validate it,
attach the resolved user to `scope["user"]` — same contract every DRF
view already gets via `request.user`.

Token is read from the `?token=` query string, since a browser/mobile
WebSocket client cannot set a custom `Authorization` header on the
opening handshake the way an HTTP client can. This is the standard
pattern for JWT-over-WebSocket with Channels; the token is short-lived
(SIMPLE_JWT's ACCESS_TOKEN_LIFETIME) same as any other API call, and the
connection is over wss:// in production so it's not sent in the clear.

NOTE (fix — duplicate-middleware consolidation): this file used to carry
its own independent JWTAuthMiddleware implementation, separate from (and
never verified line-by-line against) message/Middleware.py's — the
`message` app is a fully independent chat system with no dependency on
`liveclass`, so having either app import the other's auth middleware is
the wrong direction. The actual logic now lives in the project-level
`LearnScroll/ws_auth.py` (alongside settings.py/asgi.py), a location
neutral to both apps — this file just re-exports it so every existing
`from liveclass.ws_auth import JWTAuthMiddleware` call (including the
asgi.py wiring below) keeps working unchanged.

Wire this into asgi.py (project-level, not part of this liveclass
upload — see settings.py's own note re: files outside its scope) as:

    from channels.routing import ProtocolTypeRouter, URLRouter
    from channels.security.websocket import AllowedHostsOriginValidator
    from django.core.asgi import get_asgi_application
    import django

    django.setup()  # must run before importing anything that touches models
    django_asgi_app = get_asgi_application()

    from liveclass.routing import websocket_urlpatterns
    from liveclass.ws_auth import JWTAuthMiddleware

    application = ProtocolTypeRouter({
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            JWTAuthMiddleware(URLRouter(websocket_urlpatterns))
        ),
    })
"""

from LearnScroll.ws_auth import JWTAuthMiddleware, get_user_from_token

__all__ = ["JWTAuthMiddleware", "get_user_from_token"]