# tuitionclass/models/parent_messaging.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

import logging
from login.models import User
from django.db import models
from .schedules import ClassSession
from .classroom import Classroom


# ---------------------------------------------------------------------------
# 20B. PARENT -> TEACHER STRUCTURED-TEMPLATE MESSAGING (task 69)
#
# WHY THIS EXISTS: ClassQuery above already lets a student/parent send free
# text to a classroom's teacher, but that's exactly the surface a platform
# this size can't leave wide open for what's meant to be lightweight parent
# outreach (attendance check, fee query, leave intimation, a callback
# request) rather than an academic doubt/discussion thread. Free text there
# means: no floor on what a parent can type (spam, harassment, scraped
# links, personal-detail phishing dressed up as a "question"), no
# consistent way for a teacher juggling many classrooms/parents to triage
# what a message is even about, and no way for the platform to reason
# about volume/abuse patterns across message types.
#
# The fix is structural, not a content filter: a parent can ONLY select
# from a small, platform-curated set of ParentMessageTemplate rows
# (managed exclusively via the Django admin — see ParentMessageTemplateAdmin
# in admin.py; there is deliberately no create/update endpoint for
# templates anywhere in this app's public API) and, for templates that
# need it, ONE additional closed choice: a ClassSession FK constrained to
# that classroom's own sessions — never free text. ParentTeacherMessage
# resolves the chosen template into a snapshot (`resolved_message`) at
# send-time in ParentTeacherMessageViewSet.perform_create, so the actual
# wording a teacher receives is always one of the platform's own authored
# sentences — never anything a parent typed.
#
# The teacher's reply is deliberately NOT template-locked (`teacher_reply`
# is free text) — the teacher/co-teacher/moderator side is already gated by
# `_can_manage_classroom` (views.py), the same trusted-staff boundary
# ClassQuery.answer relies on, so the misuse concern this feature exists
# for doesn't apply to that side of the conversation.
# ---------------------------------------------------------------------------
class ParentMessageTemplate(models.Model):
    """A platform-curated message a parent/student can send to a
    classroom's teacher. Deliberately NOT parent/teacher-editable — see the
    module note above for why. `body_template` may reference the
    `{child_name}` and `{classroom_title}` placeholders, both filled in
    server-side from data the platform already trusts (the sender's own
    profile name, the classroom's own title) — never from anything typed
    in the request."""

    class Category(models.TextChoices):
        ATTENDANCE = "attendance", "Attendance"
        FEE_OR_PASS = "fee_or_pass", "Fee / Pass"
        LEAVE_REQUEST = "leave_request", "Leave request"
        TECHNICAL_ISSUE = "technical_issue", "Technical issue"
        CALLBACK_REQUEST = "callback_request", "Request a callback"
        FEEDBACK = "feedback", "Feedback"
        OTHER = "other", "Other"

    category = models.CharField(max_length=20, choices=Category.choices)
    title = models.CharField(
        max_length=120,
        help_text="Short label shown in the template picker, e.g. 'My child will be absent today'.",
    )
    body_template = models.TextField(
        help_text=(
            "The exact sentence sent to the teacher. May use {child_name} and "
            "{classroom_title} placeholders — both filled in server-side, never "
            "from user input."
        )
    )
    requires_session = models.BooleanField(
        default=False,
        help_text=(
            "If set, the sender must also pick one of the classroom's own sessions "
            "(e.g. 'About today's class') — a closed choice, never free text."
        ),
    )
    is_active = models.BooleanField(
        default=True,
        help_text=(
            "Retire a template without deleting it — past messages that used it keep "
            "their resolved_message snapshot either way (see ParentTeacherMessage.template, "
            "on_delete=PROTECT)."
        ),
    )
    display_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "category", "title"]

    def __str__(self):
        return f"[{self.get_category_display()}] {self.title}"

    def resolve(self, *, child_name: str, classroom_title: str) -> str:
        """Fill the two whitelisted placeholders. Falls back to the raw
        template rather than raising if a template author used an
        unsupported placeholder key — a typo in admin-authored copy should
        never break a parent's ability to send a message."""
        try:
            return self.body_template.format(child_name=child_name, classroom_title=classroom_title)
        except (KeyError, IndexError):
            logging.getLogger("tuitionclass.models").warning(
                "ParentMessageTemplate %s body_template has an unsupported placeholder; "
                "sending it unresolved.", self.pk,
            )
            return self.body_template


class ParentTeacherMessage(models.Model):
    """One structured message from an enrolled parent/student to a
    classroom's teacher, built entirely from a ParentMessageTemplate — see
    the module note above. Visibility/permission split mirrors ClassQuery:
    the sender only ever sees their own messages unless they manage the
    classroom (see ParentTeacherMessageViewSet.get_queryset)."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESPONDED = "responded", "Responded"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="parent_messages")
    session = models.ForeignKey(
        ClassSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="parent_messages",
        help_text="Set only when the chosen template has requires_session=True.",
    )
    template = models.ForeignKey(
        ParentMessageTemplate,
        on_delete=models.PROTECT,
        related_name="messages",
        help_text=(
            "PROTECT, not CASCADE/SET_NULL: a template must never be hard-deleted while "
            "any sent message still references it — retire it via is_active instead."
        ),
    )
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name="parent_messages_sent")

    # Snapshot of template.resolve(...) taken at send time (see
    # ParentTeacherMessageViewSet.perform_create) — never editable after
    # creation. This is the audit trail of what was ACTUALLY sent,
    # independent of any later edit to the template's own body_template in
    # admin. editable=False keeps it out of ModelForm/admin-form writes;
    # the DRF serializer separately marks it read_only.
    resolved_message = models.TextField(editable=False)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)

    teacher_reply = models.TextField(blank=True)
    replied_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="parent_messages_replied"
    )
    replied_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # ParentTeacherMessageViewSet.get_queryset's teacher-side
            # "?classroom=<id>" listing filters exactly this shape.
            models.Index(fields=["classroom", "status", "-created_at"]),
            # "my sent messages" listing (no classroom filter).
            models.Index(fields=["sender", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.sender} -> {self.classroom.title}: {self.template.title} ({self.get_status_display()})"
