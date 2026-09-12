# Generated manually (no Django install available in this pass) to add
# TASK 1's `UserPreference` model. Field defs below match
# `user_profile/models.py::UserPreference` exactly — regenerate with
# `manage.py makemigrations user_profile` and diff against this file if
# you want Django's own migration writer to confirm it, but there's
# nothing here beyond one CreateModel, so it should come out identical.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('user_profile', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserPreference',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('theme', models.CharField(choices=[('light', 'Light'), ('dark', 'Dark'), ('system', 'System')], default='system', max_length=10)),
                ('language', models.CharField(default='en', max_length=10)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='preferences', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-updated_at'],
            },
        ),
    ]