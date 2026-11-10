"""
user_profile/profile_state.py - P10 "what kind of profile page is this?".   NOT RUN.

The app used to guess the page type from a mix of flags (`is_restricted_view`, `is_blocked_by_me`, a bare 404).
Now the profile detail response carries ONE word, `profile_state`:

    ok             normal profile (own, public, or I am an accepted follower)
    private        private account and I do not follow it  -> "This account is private" card
    blocked_by_me  I blocked this account                  -> identity card + Unblock
    suspended      the account is switched off (is_active=False; suspended / deactivated / waiting for deletion)
                   -> "This account isn't available" - only id + username are sent, nothing else

What is NOT a state on purpose: "they blocked me" and "no such user" - both stay a plain 404, so blocking never leaks
who blocked whom. The app shows the same "Profile not found" page for both.
"""

OK = "ok"
PRIVATE = "private"
BLOCKED_BY_ME = "blocked_by_me"
SUSPENDED = "suspended"

ALL_STATES = (OK, PRIVATE, BLOCKED_BY_ME, SUSPENDED)


def classify(*, is_blocked_by_me: bool, is_suspended: bool, is_restricted_view: bool) -> str:
    """Same precedence the view uses to pick the payload: blocked-by-me first (the Unblock button must stay reachable),
    then suspended, then private."""
    if is_blocked_by_me:
        return BLOCKED_BY_ME
    if is_suspended:
        return SUSPENDED
    if is_restricted_view:
        return PRIVATE
    return OK


def unavailable_card(user) -> dict:
    """The only things a stranger may learn about a switched-off account: its id and @username. No name, photo, bio, counts."""
    return {
        "id": user.pk,
        "username": user.username,
        "first_name": "",
        "last_name": "",
        "is_private": False,
        "is_verified": False,
    }
