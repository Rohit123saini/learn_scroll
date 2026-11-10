# LearnScroll/ws_auth.py
"""
Project-level (settings.py/asgi.py ke sath) shared JWT-over-WebSocket
auth middleware — Django Channels ke liye.

Django Channels ka default AuthMiddlewareStack sirf session/cookie auth
samajhta hai. Iss project (LearnScroll) ka REST_FRAMEWORK settings.py
sirf JWTAuthentication use karta hai — client (mobile app / SPA) cookie
nahi, JWT access-token bhejta hai. Isliye websocket connect hote hi
query-string se token nikal ke verify karna padta hai, warna
scope['user'] hamesha AnonymousUser rahega.

CLIENT connect karega (X6b -- JWT URL me nahi):
    1. POST /login/auth/ws-ticket/   (Authorization: Bearer <JWT>)   ->  {"ticket": "...", "expires_in": 30}
    2. wss://yourdomain.com/ws/<jo bhi path>/?ticket=<ticket>        (ek baar chalta hai, 30 s)

    P-B2: `?token=<JWT>` ab DEFAULT band hai (settings.WS_ALLOW_QUERY_TOKEN=false). Sirf `?ticket=` chalta hai.
    Purane app builds ke liye env me WS_ALLOW_QUERY_TOKEN=true se wapas khul sakta hai (release_check / check_security_settings
    production me use FAIL karte hain). Ticket ka kaam login/ws_ticket.py me hai.

Yeh project ki EK hi copy hai — `message` (chat/calls/study-rooms) aur
`tuitionclass` (classroom realtime) dono isi se apna WS auth lete hain.
Pehle dono apps ke paas apna-apna independent, kabhi-compare-na-hui copy
tha (message/Middleware.py vs tuitionclass/ws_auth.py) — ab dono sirf yahan
se import karte hain, taaki future me sirf ek jagah update karni pade
aur dono kabhi silently mismatch na ho.

Wire this into LearnScroll/asgi.py as:

    from channels.routing import ProtocolTypeRouter, URLRouter
    from channels.security.websocket import AllowedHostsOriginValidator
    from django.core.asgi import get_asgi_application
    import django

    django.setup()
    django_asgi_app = get_asgi_application()

    from tuitionclass.routing import websocket_urlpatterns as tuitionclass_ws
    from message.routing import websocket_urlpatterns as message_ws
    from LearnScroll.ws_auth import JWTAuthMiddleware

    application = ProtocolTypeRouter({
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            JWTAuthMiddleware(URLRouter(tuitionclass_ws + message_ws))
        ),
    })
"""
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken


def _active_user(user_id, session_jti):
    """Shared by the JWT and the ticket path: the user must be active and (when the login carries a session_jti)
    that session must still exist and not be expired. Anything else -> AnonymousUser."""
    from django.contrib.auth import get_user_model
    from login.models import AuthToken
    User = get_user_model()  # yahi AUTH_USER_MODEL = "login.User" resolve karega
    try:
        # L1 gaps 10-11: the REST side rejects an access token whose session was ended
        # (logout / "sign this device out" / password change) -- do the same here, or a
        # signed-out device could keep opening sockets until its short access token dies.
        # Read-only: a socket connect is not "activity", so no touch() (that stays REST-only).
        # Tokens without a session_jti claim are legacy and fall back to their own exp.
        if session_jti:
            session = AuthToken.objects.filter(jti=session_jti).first()
            if session is None or session.is_expired():
                return AnonymousUser()
        return User.objects.get(id=user_id, is_active=True)
    except (User.DoesNotExist, ValueError, TypeError):
        return AnonymousUser()


@database_sync_to_async
def get_user_from_token(token):
    """Validates the token and resolves it to a user, or AnonymousUser on
    any failure — bad signature, expired, malformed, user deleted, user
    deactivated. Every failure mode here is caught and degrades safely
    to AnonymousUser rather than raising, so a bad token can never crash
    the WebSocket connection — it just connects unauthenticated, same as
    no token at all."""
    try:
        validated_token = AccessToken(token)
        user_id = validated_token['user_id']
        return _active_user(user_id, validated_token.get('session_jti'))
    except (InvalidToken, TokenError, KeyError):
        return AnonymousUser()


@database_sync_to_async
def get_user_from_ticket(ticket):
    """X6b: redeem a one-time ticket (login/ws_ticket.py) -> the user it was issued to, or AnonymousUser.
    A ticket is single use, so a replayed / logged one is simply 'unknown'."""
    from login import ws_ticket
    redeemed = ws_ticket.redeem(ticket)
    if not redeemed:
        return AnonymousUser()
    user_id, session_jti = redeemed
    return _active_user(user_id, session_jti)


def _query_token_allowed():
    from django.conf import settings
    return bool(getattr(settings, "WS_ALLOW_QUERY_TOKEN", False))  # P-B2: fail closed if the setting is missing


class JWTAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        query_string = parse_qs(scope.get('query_string', b'').decode())
        ticket = query_string.get('ticket', [None])[0]
        token = query_string.get('token', [None])[0]

        scope['user'] = AnonymousUser()
        if ticket:
            # X6b: preferred - one-time ticket, the JWT never appears in the URL.
            scope['user'] = await get_user_from_ticket(ticket)
        elif token and _query_token_allowed():
            # Legacy `?token=<JWT>` (old app builds). OFF by default (P-B2); only reached with WS_ALLOW_QUERY_TOKEN=true.
            scope['user'] = await get_user_from_token(token)

        return await super().__call__(scope, receive, send)
