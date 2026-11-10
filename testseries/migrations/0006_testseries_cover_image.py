from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("testseries", "0005_testseries_referral_optin"),
    ]

    operations = [
        migrations.AddField(
            model_name="testseries",
            name="cover_image",
            field=models.ImageField(blank=True, null=True, upload_to="testseries/covers/"),
        ),
    ]
