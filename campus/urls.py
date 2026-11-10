# campus/urls.py
from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    AcademicSessionViewSet,
    assigmentsSubmissionViewSet,
    assigmentsViewSet,
    AttendanceViewSet,
    CampusAnalyticsSnapshotViewSet,
    CampusDoubtViewSet,
    CampusLiveSessionViewSet,
    CampusParentLinkViewSet,
    CampusViewSet,
    ClassTeacherassigmentsViewSet,
    DepartmentViewSet,
    DigitalIDCardViewSet,
    ExamTermViewSet,
    FeeInvoiceViewSet,
    FeePaymentViewSet,
    FeeStructureViewSet,
    MyClassesView,
    NoticeViewSet,
    ParentLinkVerifyView,
    ResultEntryViewSet,
    RoomViewSet,
    SchoolClassViewSet,
    SectionViewSet,
    StaffProfileViewSet,
    StudentEnrollmentViewSet,
    SubjectTeacherassigmentsViewSet,
    SubjectViewSet,
    SyllabusProgressViewSet,
    SyllabusUnitViewSet,
    TestAttemptViewSet,
    TestSeriesViewSet,
    TimeSlotViewSet,
    TimetableEntryViewSet,
)
from .parent_invite import (
    CampusParentInviteBulkView,
    CampusParentInviteSingleView,
    CampusParentLinkConfirmView,
)
from .fee_gateway import FeeRazorpayWebhookView
from .finance_views import (
    FeeConcessionGrantViewSet,
    FeeConcessionViewSet,
    FeeReconciliationItemViewSet,
    FeeReconciliationRunViewSet,
    FinanceExportView,
    FinanceSummaryView,
)
from .report_views import ReportCatalogView, ReportExportView
from .privacy_views import (
    PrivacyChildDetailView,
    PrivacyChildrenView,
    PrivacyMyChildrenView,
    PrivacyPolicyView,
    PrivacyRemindView,
    PrivacySummaryView,
)
from .safety_views import (
    BackupDetailView,
    BackupDownloadView,
    BackupListCreateView,
    BackupRestoreView,
    ConsentAdminRecordView,
    ConsentLogExportView,
    ConsentLogView,
    ConsentMeView,
    ConsentSummaryView,
    DeletionRequestActionView,
    DeletionRequestDetailView,
    DeletionRequestListCreateView,
    DeletionRequestPreviewView,
    SafetyOverviewView,
)
from .settings_views import (
    SettingsCalendarView,
    SettingsGradingView,
    SettingsHolidayListView,
    SettingsHolidayWriteView,
    SettingsLogoView,
    SettingsOverviewView,
    SettingsProfileView,
    SettingsSlotsActionView,
    SettingsSlotsView,
    SettingsWorkingDaysView,
)
from .admission_views import AdmissionViewSet
from .homework_diary import HomeworkDiaryViewSet
from .resources import ClassResourceViewSet, ResourceFolderViewSet
from .recording_views import ClassNoteViewSet, RecordingViewSet, StudentNoteViewSet
from .staff_leave_views import CampusHolidayViewSet, StaffLeaveViewSet
from .substitute_views import SubstituteAssignmentViewSet, TeacherAbsenceViewSet
from .ptm_views import PTMBookingViewSet, PTMEventViewSet, PTMSlotViewSet
from .parent_chat_views import (
    ParentChatContactsView,
    ParentChatThreadViewSet,
    ParentChatUnreadView,
    ParentCommsSettingsView,
)
from .parent_dashboard import ParentDashboardView
from .parent_weekly_views import ParentWeeklyView  # P-F14
from .pilot_kit import CampusPilotKitView
from .campus_invite import (
    CampusInviteCodeGenerateView,
    CampusInviteCodeListView,
    CampusInviteCodeRedeemView,
    CampusInviteCodeRevokeView,
)

router = DefaultRouter()

