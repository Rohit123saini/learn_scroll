# campus/views/__init__.py — P-C4: the old god-file campus/views.py is now this package.
# Everything that used to be importable from `campus.views` still is (urls.py, other apps, tests, management commands).
# Module map: see STATUS_PC4_CAMPUS_VIEWS.md. Do NOT add logic here; add it to the matching submodule and re-export it below.

# campus/views.py
import logging
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, DecimalField, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

# FEE-2: fee is now paid out of the same wallet tuitionclass already uses,
# not a separate gateway — user_profile owns CoinLedger/record_transaction,
# campus only ever calls through it, never writes to CoinLedger directly
# (same boundary user_profile/models.py's own CoinLedger docstring lays out).
from user_profile.models import CoinLedger

# [Task 11] `assigmentsViewSet`/`assigmentsSubmissionViewSet` below are now
# thin proxies over the unified `assigments` app (see `campus.assigments`'s
# own [DEPRECATED] docstring in models.py) rather than campus's own
# deprecated assigments/assigmentsSubmission models — imported directly at
# module level, same reasoning as `campus.bridge`'s own Task 11 addition
# docstring gives: `assigments` is a confirmed, fully-built sibling app,
# not an unverified dependency that needs a lazy-import degrade. Aliased
# ("Unified...") so a reader never confuses these with campus's own,
# now-deprecated `assigments`/`assigmentsSubmission` models — this file no
# longer imports those at all, since nothing here touches them any more;
# the one remaining reader of the old rows is the one-time
# `migrate_campus_assigmentss_to_unified` management command.
from assignments.models import assigments as Unifiedassigments
from assignments.models import assigmentsSource as UnifiedassigmentsSource
from assignments.models import assigmentsSubmission as UnifiedassigmentsSubmission

# [Task 13] Same posture as the `assigments` imports directly above:
# `testseries` is a confirmed, fully-built sibling app for this task
# (its own `testseries/bridge.py` module docstring documents this exact
# `source="campus"` calling contract), not an unverified dependency —
# hard top-level import, no lazy-import degrade.
from testseries.models import TestAttempt, TestSeries

# (notice push now goes through bridge.notify(push=True) in NoticeViewSet.perform_create.)
# FIX (this pass) — ParentLinkVerifyView now resolves directly against
# ParentAccessCode instead of the broken bridge.resolve_parent_from_token
# unpack; see that view's docstring below.
from message.models import ParentAccessCode
# Task 5 subtask 3 — same fire-and-forget dispatch bridge.notify already
# uses for push, reused here for FeePaymentViewSet's receipt-email side
# effect (see campus/tasks.py::send_fee_receipt_email).
from core.async_utils import dispatch_after_commit

from .. import bridge
from .. import notice_rules
from .. import notices as notices_service
from .. import attendance_edit
from .. import audit_query
from .. import fee_gateway
from .. import bulk_ops
from .. import grading
from .. import live_class
from .. import recordings
from .. import results_csv
from .. import tasks as campus_tasks
from ..bridge import NotifTypes
from ..receipt_pdf import PdfUnavailable, render_pdf
from .. import finance, finance_rules
from ..finance_views import need as finance_need
from ..invoice_pdf import render_invoice_pdf
from ..report_card_pdf import render_report_card_pdf
from .. import progress as progress_data
from ..receipt_pdf import _display_name as _receipt_display_name
from ..services import compute_attendance_summary, generate_report_card_data
# [T4 §A-§D] participants / control-panel / roster / audit
from .. import panel as campus_panel
from .. import dashboard as campus_dashboard
from .. import year_end
from .. import participants as campus_participants
from .. import roster as campus_roster
from ..audit import AuditedModelViewSetMixin, log_action
from ..soft_delete_views import SoftDeleteViewSetMixin
from ..models import CampusAuditLog
from ..roster import RosterError
# B-4 fix — see campus/throttles.py module docstring for why these are
# separate classes (each with its own fixed `scope`) rather than a
# shared `throttle_scope` attribute: several of them apply to different
# @action methods living on the same ViewSet.
from ..throttles import (
    CampusDoubtPostThrottle,
    CampusFeePaymentThrottle,
    CampusLiveSessionJoinThrottle,
    CampusEmergencyBroadcastThrottle,
    CampusNoticePostThrottle,
    CampusParentLinkVerifyThrottle,
    CampusRecordingControlThrottle,
    CampusReportExportThrottle,
    CampusResultsImportThrottle,
    CampusResultsPublishThrottle,
)
from ..models import (
    AcademicSession,
    Attendance,
    AttendanceEditLog,
    Campus,
    CampusAnalyticsSnapshot,
    CampusDoubt,
    CampusDoubtReply,
    CampusLiveSession,
    CampusParentLink,
    ClassMode,
    ClassTeacherassigments,
    Department,
    DigitalIDCard,
    ExamTerm,
    FeeInvoice,
    FeePayment,
    FeeStructure,
    Notice,
    ResultEntry,
    Room,
    SchoolClass,
    Section,
    StaffProfile,
    StudentEnrollment,
    Subject,
    SubjectTeacherassigments,
    SubstituteAssignment,
    SyllabusProgress,
    SyllabusUnit,
    TimeSlot,
    TimetableEntry,
)
from ..permissions import (
    IsCampusAdminOrPrincipal,
    IsPlatformAdmin,
    IsRosterManagerOrReadOnly,
    IsSectionSubjectStaffOrReadOnly,
    ROLE_ACTIONS,
    get_campus_role,
    hod_department_id,
    user_can,
    can_manage_section_subject,
    can_post_notice,
    is_any_active_staff,
    is_campus_admin_or_principal,
    is_campus_approved,
    is_class_teacher_of_section,
    is_linked_parent_of_student,
)
from ..serializers import (
    AcademicSessionSerializer,
    AttendanceSerializer,
    CampusAnalyticsSnapshotSerializer,
    CampusDoubtDetailSerializer,
    CampusDoubtReplySerializer,
    CampusDoubtSerializer,
    CampusLiveSessionSerializer,
    CampusParentLinkSerializer,
    CampusSerializer,
    ClassTeacherassigmentsSerializer,
    DepartmentSerializer,
    DigitalIDCardSerializer,
    ExamTermSerializer,
    FeeInvoiceSerializer,
    FeePaymentSerializer,
    FeeStructureSerializer,
    NoticeSerializer,
    ResultEntrySerializer,
    RoomSerializer,
    SchoolClassSerializer,
    SectionSerializer,
    StaffProfileSerializer,
    SyllabusProgressSerializer,
    SyllabusUnitSerializer,
    TimeSlotSerializer,
    TimetableConflictCheckSerializer,
    TimetableEntrySerializer,
    StudentEnrollmentSerializer,
    SubjectSerializer,
    SubjectTeacherassigmentsSerializer,
)

