"""


--- T3.2 ---
This module used to be one 4179-line views.py. It is now a package split by
responsibility; every public/private name that other code imported from
`message.views` is re-exported here so existing imports keep working.
Patching (unittest.mock) must target the submodule that *uses* the name,
e.g. `message.views.<submodule>.<name>`.
"""


# --- names the old monolith imported (kept for `from .views import <name>` compatibility) ---
# flake8: noqa
import logging
import os
import uuid
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Prefetch, Q, Sum
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .. import message_requests, search_utils, sticky_notes, user_notes
from ..admin_only import (
    admin_only_block_response,
    is_admins_only,
    normalize_permission,
    post_permission_system_message,
)
from ..attendance_utils import compute_attendance_stats
from ..cache_utils import get_presence_cached, invalidate_group_role_cache, set_presence_cache
from ..constants import MAX_PINNED_PER_CONVERSATION
from ..group_rules import (
    check_daily_message_limit,
    check_group_permission,
    is_group_admin_or_mod,
)
from ..livekit_utils import (
    EgressError,
    generate_livekit_token,
    start_room_recording,
    stop_room_recording,
)
from ..media_utils import create_group_media_for_message
from ..mentions import extract_mentioned_user_ids
from ..models import (
    BlockedUser,
    CallParticipant,
    CallSession,
    CallStatus,
    CallType,
    Conversation,
    ConversationParticipant,
    ConversationType,
    DeviceToken,
    DisappearingDuration,
    DoubtQuestion,
    DoubtUpvote,
    Group,
    GroupJoinRequest,
    GroupMedia,
    GroupMember,
    Message,
    MessageReaction,
    MessageStatus,
    MessageType,
    Poll,
    PollOption,
    PollVote,
    RequestStatus,
    StudyRoomAttendance,
    StudyRoomNote,
    StudyRoomState,
)
from ..offline_queue import flush_offline_queue
from ..permissions import IsConversationParticipant, IsGroupAdminOrModerator, IsMessageSender
from ..push_utils import (
    send_call_cancelled_push,
    send_chat_message_push,
    send_incoming_call_push,
    send_mention_push,
    send_push_to_users,
)
from ..serializers import (
    BlockedUserSerializer,
    CallSessionSerializer,
    ConversationListSerializer,
    ConversationSettingsSerializer,
    ConversationWallpaperSerializer,
    DirectoryGroupSerializer,
    DirectoryPersonSerializer,
    DoubtAnswerSerializer,
    DoubtCreateSerializer,
    DoubtQuestionSerializer,
    GroupCreateSerializer,
    GroupDiscoverSerializer,
    GroupJoinRequestSerializer,
    GroupMediaSerializer,
    GroupMemberSerializer,
    GroupSerializer,
    MessageCreateSerializer,
    MessageReactionSerializer,
    MessageReadStatusSerializer,
    MessageSearchResultSerializer,
    MessageSerializer,
    PollCreateSerializer,
    PollSerializer,
    PollVoteSerializer,
    ReadReceiptSettingsSerializer,
    ScheduleMessageSerializer,
    UserMiniSerializer,
    UserPresenceSerializer,
)
from ..services import (
    add_members_to_group,
    add_or_reactivate_participant,
    answer_doubt_question,
    create_group,
    remove_group_member,
    update_group_member_role,
)
from ..tasks import generate_link_preview_task, transcribe_voice_message_task
from ..throttles import (
    CallInitiateIPThrottle,
    CallInitiateThrottle,
    GroupCreateThrottle,
    MessageSendIPThrottle,
    MessageSendThrottle,
    ReactionThrottle,
    TranslateThrottle,
)
from ..translation_service import (
    SUPPORTED_LANGUAGES,
    TranslationError,
    TranslationServiceUnavailable,
    UnsupportedLanguageError,
    translate_text,
)
from ..user_display import build_user_mini, get_display_name, get_profile_photo_url

from ._common import (
    LIVEKIT_WS_URL,
    MessagePagination,
    StandardPagination,
    User,
    _conversation_list_qs_for,
    is_blocked_pair,
    with_message_list_prefetch,
)

from .calls import (
    CallActionView,
    CallHistoryViewSet,
    CallInitiateView,
    CallOpsAPIView,   # X1
    CallRecordingNoticeAckView,   # C16
    CallRecordingNoticeView,   # C16
    CallRecordingView,
    CallTelemetryView,   # X1
    CallTokenRefreshView,   # C13
)

from .conversations import (
    ConversationViewSet,
)

from .groups import (
    DoubtQuestionViewSet,
    GroupViewSet,
)

from .messages import (
    MessageViewSet,
)

from .social import (
    BlockedUserViewSet,
    ChatDirectorySearchView,
    DeviceTokenView,
    MessageRequestAcceptView,
    MessageRequestDeclineView,
    MessageRequestListView,
    MyNoteView,
    ReadReceiptSettingsView,
    UserNotesListView,
    UserPresenceView,
    _MessageRequestRespondView,
)

from .study_room import (
    StudyRoomJoinView,
    StudyRoomNotesView,
    StudyRoomStateView,
    StudyRoomStateHistoryView,   # S3
    StudyRoomStateRestoreView,   # S3
    StudyRoomActivityView,       # S5 idle time
    StudyRoomOpsAPIView,         # X1
    StudyRoomStreakView,
    StudyRoomTelemetryView,      # X1
    StudyRoomTokenRefreshView,   # C13
)

from .study_room_review import (   # S4 leftover — staff review of study room reports
    StudyRoomReportActionView,
    StudyRoomReportQueueView,
    StudyRoomReportSummaryView,
)

from .study_room_controls import (   # S4 — host / moderation controls (+ S8 rules / block)
    StudyRoomAdmitView,
    StudyRoomBlockView,          # S8
    StudyRoomHandView,
    StudyRoomMuteView,
    StudyRoomRemoveView,
    StudyRoomReportView,
    StudyRoomRoleView,
    StudyRoomRoomInfoView,
    StudyRoomRulesView,          # S8
    StudyRoomSettingsView,
)
