"""
message/minor_safety.py

DM rule for minors (under 18, see login/age.py):

    An ADULT may not start a DM with a MINOR unless the minor already
    follows that adult (accepted follow). Minor<->minor, adult<->adult and
    anyone involving a user with no date of birth on file are unaffected.

Why "minor follows adult": it is the minor's own explicit opt-in, and it
keeps the legitimate cases working (a student following their teacher, then
the teacher replying) while stopping cold outreach from strangers.

Enforced at the two places a brand-new 1-1 chat is started from the app:
`ConversationViewSet.start_private` and the story-reply endpoint. An
already-existing conversation is never touched.
"""

from user_profile.models import Follow


def dm_blocked_for_minor(sender, receiver) -> bool:
    """True if `sender` must not start a new DM with `receiver`."""
    if not (receiver.is_minor and sender.is_verified_adult):
        return False
    return not Follow.objects.filter(
        follower=receiver, following=sender, status=Follow.Status.ACCEPTED,
    ).exists()


MINOR_DM_BLOCKED_MESSAGE = "You can't start a chat with this user."


# S1 — Study room (live audio/video room) bhi ek DM-jaisa live channel hai. Naya DM shuru karne ka
# rule yahan join pe bhi lagta hai: adult <-> minor 1-1 study room tabhi jab minor ne adult ko
# follow kiya ho. Dono direction check hote hain (kaun pehle join kare, usse farq nahi padta).
def dm_pair_blocked_for_minor(user_a, user_b) -> bool:
    """True if a 1-1 live room between these two users violates the minor DM rule."""
    return dm_blocked_for_minor(user_a, user_b) or dm_blocked_for_minor(user_b, user_a)


MINOR_STUDY_ROOM_BLOCKED_MESSAGE = "You can't join a study room with this user."
