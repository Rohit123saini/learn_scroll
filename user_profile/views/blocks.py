# user_profile/views/blocks.py — split out of the old user_profile/views.py (P-C4). Code is moved verbatim.

from ..models import (
    BlockUser,
    Follow,
    RestrictUser,
)
from ..serializers import (
    BlockUserSerializer,
    ContentReportCreateSerializer,
    RestrictUserSerializer,
)
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from ..services import (
    ReportRateLimited,
    file_report,
)
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from drf_spectacular.utils import extend_schema
from .. import relationships
from rest_framework import status
from django.db import transaction


# Block / Unblock user
# Model (BlockUser) profile app me hai isliye API bhi yahin — message
# app sirf inhe consume karega (chat screen "is-blocked?" check).
from ..throttles import BlockBurstThrottle, BlockDailyThrottle, ReportBurstThrottle


class BlockedUsersView(GenericAPIView):
    """
    GET  /profile/blocked-users/          -> maine jinko block kiya hai unki list
    POST /profile/blocked-users/  {"blocked": <user_id>} -> block karo

    POST is rate limited (user_profile/throttles.py: 20/min + 200/day, shared
    with unblock); the list GET is not.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = BlockUserSerializer
    throttle_classes = [UserRateThrottle, BlockBurstThrottle, BlockDailyThrottle]

    @extend_schema(
        responses={200: BlockUserSerializer(many=True)},
        description="List of users blocked by the current user",
    )
    def get(self, request):
        qs = BlockUser.objects.filter(blocker=request.user).select_related("blocked")

        # ?q= — search my blocked list by username / name.
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(blocked__username__icontains=q)
                | Q(blocked__first_name__icontains=q)
                | Q(blocked__last_name__icontains=q)
            )

        # ?limit=&offset= — optional paging. Without `limit` the whole list
        # comes back like before (older app builds), capped at 500.
        try:
            limit = int(request.query_params.get("limit", ""))
            limit = min(max(limit, 1), 100)
        except (TypeError, ValueError):
            limit = None
        try:
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except (TypeError, ValueError):
            offset = 0

        if limit is None:
            rows, has_more = list(qs[:500]), False
        else:
            rows = list(qs[offset:offset + limit + 1])
            has_more = len(rows) > limit
            rows = rows[:limit]

        serializer = self.get_serializer(rows, many=True)
        return Response({
            "status": True,
            "message": "Blocked users fetched successfully.",
            "data": serializer.data,
            "has_more": has_more,
            "next_offset": (offset + len(rows)) if has_more else None,
        }, status=status.HTTP_200_OK)

    @extend_schema(
        request=BlockUserSerializer,
        responses={201: BlockUserSerializer, 200: BlockUserSerializer},
        description="Block a user",
    )
    @transaction.atomic
    def post(self, request):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        blocked_user = serializer.validated_data["blocked"]
        want_new_accounts = bool(serializer.validated_data.get("block_new_accounts", False))

        block_obj, created = BlockUser.objects.get_or_create(
            blocker=request.user,
            blocked=blocked_user,
            defaults={"block_new_accounts": want_new_accounts},
        )
        # Blocking again can switch "also block new accounts" on (never silently off).
        if not created and want_new_accounts and not block_obj.block_new_accounts:
            block_obj.block_new_accounts = True
            block_obj.save(update_fields=["block_new_accounts"])

        # "Block + Report" in one tap. A failed report must never undo the block.
        report_filed = False
        report_reason = serializer.validated_data.get("report_reason")
        if report_reason:
            try:
                _, report_filed = file_report(
                    request.user, "user", blocked_user.id, report_reason,
                    serializer.validated_data.get("report_details", ""),
                )
            except (LookupError, ValueError, ReportRateLimited):
                report_filed = False

        if created:
            # Block hote hi dono taraf ka follow-relation khatam karo, aur
            # jo ACCEPTED tha uska count bhi ghata do (FollowAPIView ke
            # unfollow wale logic jaisa hi).
            # `QuerySet.delete()` sends post_delete per Follow row (a
            # receiver is registered), so the counters are corrected by
            # user_profile/signals.py — no manual F() decrement needed.
            Follow.objects.filter(
                Q(follower=request.user, following=blocked_user)
                | Q(follower=blocked_user, following=request.user)
            ).delete()

        return Response({
            "status": True,
            "message": "User blocked successfully." if created else "User already blocked.",
            "report_filed": report_filed,
            "data": self.get_serializer(block_obj).data,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class ContentReportView(GenericAPIView):
    """
    POST /profile/reports/ {"target_type": "user|post|comment|story|message|class_chat|live_session|profile_photo",
                            "target_id": "...", "reason": "...", "details": "..."}

    201 first time, 200 if I had already reported the same thing. The
    reported person is never notified. Block / Restrict / Mute are separate
    calls (or "Block + Report" via `report_reason` on POST /blocked-users/).
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ContentReportCreateSerializer
    throttle_classes = [UserRateThrottle, ReportBurstThrottle]

    @extend_schema(request=ContentReportCreateSerializer, description="Report an account, post, comment or story")
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": False, "message": "Validation failed.", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        d = serializer.validated_data
        try:
            report, created = file_report(
                request.user, d["target_type"], d["target_id"], d["reason"], d.get("details", ""),
            )
        except LookupError as exc:
            return Response({"status": False, "message": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as exc:
            return Response({"status": False, "message": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except ReportRateLimited:
            return Response(
                {"status": False, "message": "Too many reports. Please try again later."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        body = {"status": True, "message": "Report received." if created else "You already reported this.", "id": report.id}
        # Copyright reports need the formal notice (copyrights app) - the app opens that form next.
        if d["reason"] == "copyright" and d["target_type"] in ("post", "story"):
            body["next"] = "copyright_claim"
            body["message"] = "To take this down, please complete the copyright notice."
        if d["reason"] == "child_safety":
            # Urgent: tell the reporter what to do next (Childline 1098 / police 112 / online portals).
            from ..child_safety import help_info

            body["next"] = "child_safety_help"
            body["help"] = help_info()
            body["message"] = help_info()["message"]
        return Response(body, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class UnblockUserView(GenericAPIView):
    """
    DELETE /profile/blocked-users/<id>/

    `<id>` ya to BlockUser record ki apni id ho sakti hai, ya seedha
    target USER ki id — dono support karte hain (chat screen seedha
    otherParticipant.id pass karta hai, alag se record-id track nahi karta).
    """
    permission_classes = [IsAuthenticated]
    serializer_class = BlockUserSerializer
    # Same two buckets as block (one shared per-user quota) so alternating
    # block / unblock can't dodge the limit.
    throttle_classes = [UserRateThrottle, BlockBurstThrottle, BlockDailyThrottle]

    @extend_schema(description="Unblock a user (accepts BlockUser id or target user id)")
    def delete(self, request, id):
        block_obj = BlockUser.objects.filter(
            Q(pk=id) | Q(blocked_id=id),
            blocker=request.user,
        ).first()

        if not block_obj:
            return Response({
                "status": False,
                "message": "Block record not found.",
            }, status=status.HTTP_404_NOT_FOUND)

        block_obj.delete()
        return Response({
            "status": True,
            "message": "User unblocked successfully.",
        }, status=status.HTTP_200_OK)


# TASK 18 — Restrict / Unrestrict user
# Deliberately mirrors BlockedUsersView/UnblockUserView just above (same
# request/response shape, POST body {"restricted": <user_id>}) for
# frontend consistency. The one functional difference from block's POST
# handler: restricting someone does NOT touch Follow rows or
# followers/following counts — see RestrictUser's docstring in
# models.py for why (restrict is silent and non-blocking by design).
class RestrictedUsersView(GenericAPIView):
    """
    GET  /profile/restricted-users/                -> maine jinko restrict kiya hai unki list
    POST /profile/restricted-users/  {"restricted": <user_id>} -> restrict karo
    """
    permission_classes = [IsAuthenticated]
    serializer_class = RestrictUserSerializer

    @extend_schema(
        responses={200: RestrictUserSerializer(many=True)},
        description="List of users restricted by the current user",
    )
    def get(self, request):
        qs = RestrictUser.objects.filter(user=request.user).select_related("restricted")
        # P8: ?q= search + optional ?limit=&offset= paging (same contract as blocked-users/;
        # without `limit` the whole list comes back like before, capped at 500).
        q = (request.query_params.get("q") or "").strip()[:relationships.MAX_QUERY_LEN]
        if q:
            qs = qs.filter(
                Q(restricted__username__icontains=q)
                | Q(restricted__first_name__icontains=q)
                | Q(restricted__last_name__icontains=q)
            )
        rows, has_more, next_offset = relationships.paged_rows(request.query_params, qs)
        serializer = self.get_serializer(rows, many=True)
        return Response({
            "status": True,
            "message": "Restricted users fetched successfully.",
            "data": serializer.data,
            "has_more": has_more,
            "next_offset": next_offset,
        }, status=status.HTTP_200_OK)

    @extend_schema(
        request=RestrictUserSerializer,
        responses={201: RestrictUserSerializer, 200: RestrictUserSerializer},
        description="Restrict a user (silent — the restricted user is never notified).",
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        restricted_user = serializer.validated_data["restricted"]

        # get_or_create, not create — same idempotency reasoning as
        # BlockedUsersView.post: a double-tap/retry should return the
        # existing record instead of a 400 from the UniqueConstraint.
        restrict_obj, created = RestrictUser.objects.get_or_create(
            user=request.user,
            restricted=restricted_user,
        )

        # No Follow/count changes here on purpose (unlike block) —
        # restrict must not change what either party can see or do,
        # only what the restricting user is exposed to from the other
        # side, and only once posts/message/notifications consume
        # `is_restricted_between()`.

        return Response({
            "status": True,
            "message": "User restricted successfully." if created else "User already restricted.",
            "data": self.get_serializer(restrict_obj).data,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class UnrestrictUserView(GenericAPIView):
    """
    DELETE /profile/restricted-users/<id>/

    Same `<id>` flexibility as UnblockUserView — either the RestrictUser
    record's own id, or the target user's id directly.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = RestrictUserSerializer

    @extend_schema(description="Unrestrict a user (accepts RestrictUser id or target user id)")
    def delete(self, request, id):
        restrict_obj = RestrictUser.objects.filter(
            Q(pk=id) | Q(restricted_id=id),
            user=request.user,
        ).first()

        if not restrict_obj:
            return Response({
                "status": False,
                "message": "Restrict record not found.",
            }, status=status.HTTP_404_NOT_FOUND)

        restrict_obj.delete()
        return Response({
            "status": True,
            "message": "User unrestricted successfully.",
        }, status=status.HTTP_200_OK)
