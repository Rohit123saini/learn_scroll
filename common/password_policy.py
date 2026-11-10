"""
Strong-password policy for STAFF accounts (moderators / admins). Task 14.

Students keep Django's normal validators (8+ chars etc.). Anyone with
``is_staff`` or ``is_superuser`` -- i.e. anyone who can take down content, issue
copyright strikes or terminate accounts -- must use a much stronger password.

The policy is enforced in TWO places so it cannot be skipped:
  1. ``StaffStrongPasswordValidator`` (in AUTH_PASSWORD_VALIDATORS): runs whenever a
     password is SET through Django (admin password-change form, ``changepassword``,
     any serializer that calls validate_password(user=...)).
  2. The admin login form (common/admin_site.py): runs on every login, because some
     paths (``createsuperuser``, flipping ``is_staff`` on an existing user, a password
     set before this policy existed) never hit validator #1.
"""
import re

from django.conf import settings
from django.core.exceptions import ValidationError


def staff_min_length() -> int:
    return int(getattr(settings, "STAFF_PASSWORD_MIN_LENGTH", 14))


def is_staff_like(user) -> bool:
    return bool(user and (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)))


def _identity_bits(user):
    bits = []
    for attr in ("username", "first_name", "last_name"):
        v = (getattr(user, attr, "") or "").strip().lower()
        if len(v) >= 3:
            bits.append(v)
    email = (getattr(user, "email", "") or "").strip().lower()
    if "@" in email and len(email.split("@", 1)[0]) >= 3:
        bits.append(email.split("@", 1)[0])
    return bits


def staff_password_problems(password: str, user=None) -> list:
    """Human-readable list of what is wrong with `password` (empty list = OK)."""
    password = password or ""
    problems = []
    n = staff_min_length()
    if len(password) < n:
        problems.append(f"be at least {n} characters long")
    if not re.search(r"[a-z]", password):
        problems.append("contain a lowercase letter")
    if not re.search(r"[A-Z]", password):
        problems.append("contain an uppercase letter")
    if not re.search(r"\d", password):
        problems.append("contain a digit")
    if not re.search(r"[^A-Za-z0-9]", password):
        problems.append("contain a symbol (e.g. ! @ # $ %)")
    if len(set(password)) < 6:
        problems.append("not be made of a few repeated characters")
    low = password.lower()
    if user is not None and any(bit in low for bit in _identity_bits(user)):
        problems.append("not contain your username, name or email")
    return problems


class StaffStrongPasswordValidator:
    """Add to AUTH_PASSWORD_VALIDATORS. No effect for non-staff users."""

    def validate(self, password, user=None):
        if not is_staff_like(user):
            return
        problems = staff_password_problems(password, user)
        if problems:
            raise ValidationError(
                "Staff passwords must " + "; ".join(problems) + ".",
                code="staff_password_weak",
            )

    def get_help_text(self):
        return (f"Staff / moderator passwords need at least {staff_min_length()} characters with upper- and "
                "lower-case letters, a digit and a symbol, and must not contain your name or email.")
