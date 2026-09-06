# message/throttles.py
#
# 🔥 NAYI FILE — Poori app me pehle sirf `AiStudyThrottle` (views_ai.py) tha.
# Message send, call-initiate, aur group-create — teeno unprotected the,
# jo spam/abuse aur FCM-cost blowup ka sabse aasaan raasta hai (ek compromised
# token, ek tight loop, aur poore users ka inbox spam ho jaata).
#
# DRF `UserRateThrottle` subclasses use karte hain (per-authenticated-user,
# cache-backed — same pattern jo `AiStudyThrottle` already follow karta tha),
# taaki style consistent rahe.
#
# ---------------------------------------------------------------
# SETUP (views.py me):
#
#   from .throttles import (
#       MessageSendThrottle, CallInitiateThrottle, GroupCreateThrottle,
#       ReactionThrottle,
#   )
#
#   class ConversationViewSet(...):
#       def get_throttles(self):
#           if self.action == 'messages' and self.request.method == 'POST':
#               return [MessageSendThrottle()]
#           return super().get_throttles()
#
#   class CallInitiateView(APIView):
#       throttle_classes = [CallInitiateThrottle]
#
#   class GroupViewSet(...):
#       def get_throttles(self):
#           if self.action == 'create':
#               return [GroupCreateThrottle()]
#           return super().get_throttles()
#
#   class MessageViewSet(...):
#       def get_throttles(self):
#           if self.action == 'react':
#               return [ReactionThrottle()]
#           return super().get_throttles()
#
# settings.py me rate override karna ho to (optional):
#   REST_FRAMEWORK = {
#       ...
#       "DEFAULT_THROTTLE_RATES": {
#           "message_send": "60/min",
#           "call_initiate": "10/min",
#           "group_create": "5/min",
#           "reaction": "120/min",
#           "ai_study": "20/min",
#       }
#   }
# ---------------------------------------------------------------

from rest_framework.throttling import UserRateThrottle


class MessageSendThrottle(UserRateThrottle):
    """
    REST message-send abuse guard. WS path (`ChatConsumer.handle_new_message`)
    should get an equivalent per-connection guard — see `WSMessageRateLimiter`
    below, since DRF throttles only apply to REST views.
    """
    rate = '60/min'
    scope = 'message_send'


class CallInitiateThrottle(UserRateThrottle):
    """
    Har call ek FCM push + LiveKit room bhi banata hai — ye dono cheezein
    normal message se zyada "costly" hain, isliye tighter limit.
    """
    rate = '10/min'
    scope = 'call_initiate'


class GroupCreateThrottle(UserRateThrottle):
    """Group creation spam (mass-group-creation bots) rokne ke liye."""
    rate = '5/min'
    scope = 'group_create'


class ReactionThrottle(UserRateThrottle):
    """Reaction-spam (rapid emoji toggling) ke liye — generous but bounded."""
    rate = '120/min'
    scope = 'reaction'


class TranslateThrottle(UserRateThrottle):
    """
    🔥 NAYA — Feature 9: message translate. External translation API
    (Google Cloud Translate / etc.) har call pe cost karti hai, isliye
    normal message-send se tighter — 30/min ek user ko kaafi hai, chahe
    wo poori chat scroll karke translate spam bhi kare.
    """
    rate = '30/min'
    scope = 'translate'


# ---------------------------------------------------------------
# WEBSOCKET SIDE — DRF throttles don't apply to Channels consumers.
# Ye ek chhota, dependency-free sliding-window limiter hai jo Django cache
# (same backend jo `ai_service.py` already use karta hai) use karta hai,
# taaki REST aur WS dono paths par same kind of protection ho.
# ---------------------------------------------------------------

from django.core.cache import cache


# ---------------------------------------------------------------
# 🔥 NAYA — per-IP throttle. Upar wale sab throttles `UserRateThrottle`
# hain (per-AUTHENTICATED-user). Ye kaafi hai normal abuse ke liye, par
# agar attacker ke paas multiple accounts/tokens hain (fake signups, leaked
# tokens, ya ek compromised device se multiple logins) to per-user limit
# bypass ho jaata hai — IP wahi rehta hai. Ye ek extra safety-net layer
# hai, per-user throttle ko REPLACE nahi karta — dono ek saath lagao
# (`get_throttles` me list me dono add kar do, DRF sabko check karta hai).
# ---------------------------------------------------------------
from rest_framework.throttling import SimpleRateThrottle


class ScopedIPThrottle(SimpleRateThrottle):
    """
    Generic per-IP throttle jisme `scope` request ke hisaab se set karo.
    Usage:
        class MessageSendIPThrottle(ScopedIPThrottle):
            scope = 'message_send_ip'
            rate = '120/min'
    `X-Forwarded-For` ko respect karta hai (load balancer/nginx ke peeche
    real client IP ke liye) — agar nginx `X-Forwarded-For` set nahi karta
    to `REMOTE_ADDR` pe fallback hota hai (DRF ka default `get_ident`).
    """
    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class MessageSendIPThrottle(ScopedIPThrottle):
    """REST message-send ke liye IP-level safety net (per-user throttle ke saath)."""
    scope = 'message_send_ip'
    rate = '120/min'


