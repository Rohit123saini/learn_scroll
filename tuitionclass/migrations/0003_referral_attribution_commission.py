import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tuitionclass", "0002_notice_source"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ReferralCode",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=12, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="referral_code_row", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="ReferralAttribution",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_type", models.CharField(choices=[("app", "App invite"), ("testseries", "Test series"), ("classroom", "Classroom"), ("certificate", "Certificate share")], default="app", max_length=20)),
                ("source_id", models.CharField(blank=True, default="", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("first_purchase_at", models.DateTimeField(blank=True, null=True)),
                ("referee", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="referral_attribution", to=settings.AUTH_USER_MODEL)),
                ("referrer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="referral_attributions_made", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="ReferralCommission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("testseries", "Test series"), ("classroom", "Classroom")], max_length=12)),
                ("source_id", models.CharField(blank=True, default="", max_length=64)),
                ("reference", models.CharField(max_length=150, unique=True)),
                ("gross_coins", models.PositiveIntegerField(help_text="What the commission was calculated on.")),
                ("percent", models.DecimalField(decimal_places=2, default=0, max_digits=5)),
                ("commission_coins", models.PositiveIntegerField()),
                ("status", models.CharField(choices=[("paid", "Paid"), ("blocked", "Blocked (fraud rule)")], db_index=True, default="paid", max_length=10)),
                ("block_reason", models.CharField(blank=True, default="", max_length=40)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("referee", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="referral_commissions_generated", to=settings.AUTH_USER_MODEL)),
                ("referrer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="referral_commissions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="referralattribution",
            index=models.Index(fields=["referrer", "-created_at"], name="tuitioncl_referrer_9f3a1c_idx"),
        ),
        migrations.AddIndex(
            model_name="referralcommission",
            index=models.Index(fields=["referrer", "-created_at"], name="tuitioncl_referrer_2b7e44_idx"),
        ),
        migrations.AddIndex(
            model_name="referralcommission",
            index=models.Index(fields=["referrer", "status", "created_at"], name="tuitioncl_referrer_5c81d0_idx"),
        ),
    ]
