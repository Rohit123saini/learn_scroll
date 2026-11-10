# Class resources (content share): subject-wise folders + PDF / video / link
# resources + per-user open tracking. Hand-written to match
# campus.models.ResourceFolder / ClassResource / ClassResourceView exactly
# (explicit index + constraint names, so `makemigrations --check` shows no drift).
import campus.resource_rules
import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("campus", "0010_staff_leave_rules"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ResourceFolder",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=120)),
                ("order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="resource_folders", to="campus.campus")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="campus_resource_folders_created", to=settings.AUTH_USER_MODEL)),
                ("parent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="children", to="campus.resourcefolder")),
                ("section", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="resource_folders", to="campus.section")),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="resource_folders", to="campus.academicsession")),
                ("subject", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="resource_folders", to="campus.subject")),
            ],
            options={
                "ordering": ["order", "name"],
                "indexes": [
                    models.Index(fields=["section", "subject", "is_active"], name="res_folder_sec_subj_idx"),
                    models.Index(fields=["parent", "is_active"], name="res_folder_parent_idx"),
                    models.Index(fields=["campus", "session"], name="res_folder_campus_sess_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(condition=models.Q(("is_active", True), ("parent__isnull", True)), fields=("section", "subject", "name"), name="res_folder_unique_active_root"),
                    models.UniqueConstraint(condition=models.Q(("is_active", True), ("parent__isnull", False)), fields=("parent", "name"), name="res_folder_unique_active_child"),
                    models.CheckConstraint(condition=models.Q(("parent", models.F("id")), _negated=True), name="res_folder_not_own_parent"),
                ],
            },
        ),
        migrations.CreateModel(
            name="ClassResource",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("pdf", "PDF"), ("video", "Video"), ("document", "Document"), ("image", "Image"), ("link", "Link")], db_index=True, max_length=10)),
                ("title", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True)),
                ("file", models.FileField(blank=True, upload_to="campus/resources/%Y/%m/", validators=[django.core.validators.FileExtensionValidator(allowed_extensions=["pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "txt", "jpg", "jpeg", "png", "webp", "mp4", "m4v", "mov", "webm"]), campus.resource_rules.validate_resource_size])),
                ("external_url", models.URLField(blank=True, max_length=500, validators=[django.core.validators.URLValidator(schemes=["http", "https"])])),
                ("thumbnail", models.FileField(blank=True, upload_to="campus/resources/thumbs/%Y/%m/", validators=[django.core.validators.FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png", "webp"]), campus.resource_rules.validate_thumbnail_size])),
                ("original_name", models.CharField(blank=True, max_length=255)),
                ("file_size", models.PositiveBigIntegerField(default=0)),
                ("duration_seconds", models.PositiveIntegerField(blank=True, null=True)),
                ("is_published", models.BooleanField(default=True)),
                ("is_pinned", models.BooleanField(default=False)),
                ("order", models.PositiveIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="class_resources", to="campus.campus")),
                ("folder", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="resources", to="campus.resourcefolder")),
                ("posted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="campus_class_resources_posted", to=settings.AUTH_USER_MODEL)),
                ("section", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="class_resources", to="campus.section")),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="class_resources", to="campus.academicsession")),
                ("subject", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="class_resources", to="campus.subject")),
            ],
            options={
                "ordering": ["-is_pinned", "order", "-created_at"],
                "indexes": [
                    models.Index(fields=["folder", "is_active", "-created_at"], name="res_folder_active_idx"),
                    models.Index(fields=["section", "subject", "-created_at"], name="res_sec_subj_idx"),
                    models.Index(fields=["campus", "session", "-created_at"], name="res_campus_sess_idx"),
                ],
                "constraints": [
                    models.CheckConstraint(condition=models.Q(models.Q(("file", ""), _negated=True), ("external_url", ""), _connector="AND") | models.Q(("file", ""), models.Q(("external_url", ""), _negated=True), _connector="AND"), name="class_resource_file_xor_link"),
                    models.CheckConstraint(condition=models.Q(("kind", "link"), _negated=True) | models.Q(("external_url", ""), _negated=True), name="class_resource_link_has_url"),
                ],
            },
        ),
        migrations.CreateModel(
            name="ClassResourceView",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("view_count", models.PositiveIntegerField(default=1)),
                ("first_viewed_at", models.DateTimeField(auto_now_add=True)),
                ("last_viewed_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("resource", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="views", to="campus.classresource")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="campus_resource_views", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-last_viewed_at"],
                "indexes": [models.Index(fields=["resource", "-last_viewed_at"], name="res_view_resource_idx")],
                "constraints": [models.UniqueConstraint(fields=("resource", "user"), name="res_view_unique_per_user")],
            },
        ),
    ]