# Phase 1 — structural hierarchy
router.register("campuses", CampusViewSet, basename="campus")
router.register("sessions", AcademicSessionViewSet, basename="academic-session")
router.register("departments", DepartmentViewSet, basename="department")
router.register("classes", SchoolClassViewSet, basename="school-class")
router.register("sections", SectionViewSet, basename="section")
router.register("subjects", SubjectViewSet, basename="subject")
router.register("rooms", RoomViewSet, basename="room")
router.register("staff", StaffProfileViewSet, basename="staff-profile")

# Phase 2 — assigmentss & enrollment
# T0.5 — URL typo fix: canonical paths are now `class-teacher-assignments/` and
# `subject-teacher-assignments/` (spelling fixed). The OLD typo paths
# (`...-assigmentss/`) stay registered as DEPRECATED ALIASES for one release so already-
# shipped app builds keep working — delete the two "legacy alias" lines below after that
# release. Same viewsets; only `basename` differs (DRF needs unique route names), so
# `reverse("class-teacher-assigments-list")` etc. keep resolving to the legacy alias.
router.register("class-teacher-assignments", ClassTeacherassigmentsViewSet, basename="class-teacher-assignments")
router.register("subject-teacher-assignments", SubjectTeacherassigmentsViewSet, basename="subject-teacher-assignments")
# legacy alias (deprecated, remove after one release):
router.register("class-teacher-assigmentss", ClassTeacherassigmentsViewSet, basename="class-teacher-assigments")
# legacy alias (deprecated, remove after one release):
router.register("subject-teacher-assigmentss", SubjectTeacherassigmentsViewSet, basename="subject-teacher-assigments")
router.register("enrollments", StudentEnrollmentViewSet, basename="student-enrollment")
router.register("parent-links", CampusParentLinkViewSet, basename="campus-parent-link")

# Phase 3 — notices
router.register("notices", NoticeViewSet, basename="notice")

# Phase 4 — tuition classes
router.register("live-sessions", CampusLiveSessionViewSet, basename="campus-live-session")

# Phase 5 — timetable & attendance
router.register("time-slots", TimeSlotViewSet, basename="time-slot")
router.register("timetable-entries", TimetableEntryViewSet, basename="timetable-entry")
router.register("attendance", AttendanceViewSet, basename="attendance")

# Phase 6 — assigmentss & syllabus
router.register("assigmentss", assigmentsViewSet, basename="assigments")
router.register("assigments-submissions", assigmentsSubmissionViewSet, basename="assigments-submission")
router.register("syllabus-units", SyllabusUnitViewSet, basename="syllabus-unit")
router.register("syllabus-progress", SyllabusProgressViewSet, basename="syllabus-progress")

# Homework Diary — daily, class-wise (see campus/homework_diary.py)
router.register("homework-diary", HomeworkDiaryViewSet, basename="campus-homework-diary")

# Class resources — subject-wise folders of PDF / video / links (see campus/resources.py)
router.register("resource-folders", ResourceFolderViewSet, basename="campus-resource-folder")
router.register("class-resources", ClassResourceViewSet, basename="campus-class-resource")

# Recorded classes + class notes + private student notes (see campus/recording_views.py)
router.register("recordings", RecordingViewSet, basename="campus-recording")
router.register("class-notes", ClassNoteViewSet, basename="campus-class-note")
router.register("my-notes", StudentNoteViewSet, basename="campus-my-note")

# Substitute teacher — absent teacher -> per-period substitute (see campus/substitutes.py)
router.register("teacher-absences", TeacherAbsenceViewSet, basename="campus-teacher-absence")
router.register("substitutes", SubstituteAssignmentViewSet, basename="campus-substitute")

# Staff leave — staff apply, admin/principal approve (see campus/staff_leave.py).
# Approving records a TeacherAbsence, so it feeds the substitute flow above.
router.register("staff-leaves", StaffLeaveViewSet, basename="campus-staff-leave")
# Holiday calendar — closed days never count as leave days.
router.register("holidays", CampusHolidayViewSet, basename="campus-holiday")

