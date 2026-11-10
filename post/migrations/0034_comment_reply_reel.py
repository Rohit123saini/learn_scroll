# Reply-with-reel: PostComment.reply_reel (nullable FK to Post, SET_NULL).
# Written by hand, NOT generated and NOT run - run `makemigrations --check` once to confirm it matches models.py.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('post', '0033_story_audience_f3'),
    ]

    operations = [
        migrations.AddField(
            model_name='postcomment',
            name='reply_reel',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='reel_replies', to='post.post',
            ),
        ),
    ]
