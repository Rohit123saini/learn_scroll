# FINANCE — fee head + late-fine policy, concessions, invoice numbers / breakdown,
# payment reconciliation. Written by hand (nothing run): verify with
#   python manage.py makemigrations --check
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


def backfill_invoices(apps, schema_editor):
    """campus + base_amount for every old invoice; then gap-free numbers per (campus, session)
    in creation order, and the counter rows so new invoices continue the sequence."""
    FeeInvoice = apps.get_model("campus", "FeeInvoice")
    Counter = apps.get_model("campus", "FeeInvoiceCounter")
    rows = (
        FeeInvoice.objects.select_related("enrollment__section__school_class", "fee_structure__session")
        .order_by("created_at", "id")
    )
    counters = {}
    for inv in rows.iterator(chunk_size=500):
        campus_id = inv.enrollment.section.school_class.campus_id
        session = inv.fee_structure.session
        key = (campus_id, session.id)
        counters[key] = counters.get(key, 0) + 1
        if session.start_date.year == session.end_date.year:
            label = str(session.start_date.year)
        else:
            label = f"{session.start_date.year}-{str(session.end_date.year)[-2:]}"
        inv.campus_id = campus_id
        inv.base_amount = inv.amount_due
        inv.invoice_no = f"INV/{label}/{counters[key]:06d}"
        inv.save(update_fields=["campus", "base_amount", "invoice_no"])
    for (campus_id, session_id), last in counters.items():
        Counter.objects.update_or_create(campus_id=campus_id, session_id=session_id, defaults={"last_no": last})


