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
  POST /copyright/verify-email/                 send a 6-digit code to the address you will put on the notice
  POST /copyright/verify-email/confirm/         {email, code}  (required before filing)
  GET/POST /copyright/claims/<id>/evidence/     list / upload proof of ownership (multipart field "files")
  GET  /copyright/standing/                     my strikes + standing
  POST /copyright/strikes/<id>/appeal/          appeal one strike  {"message": "..."}
  GET  /copyright/appeals/                      my strike appeals
  POST /copyright/termination-appeal/           PUBLIC (a closed account can't log in): {"identifier", "contact_email", "message"}
  GET  /copyright/policy/                       PUBLIC: policy numbers + grievance officer / DMCA agent (JSON)
  GET  /copyright/policy/page/                  PUBLIC: the same as a web page (also at /legal/copyright-policy/)

Staff decisions (review / uphold / restore / strikes / terminate) are done in
Django admin (copyrights/admin.py) - same services, same level checks.
"""

from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from rest_framework.throttling import AnonRateThrottle
from common.open_throttles import PublicReadThrottle  # P-B3

from . import appeals, claimant as claimant_rules, policy, services, transparency
from .models import CopyrightAppeal, CopyrightClaim, CopyrightStanding, CopyrightStrike
from .serializers import (
    AppealSerializer, ClaimantClaimSerializer, ClaimCreateSerializer, CounterNoticeCreateSerializer, EmailCodeSerializer,
    EmailSerializer, EvidenceSerializer,
    OwnerNoticeSerializer, StandingSerializer, StrikeAppealCreateSerializer, StrikeSerializer, TextSerializer,
    TerminationAppealCreateSerializer,
)

_HTTP = {
    "not_found": 404, "own_content": 400, "incomplete": 400, "bad_state": 409, "duplicate": 409,
    "rate_limited": 429, "claimant_blocked": 403, "terminated": 403, "protected": 403,
    "email_unverified": 403, "bad_code": 400, "code_expired": 400,
    "four_eyes": 403,
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
            claim, created = services.submit_claim(
                request.user, ser.validated_data, evidence_files=request.FILES.getlist("evidence"))
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


class StrikeAppealView(_Base):
    @extend_schema(request=StrikeAppealCreateSerializer, responses=AppealSerializer)
    def post(self, request, strike_id):
        ser = StrikeAppealCreateSerializer(data=request.data)
        if not ser.is_valid():
            return Response({"status": False, "message": "Validation failed.", "errors": ser.errors}, status=400)
        try:
            appeal = appeals.file_strike_appeal(request.user, strike_id, ser.validated_data["message"])
        except services.ClaimError as exc:
            return _err(exc)
        return Response({"status": True, "message": "Appeal received.", "data": AppealSerializer(appeal).data},
                        status=status.HTTP_201_CREATED)


class MyAppealsView(_Base):
    @extend_schema(responses=AppealSerializer(many=True))
    def get(self, request):
        qs = CopyrightAppeal.objects.filter(user=request.user)[:100]
        return Response({"status": True, "data": AppealSerializer(qs, many=True).data})


class TerminationAppealThrottle(AnonRateThrottle):
    scope = "copyright_termination_appeal"


class TerminationAppealView(APIView):
    """Public on purpose: a terminated account can't log in. Always the same answer, so it
    can't be used to find out which accounts exist or were terminated."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [TerminationAppealThrottle]

    @extend_schema(request=TerminationAppealCreateSerializer)
    def post(self, request):
        ser = TerminationAppealCreateSerializer(data=request.data)
        if not ser.is_valid():
            return Response({"status": False, "message": "Validation failed.", "errors": ser.errors}, status=400)
        d = ser.validated_data
        appeals.file_termination_appeal(d["identifier"], d["contact_email"], d["message"])
        return Response({"status": True, "message": appeals.GENERIC_TERMINATION_REPLY}, status=status.HTTP_202_ACCEPTED)


class PolicyView(APIView):
    """Public (no login): the app shows this on its Copyright policy screen."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PublicReadThrottle]  # P-B3

    def get(self, request):
        return Response({"status": True, "data": policy.policy_context()})


def policy_page(request):
    """Public web page - link it from the Play Store / App Store listing, Terms and Privacy."""
    return HttpResponse(policy.render_policy_html())


class TransparencyThrottle(AnonRateThrottle):
    scope = "copyright_transparency"


class TransparencyView(APIView):
    """Public (no login): counts of claims / removals / restorations for one period - see transparency.py.
    ?year=2026&half=1 | ?year=2026 | ?from=2026-01-01&to=2026-06-30 ; default = the last 180 days."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [TransparencyThrottle]

    def get(self, request):
        if not transparency.is_public():
            return Response({"status": False, "message": "Not published."}, status=status.HTTP_404_NOT_FOUND)
        try:
            report = transparency.get_report(request.query_params)
        except ValueError as exc:
            return Response({"status": False, "message": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"status": True, "data": report})


def transparency_page(request):
    """Public web page - link it from the Copyright Policy, Terms and the store listing."""
    if not transparency.is_public():
        return HttpResponse("Not published.", status=404, content_type="text/plain")
    try:
        report = transparency.get_report(request.GET)
    except ValueError as exc:
        return HttpResponse(str(exc), status=400, content_type="text/plain")
    return HttpResponse(transparency.render_html(report))


class EmailCodeSendView(_Base):
    @extend_schema(request=EmailSerializer)
    def post(self, request):
        ser = EmailSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            claimant_rules.send_email_code(request.user, ser.validated_data["email"])
        except services.ClaimError as exc:
            return _err(exc)
        return Response({"status": True, "message": "We sent a 6-digit code to that address."})


class EmailCodeConfirmView(_Base):
    @extend_schema(request=EmailCodeSerializer)
    def post(self, request):
        ser = EmailCodeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            claimant_rules.confirm_email_code(request.user, ser.validated_data["email"], ser.validated_data["code"])
        except services.ClaimError as exc:
            return _err(exc)
        return Response({"status": True, "message": "E-mail verified."})


class ClaimEvidenceView(_Base):
    def get(self, request, claim_id):
        claim = get_object_or_404(CopyrightClaim, id=claim_id, claimant=request.user)
        rows = claim.evidence.exclude(kind="content_snapshot")
        return Response({"status": True, "data": EvidenceSerializer(rows, many=True).data})

    def post(self, request, claim_id):
        claim = get_object_or_404(CopyrightClaim, id=claim_id, claimant=request.user)
        files = request.FILES.getlist("files")
        if not files:
            return Response({"status": False, "code": "incomplete", "message": "Attach at least one file."}, status=400)
        try:
            rows = services.add_claim_evidence(claim, request.user, files, kind=request.data.get("kind") or "ownership_proof")
        except services.ClaimError as exc:
            return _err(exc)
        claim.refresh_from_db()
        return Response({"status": True, "data": EvidenceSerializer(rows, many=True).data}, status=201)
