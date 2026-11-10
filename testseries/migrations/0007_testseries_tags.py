from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("testseries", "0006_testseries_cover_image"),
    ]

    operations = [
        migrations.AddField(
            model_name="testseries",
            name="tags",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