class Migration(migrations.Migration):

    dependencies = [
        ("campus", "0017_enrollment_promoted_status"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ---- FeeStructure
        migrations.AddField("feestructure", "category", models.CharField(
            choices=[("tuition", "Tuition"), ("admission", "Admission"), ("transport", "Transport"),
                     ("exam", "Exam"), ("lab", "Lab / Library"), ("hostel", "Hostel"),
                     ("uniform", "Uniform / Books"), ("other", "Other")],
            default="other", max_length=15)),
        migrations.AddField("feestructure", "allow_concession", models.BooleanField(default=True)),
        migrations.AddField("feestructure", "late_fine_type", models.CharField(
            choices=[("none", "No late fine"), ("flat", "Flat amount (once)"),
                     ("per_day", "Amount per day late"), ("percent", "Percent of unpaid (once)")],
            default="none", max_length=10)),
        migrations.AddField("feestructure", "late_fine_value", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
        migrations.AddField("feestructure", "late_fine_grace_days", models.PositiveSmallIntegerField(default=0)),
        migrations.AddField("feestructure", "late_fine_cap", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),

        # ---- FeeInvoice
        migrations.AddField("feeinvoice", "campus", models.ForeignKey(
            blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
            related_name="fee_invoices", to="campus.campus")),
        migrations.AddField("feeinvoice", "invoice_no", models.CharField(blank=True, db_index=True, max_length=40)),
        migrations.AddField("feeinvoice", "base_amount", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
        migrations.AddField("feeinvoice", "concession_amount", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
        migrations.AddField("feeinvoice", "fine_amount", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
        migrations.AddField("feeinvoice", "fine_waived", models.BooleanField(default=False)),
        migrations.AddField("feeinvoice", "fine_computed_on", models.DateField(blank=True, null=True)),
        migrations.AddField("feeinvoice", "adjustment_note", models.CharField(blank=True, max_length=255)),

        # ---- FeePayment
        migrations.AddField("feepayment", "reconciled_at", models.DateTimeField(blank=True, null=True)),

        # ---- numbering
        migrations.CreateModel(
            name="FeeInvoiceCounter",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("last_no", models.PositiveIntegerField(default=0)),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to="campus.campus")),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to="campus.academicsession")),
            ],
        ),
        migrations.AddConstraint("feeinvoicecounter", models.UniqueConstraint(
            fields=("campus", "session"), name="unique_invoice_counter")),

        migrations.RunPython(backfill_invoices, migrations.RunPython.noop),

        migrations.AddConstraint("feeinvoice", models.UniqueConstraint(
            fields=("campus", "invoice_no"), condition=~Q(invoice_no=""), name="unique_invoice_no_per_campus")),

        # ---- concessions
        migrations.CreateModel(
            name="FeeConcession",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=100)),
                ("reason", models.CharField(
                    choices=[("sibling", "Sibling"), ("merit", "Merit"), ("staff_ward", "Staff ward"),
                             ("scholarship", "Scholarship"), ("financial_aid", "Financial aid"), ("other", "Other")],
                    default="other", max_length=15)),
                ("kind", models.CharField(choices=[("percent", "Percent"), ("flat", "Flat amount")],
                                          default="percent", max_length=10)),
                ("value", models.DecimalField(decimal_places=2, max_digits=10)),
                ("applies_to_category", models.CharField(blank=True, max_length=15)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                             related_name="fee_concessions", to="campus.campus")),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.AddConstraint("feeconcession", models.UniqueConstraint(
            fields=("campus", "name"), name="unique_concession_name_per_campus")),
        migrations.AddConstraint("feeconcession", models.CheckConstraint(
            condition=Q(value__gt=0), name="feeconcession_value_positive")),
        migrations.AddConstraint("feeconcession", models.CheckConstraint(
            condition=~Q(kind="percent") | Q(value__lte=100), name="feeconcession_percent_max_100")),

        migrations.CreateModel(
            name="FeeConcessionGrant",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("note", models.CharField(blank=True, max_length=255)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("concession", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                                 related_name="grants", to="campus.feeconcession")),
                ("enrollment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                                 related_name="fee_concession_grants", to="campus.studentenrollment")),
                ("fee_structure", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                                                    related_name="concession_grants", to="campus.feestructure")),
                ("granted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                 related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex("feeconcessiongrant", models.Index(fields=["enrollment", "is_active"],
                                                               name="campus_feec_enrollm_8c1f1d_idx")),
        migrations.AddConstraint("feeconcessiongrant", models.UniqueConstraint(
            fields=("concession", "enrollment"), condition=Q(fee_structure__isnull=True), name="unique_grant_all_fees")),
        migrations.AddConstraint("feeconcessiongrant", models.UniqueConstraint(
            fields=("concession", "enrollment", "fee_structure"), condition=Q(fee_structure__isnull=False),
            name="unique_grant_one_fee")),

        # ---- reconciliation
        migrations.CreateModel(
            name="FeeReconciliationRun",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("source", models.CharField(
                    choices=[("razorpay_csv", "Razorpay CSV report"), ("razorpay_api", "Razorpay API"),
                             ("internal", "Internal checks")], max_length=15)),
                ("period_from", models.DateField(blank=True, null=True)),
                ("period_to", models.DateField(blank=True, null=True)),
                ("file_name", models.CharField(blank=True, max_length=200)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("summary", models.JSONField(blank=True, default=dict)),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                             related_name="fee_reconciliation_runs", to="campus.campus")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                 related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex("feereconciliationrun", models.Index(fields=["campus", "-created_at"],
                                                                 name="campus_feer_campus__5b0e0a_idx")),
        migrations.CreateModel(
            name="FeeReconciliationItem",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("category", models.CharField(db_index=True, max_length=30)),
                ("severity", models.CharField(max_length=10)),
                ("suggested_action", models.CharField(default="none", max_length=15)),
                ("detail", models.CharField(blank=True, max_length=300)),
                ("gateway_order_id", models.CharField(blank=True, db_index=True, max_length=100)),
                ("gateway_payment_id", models.CharField(blank=True, db_index=True, max_length=100)),
                ("our_amount", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("gateway_amount", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("our_status", models.CharField(blank=True, max_length=15)),
                ("gateway_status", models.CharField(blank=True, max_length=15)),
                ("gateway_method", models.CharField(blank=True, max_length=20)),
                ("gateway_fee", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ("gateway_tax", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ("settlement_id", models.CharField(blank=True, max_length=60)),
                ("resolution", models.CharField(
                    choices=[("open", "Open"), ("resolved", "Resolved"), ("ignored", "Ignored"),
                             ("auto", "Matched"), ("superseded", "Superseded by a newer run")],
                    db_index=True, default="open", max_length=12)),
                ("resolution_action", models.CharField(blank=True, max_length=20)),
                ("resolution_note", models.CharField(blank=True, max_length=255)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                          related_name="items", to="campus.feereconciliationrun")),
                ("payment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                              related_name="+", to="campus.feepayment")),
                ("invoice", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                              related_name="+", to="campus.feeinvoice")),
                ("resolved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                  related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-run__created_at", "severity", "category"]},
        ),
        migrations.AddIndex("feereconciliationitem", models.Index(fields=["run", "resolution"],
                                                                  name="campus_feer_run_id_3a7c10_idx")),
    ]
