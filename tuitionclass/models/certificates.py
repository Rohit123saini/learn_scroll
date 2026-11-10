# tuitionclass/models/certificates.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

import uuid
from django.core.validators import FileExtensionValidator
from login.models import User
from django.db import models
from .classroom import Classroom
from .validators import MaxFileSizeValidator


# ---------------------------------------------------------------------------
# 16. CERTIFICATES (issued on course/classroom completion)
# ---------------------------------------------------------------------------
class Certificate(models.Model):
    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="certificates")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="certificates")

    certificate_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    certificate_file = models.FileField(
        upload_to="certificates/", null=True, blank=True,
        validators=[MaxFileSizeValidator(10), FileExtensionValidator(["pdf"])],
    )
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("classroom", "student")

    def __str__(self):
        return f"Certificate: {self.student} - {self.classroom.title}"


# ---------------------------------------------------------------------------
# 16b. STUDENT REPORT CARD
#
# GAP FIX — message/views_parent.py already imports and uses
# `StudentReportCard as TuitionclassStudentReportCard` (parent dashboard
# "latest report card" block: period_label, attendance_percent,
# homework_completion_percent, average_marks, teacher_remark), but the
# model itself was never defined here — that import was failing with
# ImportError at Django startup. Shape below matches exactly what
# StudentReportCardList._build_results() in views_parent.py reads off
# each row; `.order_by('classroom_id', '-id')` there relies on the
# default auto `id` pk, so no custom ordering is imposed beyond
# newest-first for direct queries against this model on its own.
# ---------------------------------------------------------------------------
class StudentReportCard(models.Model):
    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="report_cards")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="report_cards")

    # [FIX] StudentReportCardSerializer (tuitionclass/serializers.py) has
    # always listed `created_by` in both `fields` and `read_only_fields`
    # — its own comment there says it's server-set, same as
    # LivePoll.created_by / Coupon.created_by / ClassHoliday.created_by
    # elsewhere in this file — but this model never actually had the
    # column, which is what crashed drf-spectacular's schema generation
    # (`ImproperlyConfigured: Field name 'created_by' is not valid for
    # model 'StudentReportCard'`). Not a serializer bug to work around;
    # this field was simply missed when the model itself was added late
    # (see this model's own GAP FIX comment above — it was added only to
    # unblock message/views_parent.py's import).
    #
    # NOT copied verbatim from LivePoll/Coupon/ClassHoliday's
    # `created_by` (those are `on_delete=CASCADE`, required) — this
    # table already has existing rows (confirmed by `makemigrations`
    # refusing a non-nullable AddField without a default), and there's
    # no way to know who actually created any of them after the fact.
    # Backfilling with a fake default (e.g. some placeholder admin user)
    # would be worse than null — it would silently attribute old report
    # cards to the wrong person. So this is nullable + SET_NULL, same
    # reasoning `reviewed_by`/`banned_by`-style fields elsewhere use for
    # "optional, and a deleted user shouldn't cascade-delete history"
    # rather than a hard requirement from day one. New rows going
    # forward should still always get this set —
    # `parent_link_views.ReportCardViewSet.perform_create()` is expected
    # to pass `created_by=self.request.user`, same as
    # `ClassHolidayViewSet.perform_create()` already does for
    # `ClassHoliday.created_by` — null here is a backfill accommodation
    # for old rows, not a green light to skip setting it on new ones.
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="report_cards_created",
    )

    # e.g. "October 2026", "Term 1", "Week 12" — teacher-defined free text,
    # not a structured date range, since reporting cadence varies per
    # classroom/subject.
    period_label = models.CharField(max_length=100)

    attendance_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    homework_completion_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    average_marks = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    teacher_remark = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # Matches the exact query shape in views_parent.py:
            # filter(classroom_id__in=..., student=...).order_by('classroom_id', '-id')
            models.Index(fields=["classroom", "student"]),
        ]

    def __str__(self):
        return f"Report card: {self.student} - {self.classroom.title} ({self.period_label})"
