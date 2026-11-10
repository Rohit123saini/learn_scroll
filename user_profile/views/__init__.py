# user_profile/views/__init__.py — P-C4: the old god-file user_profile/views.py is now this package.
# Everything that used to be importable from `user_profile.views` still is (urls.py, other apps, tests, management commands).
# Module map: see STATUS_PC4_USER_PROFILE_VIEWS.md. Do NOT add logic here; add it to the matching submodule and re-export it below.

# user_profile/views.py
import hashlib
import hmac
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.http import Http404
from django.utils import timezone
from django.http.request import RawPostDataException
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import filters, status
from rest_framework.generics import GenericAPIView, ListAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.pagination import get_max_page_size
from common.feature_flags import cashout_disabled_response as _cashout_disabled_response, coin_cashout_enabled as _coin_cashout_enabled  # P-G1

from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle

from .. import activity, fraud
from .. import profile_header  # P1
from .. import privacy as privacy_rules  # S4
from ..pref_etag import if_match_state, if_none_match_hits, make_etag  # S2
from ..discovery import mutual_followers_queryset, suggested_users_queryset  # P8-BE
from .. import relationships  # P8
from .. import profile_state  # P10
from ..models import (
    BlockUser,
    CoinLedger,
    CoinLedgerBusy,
    CoinPurchaseRequest,
    CoinWithdrawalRequest,
    DailyUsage,
    Follow,
    PrivacySettings,
    RestrictUser,
    Streak,
    StreakFreezeError,
    SpendLimitExceeded,
    UserPreference,
    WeeklyRecap,
    WithdrawalNotEligible,
)
from ..activity import ActivityLimitSerializer, HeartbeatSerializer  # P14-BE
from .. import role_settings  # S12
from ..services import (  # P11-BE
    ReportRateLimited,
    blocked_user_ids,
    file_report,
    pending_follow_requests_for,
    remove_follower,
)
from ..throttles import CoinPurchaseBurstThrottle, CoinPurchaseDailyThrottle
from login.sensitive import SensitiveWriteThrottle, changed_fields as _x6_changed_fields, record_change as _x6_record_change  # X6
from login import audit as _x6_audit  # X6 -- audit Event names
from ..serializers import (
    MutualFollowersResponseSerializer,  # P8-BE
    SimilarUsersResponseSerializer,  # P8-BE
    BlockUserSerializer,
    BlockedTargetUserProfileSerializer,
    BulkFollowRequestSerializer,  # P8
    ContentReportCreateSerializer,
    CoinLedgerSerializer,
    CoinPurchaseConfirmSerializer,
    CoinPurchaseRequestSerializer,
    CoinWithdrawalActionSerializer,
    CoinWithdrawalAdminSerializer,
    CoinWithdrawalRequestSerializer,
    FollowActionResponseSerializer,
    FollowListRowSerializer,
    MessageContactSearchSerializer,
    PrivacySettingsSerializer,
    ProfileUpdateSerializer,
    RestrictedTargetUserProfileSerializer,
    RestrictUserSerializer,
    StreakSerializer,
    TargetUserProfileSerializer,
    UserPreferenceSerializer,
    UserProfileDetailResponseSerializer,
    UserProfileSerializer,
    UserSearchSerializer,
    WeeklyRecapSerializer,
    bulk_accepted_connection_ids,
    bulk_viewer_follow_status,
)

# ---- re-exports (explicit; private helpers included on purpose) ----
from ._common import (  # noqa: E402,F401
    User,
    logger,
    _wallet_busy_response,
    _refuse_adult_profile_photo,
    is_blocked_between,
    is_restricted_between,
)
from .profile import (  # noqa: E402,F401
    ProfileView,
    UserProfileDetailView,
)
from .search import (  # noqa: E402,F401
    UserSearchView,
    MutualFollowersView,
    SimilarUsersView,
    MessageContactSearchView,
)
from .follow import (  # noqa: E402,F401
    _follow_lists_hidden_response,
    FollowersListView,
    FollowingListView,
    _new_follower_title,
    _new_follower_batch_kwargs,
    FollowAPIView,
    AcceptFollowRequestView,
    RejectFollowRequestView,
    RemoveFollowerView,
    FollowRequestsListView,
    BulkFollowRequestsView,
    RelationshipSummaryView,
)
from .profile_update import (  # noqa: E402,F401
    _X6_PROFILE_SENSITIVE,
    UpdateProfileView,
    UsernameCheckView,
)
from .blocks import (  # noqa: E402,F401
    BlockBurstThrottle,
    BlockDailyThrottle,
    ReportBurstThrottle,
    BlockedUsersView,
    ContentReportView,
    UnblockUserView,
    RestrictedUsersView,
    UnrestrictUserView,
)
from .wallet import (  # noqa: E402,F401
    CoinLedgerListView,
    BuyCoinView,
    GatewayWebhookSignatureError,
    _verify_gateway_webhook_signature,
    BuyCoinConfirmView,
    AdminCoinPurchaseConfirmView,
    CoinWithdrawalRequestView,
    CoinWithdrawalAdminListView,
    CoinWithdrawalAdminActionView,
)
from .preferences import (  # noqa: E402,F401
    UserPreferenceView,
    LearningPreferencesView,
    PrivacySettingsView,
)
from .streaks import (  # noqa: E402,F401
    StreakView,
    StreakFreezeBuyView,
    DailyGoalView,
    WeeklyRecapView,
    WeeklyRecapCardAPIView,
)
from .activity_views import (  # noqa: E402,F401
    ActivityView,
    ActivityHeartbeatView,
)