class CallInitiateIPThrottle(ScopedIPThrottle):
    """Call-initiate IP-level safety net — FCM push + LiveKit room, dono costly."""
    scope = 'call_initiate_ip'
    rate = '20/min'


# ---------------------------------------------------------------
# 🔥 NAYA — Parent Mode (Feature 8): `/parent/verify/` is the ONE
# endpoint in this whole flow that's unauthenticated (`AllowAny` — a
# parent has no login). `ParentAccessCode.code` is an 8-char code from a
# 32-symbol alphabet, but "hard to guess" isn't a substitute for a rate
# limit on an endpoint anyone on the internet can hit — this is per-IP
# since there's no user to key on yet at this point in the flow.
# ---------------------------------------------------------------
class ParentCodeVerifyThrottle(ScopedIPThrottle):
    """Brute-force guard on parent-code verification (unauthenticated)."""
    scope = 'parent_code_verify_ip'
    rate = '10/min'


class WSMessageRateLimiter:
    """
    Usage (ChatConsumer.handle_new_message ke start me):

        from .throttles import WSMessageRateLimiter

        allowed, retry_after = WSMessageRateLimiter.check(self.scope['user'].id)
        if not allowed:
            await self.send_json({
                "type": "error",
                "code": "rate_limited",
                "detail": f"Bahut fast bhej rahe ho, {retry_after}s ruko.",
            })
            return

    Fixed-window counter per user per minute — simple, no extra infra
    (Django cache already backed by Redis in most deployments), good enough
    for abuse-prevention (not billing-grade precision).
    """
    LIMIT = 60          # messages per window
    WINDOW_SECONDS = 60

    @classmethod
    def check(cls, user_id) -> tuple[bool, int]:
        key = f"ws_msg_rl:{user_id}"
        try:
            count = cache.incr(key)
        except ValueError:
            # key didn't exist yet
            cache.set(key, 1, cls.WINDOW_SECONDS)
            count = 1

        if count > cls.LIMIT:
            return False, cls.WINDOW_SECONDS

        return True, 0


# message/throttles.py — ADDITIONS ONLY
"""
⚠️ MERGE NOTE: `throttles.py` upload nahi hui thi is batch me.
`CHAT_APP_DOCUMENTATION.md` confirm karta hai ki wo already exist karti
hai (`MessageSendThrottle`, `CallInitiateThrottle`, `GroupCreateThrottle`,
`ReactionThrottle`, `ParentCodeVerifyThrottle`, `TranslateThrottle`, etc.
sab already wahan hain). Neeche sirf 2 NAYI classes hain — apni asli
`throttles.py` ke end me paste kar dena, existing kuch mat chhedo.

Dono ke liye `settings.py`'s `DEFAULT_THROTTLE_RATES` me entry chahiye
(is app me pehle se hi 7+ scopes is entry ke bina missing hain, jo
`ImproperlyConfigured` crash deti hain — same category ka bug, isliye
yahan explicitly likh raha hoon taaki ye do naye scope us list me na
judein):

    DEFAULT_THROTTLE_RATES = {
        ...,
        "focus_session": "20/min",
        "parent_code_reveal": "10/hour",
    }
"""
from rest_framework.throttling import UserRateThrottle


class FocusSessionThrottle(UserRateThrottle):
    """
    🔧 GAP FIX — `views_focus.py`'s own header comment already suggested
    this (scope `focus_session`, `20/min`) as an optional next step, but
    it was never implemented. Low blast-radius (own-account only, no
    fan-out) lekin abuse-prevention zero thi — ek user rapid-fire
    start/cancel spam kar sakta tha (har start/cancel ek DB write hai).
    20/min free cheez karta hai kisi genuine use-case ko friction dena
    (koi bhi normal student 20 baar/min focus session start/stop nahi
    karega) jabki spam-loop ko turant cap kar deta hai.
    """
    scope = 'focus_session'


class ParentCodeRevealThrottle(UserRateThrottle):
    """
    🔧 NEW — `ParentAccessCodeRevealView` (views_parent.py) ke liye.
    List ab sirf masked code dikhata hai; poora plaintext dobara dekhne
    ka EK hi rasta hai — ye endpoint. 10/hour kaafi hai kisi genuine
    "parent ne dobara maanga" case ke liye, lekin agar kisi ka session/
    device compromise ho jaaye to bhi saare active codes ko bulk-scrape
    karna is rate pe impractical ho jaata hai.
    """
    scope = 'parent_code_reveal'