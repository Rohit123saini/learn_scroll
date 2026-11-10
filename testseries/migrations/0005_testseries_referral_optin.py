from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("testseries", "0004_testseriespurchase_referral"),
    ]

    operations = [
        migrations.AddField(
            model_name="testseries",
            name="referral_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="testseries",
            name="referral_commission_percent",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=5),
        ),
    ]
