"""
message/study_room_events.py

S4 — REST (host controls) se study room ke WebSocket group (`chat_<conversation_id>`) me event bhejne ka helper.

Consumer ka `study_room_broadcast` handler in optional keys ko samajhta hai:
  target_user_ids : list[str]  -> sirf in users ke sockets ko jaata hai (jaise `removed_from_room`, `role_changed`)
  manager_only    : bool       -> sirf host / co-host sockets ko (jaise `waiting_room_request`, `hand_raised` list)
Dono na ho to room ke sabhi sockets ko (sender-exclusion nahi; `sender_channel_name` khali).
Best-effort: channel layer down ho to REST response phir bhi sahi jaata hai (client poll / refresh se sync hota hai).
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

# Server -> client study_room_event actions (Flutter `_handleRoomEvent` inhe sunta hai)
EVT_SETTINGS_CHANGED = 'room_settings_changed'    # {settings}
EVT_ROLE_CHANGED = 'role_changed'                 # {userId, role, permissions}
EVT_REMOVED = 'removed_from_room'                 # {userId, bySelf?}  (target ko)
EVT_MUTED = 'muted_by_host'                       # {userIds|all}
EVT_WAITING_REQUEST = 'waiting_room_request'      # manager only: {userId, displayName}
EVT_WAITING_DECISION = 'waiting_room_decision'    # target ko: {userId, status: admitted|denied}
EVT_HAND = 'hand_raised'                          # {userId, raised, displayName}
EVT_RULES_CHANGED = 'room_rules_changed'          # S8: {version}  — group admin ne rules badle, dobara accept karna padega
EVT_BOARD_RESTORED = 'board_restored'             # S3: {version, byUserId} — sab clients board + notes dobara GET karein
EVT_BLOCK_CHANGED = 'room_block_changed'          # S8: target ko (dono users ko alag-alag): {userId (doosra), blocked}


def broadcast(conversation_id, action, data, target_user_ids=None, manager_only=False):
    layer = get_channel_layer()
    if layer is None:
        return False
    event = {
        'type': 'study_room_broadcast',
        'sender_channel_name': '',
        'action': action,
        'data': data,
    }
    if target_user_ids:
        event['target_user_ids'] = [str(u) for u in target_user_ids]
    if manager_only:
        event['manager_only'] = True
    try:
        async_to_sync(layer.group_send)(f'chat_{conversation_id}', event)
        return True
    except Exception:
        logger.warning('study room broadcast %s failed conv=%s', action, conversation_id, exc_info=True)
        return False
