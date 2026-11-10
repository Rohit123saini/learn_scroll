# tuitionclass/models/homework.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

from django.core.validators import FileExtensionValidator
from login.models import User
from django.db import models
from .schedules import ClassSession
from .classroom import Classroom
from .validators import (
    DOCUMENT_MEDIA_EXTENSIONS,
    MaxFileSizeValidator,
)


# ---------------------------------------------------------------------------
# 10. assigmentsS / HOMEWORK
# ---------------------------------------------------------------------------
class assigments(models.Model):
    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="assigmentss")
    session = models.ForeignKey(
        ClassSession, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigmentss"
    )

    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    attachment = models.FileField(
        upload_to="assigmentss/", null=True, blank=True,
        validators=[MaxFileSizeValidator(50), FileExtensionValidator(DOCUMENT_MEDIA_EXTENSIONS)],
    )

    due_date = models.DateTimeField()
    max_score = models.PositiveIntegerField(default=100)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due_date"]

    def __str__(self):
        return f"{self.title} ({self.classroom.title})"


class assigmentsSubmission(models.Model):
    assigments = models.ForeignKey(assigments, on_delete=models.CASCADE, related_name="submissions")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tuitionclass_assigments_submissions")

    file = models.FileField(
        upload_to="assigments_submissions/",
        validators=[MaxFileSizeValidator(50), FileExtensionValidator(DOCUMENT_MEDIA_EXTENSIONS)],
    )
    submitted_at = models.DateTimeField(auto_now_add=True)

    score = models.PositiveIntegerField(null=True, blank=True)
    feedback = models.TextField(blank=True)
    graded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("assigments", "student")
        ordering = ["-submitted_at"]

    def is_late(self) -> bool:
        return self.submitted_at > self.assigments.due_date

    def __str__(self):
        return f"{self.student} -> {self.assigments}"
