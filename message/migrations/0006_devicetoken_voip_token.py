# C5 — hand-written (makemigrations NAHI chalaya). Apply karne se pehle
# `python manage.py makemigrations --check --dry-run` chala lena: agar tumhare
# repo me 0005 ke baad koi aur message migration aa chuki ho to `dependencies`
# usi ke hisaab se badlo.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('message', '0005_doubt_image_multi_answers'),
    ]

    operations = [
        migrations.AddField(
            model_name='devicetoken',
            name='voip_token',
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True),
        ),
    ]
