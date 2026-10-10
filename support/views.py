"""support/views.py — Help & feedback API. Mounted at /support/ (see urls.py)."""
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.pagination import StandardPagination

from . import services
from .models import BugReport, FeatureRequest, FeatureVote, SupportMessage, SupportTicket
from .serializers import (
    BugReportCreateSerializer,
    BugReportSerializer,
    FeatureRequestCreateSerializer,
    FeatureRequestSerializer,
    MessageCreateSerializer,
    SupportMessageSerializer,
    TicketCreateSerializer,
    TicketDetailSerializer,
    TicketListSerializer,
)
from .throttles import (
    BugReportThrottle,
    FeatureRequestThrottle,
    FeatureVoteThrottle,
    TicketCreateThrottle,
    TicketMessageThrottle,
)

MAX_OPEN_TICKETS_PER_USER = 10


# ---------------------------------------------------------------- tickets
class TicketListCreateView(APIView):
    """GET /support/tickets/  — my tickets, newest activity first.
       POST /support/tickets/ — {subject, category, message}."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [TicketCreateThrottle]

    def get(self, request):
        qs = SupportTicket.objects.filter(user=request.user)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(TicketListSerializer(page, many=True).data)

    def post(self, request):
        ser = TicketCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        open_count = SupportTicket.objects.filter(
            user=request.user, status__in=[SupportTicket.Status.OPEN, SupportTicket.Status.ANSWERED]
        ).count()
        if open_count >= MAX_OPEN_TICKETS_PER_USER:
            return Response(
                {"detail": "You already have several open tickets. Please wait for a reply or close one first."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        d = ser.validated_data
        with transaction.atomic():
            ticket = SupportTicket.objects.create(user=request.user, subject=d["subject"], category=d["category"])
            SupportMessage.objects.create(ticket=ticket, sender=request.user, is_staff=False, body=d["message"])
        return Response(TicketDetailSerializer(ticket).data, status=status.HTTP_201_CREATED)


class TicketDetailView(APIView):
    """GET /support/tickets/<id>/ — the whole thread. Opening it clears the unread flag."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        ticket = get_object_or_404(SupportTicket, pk=pk, user=request.user)
        if ticket.has_unread_reply:
            SupportTicket.objects.filter(pk=ticket.pk).update(has_unread_reply=False)
            ticket.has_unread_reply = False
        return Response(TicketDetailSerializer(ticket).data)


class TicketMessageCreateView(APIView):
    """POST /support/tickets/<id>/messages/ — {body}. Reopens an answered/resolved ticket."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [TicketMessageThrottle]

    def post(self, request, pk):
        ticket = get_object_or_404(SupportTicket, pk=pk, user=request.user)
        ser = MessageCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            msg = services.add_user_message(ticket, request.user, ser.validated_data["body"])
        except services.TicketClosed:
            return Response({"detail": "This ticket is closed. Please open a new one."}, status=status.HTTP_409_CONFLICT)
        except services.TicketFull:
            return Response({"detail": "This conversation is too long. Please open a new ticket."},
                            status=status.HTTP_409_CONFLICT)
        return Response(SupportMessageSerializer(msg).data, status=status.HTTP_201_CREATED)


class TicketCloseView(APIView):
    """POST /support/tickets/<id>/close/ — the user is done; no more replies."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        ticket = get_object_or_404(SupportTicket, pk=pk, user=request.user)
        if ticket.status != SupportTicket.Status.CLOSED:
            ticket.status = SupportTicket.Status.CLOSED
            ticket.save(update_fields=["status", "updated_at"])
        return Response(TicketListSerializer(ticket).data)


# ------------------------------------------------------------ bug reports
class BugReportView(APIView):
    """POST /support/bug-reports/ (multipart; screenshot optional) · GET = my reports."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    throttle_classes = [BugReportThrottle]

    def get(self, request):
        qs = BugReport.objects.filter(user=request.user)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(
            BugReportSerializer(page, many=True, context={"request": request}).data
        )

    def post(self, request):
        ser = BugReportCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        report = ser.save(user=request.user)
        return Response(
            BugReportSerializer(report, context={"request": request}).data, status=status.HTTP_201_CREATED
        )


# -------------------------------------------------------- feature requests
class FeatureRequestListCreateView(GenericAPIView):
    """GET /support/feature-requests/?sort=top|new&status=&q=
       POST /support/feature-requests/ — {title, description}; the author's own vote is added."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [FeatureRequestThrottle]
    serializer_class = FeatureRequestSerializer

    def get_queryset(self):
        req = self.request
        qs = (
            FeatureRequest.objects.filter(is_hidden=False)
            .select_related("author")
            .annotate(has_voted=Exists(FeatureVote.objects.filter(request_id=OuterRef("pk"), user=req.user)))
        )
        st = req.query_params.get("status")
        if st in FeatureRequest.Status.values:
            qs = qs.filter(status=st)
        q = (req.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))
        if req.query_params.get("sort") == "new":
            return qs.order_by("-created_at")
        return qs.order_by("-votes_count", "-created_at")

    def get(self, request):
        paginator = StandardPagination()
        page = paginator.paginate_queryset(self.get_queryset(), request, view=self)
        return paginator.get_paginated_response(
            FeatureRequestSerializer(page, many=True, context={"request": request}).data
        )

    def post(self, request):
        ser = FeatureRequestCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        # Cheap duplicate guard: same author, same title (case-insensitive).
        if FeatureRequest.objects.filter(author=request.user, title__iexact=d["title"]).exists():
            return Response({"detail": "You already suggested this."}, status=status.HTTP_409_CONFLICT)

        with transaction.atomic():
            fr = FeatureRequest.objects.create(author=request.user, title=d["title"], description=d["description"])
            FeatureVote.objects.create(request=fr, user=request.user)  # the author wants it too

        # Public text -> same flag-only auto-moderation as posts/comments.
        try:
            from user_profile.automod import screen_and_flag

            screen_and_flag(
                target_type="feature", target_id=fr.pk, user_id=request.user.pk,
                text=f"{fr.title} {fr.description}", surface="post",
            )
        except Exception:
            pass

        fr = self._with_vote_flag(fr, request.user)
        return Response(FeatureRequestSerializer(fr, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @staticmethod
    def _with_vote_flag(fr, user):
        return (
            FeatureRequest.objects.filter(pk=fr.pk)
            .select_related("author")
            .annotate(has_voted=Exists(FeatureVote.objects.filter(request_id=OuterRef("pk"), user=user)))
            .get()
        )


class FeatureVoteView(APIView):
    """POST /support/feature-requests/<id>/vote/ — toggles my vote.
    Returns {voted, votes_count}."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [FeatureVoteThrottle]

    def post(self, request, pk):
        fr = get_object_or_404(FeatureRequest, pk=pk, is_hidden=False)
        if fr.status in (FeatureRequest.Status.SHIPPED, FeatureRequest.Status.DECLINED):
            return Response({"detail": "Voting is closed for this request."}, status=status.HTTP_409_CONFLICT)

        existing = FeatureVote.objects.filter(request=fr, user=request.user)
        if existing.exists():
            existing.delete()
            voted = False
        else:
            try:
                with transaction.atomic():
                    FeatureVote.objects.create(request=fr, user=request.user)
                voted = True
            except IntegrityError:  # double-tap race: the other request already voted
                voted = True
        fr.refresh_from_db(fields=["votes_count"])
        return Response({"voted": voted, "votes_count": fr.votes_count})
