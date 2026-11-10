# Story studio F3 (part 2, PARTIAL): five new sticker kinds. Only the `kind` choices change (no column change).
# Written by hand, NOT generated and NOT run - run `makemigrations --check` once to confirm it matches models.py.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('post', '0030_lectures'),
    ]

    operations = [
        migrations.AlterField(
            model_name='storysticker',
            name='kind',
            field=models.CharField(
                choices=[
                    ('mention', 'Mention'), ('link', 'Link'), ('poll', 'Poll'), ('question', 'Question'),
                    ('location', 'Location'), ('countdown', 'Countdown'), ('quiz', 'Quiz'),
                    ('add_yours', 'Add yours'), ('gif', 'GIF'),
                ],
                max_length=20,
            ),
        ),
    ]