from .. import visibility as vis_mod  # noqa: E402
from ..visibility import accessible_section_ids_and_campus_map, get_visibility  # noqa: E402

# ---- re-exports (explicit; private helpers included on purpose) ----
from ._common import (  # noqa: E402,F401
    logger,
    _truthy,
    RosterAPIError,
    get_my_campus_ids,
    CampusMemberScopedMixin,
)
from .campus_viewset import (  # noqa: E402,F401
    CampusViewSet,
)
from .academics import (  # noqa: E402,F401
    AcademicSessionViewSet,
    DepartmentViewSet,
    SchoolClassViewSet,
    SectionViewSet,
    SubjectViewSet,
    RoomViewSet,
    StaffProfileViewSet,
    ClassTeacherassigmentsViewSet,
    SubjectTeacherassigmentsViewSet,
)
from .enrollment import (  # noqa: E402,F401
    StudentEnrollmentViewSet,
)
from .notices import (  # noqa: E402,F401
    NoticeAPIError,
    NoticeViewSet,
)
from .live_sessions import (  # noqa: E402,F401
    CampusLiveSessionViewSet,
)
from .timetable import (  # noqa: E402,F401
    TimeSlotViewSet,
    TimetableEntryViewSet,
)
from .attendance import (  # noqa: E402,F401
    AttendanceViewSet,
)
from .campus_assignments import (  # noqa: E402,F401
    _my_section_ids_and_campus_map,
    _section_for_assigments,
    _UNSET,
    _batch_assigment_context,
    _paginate_if_requested,
    _serialize_campus_assigments,
    _serialize_campus_submission,
    assigmentsViewSet,
    assigmentsSubmissionViewSet,
)
from .campus_testseries import (  # noqa: E402,F401
    _section_for_testseries,
    _serialize_campus_testseries,
    TestSeriesViewSet,
    _serialize_campus_attempt,
    TestAttemptViewSet,
)
from .syllabus import (  # noqa: E402,F401
    SyllabusUnitViewSet,
    SyllabusProgressViewSet,
)
from .results import (  # noqa: E402,F401
    ExamTermViewSet,
    _publish_flag,
    ResultEntryViewSet,
)
from .idcards import (  # noqa: E402,F401
    DigitalIDCardViewSet,
)
from .fees import (  # noqa: E402,F401
    _require_fee_module_enabled,
    FeeStructureViewSet,
    FeeInvoiceViewSet,
    FeePaymentViewSet,
)
from .parents import (  # noqa: E402,F401
    CampusAnalyticsSnapshotViewSet,
    CampusParentLinkViewSet,
    ParentLinkVerifyView,
)
from .my_classes import (  # noqa: E402,F401
    MyClassesView,
)
from .doubts import (  # noqa: E402,F401
    CampusDoubtViewSet,
)
