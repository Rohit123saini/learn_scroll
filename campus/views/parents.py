# campus/views/parents.py — split out of the old campus/views.py (P-C4). Code is moved verbatim.

from rest_framework.views import APIView
from ..models import (
    AcademicSession,
    Campus,
    CampusAnalyticsSnapshot,
    CampusParentLink,
    StudentEnrollment,
)
from ..serializers import (
    CampusAnalyticsSnapshotSerializer,
    CampusParentLinkSerializer,
)
from ..throttles import CampusParentLinkVerifyThrottle
from rest_framework.permissions import IsAuthenticated
from ..permissions import (
    IsCampusAdminOrPrincipal,
    is_campus_approved,
)
from message.models import ParentAccessCode
from django.db.models import (
    Prefetch,
    Q,
)
from rest_framework.response import Response
from rest_framework.decorators import action
from django.core.cache import cache
from rest_framework import (
    status,
    viewsets,
)
from ._common import (
    CampusMemberScopedMixin,
    logger,
)


class CampusAnalyticsSnapshotViewSet(CampusMemberScopedMixin, viewsets.ReadOnlyModelViewSet):
    """Read-only — rows are written exclusively by the
    `campus-refresh-analytics-snapshot` Celery task (design doc §8),
    never from a request."""
    serializer_class = CampusAnalyticsSnapshotSerializer
    campus_field_path = "campus"
    member_scope = "none"
    permission_classes = [IsAuthenticated, IsCampusAdminOrPrincipal]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(CampusAnalyticsSnapshot.objects.all(), self.request)

    @action(detail=False, methods=["get"])
    def latest(self, request):
        campus_id = request.query_params.get("campus")
        if not campus_id:
            return Response({"detail": "campus query param is required."}, status=status.HTTP_400_BAD_REQUEST)
        snapshot = self.get_queryset().filter(campus_id=campus_id).order_by("-computed_at").first()
        if not snapshot:
            # T0.5 — "abhi snapshot nahi bana" error nahi, ek normal empty state hai
            # (naya campus / Celery task abhi chala nahi). Pehle 404 => client/logs me noise.
            # Ab 200 {empty: true}, aur ek best-effort refresh job queue ho jaata hai
            # (5 min me ek baar per campus) taaki agli baar data mile.
            return Response({
                "empty": True,
                "campus": str(campus_id),
                "computed_at": None,
                "data": {},
                "refresh_queued": self._queue_snapshot_refresh(request, campus_id),
            })
        return Response(self.get_serializer(snapshot).data)

    def _queue_snapshot_refresh(self, request, campus_id):
        from django.core.cache import cache

        from ..models import AcademicSession
        from ..tasks import refresh_analytics_snapshot

        gate = f"campus_snapshot_refresh_queued:{campus_id}"
        try:
            if not cache.add(gate, 1, 300):
                return False  # abhi-abhi queue hua tha
            # Scope check: sirf apne member-campus ke session pe job (same visibility rules).
            session = (
                self.filter_queryset_to_my_campuses(AcademicSession.objects.all(), request)
                .filter(campus_id=campus_id, is_current=True)
                .first()
            )
            if session is None:
                cache.delete(gate)
                return False
            refresh_analytics_snapshot.delay(str(campus_id), str(session.id))
            return True
        except Exception:  # broker down etc. — read endpoint kabhi fail nahi hona chahiye
            logger.warning("analytics snapshot refresh could not be queued campus=%s", campus_id, exc_info=True)
            return False


