"""
core/views.py

NotificationViewSet + NotificationPreferenceView, moved here from
liveclass/views.py (task 42) and generalized (tasks 45/46) now that
Notification is a single shared table covering both liveclass and
message-app event types.

Endpoints (wired in core/urls.py, mounted under whatever prefix the root
urlconf gives `core.urls` — see that file):

    GET    notifications/                 list, newest first
                                           (?is_read=true/false, ?source=message|liveclass)
    GET    notifications/{id}/            retrieve one
    DELETE notifications/{id}/            clear one (own only)
    GET    notifications/unread-count/    badge count for the bell icon
    POST   notifications/{id}/mark-read/  mark one as read
    POST   notifications/mark-all-read/   mark every unread one as read
    GET/PATCH notification-preferences/me/   always the caller's own row
"""
from django.utils import timezone
from rest_framework import mixins, pagination, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Notification, NotificationPreference
from .serializers import NotificationPreferenceSerializer, NotificationSerializer


def _is_truthy(value) -> bool:
    """Parse a query-string flag like ?is_read=1 / ?is_read=true properly
    — a bare `if request.query_params.get(...)` would treat "false" as
    truthy since it's a non-empty string."""
    return str(value).strip().lower() in ("1", "true", "yes", "on")


class NotificationPagination(pagination.LimitOffsetPagination):
    """Simple limit/offset pagination (?limit=&offset=, default 30, max
    100) rather than DRF's PageNumberPagination — this app doesn't assume
    a project-wide DEFAULT_PAGINATION_CLASS. Also folds `unread_count`
    into every list response (task 46's "count, unread_count, results"
    shape) so the client gets the badge number for free on the same
    request that populates the bell dropdown, no second round-trip."""

    default_limit = 30
    max_limit = 100

    def get_paginated_response(self, data):
        unread_count = Notification.objects.filter(recipient=self.request.user, is_read=False).count()
        return Response(
            {
                "count": self.count,
                "unread_count": unread_count,
                "results": data,
            }
        )


class NotificationViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Own notifications only — nobody can read or clear anyone else's."""

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = NotificationPagination

    def get_queryset(self):
        qs = Notification.objects.filter(recipient=self.request.user).select_related("classroom", "session")

        is_read = self.request.query_params.get("is_read")
        if is_read is not None:
            qs = qs.filter(is_read=_is_truthy(is_read))

        # task 46 — unified list, split-able by ?source=message|liveclass
        # until Phase 5 (Posts/Follow/Like) joins this same system and the
        # split stops being a two-way one.
        source = self.request.query_params.get("source")
        if source == "message":
            qs = qs.filter(notif_type__in=Notification.MESSAGE_APP_TYPES)
        elif source == "liveclass":
            qs = qs.exclude(notif_type__in=Notification.MESSAGE_APP_TYPES)

        return qs

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        # task 45 — single badge count for everyone, regardless of which
        # app produced the notification, since it's one shared table now.
        count = Notification.objects.filter(recipient=request.user, is_read=False).count()
        return Response({"unread_count": count})

    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        notification.mark_read()
        return Response(NotificationSerializer(notification).data)

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        # Bulk UPDATE instead of looping + calling .mark_read() per row —
        # a user with hundreds of unread notifications shouldn't cost
        # hundreds of UPDATE statements for one "clear my badge" tap.
        updated = Notification.objects.filter(recipient=request.user, is_read=False).update(
            is_read=True, read_at=timezone.now()
        )
        return Response({"marked_read": updated})


class NotificationPreferenceView(APIView):
    """Always exactly one row: the caller's own. There is no id in the
    URL and no way to read or write anyone else's preferences — same
    "own data only" boundary as NotificationViewSet above."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        pref = NotificationPreference.for_user(request.user)
        return Response(NotificationPreferenceSerializer(pref).data)

    def patch(self, request):
        pref = NotificationPreference.for_user(request.user)
        serializer = NotificationPreferenceSerializer(pref, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)