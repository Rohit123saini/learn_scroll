"""
copyrights/permissions.py - the three staff levels.

  L1  Reviewer       triage: take a claim, ask the claimant for info, add notes,
                     reject a clearly incomplete / invalid claim.
  L2  Senior         everything L1 can + uphold a claim (remove content, issue a
                     strike), restore held content, decide counter-notices,
                     decide claims against high-reach / verified accounts,
                     mark a claim as bad-faith.
  L3  Copyright admin everything L2 can + revoke strikes, reverse an upheld
                     decision, terminate / reinstate accounts.

The level is DERIVED from Django permissions (so it can be managed from the
normal admin Groups screen - `manage.py setup_copyright_roles` creates the three
groups). Superusers are always L3. The service layer re-checks the level on every
action, so the REST API, Django admin and shell all obey the same rules.
"""

from django.core.exceptions import PermissionDenied

L1, L2, L3 = 1, 2, 3

PERM_REVIEW = "copyrights.review_claim"
PERM_UPHOLD = "copyrights.uphold_claim"
PERM_REVOKE = "copyrights.revoke_strike"
PERM_TERMINATE = "copyrights.terminate_account"

# codename lists per role group (setup_copyright_roles command).
ROLE_GROUPS = {
    "Copyright L1 Reviewer": ["review_claim"],
    "Copyright L2 Senior": ["review_claim", "uphold_claim"],
    "Copyright L3 Admin": ["review_claim", "uphold_claim", "revoke_strike", "terminate_account"],
}


def level_for(user) -> int:
    """0 = no copyright access, else 1..3. Never raises."""
    try:
        if user is None or not getattr(user, "is_authenticated", False):
            return 0
        if not (user.is_active and user.is_staff):
            return 0
        if user.is_superuser:
            return L3
        if user.has_perm(PERM_REVOKE) or user.has_perm(PERM_TERMINATE):
            return L3
        if user.has_perm(PERM_UPHOLD):
            return L2
        if user.has_perm(PERM_REVIEW):
            return L1
    except Exception:
        return 0
    return 0


def require_level(user, needed: int, action: str = "this action"):
    """Raise PermissionDenied unless `user` is staff of at least level `needed`."""
    if level_for(user) < needed:
        raise PermissionDenied(f"You need copyright staff level {needed} for {action}.")


def staff_at_least(level: int):
    """Active staff users of at least `level` (for escalation notifications)."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    out = []
    for u in User.objects.filter(is_active=True, is_staff=True):
        if level_for(u) >= level:
            out.append(u)
    return out
