from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("login", "0004_user_study_class_user_target_exam"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="onboarding_role",
            field=models.CharField(blank=True, default="", max_length=10),
        ),
    ]
