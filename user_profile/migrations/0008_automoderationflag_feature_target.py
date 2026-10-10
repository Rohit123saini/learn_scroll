from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user_profile", "0007_userpreference_font_scale"),
    ]

    operations = [
        migrations.AlterField(
            model_name="automoderationflag",
            name="target_type",
            field=models.CharField(
                choices=[("post", "Post"), ("comment", "Comment"), ("story", "Story"), ("bio", "Profile bio"),
                         ("message", "Direct message"), ("feature", "Feature request")],
                max_length=10,
            ),
        ),
    ]
