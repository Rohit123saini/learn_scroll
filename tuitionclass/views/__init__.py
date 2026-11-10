"""
tuitionclass/views.py

DRF ViewSets for the tuitionclass app.

Design notes:
    - Every viewset requires authentication (IsAuthenticated).
    - "Owner" fields (teacher/student/sender/created_by/uploaded_by/user) are
      always taken from request.user in perform_create — never from the payload.
    - A few models need business logic beyond plain CRUD, exposed as @action
      endpoints: ClassSession.join, ClassJoinRequest.accept/reject/cancel,
      LivePoll.vote, assigmentsSubmission.grade, SessionParticipant.leave.
    - Querysets are scoped where it matters for privacy (e.g. a student should
      not see another student's pass purchases or wallet ledger).

--- T3.2 ---
This module used to be one 7121-line views.py. It is now a package split by
responsibility; every public/private name that other code imported from
`tuitionclass.views` is re-exported here so existing imports keep working.
Patching (unittest.mock) must target the submodule that *uses* the name,
e.g. `tuitionclass.views.<submodule>.<name>`.
"""


# --- names the old monolith imported (kept for `from .views import <name>` compatibility) ---
# flake8: noqa
import hashlib
import hmac
import logging
import uuid
from assignments.serializers import (
    assigmentsSerializer as UnifiedassigmentsSerializer,
    assigmentsSubmissionSerializer as UnifiedassigmentsSubmissionSerializer,
)
from core.classroom_chat_bridge import resolve_parent_from_token
from core.models import Notification, NotificationPreference
from core.services import create_bulk_notifications, create_notification
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Case, Count, F, IntegerField, Q, Sum, Value, When
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import mixins, pagination, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, Throttled, ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from .. import bridge
from ..livekit_utils import (
    LIVEKIT_URL,
    LiveKitError,
    ParticipantRole,
    ensure_room,
    generate_livekit_token,
    remove_participant,
    set_participant_audio_muted,
    start_room_recording,
    stop_room_recording,
    verify_webhook_event,
)
from ..models import (
    BreakoutRoom,
    Certificate,
    ChatMessage,
    ChatMessageRead,
    ChatMessageReport,
    ChatReaction,
    ClassHoliday,
    ClassJoinRequest,
    ClassMaterial,
    ClassPass,
    ClassQuery,
    ClassReminder,
    ClassSchedule,
    ClassSession,
    Classroom,
    ClassroomBan,
    ClassroomReport,
    ClassroomReview,
    ClassroomShare,
    ClassroomStaff,
    ClassroomTrialAccess,
    ClassroomWishlist,
    CoinPurchase,
    CoinTransaction,
    CoinWithdrawal,
    Coupon,
    LivePoll,
    Notice,
    ParentMessageTemplate,
    ParentTeacherMessage,
    PassDailyCharge,
    PassGift,
    PassPurchase,
    PollResponse,
    PollTemplate,
    Referral,
    ReferralAttribution,
    ReferralCommission,
    SessionCaption,
    SessionParticipant,
    SessionReaction,
    SessionReadState,
    SessionWaitlist,
    assigmentsSubmission,
    get_classroom_list_cache_version,
    get_notice_list_cache_version,
    referral_code_for_user,
    referral_code_to_user_id,
    seconds_until_next_notice_expiry,
)
from ..moderation import screen_message
from ..realtime import broadcast_to_session
from ..serializers import (
    BreakoutRoomSerializer,
    CertificateIssueSerializer,
    CertificateSerializer,
    ChatMessageReadSerializer,
    ChatMessageReportSerializer,
    ChatMessageSerializer,
    ChatReactionSerializer,
    ClassHolidaySerializer,
    ClassJoinRequestDecisionSerializer,
    ClassJoinRequestSerializer,
    ClassMaterialSerializer,
    ClassPassSerializer,
    ClassQueryAnswerSerializer,
    ClassQuerySerializer,
    ClassReferralSummarySerializer,
    ClassReminderSerializer,
    ClassScheduleSerializer,
    ClassSessionSerializer,
    ClassroomBanSerializer,
    ClassroomMyShareSerializer,
    ClassroomReferralDashboardSerializer,
    ClassroomReportSerializer,
    ClassroomReviewSerializer,
    ClassroomSerializer,
    ClassroomShareLogSerializer,
    ClassroomShareResultSerializer,
    ClassroomShareSerializer,
    ClassroomStaffSerializer,
    ClassroomStatsSerializer,
    ClassroomWishlistSerializer,
    CoinPurchaseInitiateSerializer,
    CoinPurchaseSerializer,
    CoinPurchaseVerifySerializer,
    CoinTransactionSerializer,
    CoinWithdrawalSerializer,
    CouponSerializer,
    LiveNowSessionSerializer,
    LivePollSerializer,
    MyReferralCodeSerializer,
    NoticeSerializer,
    ParentMessageTemplateSerializer,
    ParentTeacherMessageReplySerializer,
    ParentTeacherMessageSerializer,
    PassGiftSerializer,
    PassPurchaseAutoRenewSerializer,
    PassPurchaseSerializer,
    PollResponseSerializer,
    PollTemplateSerializer,
    ReferLinkResultSerializer,
    ReferralAttributeSerializer,
    ReferralCommissionSerializer,
    ReferralRedeemSerializer,
    ReferralSerializer,
    SessionCaptionSerializer,
    SessionEngagementReportSerializer,
    SessionParticipantSerializer,
    SessionReactionSerializer,
    SessionRecordingSerializer,
    SessionWaitlistSerializer,
    StudentProgressSerializer,
    TeacherEarningsSerializer,
    UserMiniSerializer,
)
from ..throttles import ParentJoinIPThrottle

