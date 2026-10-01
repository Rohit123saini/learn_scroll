# campus/urls.py
from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    AcademicSessionViewSet,
    assigmentsSubmissionViewSet,
    assigmentsViewSet,
    AttendanceViewSet,
    CampusAnalyticsSnapshotViewSet,
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
router.register("class-teacher-assigmentss", ClassTeacherassigmentsViewSet, basename="class-teacher-assigments")
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
router.register("analytics-snapshots", CampusAnalyticsSnapshotViewSet, basename="campus-analytics-snapshot")

urlpatterns = [
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