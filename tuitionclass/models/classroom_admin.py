# tuitionclass/models/classroom_admin.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

from login.models import User
from django.db import models
from .schedules import ClassSession
from .classroom import Classroom


# ---------------------------------------------------------------------------
# 12B. CLASSROOM BAN (persistent, classroom-wide — distinct from
#      SessionParticipant.kicked_at, which only blocks re-entry to the ONE
#      session a moderator kicked someone from)
#
# GAP THIS CLOSES: ClassSessionViewSet.kick() (views.py) already stops a
# disruptive participant re-joining the SAME session, but nothing stopped
# them from joining the NEXT session of the same classroom, raising a fresh
# ClassJoinRequest after a refund, or continuing to sit in chat/materials/
# doubts if they still held an active pass. A teacher who wants someone
# permanently off their classroom (not just out of one session) had no way
# to do that short of manually refusing every future join request by hand.
# ---------------------------------------------------------------------------
class ClassroomBan(models.Model):
    """A teacher/co-teacher/moderator has permanently banned this student
    from this classroom. Checked by:
        - ClassJoinRequestViewSet.perform_create (views.py) — a banned
          student can't raise a new join request.
        - _perform_join (views.py) — a banned student can't join a live
          session even if they somehow still hold a valid pass.
        - Classroom.has_access() (below) — a banned student loses room
          access outright, same tier as an expired pass.

    Creating a ban (see ClassroomViewSet.ban action) also best-effort kicks
    the student from any session they're currently live in, cancels any
    pending join request, and reverses/refunds any active PassPurchase for
    this classroom — a ban should mean "gone", not "still holds a paid
    pass they can never use again"."""

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="bans")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="classroom_bans")
    banned_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="classroom_bans_issued"
    )
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("classroom", "student")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.student} banned from {self.classroom.title}"


# ---------------------------------------------------------------------------
# 14. CO-TEACHERS / TEACHING ASSISTANTS (multi-staff classrooms)
# ---------------------------------------------------------------------------
class ClassroomStaff(models.Model):
    class Role(models.TextChoices):
        CO_TEACHER = "co_teacher", "Co-Teacher"
        MODERATOR = "moderator", "Moderator"
        TA = "ta", "Teaching Assistant"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="staff")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="staff_roles")
    role = models.CharField(max_length=15, choices=Role.choices, default=Role.TA)

    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("classroom", "user")

    def __str__(self):
        return f"{self.user} - {self.get_role_display()} @ {self.classroom.title}"


# ---------------------------------------------------------------------------
# 15. WAITLIST (session hits max_participants)
# ---------------------------------------------------------------------------
class SessionWaitlist(models.Model):
    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="waitlist")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="waitlisted_sessions")

    joined_at = models.DateTimeField(auto_now_add=True)
    notified = models.BooleanField(default=False)  # notified when a seat opens up

    class Meta:
        unique_together = ("session", "student")
        ordering = ["joined_at"]  # first-come-first-served

    def __str__(self):
        return f"{self.student} waiting for {self.session}"


# ---------------------------------------------------------------------------
# 15B. CLASSROOM REPORTS (student flags a classroom — scam, abandoned,
#      inappropriate, etc. — for platform staff to review)
# ---------------------------------------------------------------------------
class ClassroomReport(models.Model):
    """A user-filed report against a classroom. This is the other half of
    the anti-abuse fix alongside Classroom.can_be_deleted()/
    ClassroomViewSet.close(): even before a fraudulent teacher tries to
    delete anything, students who feel scammed (class never happened,
    teacher stopped responding, etc.) have somewhere to flag it, and
    enough pending reports auto-hides the classroom from Explore (see
    _auto_flag_classroom below) while staff look into it."""

    class Reason(models.TextChoices):
        SCAM = "scam", "Scam / took coins, no class"
        NOT_DELIVERING = "not_delivering", "Classes not happening as promised"
        INAPPROPRIATE = "inappropriate", "Inappropriate content or behaviour"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        REVIEWED = "reviewed", "Reviewed"
        ACTION_TAKEN = "action_taken", "Action Taken"
        DISMISSED = "dismissed", "Dismissed"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="reports")
    reported_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="classroom_reports_filed")

    reason = models.CharField(max_length=20, choices=Reason.choices)
    description = models.TextField(blank=True)

    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="classroom_reports_reviewed"
    )
    admin_note = models.CharField(max_length=255, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    # A pending report auto-flags the classroom once enough distinct users
    # have filed one — see _auto_flag_classroom below.
    AUTO_FLAG_THRESHOLD = 3

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["classroom", "status"])]
        constraints = [
            # Stop one user from spamming the same classroom with repeat
            # reports — one open report per (user, classroom) at a time.
            models.UniqueConstraint(
                fields=["classroom", "reported_by"],
                condition=models.Q(status="pending"),
                name="one_pending_report_per_user_per_classroom",
            )
        ]

    def __str__(self):
        return f"Report on {self.classroom.title} by {self.reported_by} ({self.get_status_display()})"
