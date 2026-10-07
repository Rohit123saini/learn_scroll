"""
One-time backfill so `user_profile.BlockUser` and `message.BlockedUser`
start out identical (the live receivers in user_profile/signals.py keep them
that way afterwards). Without this, everyone who blocked someone from the app
before this change would still be able to receive that person's chat messages.

Reverse is a no-op on purpose: removing mirrored rows would un-block people.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    BlockUser = apps.get_model("user_profile", "BlockUser")
    BlockedUser = apps.get_model("message", "BlockedUser")

    chat_pairs = set(
        BlockedUser._default_manager.values_list("blocker_id", "blocked_id")
    )
    profile_pairs = set(BlockUser.objects.values_list("blocker_id", "blocked_id"))

    BlockedUser.objects.bulk_create(
        [
            BlockedUser(blocker_id=a, blocked_id=b)
            for a, b in profile_pairs - chat_pairs
        ],
        batch_size=500,
    )
    BlockUser.objects.bulk_create(
        [
            BlockUser(blocker_id=a, blocked_id=b)
            for a, b in chat_pairs - profile_pairs
            if a != b
        ],
        batch_size=500,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("user_profile", "0003_coinledger_referral_commission"),
        ("message", "0002_usernote_conversationparticipant_request_status_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
