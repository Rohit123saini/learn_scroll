# 🔧 CLEANUP (this session) — drops the legacy/dead `CallSession` columns
# that were flagged DEPRECATED in models.py:
#   - `token`           -> legacy Agora join token, unused since the app
#                          fully moved to LiveKit (`livekit_utils.
#                          generate_livekit_token` generates tokens on
#                          demand instead of persisting one).
#   - `is_recording`    -> no REST/WS code anywhere ever set or read this;
#   - `recording_url`      LiveKit server-side recording/egress was never
#                          wired into this stack, so these could only ever
#                          show a false "recording" signal to a client
#                          that happened to read them.
#
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('message', '0902_conversationparticipant_wallpaper_url'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='callsession',
            name='token',
        ),
        migrations.RemoveField(
            model_name='callsession',
            name='is_recording',
        ),
        migrations.RemoveField(
            model_name='callsession',
            name='recording_url',
        ),
    ]