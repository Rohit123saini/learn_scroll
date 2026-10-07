import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("testseries", "0003_question_new_types"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="testseriespurchase",
            name="referred_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="testseries_referred_purchases", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="testseriespurchase",
            name="referral_commission_percent",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=5),
        ),
        migrations.AddField(
            model_name="testseriespurchase",
            name="referral_commission_coins",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="testseriespurchase",
            name="referral_commission_paid",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