from ._common import (
    TuitionClassPagination,
    _is_truthy,
    _safe_apply_async,
    _safe_broadcast_to_user,
    _safe_delay,
)

from .access import (
    AccessLevel,
    _access_level,
    _accessible_classroom_ids,
    _active_trial_for,
    _can_manage_classroom,
    _can_moderate_session,
    _can_view_classroom_internals,
    _has_active_trial,
    _has_full_room_access,
    _has_room_access,
    _has_room_access_no_pass,
    _org_staff_role,
    _resolve_session_roles,
)

from .classrooms import (
    ClassScheduleViewSet,
    ClassroomReportViewSet,
    ClassroomReviewViewSet,
    ClassroomStaffViewSet,
    ClassroomViewSet,
    ClassroomWishlistViewSet,
)

from .coins import (
    CoinPurchaseViewSet,
    CoinTransactionViewSet,
    CoinWithdrawalViewSet,
    ReferralViewSet,
    TeacherEarningsView,
    _create_gateway_order,
    _deprecated_write_response,
    _verify_gateway_signature,
)

from .dashboard import (
    HealthCheckView,
    MyDashboardView,
)

from .learning import (
    CertificateViewSet,
    ClassHolidayViewSet,
    ClassQueryViewSet,
    ClassReminderViewSet,
    NoticeViewSet,
    StudentProgressView,
    assigmentsSubmissionViewSet,
    assigmentsViewSet,
)

from .live_chat import (
    ChatMessageReportViewSet,
    ChatMessageViewSet,
    ClassMaterialViewSet,
    LivePollViewSet,
    PollTemplateViewSet,
)

from .parent import (
    ParentMessageTemplateViewSet,
    ParentTeacherMessageViewSet,
)

from .passes import (
    ClassJoinRequestViewSet,
    ClassPassViewSet,
    CouponViewSet,
    PassGiftViewSet,
    PassPurchaseViewSet,
    _charge_and_create_purchase,
    _coins_for_pass,
    _preview_coupon_for_pass,
)

from .sessions import (
    ClassSessionViewSet,
    LiveKitWebhookView,
    SessionParticipantViewSet,
    SessionWaitlistViewSet,
    _perform_join,
    _perform_trial_join,
    _try_promote_from_waitlist,
)
