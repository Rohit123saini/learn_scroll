# login/identifiers.py
"""
One place that decides "what is this string a user typed?" and "which account
does it belong to?" -- so OTP login, forgot/reset password and password login
can never disagree.

WHY THIS FILE EXISTS (L1 gap 5 -- identifier confusion):
    VerifyOTPView / ForgotPasswordView / ResetPasswordView used to look the
    account up with ``Q(username=target) | Q(email=target)`` (or username|phone),
    and Login used ``.get(Q(username=x) | Q(email=x))``. Signup put no limit on
    what a username could look like, so:
      * someone could register the username "victim@gmail.com" and then
        - Login with "victim@gmail.com" raised MultipleObjectsReturned (a 500
          that locked the real owner out of email login), and
        - an OTP sent to victim@gmail.com could land in the ATTACKER's account
          because ``.first()`` has no defined order.
    Rule now: a code that was sent to an email/phone is only ever matched
    against ``User.email`` / ``User.phone`` -- never against ``username``.
    Usernames can no longer look like an email or a phone number either.
"""
import re

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from .models import phone_validator

KIND_EMAIL = "email"
KIND_PHONE = "phone"
KIND_INVALID = "invalid"

# spaces, dashes and brackets people type inside a phone number
_PHONE_NOISE = re.compile(r"[\s\-()]")
# a username made only of digits (optionally with a leading +) of phone length
_PHONE_LIKE = re.compile(r"^\+?\d{8,15}$")


def normalize_identifier(raw):
    """Return ``(value, kind)`` for something typed into an "email or phone" box.

    ``kind`` is "email" | "phone" | "invalid". The returned value is the exact
    string every OTP/rate-limit key and every account lookup must use
    (emails lower-cased; phone numbers stripped of spaces/dashes/brackets).
    """
    text = (raw or "").strip()
    if not text:
        return "", KIND_INVALID

    if "@" in text:
        text = text.lower()
        try:
            validate_email(text)
        except ValidationError:
            return text, KIND_INVALID
        return text, KIND_EMAIL

    text = _PHONE_NOISE.sub("", text)
    try:
        phone_validator(text)
    except ValidationError:
        return text, KIND_INVALID
    return text, KIND_PHONE


def find_user_by_identifier(value, kind):
    """Account that OWNS this verified email/phone, or None.

    Deliberately never looks at ``username`` -- see the module docstring.
    ``User.email`` and ``User.phone`` are both unique, so there is at most one.
    """
    User = get_user_model()
    if kind == KIND_EMAIL:
        return User.objects.filter(email=value).first()
    if kind == KIND_PHONE:
        return User.objects.filter(phone=value).first()
    return None


def find_user_for_password_login(identifier):
    """Resolve what was typed in the password-login box (username OR email).

    An email is matched against ``User.email`` first. Only if nobody owns that
    email do we fall back to an exact username match (old accounts whose
    username really contains an "@"). Never raises MultipleObjectsReturned.
    """
    User = get_user_model()
    text = (identifier or "").strip()
    if not text:
        return None
    if "@" in text:
        user = User.objects.filter(email=text.lower()).first()
        if user is not None:
            return user
    return User.objects.filter(username=text).first()


def username_problem(value):
    """``(code, message)`` for a NEW username that is not allowed, or None if fine.
    The code is what the app translates (L1 gap 17)."""
    if "@" in value:
        return "username_has_at", "Username cannot contain '@'."
    if _PHONE_LIKE.match(value):
        return "username_phone_like", "Username cannot look like a phone number."
    return None


def username_error(value):
    """Human-readable reason a NEW username is not allowed, or None if fine."""
    problem = username_problem(value)
    return problem[1] if problem else None
