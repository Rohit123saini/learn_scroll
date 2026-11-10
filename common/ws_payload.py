"""
common/ws_payload.py  (T0.2)

channels_redis payloads ko msgpack se serialize karta hai, aur msgpack UUID /
datetime / Decimal jaise objects nahi samajhta ("can not serialize 'UUID'
object"). DRF `serializer.data` me UUID-pk wale models ke `id`/FK fields UUID
objects hote hain => `group_send` silently fail (aksar `except Exception` me
log hokar rah jaata hai) aur doosre members ko live event nahi milta.

  * `json_safe(payload)` — payload ko plain JSON types (dict/list/str/int/float/
    bool/None) me badalta hai (`DjangoJSONEncoder`: UUID/datetime/date/Decimal -> str).
  * `SafeRedisChannelLayer` — `RedisChannelLayer` ka thin subclass jo `send` /
    `group_send` ke andar har message pe `json_safe` lagata hai (settings.py
    CHANNEL_LAYERS me use hota hai). Isse 60+ `group_send` call-sites ek saath
    safe ho jaate hain, aur aage naye add hone wale bhi. Explicit call-sites
    (jaise `DoubtQuestionViewSet._broadcast`) phir bhi `json_safe()` use karte
    hain, kyunki consumers ka `json.dumps(event[...])` InMemory layer (dev) par bhi
    UUID pe girta hai.
"""
import json
import logging

from django.core.serializers.json import DjangoJSONEncoder

logger = logging.getLogger(__name__)


def json_safe(payload):
    """Deep-convert `payload` to JSON-safe python primitives (UUID -> str etc.)."""
    return json.loads(json.dumps(payload, cls=DjangoJSONEncoder))


def _safe_message(message):
    try:
        return json_safe(message)
    except (TypeError, ValueError):
        # Aisa kuch (jaise raw bytes) jo JSON me nahi jaata — msgpack use native
        # handle kar leta hai, isliye original wapas do, tod-phod mat karo.
        logger.debug("ws_payload: message not JSON-able, sending as-is", exc_info=True)
        return message


try:  # channels_redis sirf Redis wale setup me installed hona zaroori hai
    from channels_redis.core import RedisChannelLayer

    class SafeRedisChannelLayer(RedisChannelLayer):
        async def send(self, channel, message):
            return await super().send(channel, _safe_message(message))

        async def group_send(self, group, message):
            return await super().group_send(group, _safe_message(message))

except ImportError:  # pragma: no cover — InMemory dev setup me ye class use hi nahi hoti
    SafeRedisChannelLayer = None
