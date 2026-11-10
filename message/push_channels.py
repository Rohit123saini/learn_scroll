# message/push_channels.py
"""
N3 — Android notification-channel ids, ek jagah.

Channels (Flutter `notifications/services/push_channels.dart` inhi ids ko banata hai; dono taraf ek hi naam):

    chat_messages   Messages (LEGACY id - purane app builds ke saath compatible, isliye rename nahi kiya)
    announcements   Teacher / group-admin announcements (LEGACY id)
    calls           Calls / missed calls            importance HIGH (max)
    classes         Live class, class + campus updates
    tests           Tests, assignments, results
    social          Likes, comments, follows, reposts
    wallet          Payments, fees, withdrawals
    silent          Streak / recap / digest - no sound, no heads-up
    notifications   Default rich channel (safety + anything unmapped) - high

Purana app build jo naya id nahi jaanta wo bhi crash nahi karta: `showRichPushNotification` (Flutter) unknown
channel id par channel on-demand bana leta hai.
"""

CHANNEL_MESSAGES = 'chat_messages'
CHANNEL_ANNOUNCEMENTS = 'announcements'
CHANNEL_CALLS = 'calls'
CHANNEL_CLASSES = 'classes'
CHANNEL_TESTS = 'tests'
CHANNEL_SOCIAL = 'social'
CHANNEL_WALLET = 'wallet'
CHANNEL_SILENT = 'silent'
CHANNEL_DEFAULT = 'notifications'

ALL_CHANNELS = (
    CHANNEL_MESSAGES, CHANNEL_ANNOUNCEMENTS, CHANNEL_CALLS, CHANNEL_CLASSES, CHANNEL_TESTS,
    CHANNEL_SOCIAL, CHANNEL_WALLET, CHANNEL_SILENT, CHANNEL_DEFAULT,
)

# core.Notification.preference_groups() key -> channel (N1 ke groups reuse - ek hi catalogue).
GROUP_TO_CHANNEL = {
    'classes': CHANNEL_CLASSES,
    'campus': CHANNEL_CLASSES,
    'assignments_tests': CHANNEL_TESTS,
    'messages_calls': CHANNEL_MESSAGES,
    'social': CHANNEL_SOCIAL,
    'learning': CHANNEL_SILENT,
    'payments': CHANNEL_WALLET,
    'safety': CHANNEL_DEFAULT,   # emergency / copyright: high importance, never silent
    'other': CHANNEL_DEFAULT,
}

_TYPE_TO_CHANNEL = None


def _type_map():
    global _TYPE_TO_CHANNEL
    if _TYPE_TO_CHANNEL is None:
        from core.models import Notification  # lazy: core.services -> push_utils import chain me cycle na bane

        out = {}
        for group, types in Notification.preference_groups().items():
            channel = GROUP_TO_CHANNEL.get(group, CHANNEL_DEFAULT)
            for value in types:
                out.setdefault(value, channel)  # DOUBT_ANSWERED jaisa type 2 groups me ho to pehla jeetta hai
        incoming = getattr(Notification.NotifType, 'INCOMING_CALL', None)
        if incoming is not None:
            out[incoming.value] = CHANNEL_CALLS
        _TYPE_TO_CHANNEL = out
    return _TYPE_TO_CHANNEL


def channel_for_notif_type(notif_type) -> str:
    """NotifType value -> Android channel id. Unknown / future type -> default channel (kabhi raise nahi karta)."""
    try:
        return _type_map().get(str(notif_type or ''), CHANNEL_DEFAULT)
    except Exception:
        return CHANNEL_DEFAULT
