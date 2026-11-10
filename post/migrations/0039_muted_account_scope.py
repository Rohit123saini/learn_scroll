# P8: MutedAccount.mute_posts / mute_stories - "mute posts but keep their stories" (and the other way round).
# Purely additive: every existing row gets True/True, i.e. exactly the old "mute everything" behaviour.
# The CheckConstraint only forbids True-nothing rows (both False), which the app never writes (it deletes the row).
# Hand-written (nothing was run): check with `python manage.py makemigrations --check --dry-run`.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('post', '0038_highlight_position'),
    ]

    operations = [
        migrations.AddField(
            model_name='mutedaccount',
            name='mute_posts',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='mutedaccount',
            name='mute_stories',
            field=models.BooleanField(default=True),
        ),
        migrations.AddConstraint(
            model_name='mutedaccount',
            constraint=models.CheckConstraint(
                condition=models.Q(('mute_posts', True), ('mute_stories', True), _connector='OR'),
                name='muted_account_scope_not_empty',
            ),
        ),
    ]
