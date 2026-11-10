# tuitionclass/models/__init__.py — P-C4: the old god-file tuitionclass/models.py is now this package.
# Everything that used to be importable from `tuitionclass.models` still is (serializers, views, admin, other apps, migrations, tests).
# Module map: see STATUS_PC4_TUITIONCLASS_MODELS.md. Do NOT add logic here; add it to the matching submodule and re-export it below.

"""
tuitionclass/models.py

Online tuition-class platform.

App structure assumed:
    login/            -> custom User model (AbstractUser) already exists here
    tuitionclass/         -> this new app (classrooms, scheduling, passes, sessions)

Flow (open marketplace — ANY authenticated user can be a "teacher" for their
own classroom, and ANY authenticated user can be a "student" who buys a pass
for someone else's classroom. There is no separate teacher/student role on
the User model; a person is a teacher only in the context of the Classroom
they created):
    Any user creates a Classroom (becomes its teacher)
      -> defines one or more ClassSchedule (recurrence rules: daily/weekly/monthly/
         yearly/specific date/weekday/weekend)
      -> ClassSession rows are the actual generated occurrences (what students join)
      -> Classroom's teacher creates ClassPass(es) (daily/weekly/monthly/yearly/free, priced in coins)
      -> Any other user raises a ClassJoinRequest against one of those passes
         (NOT a direct purchase — see ClassJoinRequestViewSet in views.py)
      -> The classroom's teacher/co-teacher/moderator accepts or rejects it.
         Only on acceptance is a PassPurchase actually created and coins
         debited from the requester's User.coin balance (coin economy only —
         the purchase is created ONLY if the requester has enough coins at
         accept-time; see ClassJoinRequestViewSet.accept in views.py). Until
         then the requester holds no pass and is not a participant.
      -> PassPurchase.is_valid() gates join access to ClassSession, and
         Classroom.has_access() gates everything else classroom-detail-ish
         (schedule, sessions, materials, notices, holidays, doubts). A user
         with no valid pass can only ever see a classroom's public listing
         info (title/description/subject/language/cover) plus its reviews.
      -> SessionParticipant logs who actually joined which session (attendance)
"""

import logging
import secrets
import uuid
import zoneinfo
from datetime import datetime as _datetime
from datetime import timedelta as _timedelta
from datetime import timezone as _dt_timezone
from decimal import ROUND_HALF_UP, Decimal

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.contrib.postgres.indexes import GinIndex
from django.db import IntegrityError as DjangoIntegrityError
from django.db import models, transaction
from django.db.models import F
from django.db.models.functions import Greatest
from django.utils import timezone
from django.utils.deconstruct import deconstructible

from login.models import User