# Parents se baat — PTM scheduling + rules-based teacher<->parent chat
# (see campus/ptm.py, campus/parent_chat.py, campus/parent_comms.py)
router.register("ptm/events", PTMEventViewSet, basename="campus-ptm-event")
router.register("ptm/slots", PTMSlotViewSet, basename="campus-ptm-slot")
router.register("ptm/bookings", PTMBookingViewSet, basename="campus-ptm-booking")
router.register("parent-chat/threads", ParentChatThreadViewSet, basename="campus-parent-chat-thread")

# Admission — enquiry -> application -> interview -> offer -> enrolled (see campus/admission.py)
router.register("admissions", AdmissionViewSet, basename="campus-admission")

# Phase 7 — results
router.register("exam-terms", ExamTermViewSet, basename="exam-term")
router.register("results", ResultEntryViewSet, basename="result-entry")

# Task 13 — test series (thin proxy over the unified `testseries` app,
# same "campus" source posture `assigmentss`/`assigments-submissions`
# above already take toward the unified `assigments` app)
router.register("test-series", TestSeriesViewSet, basename="campus-test-series")
router.register("test-attempts", TestAttemptViewSet, basename="campus-test-attempt")

# Phase 8 — optional / future-ready modules
router.register("digital-id-cards", DigitalIDCardViewSet, basename="digital-id-card")
router.register("fee-structures", FeeStructureViewSet, basename="fee-structure")
router.register("fee-invoices", FeeInvoiceViewSet, basename="fee-invoice")
router.register("fee-payments", FeePaymentViewSet, basename="fee-payment")
# FINANCE — concessions, per-student grants, gateway reconciliation
router.register("fee-concessions", FeeConcessionViewSet, basename="fee-concession")
router.register("fee-concession-grants", FeeConcessionGrantViewSet, basename="fee-concession-grant")
router.register("finance/reconciliation/runs", FeeReconciliationRunViewSet, basename="finance-recon-run")
router.register("finance/reconciliation/items", FeeReconciliationItemViewSet, basename="finance-recon-item")
# T4 §F — doubts
router.register("doubts", CampusDoubtViewSet, basename="campus-doubt")

router.register("analytics-snapshots", CampusAnalyticsSnapshotViewSet, basename="campus-analytics-snapshot")

