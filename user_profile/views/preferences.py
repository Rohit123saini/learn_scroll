# user_profile/views/preferences.py — split out of the old user_profile/views.py (P-C4). Code is moved verbatim.

from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from ..models import (
    PrivacySettings,
    Streak,
    UserPreference,
)
from ..serializers import (
    PrivacySettingsSerializer,
    UserPreferenceSerializer,
)
from rest_framework.response import Response
from login.sensitive import (
    SensitiveWriteThrottle,
    changed_fields as _x6_changed_fields,
    record_change as _x6_record_change,
)
from rest_framework.throttling import UserRateThrottle
from login import audit as _x6_audit
from drf_spectacular.utils import extend_schema
from ..pref_etag import (
    if_match_state,
    if_none_match_hits,
    make_etag,
)
from .. import privacy as privacy_rules
from rest_framework import status
from django.db import transaction
from ._common import logger


class UserPreferenceView(GenericAPIView):
    """
    TASK 1 — GET/PATCH /user-profile/preferences/me/, same shape as
    core's `notification-preferences/me/`.

    GET always returns 200, never 404: `UserPreference.for_user()`
    get-or-creates the row, so a user who's never touched their
    theme/language still gets defaults back on first read instead of
    an empty state the frontend has to special-case.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = UserPreferenceSerializer

    @extend_schema(
        responses={200: UserPreferenceSerializer},
        description="Get current user's theme/language preferences.",
    )
    def get(self, request):
        preference = UserPreference.for_user(request.user)
        etag = make_etag(preference)
        # S2 — client ke paas yahi version hai to body mat bhejo.
        if if_none_match_hits(request.headers.get("If-None-Match"), etag):
            return Response(status=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
        serializer = self.get_serializer(preference)
        return Response({
            "status": True,
            "message": "Preferences fetched successfully.",
            "data": serializer.data,
        }, status=status.HTTP_200_OK, headers={"ETag": etag})

    @extend_schema(
        request=UserPreferenceSerializer,
        responses={200: UserPreferenceSerializer},
        description="Update current user's theme/language preferences. Send only fields you want to update.",
    )
    def patch(self, request):
        # S2 — optional optimistic concurrency. `If-Match` na ho (purane clients) to pehle jaisa field-wise
        # last-write-wins; ho aur stale ho to 412 + current preferences, kuch save nahi hota.
        # Row lock: check aur save beech me doosra device ghus na sake.
        with transaction.atomic():
            UserPreference.for_user(request.user)  # lazy get-or-create
            preference = UserPreference.objects.select_for_update().get(user=request.user)
            current_etag = make_etag(preference)

            if if_match_state(request.headers.get("If-Match"), current_etag) == "stale":
                return Response({
                    "status": False,
                    "code": "preferences_conflict",
                    "message": "Preferences kisi aur device se badal chuki hain.",
                    "data": self.get_serializer(preference).data,
                }, status=status.HTTP_412_PRECONDITION_FAILED, headers={"ETag": current_etag})

            alerts_before = preference.login_alerts  # S3 -- audit when the new-device e-mail switch flips
            serializer = self.get_serializer(preference, data=request.data, partial=True)
            if serializer.is_valid():
                serializer.save()
                saved = True
            else:
                saved = False

        if saved:
            # S3 -- turning login alerts off/on is a security-relevant change: leave a trail in the Security activity.
            try:
                if serializer.instance.login_alerts != alerts_before:
                    from login import audit as _auth_audit

                    _auth_audit.record(
                        _auth_audit.Event.LOGIN_ALERTS_CHANGED, request, user=request.user,
                        enabled=bool(serializer.instance.login_alerts),
                    )
            except Exception:
                logger.warning("login_alerts audit failed (user=%s)", request.user.pk, exc_info=True)
            try:  # X3 - which preference fields changed (names only, consent-gated, never the values)
                from core import product_analytics as _pa

                _pa.track_settings_changed(request.user, "learning", request.data, serializer.fields,
                                           section_of=_pa.user_pref_section)
            except Exception:
                pass
            # Study profile / Exam Mode badla to feed ke cached candidate pools purane
            # ho gaye — invalidate, taaki agla feed refresh naye settings dikhaye.
            if any(f in request.data for f in UserPreferenceSerializer.STUDY_PROFILE_FIELDS):
                try:
                    from post import feed_cache

                    feed_cache.invalidate(request.user.pk)
                except Exception:
                    logger.warning("feed cache invalidate failed (user=%s)", request.user.pk, exc_info=True)
            return Response({
                "status": True,
                "message": "Preferences updated successfully.",
                "data": serializer.data,
            }, status=status.HTTP_200_OK, headers={"ETag": make_etag(serializer.instance)})

        return Response({
            "status": False,
            "message": "Validation failed.",
            "errors": serializer.errors,
        }, status=status.HTTP_400_BAD_REQUEST)

class LearningPreferencesView(GenericAPIView):
    """
    S8 -- GET /profile/learning/ : the whole "Learning preferences" screen in ONE read-only response.

    Joins UserPreference (exam target / class / subjects / exam date / Exam Mode / study reminder / focus schedule /
    content level + languages) with Streak (daily goal) and adds "what do these choices change" codes + a setup
    completeness %. WRITES do not go here: they use the existing endpoints (PATCH /profile/preferences/me/ for the
    preference fields - offline queue + ETag from S2 keep working - and PATCH /profile/daily-goal/ for the goal),
    so there is exactly one write path per field.

    Never creates rows (GET without side effects): no UserPreference row = defaults.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(description="Learning preferences summary (read-only). Write through preferences/me/ + daily-goal/.")
    def get(self, request):
        from .. import learning_prefs

        pref = UserPreference.objects.filter(user=request.user).first() or UserPreference(user=request.user)
        streak = Streak.objects.filter(user=request.user).first()
        tz = learning_prefs.user_tz(request.user.pk)
        return Response({
            "status": True,
            "message": "Learning preferences fetched successfully.",
            "data": learning_prefs.build_summary(pref, streak, tz=tz),
        }, status=status.HTTP_200_OK)


