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


# ---------------------------------------------------------------
# WEBSOCKET SIDE — DRF throttles don't apply to Channels consumers.
# Ye ek chhota, dependency-free sliding-window limiter hai jo Django cache
# (same backend jo `ai_service.py` already use karta hai) use karta hai,
# taaki REST aur WS dono paths par same kind of protection ho.
# ---------------------------------------------------------------

from django.core.cache import cache


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