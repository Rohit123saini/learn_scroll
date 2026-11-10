# tuitionclass/models/materials.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

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
# 7. CLASS MATERIALS (notes, PPT/PDF, links shared by teacher)
# ---------------------------------------------------------------------------
class ClassMaterial(models.Model):
    class MaterialType(models.TextChoices):
        PDF = "pdf", "PDF"
        PPT = "ppt", "Presentation"
        DOC = "doc", "Document"
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        LINK = "link", "External Link"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="materials")
    session = models.ForeignKey(
        ClassSession, on_delete=models.SET_NULL, null=True, blank=True, related_name="materials"
    )
    uploaded_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="uploaded_materials")

    title = models.CharField(max_length=150)
    material_type = models.CharField(max_length=10, choices=MaterialType.choices)
    file = models.FileField(
        upload_to="class_materials/", null=True, blank=True,
        validators=[MaxFileSizeValidator(100), FileExtensionValidator(DOCUMENT_MEDIA_EXTENSIONS)],
    )
    external_link = models.URLField(blank=True)

    # Live-room "Present" for ppt/doc: the server converts the file to PDF once (office_convert.py) and caches it here.
    # `presentable_source_name` is the `file.name` the cached PDF was made from, so replacing the file re-converts.
    presentable_pdf = models.FileField(upload_to="class_materials/presentable/", null=True, blank=True)
    presentable_source_name = models.CharField(max_length=255, blank=True, default="")

    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.title} ({self.classroom.title})"
