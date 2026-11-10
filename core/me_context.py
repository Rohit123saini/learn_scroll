"""
core/me_context.py - "who am I, as far as the app's own UI rules are concerned".

    GET  core/me/context/          what the app needs to decide, on its own, what to show / hide
    POST core/me/safe-defaults/    apply the safe defaults a minor account must have, return the context

Before this the app could not tell whether the signed-in user is a minor or staff: `is_minor` is derived from the
date of birth on the server and `is_staff` was never sent anywhere. The three UI rules that hang on it:

  * minors see NO ads        -> `ads_enabled` (False for a known under-18 account; setting ADS_OFF_FOR_MINORS,
                                default True, is the kill switch). Unknown date of birth is NOT assumed to be a
                                minor here - the same rule login/age.py uses everywhere ("nothing is assumed").
  * safe defaults for minors -> `safe_defaults`. Today the one rule is "account stays private" (the server already
                                refuses to make it public, see ProfileUpdateSerializer.validate_is_private); an old
                                account can still violate it (e.g. DOB added later by support), `violations` says so
                                and the app calls POST safe-defaults once to fix it.
  * staff-only shortcuts     -> `is_staff` / `can_moderate` (the feed card's Review / Remove).

Everything is read from the real user row on every call - the app never caches it past a session, and it is a
UI hint only: the server enforces each rule again on the endpoints themselves (staff endpoints check is_staff,
the profile endpoint refuses a public minor account, ...).
"""
from django.conf import settings
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView


class MeContextThrottle(UserRateThrottle):
    scope = "me_context"
    rate = "60/min"


def ads_off_for_minors() -> bool:
    return bool(getattr(settings, "ADS_OFF_FOR_MINORS", True))


def safe_default_violations(user) -> list:
    """Keys of the safe defaults this user currently breaks. Empty for adults / unknown age."""
    if not user.is_minor:
        return []
    violations = []
    if not user.is_private:
        violations.append("is_private")
    return violations


def build_context(user) -> dict:
    is_minor = bool(user.is_minor)
    violations = safe_default_violations(user)
    return {
        "id": user.pk,
        "username": user.username,
        "is_staff": bool(user.is_staff),
        "can_moderate": bool(user.is_staff and user.is_active),
        "is_minor": is_minor,
        "age_known": user.date_of_birth is not None,
        "ads_enabled": not (is_minor and ads_off_for_minors()),
        "onboarding_role": user.onboarding_role or "",
        "safe_defaults": {
            "applies": is_minor,
            "ok": not violations,
            "violations": violations,
        },
    }


class MeContextView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [MeContextThrottle]

    def get(self, request):
        return Response(build_context(request.user))


class ApplySafeDefaultsView(APIView):
    """Idempotent. Only ever TIGHTENS (private on) and only for a known minor; an adult calling it changes nothing."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [MeContextThrottle]

    def post(self, request):
        user = request.user
        applied = []
        if "is_private" in safe_default_violations(user):
            user.is_private = True
            user.save(update_fields=["is_private"])
            applied.append("is_private")
        data = build_context(user)
        data["applied"] = applied
        return Response(data)
