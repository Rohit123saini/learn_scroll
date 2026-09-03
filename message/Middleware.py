# message/middleware.py
"""
Django Channels ka default AuthMiddlewareStack sirf session/cookie auth
samajhta hai. Tera REST_FRAMEWORK settings.py me sirf JWTAuthentication use
karta hai, matlab client (mobile app / SPA) cookie nahi, JWT access-token
bhejta hai. Isliye websocket connect hote hi query-string se token nikal ke
verify karna padta hai, warna scope['user'] hamesha AnonymousUser rahega.

CLIENT connect karega:
    wss://yourdomain.com/ws/chat/<conversation_id>/?token=<JWT_ACCESS_TOKEN>

NOTE (fix — duplicate-middleware consolidation): yeh file pehle apna
independent JWTAuthMiddleware carry karti thi — liveclass app me bhi
functionally almost-identical ek copy thi (liveclass/ws_auth.py), dono
kabhi line-by-line compare nahi hui thi. `message` yahan ek fully
independent chat app hai (apna poora models/views/consumers — liveclass
se koi dependency nahi), isliye `message` ko `liveclass` pe (ya
vice-versa) depend karana galat direction hoti — is file ka ASLI, tested
logic (AccessToken + manual is_active-checked query) ab
project-level `LearnScroll/ws_auth.py` me move ho gaya hai (jahan
settings.py/asgi.py bhi hain) — dono apps se neutral shared location.
Dono apps (message aur liveclass) ab wahi se import karte hain — koi ek
doosre pe depend nahi karta, aur future me sirf ek jagah update karni
padegi.
"""
from LearnScroll.ws_auth import JWTAuthMiddleware, get_user_from_token

__all__ = ["JWTAuthMiddleware", "get_user_from_token"]