# core/views.py
"""
NotificationViewSet + NotificationPreferenceView, moved here from
liveclass/views.py (task 42) and generalized (tasks 45/46) now that
Notification is a single shared table covering both liveclass and
message-app event types.

SearchView (Task 18) is the unified "search everything" endpoint. It is
the ONLY place that builds each source's permission-scoped queryset —
`core.search` itself never queries a model directly (golden rule, see
that module's own docstring). Every scoped queryset built below either
mirrors an existing viewset's own scoping exactly (assignment) or reuses
the same underlying entitlement table a bridge module already treats as
the source of truth (testseries' campus roster), so this endpoint can
never surface a row a user couldn't already reach through the normal UI
for that source.

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
    GET    search/                        Task 18 — unified cross-app search
                                           (?q=..., optional ?sources=a,b,c)
"""
from django.db.models import Q
from django.utils import timezone
from rest_framework import mixins, pagination, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Notification, NotificationPreference
from .search import search_everything
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


class SearchView(APIView):
    """[Task 18] GET /core/search/?q=...&sources=assignment,testseries,...

    Unified "search everything" endpoint. `q` is required; `sources` is
    an optional comma-separated subset of `core.search.SOURCES` keys —
    omit it to search every source this view knows how to scope.

    This view builds one already-permission-scoped queryset PER SOURCE,
    then hands them all to `core.search.search_everything()` for the
    actual ranking/merging — `core.search` itself never touches a model
    directly (see that module's own docstring for why). Each queryset
    below is either a direct mirror of that source's own existing
    viewset scoping, or built from the same entitlement table a bridge
    module already treats as the source of truth — never a new,
    independently-maintained copy of an access rule.

    STATUS (Task 18 pass):
      - ✅ assignment — mirrors `AssignmentViewSet.get_queryset()`
        (assignment/views.py) exactly: staff see everything, everyone
        else only what they posted or a personal assignment they hold
        a submission for. Campus/liveclass-sourced assignments are
        deliberately excluded here for non-staff too — that viewset
        already keeps them out (surfaced only through campus's/
        liveclass's own thin-proxy viewsets, per that file's own
        docstring), so this endpoint replicates that restriction
        rather than loosening it just because it's a search endpoint.
      - ✅ testseries — individual/published (marketplace) series, a
        user's own created series, series they've actually attempted,
        PLUS campus-context series for sections the user is actively
        enrolled in — resolved via `campus.StudentEnrollment`, the
        exact same roster source `campus.bridge.create_testseries()`
        itself uses to decide who a campus series' roster is (see that
        function's own docstring). This is reading the same table that
        bridge already treats as the source of truth, not inventing a
        new rule.
        Liveclass-context series (`source="liveclass"`) are NOT
        included — no roster/entitlement resolver for testseries
        exists on the liveclass side yet (`liveclass/bridge.py` only
        has assignment functions as of this pass). Those rows are
        simply absent from search results, never leaked; add a branch
        here once that resolver exists.
      - ⏳ message / campus_notice — NOT wired in this pass (out of
        Task 18's scope, which is assignment + testseries only). Their
        own scoped-queryset builders belong here too once that's
        tasked — `core.search.SOURCES` already has both registered,
        this view just doesn't build a queryset for them yet, so
        passing `?sources=message` today returns no message results
        rather than an error (see `search_everything()`'s own "silently
        skipped, not an error" contract).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = request.query_params.get("q", "")
        raw_sources = request.query_params.get("sources")
        requested_sources = [s.strip() for s in raw_sources.split(",") if s.strip()] if raw_sources else None

        user = request.user
        scoped_querysets = {}

        # --- assignment ------------------------------------------------
        # Mirrors AssignmentViewSet.get_queryset() (assignment/views.py)
        # verbatim. See class docstring above for why campus/liveclass
        # sourced assignments stay excluded for non-staff here too.
        from assignment.models import Assignment, AssignmentSource

        assignment_qs = Assignment.objects.all()
        if not user.is_staff:
            assignment_qs = assignment_qs.filter(
                Q(posted_by=user) | Q(source=AssignmentSource.PERSONAL, submissions__student=user)
            ).distinct()
        scoped_querysets["assignment"] = assignment_qs

        # --- testseries --------------------------------------------------
        # individual/published + own-created + attempted + campus-context
        # series for sections the user is actively enrolled in. See class
        # docstring above for the liveclass-context gap.
        from testseries.models import TestSeries
        from campus.models import StudentEnrollment

        active_section_ids = StudentEnrollment.objects.filter(
            student=user, status=StudentEnrollment.Status.ACTIVE
        ).values_list("section_id", flat=True)

        testseries_qs = TestSeries.objects.filter(
            Q(source=TestSeries.Source.INDIVIDUAL, status=TestSeries.Status.PUBLISHED)
            | Q(creator=user)
            | Q(attempts__student=user)
            | Q(source=TestSeries.Source.CAMPUS, context_type="section", context_id__in=active_section_ids)
        ).distinct()
        scoped_querysets["testseries"] = testseries_qs

        try:
            results = search_everything(scoped_querysets, query, sources=requested_sources)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)

        return Response({"results": results})