urlpatterns = [
    # FINANCE — summary + exports (plain APIViews; before router.urls)
    path("finance/summary/", FinanceSummaryView.as_view(), name="campus-finance-summary"),
    path("finance/export/", FinanceExportView.as_view(), name="campus-finance-export"),
    # REPORTS / EXPORT — one menu for attendance / marks / fees (CSV, Excel, PDF)
    path("reports/catalog/", ReportCatalogView.as_view(), name="campus-report-catalog"),
    path("reports/export/", ReportExportView.as_view(), name="campus-report-export"),
    # SAFETY — backup / restore, data delete requests, consent log (see campus/safety_views.py). The audit log
    # filter / search / export stay on `campuses/<id>/audit-log/...`.
    path("safety/<str:campus_id>/overview/", SafetyOverviewView.as_view(), name="campus-safety-overview"),
    path("safety/<str:campus_id>/backups/", BackupListCreateView.as_view(), name="campus-safety-backups"),
    path("safety/<str:campus_id>/backups/<str:backup_id>/", BackupDetailView.as_view(), name="campus-safety-backup"),
    path("safety/<str:campus_id>/backups/<str:backup_id>/download/", BackupDownloadView.as_view(),
         name="campus-safety-backup-download"),
    path("safety/<str:campus_id>/backups/<str:backup_id>/restore/", BackupRestoreView.as_view(),
         name="campus-safety-backup-restore"),
    path("safety/<str:campus_id>/deletion-requests/", DeletionRequestListCreateView.as_view(),
         name="campus-safety-deletion-requests"),
    path("safety/<str:campus_id>/deletion-requests/<str:request_id>/", DeletionRequestDetailView.as_view(),
         name="campus-safety-deletion-request"),
    path("safety/<str:campus_id>/deletion-requests/<str:request_id>/preview/", DeletionRequestPreviewView.as_view(),
         name="campus-safety-deletion-preview"),
    path("safety/<str:campus_id>/deletion-requests/<str:request_id>/approve/",
         DeletionRequestActionView.as_view(action="approve"), name="campus-safety-deletion-approve"),
    path("safety/<str:campus_id>/deletion-requests/<str:request_id>/reject/",
         DeletionRequestActionView.as_view(action="reject"), name="campus-safety-deletion-reject"),
    path("safety/<str:campus_id>/deletion-requests/<str:request_id>/execute/",
         DeletionRequestActionView.as_view(action="execute"), name="campus-safety-deletion-execute"),
    path("safety/<str:campus_id>/deletion-requests/<str:request_id>/cancel/",
         DeletionRequestActionView.as_view(action="cancel"), name="campus-safety-deletion-cancel"),
    path("safety/<str:campus_id>/consent/me/", ConsentMeView.as_view(), name="campus-safety-consent-me"),
    path("safety/<str:campus_id>/consent/log/", ConsentLogView.as_view(), name="campus-safety-consent-log"),
    path("safety/<str:campus_id>/consent/log/export/", ConsentLogExportView.as_view(),
         name="campus-safety-consent-export"),
    path("safety/<str:campus_id>/consent/summary/", ConsentSummaryView.as_view(), name="campus-safety-consent-summary"),
    path("safety/<str:campus_id>/consent/record/", ConsentAdminRecordView.as_view(), name="campus-safety-consent-record"),
    # PARENT / CHILD PRIVACY — minors' consent compliance, per-child consent detail, parent view (campus/privacy_views.py).
    path("privacy/<str:campus_id>/policy/", PrivacyPolicyView.as_view(), name="campus-privacy-policy"),
    path("privacy/<str:campus_id>/summary/", PrivacySummaryView.as_view(), name="campus-privacy-summary"),
    path("privacy/<str:campus_id>/children/", PrivacyChildrenView.as_view(), name="campus-privacy-children"),
    path("privacy/<str:campus_id>/children/<str:student_id>/", PrivacyChildDetailView.as_view(), name="campus-privacy-child"),
    path("privacy/<str:campus_id>/remind/", PrivacyRemindView.as_view(), name="campus-privacy-remind"),
    path("privacy/<str:campus_id>/my-children/", PrivacyMyChildrenView.as_view(), name="campus-privacy-my-children"),
    # CAMPUS SETTINGS — profile / grading / working days / holidays / time slots (see campus/settings_views.py).
    path("settings/<str:campus_id>/overview/", SettingsOverviewView.as_view(), name="campus-settings-overview"),
    path("settings/<str:campus_id>/profile/", SettingsProfileView.as_view(), name="campus-settings-profile"),
    path("settings/<str:campus_id>/logo/", SettingsLogoView.as_view(), name="campus-settings-logo"),
    path("settings/<str:campus_id>/grading/", SettingsGradingView.as_view(), name="campus-settings-grading"),
    path("settings/<str:campus_id>/working-days/", SettingsWorkingDaysView.as_view(), name="campus-settings-working-days"),
    path("settings/<str:campus_id>/calendar/", SettingsCalendarView.as_view(), name="campus-settings-calendar"),
    path("settings/<str:campus_id>/holidays/", SettingsHolidayListView.as_view(), name="campus-settings-holidays"),
    path("settings/<str:campus_id>/holidays/range/", SettingsHolidayWriteView.as_view(kind="range"),
         name="campus-settings-holidays-range"),
    path("settings/<str:campus_id>/holidays/bulk/", SettingsHolidayWriteView.as_view(kind="bulk"),
         name="campus-settings-holidays-bulk"),
    path("settings/<str:campus_id>/holidays/copy-year/", SettingsHolidayWriteView.as_view(kind="copy-year"),
         name="campus-settings-holidays-copy-year"),
    path("settings/<str:campus_id>/holidays/national/", SettingsHolidayWriteView.as_view(kind="national"),
         name="campus-settings-holidays-national"),
    path("settings/<str:campus_id>/time-slots/", SettingsSlotsView.as_view(), name="campus-settings-time-slots"),
    path("settings/<str:campus_id>/time-slots/generate/", SettingsSlotsActionView.as_view(action_name="generate"),
         name="campus-settings-slots-generate"),
    path("settings/<str:campus_id>/time-slots/copy-day/", SettingsSlotsActionView.as_view(action_name="copy-day"),
         name="campus-settings-slots-copy-day"),
    path("settings/<str:campus_id>/time-slots/clear/", SettingsSlotsActionView.as_view(action_name="clear"),
         name="campus-settings-slots-clear"),
    # FEE-5 — Razorpay fee webhook. BEFORE router.urls: the router's "fee-payments/<pk>/" would
    # otherwise swallow "fee-payments/webhook/" as a pk.
    path("fee-payments/webhook/razorpay/", FeeRazorpayWebhookView.as_view(), name="campus-fee-razorpay-webhook"),
    # T4 §E/§G — student/parent subject-class cards (before router.urls).
    path("my/classes/", MyClassesView.as_view(), name="campus-my-classes"),
    # Parents se baat — non-router endpoints (contacts / badge numbers / rules)
    path("parent-chat/contacts/", ParentChatContactsView.as_view(), name="campus-parent-chat-contacts"),
    path("parent-chat/unread/", ParentChatUnreadView.as_view(), name="campus-parent-chat-unread"),
    path("parent-dashboard/", ParentDashboardView.as_view(), name="campus-parent-dashboard"),
    path("parent-weekly/", ParentWeeklyView.as_view(), name="campus-parent-weekly"),  # P-F14
    path("parent-comms/settings/", ParentCommsSettingsView.as_view(), name="campus-parent-comms-settings"),
    # Listed BEFORE router.urls deliberately: the router's own
    # "parent-links/<pk>/" detail pattern would otherwise greedily
    # match "parent-links/verify/" first (treating "verify" as a pk)
    # and return 405 on POST, since CampusParentLinkViewSet is
    # read-only. This is the ONLY write path for `CampusParentLink`
    # (see that model's + `ParentLinkVerifyView`'s docstrings) and it
    # isn't scoped to an existing parent-link object, so it doesn't
    # fit the router's list/detail shape anyway.
    path("parent-links/verify/", ParentLinkVerifyView.as_view(), name="campus-parent-link-verify"),

    # NEW — "add parent" automation (bulk / manual / confirm). See
    # campus/parent_invite.py's module docstring for why confirm/ is a
    # NEW endpoint rather than reusing parent-links/verify/ above (that
    # one has a pre-existing bug in how it resolves the token).
    path(
        "parent-invite/bulk/<str:campus_id>/",
        CampusParentInviteBulkView.as_view(),
        name="campus-parent-invite-bulk",
    ),
    path(
        "parent-invite/<str:campus_id>/<str:student_id>/",
        CampusParentInviteSingleView.as_view(),
        name="campus-parent-invite-single",
    ),
    path(
        "parent-link/confirm/",
        CampusParentLinkConfirmView.as_view(),
        name="campus-parent-link-confirm",
    ),

    # NEW — Task 13/G13: campus "family" network-effect invite codes.
    # (Same "listed before router.urls" reasoning as parent-link/verify/
    # above — none of these fit the router's list/detail shape, and
    # "invite-code/redeem/" in particular must never be shadowed by a
    # would-be router pattern.)
    path(
        "<str:campus_id>/sections/<str:section_id>/invite-code/",
        CampusInviteCodeGenerateView.as_view(),
        name="campus-invite-code-generate",
    ),
    path(
        "<str:campus_id>/invite-codes/",
        CampusInviteCodeListView.as_view(),
        name="campus-invite-code-list",
    ),
    path(
        "<str:campus_id>/pilot-kit/",
        CampusPilotKitView.as_view(),
        name="campus-pilot-kit",
    ),
    path(
        "invite-code/<str:code_id>/revoke/",
        CampusInviteCodeRevokeView.as_view(),
        name="campus-invite-code-revoke",
    ),
    path(
        "invite-code/redeem/",
        CampusInviteCodeRedeemView.as_view(),
        name="campus-invite-code-redeem",
    ),
] + router.urls