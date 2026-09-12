# campus/urls.py
from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    AcademicSessionViewSet,
    AssignmentSubmissionViewSet,
    AssignmentViewSet,
    AttendanceViewSet,
    CampusAnalyticsSnapshotViewSet,
    CampusLiveSessionViewSet,
    CampusParentLinkViewSet,
    CampusViewSet,
    ClassTeacherAssignmentViewSet,
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
    SubjectTeacherAssignmentViewSet,
    SubjectViewSet,
    SyllabusProgressViewSet,
    SyllabusUnitViewSet,
    TimeSlotViewSet,
    TimetableEntryViewSet,
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

# Phase 2 — assignments & enrollment
router.register("class-teacher-assignments", ClassTeacherAssignmentViewSet, basename="class-teacher-assignment")
router.register("subject-teacher-assignments", SubjectTeacherAssignmentViewSet, basename="subject-teacher-assignment")
router.register("enrollments", StudentEnrollmentViewSet, basename="student-enrollment")
router.register("parent-links", CampusParentLinkViewSet, basename="campus-parent-link")

# Phase 3 — notices
router.register("notices", NoticeViewSet, basename="notice")

# Phase 4 — live classes
router.register("live-sessions", CampusLiveSessionViewSet, basename="campus-live-session")

# Phase 5 — timetable & attendance
router.register("time-slots", TimeSlotViewSet, basename="time-slot")
router.register("timetable-entries", TimetableEntryViewSet, basename="timetable-entry")
router.register("attendance", AttendanceViewSet, basename="attendance")

# Phase 6 — assignments & syllabus
router.register("assignments", AssignmentViewSet, basename="assignment")
router.register("assignment-submissions", AssignmentSubmissionViewSet, basename="assignment-submission")
router.register("syllabus-units", SyllabusUnitViewSet, basename="syllabus-unit")
router.register("syllabus-progress", SyllabusProgressViewSet, basename="syllabus-progress")

# Phase 7 — results
router.register("exam-terms", ExamTermViewSet, basename="exam-term")
router.register("results", ResultEntryViewSet, basename="result-entry")

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
] + router.urls