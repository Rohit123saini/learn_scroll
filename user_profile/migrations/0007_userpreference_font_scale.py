from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user_profile", "0006_automoderationflag"),
    ]

    operations = [
        migrations.AddField(
            model_name="userpreference",
            name="font_scale",
            field=models.CharField(
                choices=[("small", "Small"), ("normal", "Normal"), ("large", "Large"), ("xlarge", "Extra large")],
                default="normal",
                max_length=10,
            ),
        ),
    ]
