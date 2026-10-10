from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("login", "0003_user_date_of_birth"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="study_class",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="user",
            name="target_exam",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
    ]