# ---- re-exports (explicit; private helpers included on purpose) ----
from .cache_versions import (  # noqa: E402,F401
    CLASSROOM_LIST_CACHE_VERSION_KEY,
    get_classroom_list_cache_version,
    bump_classroom_list_cache_version,
    _broadcast_classroom_stats,
    _notice_list_cache_version_key,
    get_notice_list_cache_version,
    bump_notice_list_cache_version,
    seconds_until_next_notice_expiry,
)
from .validators import (  # noqa: E402,F401
    DOCUMENT_MEDIA_EXTENSIONS,
    MaxFileSizeValidator,
)
from .classroom import (  # noqa: E402,F401
    Classroom,
)
from .schedules import (  # noqa: E402,F401
    ClassSchedule,
    ClassSession,
)
from .recordings import (  # noqa: E402,F401
    LiveReminderSub,
    SessionChapter,
    RecordingWatch,
    RecordingNote,
    RecordingSave,
)
from .passes import (  # noqa: E402,F401
    ClassPass,
    PassPurchase,
    PassDailyCharge,
    PassGift,
    ClassJoinRequest,
)
from .live import (  # noqa: E402,F401
    BreakoutRoom,
    SessionParticipant,
    LiveModerationEvent,
)
from .student_state import (  # noqa: E402,F401
    ClassroomTrialAccess,
    ClassroomParentState,
    SessionAttendance,
    compute_attendance_percent_bulk,
    compute_attendance_counts_bulk,
)
from .materials import (  # noqa: E402,F401
    ClassMaterial,
)
from .chat import (  # noqa: E402,F401
    ChatMessage,
    ChatMessageReport,
    ChatReaction,
    ChatMessageRead,
    SessionReaction,
    SessionCaption,
    SessionReadState,
)
from .polls import (  # noqa: E402,F401
    LivePoll,
    PollResponse,
    PollTemplate,
)
from .homework import (  # noqa: E402,F401
    assigments,
    assigmentsSubmission,
)
from .social import (  # noqa: E402,F401
    ClassroomReview,
    ClassroomWishlist,
    ClassroomShare,
)
from .wallet import (  # noqa: E402,F401
    Coupon,
    CoinTransaction,
    CoinPurchase,
)
from .referral import (  # noqa: E402,F401
    _LEGACY_REFERRAL_CODE_SALT,
    _REFERRAL_CODE_PREFIX,
    _REFERRAL_CODE_ALPHABET,
    _REFERRAL_CODE_RANDOM_LEN,
    generate_referral_code,
    referral_code_for_user,
    referral_code_to_user_id,
    ReferralCode,
    Referral,
    ReferralAttribution,
    ReferralCommission,
)
from .withdrawals import (  # noqa: E402,F401
    CoinWithdrawal,
)
from .classroom_admin import (  # noqa: E402,F401
    ClassroomBan,
    ClassroomStaff,
    SessionWaitlist,
    ClassroomReport,
)
from .certificates import (  # noqa: E402,F401
    Certificate,
    StudentReportCard,
)
from .notices_queries import (  # noqa: E402,F401
    ClassReminder,
    ClassHoliday,
    Notice,
    ClassQuery,
    ClassQueryUpvote,
)
from .parent_messaging import (  # noqa: E402,F401
    ParentMessageTemplate,
    ParentTeacherMessage,
)
from .uploads import (  # noqa: E402,F401
    ChunkedUpload,
)
from .receivers import (  # noqa: E402,F401
    post_delete,
    post_save,
    pre_save,
    receiver,
    _bump_classroom_list_cache,
    _bump_notice_list_cache,
    _sync_classroom_rating,
    _sync_classroom_enrolled_count,
    _stash_previous_session_status,
    _charge_passes_for_completed_session,
    _auto_flag_classroom,
)

# ---- forward references (see each module's bottom) ----
# A few methods use models/functions that live in a later module. Python resolves those names at call time from the
# defining module's globals, so they are bound here once everything is imported. Same objects, same behaviour.
from . import (  # noqa: E402
    cache_versions as _m_cache_versions,
    classroom as _m_classroom,
    schedules as _m_schedules,
    passes as _m_passes,
    live as _m_live,
    polls as _m_polls,
    wallet as _m_wallet,
    referral as _m_referral,
    classroom_admin as _m_classroom_admin,
    notices_queries as _m_notices_queries,
)

def _link_forward_refs():
    _m_cache_versions.Notice = _m_notices_queries.Notice
    _m_classroom.ClassSchedule = _m_schedules.ClassSchedule
    _m_classroom.ClassroomReport = _m_classroom_admin.ClassroomReport
    _m_classroom.PassPurchase = _m_passes.PassPurchase
    _m_classroom.referral_code_for_user = _m_referral.referral_code_for_user
    _m_schedules.ClassHoliday = _m_notices_queries.ClassHoliday
    _m_schedules.PollResponse = _m_polls.PollResponse
    _m_schedules.SessionParticipant = _m_live.SessionParticipant
    _m_passes.CoinTransaction = _m_wallet.CoinTransaction
    _m_passes.Coupon = _m_wallet.Coupon
    _m_passes.ReferralAttribution = _m_referral.ReferralAttribution
    _m_passes.ReferralCommission = _m_referral.ReferralCommission

_link_forward_refs()
