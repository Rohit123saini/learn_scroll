from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("post", "0013_alter_post_moderation_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="post",
            name="shared_type",
            field=models.CharField(blank=True, default="", max_length=12),
        ),
        migrations.AddField(
            model_name="post",
            name="shared_id",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
