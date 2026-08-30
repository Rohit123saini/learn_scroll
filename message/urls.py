# message/middleware.py
"""
Django Channels ka default AuthMiddlewareStack sirf session/cookie auth
samajhta hai. Tera REST_FRAMEWORK settings.py me sirf JWTAuthentication use
karta hai, matlab client (mobile app / SPA) cookie nahi, JWT access-token
bhejta hai. Isliye websocket connect hote hi query-string se token nikal ke
verify karna padta hai, warna scope['user'] hamesha AnonymousUser rahega.

CLIENT connect karega:
    wss://yourdomain.com/ws/chat/<conversation_id>/?token=<JWT_ACCESS_TOKEN>
"""
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken


@database_sync_to_async
def get_user_from_token(token):
    from django.contrib.auth import get_user_model
    User = get_user_model()  # yahi tera AUTH_USER_MODEL = "login.User" resolve karega
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