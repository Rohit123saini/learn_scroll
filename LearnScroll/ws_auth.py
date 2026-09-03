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

CLIENT connect karega:
    wss://yourdomain.com/ws/<jo bhi path>/?token=<JWT_ACCESS_TOKEN>

Yeh project ki EK hi copy hai — `message` (chat/calls/study-rooms) aur
`liveclass` (classroom realtime) dono isi se apna WS auth lete hain.
Pehle dono apps ke paas apna-apna independent, kabhi-compare-na-hui copy
tha (message/Middleware.py vs liveclass/ws_auth.py) — ab dono sirf yahan
se import karte hain, taaki future me sirf ek jagah update karni pade
aur dono kabhi silently mismatch na ho.

Wire this into LearnScroll/asgi.py as:

    from channels.routing import ProtocolTypeRouter, URLRouter
    from channels.security.websocket import AllowedHostsOriginValidator
    from django.core.asgi import get_asgi_application
    import django

    django.setup()
    django_asgi_app = get_asgi_application()

    from liveclass.routing import websocket_urlpatterns as liveclass_ws
    from message.routing import websocket_urlpatterns as message_ws
    from LearnScroll.ws_auth import JWTAuthMiddleware

    application = ProtocolTypeRouter({
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            JWTAuthMiddleware(URLRouter(liveclass_ws + message_ws))
        ),
    })
"""
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken


@database_sync_to_async
def get_user_from_token(token):
    """Validates the token and resolves it to a user, or AnonymousUser on
    any failure — bad signature, expired, malformed, user deleted, user
    deactivated. Every failure mode here is caught and degrades safely
    to AnonymousUser rather than raising, so a bad token can never crash
    the WebSocket connection — it just connects unauthenticated, same as
    no token at all."""
    from django.contrib.auth import get_user_model
    User = get_user_model()  # yahi AUTH_USER_MODEL = "login.User" resolve karega
    try:
        validated_token = AccessToken(token)
        user_id = validated_token['user_id']
        return User.objects.get(id=user_id, is_active=True)
    except (InvalidToken, TokenError, User.DoesNotExist, KeyError):
        return AnonymousUser()


class JWTAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        query_string = parse_qs(scope.get('query_string', b'').decode())
        token = query_string.get('token', [None])[0]

        scope['user'] = AnonymousUser()
        if token:
            scope['user'] = await get_user_from_token(token)

        return await super().__call__(scope, receive, send)