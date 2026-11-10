# PERF — query indexes for 1000+ student campuses (see STATUS_PERF_1K.md).
# Written by hand (nothing run): verify with
#   python manage.py makemigrations --check
# On a big live Postgres table prefer `CREATE INDEX CONCURRENTLY` (AddIndexConcurrently from
# django.contrib.postgres.operations, atomic = False) — these tables are small enough at school
# scale that the plain AddIndex lock is a few seconds.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("campus", "0023_admission"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="studentenrollment",
            index=models.Index(fields=["section", "session", "status"], name="enroll_sec_sess_status_idx"),
        ),
        migrations.AddIndex(
            model_name="studentenrollment",
            index=models.Index(fields=["student", "status"], name="enroll_student_status_idx"),
        ),
        migrations.AddIndex(
            model_name="attendance",
            index=models.Index(fields=["date", "status"], name="att_date_status_idx"),
        ),
        migrations.AddIndex(
            model_name="feeinvoice",
            index=models.Index(fields=["campus", "status"], name="feeinv_campus_status_idx"),
        ),
        migrations.AddIndex(
            model_name="feeinvoice",
            index=models.Index(fields=["campus", "-created_at"], name="feeinv_campus_created_idx"),
        ),
        migrations.AddIndex(
            model_name="feepayment",
            index=models.Index(fields=["invoice", "status"], name="feepay_invoice_status_idx"),
        ),
        migrations.AddIndex(
            model_name="feeconcessiongrant",
            index=models.Index(fields=["concession", "is_active"], name="feegrant_conc_active_idx"),
        ),
        migrations.AddIndex(
            model_name="resultentry",
            index=models.Index(fields=["exam_term", "subject"], name="result_term_subject_idx"),
        ),
    ]
