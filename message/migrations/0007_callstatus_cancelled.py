# C6 — hand-written (makemigrations NAHI chalaya). Sirf `choices` badle hain
# (CallStatus.CANCELLED) — koi column/DB change nahi, SQL me kuch nahi chalta.
# `makemigrations --check --dry-run` se confirm karo ki Django koi aur diff nahi
# dikha raha; 0006 (C5 voip_token) ke baad ki hai.
from django.db import migrations, models

_CHOICES = [
    ('initiated', 'Initiated'),
    ('ringing', 'Ringing'),
    ('ongoing', 'Ongoing'),
    ('ended', 'Ended'),
    ('missed', 'Missed'),
    ('rejected', 'Rejected'),
    ('busy', 'Busy'),
    ('cancelled', 'Cancelled'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('message', '0006_devicetoken_voip_token'),
    ]

    operations = [
        migrations.AlterField(
            model_name='callsession',
            name='status',
            field=models.CharField(choices=_CHOICES, db_index=True, default='initiated', max_length=20),
        ),
        migrations.AlterField(
            model_name='callparticipant',
            name='status',
            field=models.CharField(choices=_CHOICES, default='ringing', max_length=20),
        ),
    ]
