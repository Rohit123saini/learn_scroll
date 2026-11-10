# SOFT DELETE, step 1 of 2 — add the four bookkeeping columns (see STATUS_SOFT_DELETE_AUDIT.md).
#
# Migration-safety notes (all of it additive, nothing is rewritten or dropped):
#   * is_deleted  BOOLEAN NOT NULL DEFAULT false -> on PostgreSQL 11+ a constant default is a
#     catalog-only change (no table rewrite). On SQLite Django rebuilds the table (dev only).
#   * deleted_at / delete_batch are NULLable -> no default needed, instant.
#   * deleted_by is a NULLable FK with db_index=False -> no index build; only a cheap FK constraint.
#   * No index is added here on purpose (index builds are what lock big live tables).
#   * Old code keeps working while this runs: it never reads these columns, and the new code is
#     only deployed after the migration. Rolling back = `migrate campus 0024` (drops the columns).
#
# Deploy order: run 0025 -> deploy code -> run 0026 (constraints).
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

SOFT_MODELS = [
    "campus", "academicsession", "department", "schoolclass", "section", "subject", "room",
    "staffprofile", "classteacherassigments", "subjectteacherassigments", "timeslot",
    "timetableentry", "syllabusunit", "examterm", "resultentry", "feestructure", "feeinvoice",
    "notice", "campusholiday", "digitalidcard", "campuslivesession",
]


def _ops():
    ops = []
    for m in SOFT_MODELS:
        ops += [
            migrations.AddField(model_name=m, name="is_deleted", field=models.BooleanField(default=False)),
            migrations.AddField(model_name=m, name="deleted_at", field=models.DateTimeField(blank=True, null=True)),
            migrations.AddField(
                model_name=m,
                name="deleted_by",
                field=models.ForeignKey(
                    blank=True, null=True, db_index=False, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+", to=settings.AUTH_USER_MODEL,
                ),
            ),
            migrations.AddField(model_name=m, name="delete_batch", field=models.UUIDField(blank=True, null=True)),
        ]
    return ops


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("campus", "0024_perf_indexes"),
    ]

    operations = _ops()
