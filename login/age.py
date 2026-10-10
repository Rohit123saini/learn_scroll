"""
login/age.py

Single place for age rules (minor-safety). Everything else (signup, profile
privacy lock, DM gate, discovery filters) calls these helpers so the
thresholds live in exactly one spot.

    MIN_SIGNUP_AGE  (default 13)  below this, an account can't be created.
    ADULT_AGE       (default 18)  below this the user is a "minor" and gets
                                  the protections in message/minor_safety.py
                                  and user_profile/discovery.py.

Users with NO date_of_birth (accounts created before this feature, or a
Google signup that hasn't set it yet) are treated as "unknown": they are
neither minors nor verified adults, so existing users aren't locked out
and nothing is silently assumed about them.
"""

from datetime import date

from django.conf import settings
from django.utils import timezone


def min_signup_age() -> int:
    return int(getattr(settings, "MIN_SIGNUP_AGE", 13))


def adult_age() -> int:
    return int(getattr(settings, "ADULT_AGE", 18))


def age_on(dob: date, today: date | None = None) -> int:
    today = today or timezone.localdate()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def is_minor_dob(dob: date | None, today: date | None = None) -> bool:
    return dob is not None and age_on(dob, today) < adult_age()


def is_adult_dob(dob: date | None, today: date | None = None) -> bool:
    return dob is not None and age_on(dob, today) >= adult_age()


def dob_error(dob: date, today: date | None = None) -> str | None:
    """Return a user-facing error string if `dob` can't be accepted, else None."""
    today = today or timezone.localdate()
    if dob > today:
        return "Date of birth cannot be in the future."
    if age_on(dob, today) > 120:
        return "Please enter a valid date of birth."
    if age_on(dob, today) < min_signup_age():
        return f"You must be at least {min_signup_age()} years old to use LearnScroll."
    return None


def minor_cutoff_date(today: date | None = None) -> date:
    """Anyone born AFTER this date is under ADULT_AGE today.
    (Feb 29 safe: falls back to Feb 28 in non-leap years.)"""
    today = today or timezone.localdate()
    year = today.year - adult_age()
    try:
        return today.replace(year=year)
    except ValueError:  # today is Feb 29, target year isn't a leap year
        return today.replace(year=year, day=28)
