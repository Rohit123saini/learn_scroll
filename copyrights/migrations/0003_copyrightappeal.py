# Hand-written (Django was not available when this was authored). Run
#   python manage.py makemigrations --check
# once; if it reports a difference, delete this file and run makemigrations.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('copyrights', '0002_copyrightclaim_claimant_tier_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='CopyrightAppeal',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('strike', 'Strike'), ('termination', 'Account termination')], db_index=True, max_length=12)),
                ('message', models.TextField(max_length=2000)),
                ('contact_email', models.EmailField(blank=True, default='', max_length=254)),
                ('status', models.CharField(choices=[('open', 'Waiting for review'), ('accepted', 'Accepted'), ('rejected', 'Rejected')], db_index=True, default='open', max_length=10)),
                ('decision_note', models.CharField(blank=True, default='', max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('decided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('strike', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='appeals', to='copyrights.copyrightstrike')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='copyright_appeals', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddConstraint(
            model_name='copyrightappeal',
            constraint=models.UniqueConstraint(condition=models.Q(('kind', 'termination'), ('status', 'open')), fields=('user',), name='cr_one_open_termination_appeal'),
        ),
        migrations.AddConstraint(
            model_name='copyrightappeal',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'open')), fields=('strike',), name='cr_one_open_strike_appeal'),
        ),
    ]