class CampusParentLinkViewSet(CampusMemberScopedMixin, viewsets.ReadOnlyModelViewSet):
    """
    Read-only — the only write path is `ParentLinkVerifyView` below,
    which requires a verified `message.ParentAccessCode`/token (via
    `bridge.resolve_parent_from_token`), never a plain POST here (see
    `CampusParentLink`'s model docstring).
    """
    serializer_class = CampusParentLinkSerializer
    campus_field_path = "campus"
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = self.filter_queryset_to_my_campuses(CampusParentLink.objects.all(), self.request)
        user = self.request.user
        qs = qs.filter(Q(parent=user) | Q(student=user) | Q(campus__staff_profiles__user=user, campus__staff_profiles__is_active=True)).distinct()
        # [PERF] student_detail / parent_detail joined; active_enrollment prefetched (was 3 queries per link).
        return qs.select_related("student", "parent").prefetch_related(Prefetch(
            "student__campus_enrollments",
            queryset=StudentEnrollment.objects.filter(status=StudentEnrollment.Status.ACTIVE)
            .select_related("section__school_class").order_by("-session__start_date"),
            to_attr="_active_enrollments_prefetched",
        ))


class ParentLinkVerifyView(APIView):
    """
    `POST {"campus": <id>, "token": "<parent access code>"}`.

    🔧 FIX (this pass) — this view used to call
    `bridge.resolve_parent_from_token(token)` and unpack the result as
    `parent_user, student_user = ...`. `resolve_parent_from_token()`
    actually returns a `ParentTokenResolution` object with `.student` /
    `.parent_access_code` attributes (Parent Mode is deliberately
    loginless — see `message.models.ParentAccessCode`'s own docstring; it
    has no `.parent` user concept at all) and is not a 2-tuple, so that
    unpack raised a `ValueError`/`TypeError` on every single real call —
    this endpoint 500'd unconditionally. See campus/parent_invite.py's
    module docstring for the original bug note; this view is now fixed
    to resolve directly against `message.ParentAccessCode` instead,
    exactly the way the newer `CampusParentLinkConfirmView` below already
    does, rather than going through the mismatched bridge call. Logic is
    intentionally identical to `CampusParentLinkConfirmView.post()` — kept
    as a separate endpoint only so any existing caller of this URL/name
    (e.g. `CampusService.verifyParentLink()` in the Flutter app) keeps
    working without needing to switch call sites.
    """
    permission_classes = [IsAuthenticated]
    # B-4 fix — see CampusParentLinkVerifyThrottle's docstring in
    # throttles.py: this whole view is a single POST action, so a
    # class-level throttle_classes (no get_throttles() override needed)
    # is enough, unlike NoticeViewSet/FeePaymentViewSet/
    # CampusLiveSessionViewSet above which each have other, unrelated
    # actions that should keep the project-wide default.
    throttle_classes = [CampusParentLinkVerifyThrottle]

    def post(self, request):
        # Accepts either "token" (this endpoint's original, pre-existing
        # param name) or "code" (the name the rest of the parent-invite
        # feature uses) so old and new callers both work unchanged.
        code = (request.data.get("token") or request.data.get("code") or "").strip()
        campus_id = request.data.get("campus")
        if not code or not campus_id:
            return Response({"detail": "campus and token are required."}, status=status.HTTP_400_BAD_REQUEST)

        campus = Campus.objects.filter(pk=campus_id).first()
        if not campus:
            return Response({"detail": "Campus not found."}, status=status.HTTP_404_NOT_FOUND)
        # G-2 fix — linking a parent is another "pull someone else into
        # this campus" action (see `StaffProfileViewSet`'s docstring for
        # the shared reasoning).
        if not is_campus_approved(campus.id):
            return Response(
                {"detail": "This campus is pending platform verification and can't link parents yet."},
                status=status.HTTP_403_FORBIDDEN,
            )

        access_code = ParentAccessCode.objects.filter(code=code, is_active=True).select_related("student").first()
        if not access_code or access_code.is_expired:
            return Response({"detail": "Invalid or expired token."}, status=status.HTTP_400_BAD_REQUEST)

        student = access_code.student
        if not StudentEnrollment.objects.filter(
            student=student, status=StudentEnrollment.Status.ACTIVE, section__school_class__campus_id=campus.id,
        ).exists():
            return Response(
                {"detail": "This student isn't enrolled at this campus."}, status=status.HTTP_400_BAD_REQUEST,
            )

        link, created = CampusParentLink.objects.get_or_create(
            campus=campus, student=student, parent=request.user,
        )
        return Response(
            CampusParentLinkSerializer(link).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
