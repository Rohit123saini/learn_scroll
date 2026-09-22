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
mirrors an existing viewset's own scoping exactly (assigments, message,
campus_notice) or reuses the same underlying entitlement table a bridge
module already treats as the source of truth (testseries' campus
roster), so this endpoint can never surface a row a user couldn't
already reach through the normal UI for that source.

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
import logging

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

logger = logging.getLogger(__name__)


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
        unread_count = Notification.objects.for_user(self.request.user).unread().count()
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
        qs = Notification.objects.for_user(self.request.user).select_related("classroom", "session")

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
        count = Notification.objects.for_user(request.user).unread().count()
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
        updated = Notification.objects.for_user(request.user).unread().update(
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
    """[Task 18] GET /core/search/?q=...&sources=assigments,testseries,...

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
      - ✅ assigments — mirrors `assigmentsViewSet.get_queryset()`
        (assigments/views.py) exactly: staff see everything, everyone
        else only what they posted or a personal assigments they hold
        a submission for. Campus/liveclass-sourced assigmentss are
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
        has assigments functions as of this pass). Those rows are
        simply absent from search results, never leaked; add a branch
        here once that resolver exists.
      - ✅ message [Task 13] — mirrors `ConversationViewSet.search_all()`
        (message/views.py) exactly, minus the actual FTS/trigram call
        (that part is `MESSAGE_SOURCE.run()`'s job in `core/search.py`,
        which already calls `message_search_utils.search_messages(qs,
        query)` on whatever queryset we hand it here — same contract
        `search_all()` itself uses). Scoped to: conversations the user
        is a CURRENT (`left_at__isnull=True`) member of, non-expired
        disappearing messages, and excludes `deleted_for_everyone`,
        this user's own `deleted_for_users` ("delete for me"), and
        `is_scheduled` (send-later messages not yet delivered — visible
        only to their sender via a different endpoint until they're
        actually sent). `BlockedUser` is deliberately NOT filtered here
        — per that model's own docstring it's a websocket-delivery-time
        check only, never a stored-message visibility rule, so adding
        a block filter here would be inventing a new access rule this
        endpoint has no business inventing (see this class's own intro
        paragraph).
      - ✅ campus_notice [Task 13] — mirrors `NoticeViewSet.get_queryset()`
        (campus/views.py) exactly: `Notice.objects.filter(campus_id__in=
        get_my_campus_ids(user))`. Reuses `campus.views.get_my_campus_ids`
        directly rather than re-deriving the staff/student/parent-link
        union here — that function's own docstring says it's
        centralized specifically so every campus-visibility check stays
        in sync as new membership routes get added, and reimplementing
        that union here would be exactly the kind of second,
        independently-maintained copy this view's intro paragraph warns
        against. (The `?campus=` narrowing `NoticeViewSet` itself
        supports is that endpoint's own query param, not something this
        view mirrors — `SearchView` has no equivalent scoping param.)
      - ❌ post — NOT wired in this pass (out of Task 18/13's scope,
        which covers assigments/testseries/message/campus_notice only).
        `core.search.SOURCES` doesn't register it yet either (see that
        module's own STATUS docstring) — `post/models.py` was never
        part of any upload, so `Post`'s searchable field(s) and
        visibility rule (likely via `user_profile.BlockUser`/
        `RestrictUser`) are still unconfirmed. Add a scoped-queryset
        builder here once that's tasked and `core.search.SOURCES` has
        a `POST_SOURCE` entry to match.
      - ✅ user / friend [gap-fix] — `core.search.SOURCES` already had
        `USER_SOURCE`/`FRIEND_SOURCE` registered, but this view never
        built their scoped querysets, so `?sources=user`/`?sources=
        friend` ("add friend" search) always silently came back empty.
        Now mirrors `UserSearchView.get_queryset()`'s exclusions
        exactly (active, not yourself, not blocked either direction);
        `friend` narrows that further to an ACCEPTED `Follow` either
        direction.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = request.query_params.get("q", "")
        raw_sources = request.query_params.get("sources")
        requested_sources = [s.strip() for s in raw_sources.split(",") if s.strip()] if raw_sources else None

        user = request.user
        scoped_querysets = {}

        try:
            # --- assigments ------------------------------------------------
            # Mirrors assigmentsViewSet.get_queryset() (assigments/views.py)
            # verbatim. See class docstring above for why campus/liveclass
            # sourced assigmentss stay excluded for non-staff here too.
            from assigments.models import assigments, assigmentsSource

            assigments_qs = assigments.objects.all()
            if not user.is_staff:
                assigments_qs = assigments_qs.filter(
                    Q(posted_by=user) | Q(source=assigmentsSource.PERSONAL, submissions__student=user)
                ).distinct()
            scoped_querysets["assigments"] = assigments_qs
        except Exception:
            # One app failing must not take down search for every other source.
            logger.exception("SearchView: skipping source %r (build failed).", 'assigments')

        try:
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
        except Exception:
            # One app failing must not take down search for every other source.
            logger.exception("SearchView: skipping source %r (build failed).", 'testseries')

        try:
            # --- message [Task 13] -------------------------------------------
            # Exact mirror of ConversationViewSet.search_all()'s own scoped
            # queryset (message/views.py), minus the search_messages() call
            # itself — core.search.MESSAGE_SOURCE.run() does that part. See
            # class docstring above for why BlockedUser isn't filtered here.
            from message.models import Message

            message_qs = Message.objects.filter(
                conversation__memberships__user=user,
                conversation__memberships__left_at__isnull=True,
            ).filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
            ).exclude(
                deleted_for_everyone=True,
            ).exclude(
                deleted_for_users=user,
            ).exclude(
                is_scheduled=True,
            ).distinct()
            scoped_querysets["message"] = message_qs
        except Exception:
            # One app failing must not take down search for every other source.
            logger.exception("SearchView: skipping source %r (build failed).", 'message')

        try:
            # --- campus_notice [Task 13] ---------------------------------------
            # Exact mirror of NoticeViewSet.get_queryset() (campus/views.py):
            # filter_queryset_to_my_campuses() with the default
            # campus_field_path="campus", i.e. campus_id__in=my campus ids.
            # get_my_campus_ids() is imported directly rather than
            # re-derived — see class docstring above for why.
            from campus.models import Notice
            from campus.views import get_my_campus_ids

            notice_qs = Notice.objects.filter(campus_id__in=get_my_campus_ids(user))
            scoped_querysets["campus_notice"] = notice_qs
        except Exception:
            # One app failing must not take down search for every other source.
            logger.exception("SearchView: skipping source %r (build failed).", 'campus_notice')

        try:
            # --- user / friend ["add friend" search — Task 18 gap-fix] --------
            # `core.search.USER_SOURCE` and `FRIEND_SOURCE` were both
            # registered on the SOURCES side already, but this view never
            # built either scoped queryset — so `?sources=user` and
            # `?sources=friend` silently returned nothing (see
            # `search_everything()`'s own docstring: an unscoped source is
            # skipped, not an error). Same exclusion rule
            # `UserSearchView.get_queryset()` (user_profile/views.py) already
            # applies: active users, never yourself, never anyone in a
            # `BlockUser` relationship with you either direction.
            from django.contrib.auth import get_user_model
            from user_profile.models import BlockUser, Follow

            User = get_user_model()
            blocked_pairs = BlockUser.objects.filter(
                Q(blocker=user) | Q(blocked=user)
            ).values_list("blocker_id", "blocked_id")
            excluded_ids = {user.id}
            for blocker_id, blocked_id in blocked_pairs:
                excluded_ids.add(blocker_id)
                excluded_ids.add(blocked_id)

            base_users = User.objects.filter(is_active=True).exclude(id__in=excluded_ids)
            scoped_querysets["user"] = base_users

            # "friend" = people you already have an ACCEPTED follow
            # relationship with, either direction — same `Follow` model
            # `FollowAPIView` (user_profile/views.py) writes to.
            friend_ids = set(
                Follow.objects.filter(follower=user, status=Follow.Status.ACCEPTED)
                .values_list("following_id", flat=True)
            ) | set(
                Follow.objects.filter(following=user, status=Follow.Status.ACCEPTED)
                .values_list("follower_id", flat=True)
            )
            scoped_querysets["friend"] = base_users.filter(id__in=friend_ids)
        except Exception:
            # One app failing must not take down search for every other source.
            logger.exception("SearchView: skipping source %r (build failed).", 'user/friend')

        try:
            results = search_everything(scoped_querysets, query, sources=requested_sources)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)

        return Response({"results": results})