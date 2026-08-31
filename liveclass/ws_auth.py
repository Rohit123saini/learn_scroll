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

import logging
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

logger = logging.getLogger(__name__)

_jwt_auth = JWTAuthentication()


@database_sync_to_async
def _user_from_token(raw_token: str):
    """Runs JWTAuthentication's own validated_token()/get_user() — the
    exact same code path a normal DRF request goes through — so a
    WebSocket connection can never end up trusting a token the REST API
    would have rejected (expired, blacklisted, wrong signature, etc.)."""
    try:
        validated_token = _jwt_auth.get_validated_token(raw_token)
        return _jwt_auth.get_user(validated_token)
    except (InvalidToken, TokenError) as exc:
        logger.info("WebSocket connect rejected — invalid token: %s", exc)
        return None


class JWTAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        query_string = scope.get("query_string", b"").decode("utf-8")
        token = parse_qs(query_string).get("token", [None])[0]

        scope["user"] = AnonymousUser()
        if token:
            user = await _user_from_token(token)
            if user is not None:
                scope["user"] = user

        return await super().__call__(scope, receive, send)