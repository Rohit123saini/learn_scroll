# ADMISSION — AdmissionCounter + AdmissionApplication.
# Written by hand (nothing run): verify with
#   python manage.py makemigrations --check
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("campus", "0022_campusprofile_logo"),
    ]

    operations = [
        migrations.CreateModel(
            name="AdmissionCounter",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("last_no", models.PositiveIntegerField(default=0)),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to="campus.campus")),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to="campus.academicsession")),
            ],
        ),
        migrations.AddConstraint(
            model_name="admissioncounter",
            constraint=models.UniqueConstraint(fields=("campus", "session"), name="unique_admission_counter"),
        ),
        migrations.CreateModel(
            name="AdmissionApplication",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("application_no", models.CharField(blank=True, max_length=40)),
                ("applicant_name", models.CharField(max_length=150)),
                ("date_of_birth", models.DateField(blank=True, null=True)),
                ("guardian_name", models.CharField(blank=True, max_length=150)),
                ("guardian_phone", models.CharField(blank=True, db_index=True, max_length=20)),
                ("guardian_email", models.EmailField(blank=True, max_length=254)),
                ("previous_school", models.CharField(blank=True, max_length=150)),
                ("address", models.CharField(blank=True, max_length=300)),
                ("source", models.CharField(
                    choices=[("walk_in", "Walk-in"), ("phone", "Phone"), ("referral", "Referral"),
                             ("online", "Online / social"), ("import", "Imported"), ("other", "Other")],
                    default="walk_in", max_length=10)),
                ("status", models.CharField(
                    choices=[("enquiry", "Enquiry"), ("applied", "Applied"), ("interview", "Interview / test"),
                             ("waitlisted", "Waitlisted"), ("offered", "Seat offered"), ("enrolled", "Enrolled"),
                             ("rejected", "Rejected"), ("withdrawn", "Withdrawn")],
                    db_index=True, default="enquiry", max_length=12)),
                ("follow_up_on", models.DateField(blank=True, null=True)),
                ("interview_on", models.DateTimeField(blank=True, null=True)),
                ("remarks", models.CharField(blank=True, max_length=500)),
                ("decision_note", models.CharField(blank=True, max_length=200)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="admission_applications", to="campus.campus")),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="admission_applications", to="campus.academicsession")),
                ("school_class", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="admission_applications", to="campus.schoolclass")),
                ("section", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="admission_applications", to="campus.section")),
                ("decided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="campus_admissions_decided", to=settings.AUTH_USER_MODEL)),
                ("student", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="campus_admission_applications", to=settings.AUTH_USER_MODEL)),
                ("enrollment", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="admission_application", to="campus.studentenrollment")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="campus_admissions_created", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="admissionapplication",
            index=models.Index(fields=["campus", "session", "status"], name="admission_campus_status_idx"),
        ),
        migrations.AddIndex(
            model_name="admissionapplication",
            index=models.Index(fields=["campus", "follow_up_on"], name="admission_followup_idx"),
        ),
        migrations.AddConstraint(
            model_name="admissionapplication",
            constraint=models.UniqueConstraint(
                condition=~Q(application_no=""), fields=("campus", "application_no"),
                name="unique_admission_no_per_campus"),
        ),
    ]
