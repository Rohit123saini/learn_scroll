# user_profile/views/profile_update.py — split out of the old user_profile/views.py (P-C4). Code is moved verbatim.

from rest_framework.views import APIView
from rest_framework.parsers import (
    FormParser,
    MultiPartParser,
)
from rest_framework.generics import GenericAPIView
from django.db import (
    IntegrityError,
    transaction,
)
from rest_framework.permissions import IsAuthenticated
from ..serializers import ProfileUpdateSerializer
from rest_framework.response import Response
from rest_framework.throttling import (
    ScopedRateThrottle,
    UserRateThrottle,
)
from login.sensitive import (
    SensitiveWriteThrottle,
    changed_fields as _x6_changed_fields,
    record_change as _x6_record_change,
)
from login import audit as _x6_audit
from drf_spectacular.utils import extend_schema
from .. import profile_header
from django.conf import settings
from rest_framework import status
from ._common import (
    _refuse_adult_profile_photo,
    logger,
)


# X6 -- profile fields whose change is written to the audit trail (login.AuthAuditLog) -> event name.
_X6_PROFILE_SENSITIVE = {
    "username": _x6_audit.Event.USERNAME_CHANGED,
    "date_of_birth": _x6_audit.Event.DOB_CHANGED,
    "is_private": _x6_audit.Event.ACCOUNT_PRIVACY_CHANGED,
}


class UpdateProfileView(GenericAPIView):
    """
    Update logged-in user's profile
    Allowed fields: username, first_name, last_name, bio, profile_photo, is_private,
    pronouns, category_label, links (max 5), location, cover_photo, remove_profile_photo, remove_cover_photo
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ProfileUpdateSerializer
    parser_classes = [MultiPartParser, FormParser]  # Image upload ke liye zaruri
    # X6 -- profile edits (username / DOB / private account) are writes to the account's identity: capped per hour.
    throttle_classes = [UserRateThrottle, SensitiveWriteThrottle]  # + the project-wide per-user floor
    throttle_scope = "profile_update"

    @extend_schema(
        request=ProfileUpdateSerializer,
        responses={200: ProfileUpdateSerializer},
        description="Update current user's profile. Send only fields you want to update.",
    )
    def patch(self, request):
        # Cheap early reject: an oversized upload is refused from the
        # Content-Length header alone, before the body is parsed/spooled.
        # (Not trusted on its own — SafeProfilePhotoField re-checks the
        # real file size — this just avoids parsing an obviously huge body.)
        max_bytes = getattr(settings, "PROFILE_PHOTO_MAX_BYTES", 5 * 1024 * 1024)
        try:
            declared = int(request.META.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            declared = 0
        if declared > max_bytes + 1024 * 1024:  # +1 MB slack for the other form fields
            return Response({
                "status": False,
                "message": "Upload too large.",
                "errors": {"profile_photo": [f"Photo must be at most {max_bytes // (1024 * 1024)} MB."]},
            }, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        serializer = self.get_serializer(
            request.user,
            data=request.data,
            partial=True,
            context={"request": request},
        )

        if serializer.is_valid():
            # P-F15: a new profile / cover photo goes through the optional adult-content provider
            # (common/media_moderation.py, needs settings.MEDIA_NSFW_PROVIDER; no provider = nothing happens).
            # A hit that is strong enough is refused (a profile has no "held" state); nothing is saved.
            _photo_refused = _refuse_adult_profile_photo(request)
            if _photo_refused is not None:
                return _photo_refused
            try:  # X3 - completeness BEFORE the save (the instance is mutated by it)
                completeness_before = profile_header.profile_completeness(request.user)
            except Exception:
                completeness_before = None
            # X6 - values BEFORE the save (same reason); only the field NAMES that changed are ever audited.
            sensitive_before = {k: getattr(request.user, k, None) for k in _X6_PROFILE_SENSITIVE}
            try:
                with transaction.atomic():
                    serializer.save()
            except IntegrityError:
                # P1: two people grabbed the same username in the same instant (the DB unique key decided).
                return Response({
                    "status": False,
                    "message": "Validation failed.",
                    "errors": {"username": [profile_header.username_message("taken")]},
                    "username_code": "taken",
                }, status=status.HTTP_400_BAD_REQUEST)
            try:  # X6 - audit trail for identity-level changes (field names only, never the values)
                sensitive_after = {k: getattr(request.user, k, None) for k in _X6_PROFILE_SENSITIVE}
                for _field, _event in _X6_PROFILE_SENSITIVE.items():
                    if _x6_changed_fields(request.data, [_field], sensitive_before, sensitive_after):
                        _x6_record_change(_event, request, request.user, [_field])
            except Exception:
                logger.warning("profile sensitive-change audit failed (user=%s)", request.user.pk, exc_info=True)
            try:  # X3 - profile funnel: which field got filled, new progress bucket, reached 100 % (consent-gated)
                from core import product_analytics as _pa

                _pa.track_profile_change(request.user, completeness_before,
                                         profile_header.profile_completeness(request.user))
            except Exception:
                pass
            return Response({
                "status": True,
                "message": "Profile updated successfully.",
                "data": serializer.data,
            }, status=status.HTTP_200_OK)

        errors = serializer.errors
        body = {"status": False, "message": "Validation failed.", "errors": errors}
        uname = errors.get("username")
        if uname:  # P1: machine-readable reason (taken | limit | reserved | too_short | bad_chars ...) for the app's own texts
            body["username_code"] = getattr(uname[0], "code", "") or "invalid"
        dob = errors.get("date_of_birth")
        if dob:  # P7: dob_future | dob_too_young | dob_invalid | dob_locked (the app has its own EN/HI text per code)
            body["dob_code"] = getattr(dob[0], "code", "") or "dob_invalid"
        return Response(body, status=status.HTTP_400_BAD_REQUEST)


class UsernameCheckView(APIView):
    """GET /profile/username/check/?username=<name>   (P1)

    "Is this name free?" while typing (the app debounces ~400 ms). Always 200 with
        {"status": true, "data": {"username", "valid", "available", "code", "message",
                                  "can_change", "next_change_at", "days_left", "window_days"}}
    code: ok | same | empty | too_short | too_long | bad_chars | bad_edges | all_digits | reserved | limit | taken.
    Throttled (profile_username_check) so it can't be used to list every taken name.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "profile_username_check"

    def get(self, request):
        raw = request.query_params.get("username")
        if raw is None:
            return Response({"status": False, "message": "username is required."}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"status": True, "data": profile_header.check_username(request.user, raw[:100])})