class PrivacySettingsView(GenericAPIView):
    """
    S4 -- GET / PATCH /profile/privacy/ : the person's own privacy controls (who can message / call / tag / mention /
    add them, who sees their online status and followers list, search visibility). Rules + defaults: privacy.py.

    GET never writes (no row = all defaults). PATCH takes only the fields that changed and creates the row lazily.
    `is_private` (private account) is shown for the screen but is still changed through /profile/ as before.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = PrivacySettingsSerializer
    # X6 -- privacy controls decide who can reach / see a person: capped per hour, a refusal is audited.
    throttle_classes = [UserRateThrottle, SensitiveWriteThrottle]  # + the project-wide per-user floor
    throttle_scope = "privacy_write"

    def _body(self, request, row, message):
        from .. import privacy as privacy_rules

        data = dict(self.get_serializer(row).data)
        data["is_private"] = bool(request.user.is_private)
        # Minors: show the value that is really enforced, and tell the app which controls are locked.
        locks = privacy_rules.locks_for(request.user)
        if locks["is_minor"]:
            data = privacy_rules.apply_minor_floor(data)
        data["is_minor"] = locks["is_minor"]
        data["locked_fields"] = locks["locked_fields"]
        return {"status": True, "message": message, "data": data}

    @extend_schema(responses={200: PrivacySettingsSerializer}, description="Read my privacy controls")
    def get(self, request):
        row = PrivacySettings.objects.filter(user=request.user).first() or PrivacySettings(user=request.user)
        return Response(self._body(request, row, "Privacy settings fetched successfully."))

    @extend_schema(request=PrivacySettingsSerializer, responses={200: PrivacySettingsSerializer},
                   description="Change my privacy controls. Send only the fields you want to change.")
    def patch(self, request):
        from .. import privacy as privacy_rules

        # Minor-safety locks: a minor may not open a control to everyone / turn find-by-phone/e-mail on.
        bad = privacy_rules.minor_violations(request.user, request.data)
        if bad:
            return Response(
                {
                    "status": False,
                    "code": privacy_rules.MINOR_LOCKED_CODE,
                    "message": privacy_rules.MINOR_LOCKED_TEXT,
                    "errors": {name: [text] for name, text in bad.items()},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        with transaction.atomic():
            PrivacySettings.for_user(request.user)  # lazy get-or-create
            row = PrivacySettings.objects.select_for_update().get(user=request.user)
            serializer = self.get_serializer(row, data=request.data, partial=True)
            if not serializer.is_valid():
                return Response(
                    {"status": False, "message": "Validation failed.", "errors": serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # X6 -- which controls really change (names only, never the values) -> one audit row after the save
            _x6_names = [n for n in serializer.fields if n in request.data and not serializer.fields[n].read_only]
            _x6_before = {n: getattr(row, n, None) for n in _x6_names}
            serializer.save()
            _x6_after = {n: getattr(serializer.instance, n, None) for n in _x6_names}
        _x6_changed = _x6_changed_fields(request.data, _x6_names, _x6_before, _x6_after)
        if _x6_changed:
            _x6_record_change(_x6_audit.Event.PRIVACY_CONTROLS_CHANGED, request, request.user, _x6_changed)
        try:  # X3 - which privacy controls change (field names only, consent-gated)
            from core import product_analytics as _pa

            _pa.track_settings_changed(request.user, "privacy", request.data, serializer.fields)
        except Exception:
            pass
        return Response(self._body(request, serializer.instance, "Privacy settings updated successfully."))
