# S12: TeacherLiveDefaults (teacher's defaults for new classrooms) + ParentChildLimit (parent-set daily limit on a child).
# Purely additive - existing rows change nothing.
# Hand-written (nothing was run): check with `python manage.py makemigrations --check --dry-run`.
import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("user_profile", "0019_wallet_settings_refund_method"),
    ]

    operations = [
        migrations.CreateModel(
            name="TeacherLiveDefaults",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("chat_enabled", models.BooleanField(default=True)),
                ("recording_enabled", models.BooleanField(default=True)),
                ("recording_auto", models.BooleanField(default=False)),
                ("whiteboard_enabled", models.BooleanField(default=True)),
                ("screen_share_enabled", models.BooleanField(default=True)),
                ("parents_enabled", models.BooleanField(default=False)),
                ("attendance_min_percent", models.PositiveSmallIntegerField(
                    default=50,
                    validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(100)])),
                ("attendance_edit_days", models.PositiveSmallIntegerField(
                    default=7,
                    validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(60)])),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="teacher_live_defaults",
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Teacher live defaults",
                "verbose_name_plural": "Teacher live defaults",
            },
        ),
        migrations.CreateModel(
            name="ParentChildLimit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("daily_limit_minutes", models.PositiveSmallIntegerField(
                    validators=[django.core.validators.MinValueValidator(5), django.core.validators.MaxValueValidator(1440)])),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("child", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="parent_limits",
                    to=settings.AUTH_USER_MODEL)),
                ("parent", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="child_limits_set",
                    to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name="parentchildlimit",
            index=models.Index(fields=["child"], name="parentchildlimit_child_idx"),
        ),
        migrations.AddConstraint(
            model_name="parentchildlimit",
            constraint=models.UniqueConstraint(fields=("parent", "child"), name="unique_parent_child_limit"),
        ),
    ]
