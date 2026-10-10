"""
copyrights/views.py - the user-facing API. Mounted at /copyright/.

  POST /copyright/claims/                       file a notice (rights holder)
  GET  /copyright/claims/                       notices I filed
  GET  /copyright/claims/<id>/
  POST /copyright/claims/<id>/withdraw/
  POST /copyright/claims/<id>/info/             answer a "need more information" request
  POST /copyright/claims/<id>/court-action/     report court action during a counter-notice
  GET  /copyright/notices/                      complaints about MY content
  GET  /copyright/notices/<id>/
  POST /copyright/notices/<id>/counter/         file a counter-notice
  GET  /copyright/standing/                     my strikes + standing

Staff decisions (review / uphold / restore / strikes / terminate) are done in
Django admin (copyrights/admin.py) - same services, same level checks.
"""

from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from . import services
from .models import CopyrightClaim, CopyrightStanding, CopyrightStrike
from .serializers import (
    ClaimantClaimSerializer, ClaimCreateSerializer, CounterNoticeCreateSerializer,
    OwnerNoticeSerializer, StandingSerializer, StrikeSerializer, TextSerializer,
)

_HTTP = {
    "not_found": 404, "own_content": 400, "incomplete": 400, "bad_state": 409, "duplicate": 409,
    "rate_limited": 429, "claimant_blocked": 403, "terminated": 403, "protected": 403,
}


def _err(exc):
    return Response(
        {"status": False, "code": exc.code, "message": exc.message},
        status=_HTTP.get(exc.code, status.HTTP_400_BAD_REQUEST),
    )


class _Base(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [UserRateThrottle]


class ClaimListCreateView(_Base):
    @extend_schema(request=ClaimCreateSerializer, description="File a copyright notice")
    def post(self, request):
        ser = ClaimCreateSerializer(data=request.data)
        if not ser.is_valid():
            return Response({"status": False, "message": "Validation failed.", "errors": ser.errors}, status=400)
        try:
            claim, created = services.submit_claim(request.user, ser.validated_data)
        except services.ClaimError as exc:
            return _err(exc)
        return Response(
            {"status": True, "message": "Notice received." if created else "You already filed a notice for this.",
             "data": ClaimantClaimSerializer(claim).data},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @extend_schema(responses=ClaimantClaimSerializer(many=True))
    def get(self, request):
        qs = CopyrightClaim.objects.filter(claimant=request.user).select_related("counter_notice")[:100]
        return Response({"status": True, "data": ClaimantClaimSerializer(qs, many=True).data})


class ClaimDetailView(_Base):
    def get(self, request, claim_id):
        claim = get_object_or_404(CopyrightClaim, id=claim_id, claimant=request.user)
        return Response({"status": True, "data": ClaimantClaimSerializer(claim).data})


class ClaimWithdrawView(_Base):
    def post(self, request, claim_id):
        claim = get_object_or_404(CopyrightClaim, id=claim_id, claimant=request.user)
        try:
            claim = services.withdraw_claim(claim, request.user)
        except services.ClaimError as exc:
            return _err(exc)
        return Response({"status": True, "data": ClaimantClaimSerializer(claim).data})


class ClaimInfoView(_Base):
    @extend_schema(request=TextSerializer)
    def post(self, request, claim_id):
        claim = get_object_or_404(CopyrightClaim, id=claim_id, claimant=request.user)
        ser = TextSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            claim = services.provide_info(claim, request.user, ser.validated_data["text"])
        except services.ClaimError as exc:
            return _err(exc)
        return Response({"status": True, "data": ClaimantClaimSerializer(claim).data})


class ClaimCourtActionView(_Base):
    @extend_schema(request=TextSerializer)
    def post(self, request, claim_id):
        claim = get_object_or_404(CopyrightClaim, id=claim_id, claimant=request.user)
        ser = TextSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            services.report_court_action(claim, request.user, ser.validated_data["text"])
        except services.ClaimError as exc:
            return _err(exc)
        claim.refresh_from_db()
        return Response({"status": True, "data": ClaimantClaimSerializer(claim).data})


class NoticeListView(_Base):
    @extend_schema(responses=OwnerNoticeSerializer(many=True))
    def get(self, request):
        qs = CopyrightClaim.objects.filter(content_owner=request.user).select_related("counter_notice", "strike")[:100]
        return Response({"status": True, "data": OwnerNoticeSerializer(qs, many=True).data})


class NoticeDetailView(_Base):
    def get(self, request, claim_id):
        claim = get_object_or_404(CopyrightClaim, id=claim_id, content_owner=request.user)
        return Response({"status": True, "data": OwnerNoticeSerializer(claim).data})


class NoticeCounterView(_Base):
    @extend_schema(request=CounterNoticeCreateSerializer)
    def post(self, request, claim_id):
        claim = get_object_or_404(CopyrightClaim, id=claim_id, content_owner=request.user)
        ser = CounterNoticeCreateSerializer(data=request.data)
        if not ser.is_valid():
            return Response({"status": False, "message": "Validation failed.", "errors": ser.errors}, status=400)
        try:
            services.file_counter_notice(request.user, claim, ser.validated_data)
        except services.ClaimError as exc:
            return _err(exc)
        except PermissionDenied as exc:
            return Response({"status": False, "message": str(exc)}, status=403)
        claim.refresh_from_db()
        return Response({"status": True, "data": OwnerNoticeSerializer(claim).data}, status=status.HTTP_201_CREATED)


class StandingView(_Base):
    def get(self, request):
        standing, _ = CopyrightStanding.objects.get_or_create(user=request.user)
        strikes = CopyrightStrike.objects.filter(user=request.user).order_by("-issued_at")[:20]
        return Response({
            "status": True,
            "data": {**StandingSerializer(standing).data, "strikes": StrikeSerializer(strikes, many=True).data},
        })
