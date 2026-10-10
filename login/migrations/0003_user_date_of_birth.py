from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("login", "0002_user_category_label_user_links_user_pronouns_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="date_of_birth",
            field=models.DateField(blank=True, null=True),
        ),
    ]
