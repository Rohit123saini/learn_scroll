# core/views.py
"""
NotificationViewSet + NotificationPreferenceView, moved here from
tuitionclass/views.py (task 42) and generalized (tasks 45/46) now that
Notification is a single shared table covering both tuitionclass and
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
                                           (?is_read=true/false, ?source=message|tuitionclass,
                                            ?category=mentions|follows|classroom|tests|other)
    GET    notifications/{id}/            retrieve one
    DELETE notifications/{id}/            clear one (own only)
    GET    notifications/unread-count/    badge count for the bell icon
                                           (?source=..., ?category=...)
    POST   notifications/{id}/mark-read/  mark one as read
    POST   notifications/mark-all-read/   mark every unread one as read
    GET/PATCH notification-preferences/me/   always the caller's own row
    POST   notification-mutes/{user_id}/     N6-BE — mute that user's activity
    DELETE notification-mutes/{user_id}/     N6-BE — unmute
    GET    search/                        Task 18 — unified cross-app search
                                           (?q=..., optional ?sources=a,b,c)
    GET    notice-board/                  Task 12 — home-screen Notice Board
                                           (?limit=&offset=), merges campus
                                           notices + tuition-class notices
"""
import logging

from django.db.models import Q
from django.utils import timezone
from rest_framework import mixins, pagination, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from login.models import User

from .models import Notification, NotificationMute, NotificationPreference, OnboardingProgress
from .search import search_everything
from .serializers import NotificationPreferenceSerializer, NotificationSerializer

logger = logging.getLogger(__name__)


def _is_truthy(value) -> bool:
    """Parse a query-string flag like ?is_read=1 / ?is_read=true properly
    — a bare `if request.query_params.get(...)` would treat "false" as
    truthy since it's a non-empty string."""
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _minimal_user(user):
    """Same shape as `campus.serializers.MinimalUserSerializer` — kept as
    a plain dict here (not a real serializer) since `NoticeBoardView`
    below builds its rows as plain dicts from two different apps'
    models, not from one queryset a ModelSerializer could sit on top
    of."""
    if user is None:
        return None
    return {
        "id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }


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

        # task 46 — unified list, split-able by ?source=message|tuitionclass
        # until Phase 5 (Posts/Follow/Like) joins this same system and the
        # split stops being a two-way one.
        source = self.request.query_params.get("source")
        qs = self._filter_by_source(qs, source)

        # N3-BE — ?category=mentions|follows|classroom|tests|other. Same
        # "unknown value is ignored" behaviour as ?source=, and it stacks
        # with it (source AND category).
        qs = Notification.filter_by_category(qs, self.request.query_params.get("category"))

        return qs

    @staticmethod
    def _filter_by_source(qs, source):
        """Shared by `get_queryset` and `unread_count` — same two-way
        message/tuitionclass split, one place, so they can never drift out
        of sync with each other."""
        if source == "message":
            return qs.filter(notif_type__in=Notification.MESSAGE_APP_TYPES)
        elif source == "tuitionclass":
            return qs.exclude(notif_type__in=Notification.MESSAGE_APP_TYPES)
        return qs

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        # task 45 — single badge count for everyone, regardless of which
        # app produced the notification, since it's one shared table now.
        #
        # 🔥 FIX (settings/nav pass) — now accepts the same ?source=
        # message|tuitionclass split as the list endpoint. The bell icon
        # (home.dart) wants a tuitionclass-only count and the Chats tab
        # wants a message-only count — previously this always returned
        # the combined total, so the bell badge double-counted unread
        # messages that were *also* shown as a badge on the Chats tab.
        source = request.query_params.get("source")
        qs = self._filter_by_source(Notification.objects.for_user(request.user).unread(), source)
        qs = Notification.filter_by_category(qs, request.query_params.get("category"))
        return Response({"unread_count": qs.count()})

    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        notification.mark_read()
        return Response(NotificationSerializer(notification, context=self.get_serializer_context()).data)

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


class NotificationMuteView(APIView):
    """N6-BE — mute / unmute one account's notifications for the caller.
    `user_id` in the URL is the user being muted; the muter is always
    request.user, so nobody can create or remove anyone else's mutes.

    POST   -> 201 (newly muted) / 200 (already muted); idempotent.
    DELETE -> 204 whether or not a mute existed; idempotent.
    Muting yourself is a 400. The muted user is never told (no
    notification, no response difference on their side).
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, user_id):
        if user_id == request.user.id:
            return Response({"detail": "You can't mute yourself."}, status=status.HTTP_400_BAD_REQUEST)
        if not User.objects.filter(id=user_id).exists():
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        _, created = NotificationMute.objects.get_or_create(user=request.user, muted_actor_id=user_id)
        return Response(
            {"muted": True, "user_id": user_id},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request, user_id):
        NotificationMute.objects.filter(user=request.user, muted_actor_id=user_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


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
        a submission for. Campus/tuitionclass-sourced assigmentss are
        deliberately excluded here for non-staff too — that viewset
        already keeps them out (surfaced only through campus's/
        tuitionclass's own thin-proxy viewsets, per that file's own
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
        Tuitionclass-context series (`source="tuitionclass"`) are NOT
        included — no roster/entitlement resolver for testseries
        exists on the tuitionclass side yet (`tuitionclass/bridge.py` only
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
            # verbatim. See class docstring above for why campus/tuitionclass
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
            # docstring above for the tuitionclass-context gap.
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


class NoticeBoardView(APIView):
    """
    Task 12 — the home screen's "Notice Board" quick-action used to be a
    bare `featureComingSoon` snackbar (see `home.dart`'s
    `_buildQuickActionsGrid` comment) even though TWO real notice
    features already exist — `campus.Notice` (campus/department/class/
    section-scoped) and `tuitionclass.Notice` (per-classroom). Neither app
    owns "the user's home-screen notice feed" on its own (a user can be
    in campuses AND enrolled in classrooms at the same time), so — same
    reasoning `core.search` already documents for cross-app aggregation
    — that merge lives here in `core`, not in either app.

    Pure read/merge layer, same posture as `core.search`'s golden rule
    (see that module's docstring): this view never invents a new access
    rule, it only unions two ALREADY permission-scoped querysets and
    re-sorts them —
      - campus notices: `campus.views.get_my_campus_ids(user)`, the
        exact same centralized scoping function `SearchView`'s
        `campus_notice` source above already reuses instead of
        re-deriving.
      - tuition-class notices: `tuitionclass.views._accessible_classroom_ids
        (user)` — every classroom the user teaches/staffs OR has ever
        held a successful pass for, the same set
        `NoticeViewSet.get_queryset()` (tuitionclass/views.py) implicitly
        relies on via `_can_view_classroom_internals`. Expired notices
        (`expires_at` in the past) are excluded, same as that
        viewset's own default (`include_expired` off).

    GET core/notice-board/?limit=&offset=
        -> {"count": <total merged>, "results": [ {...}, ... ]}

    Each result (unified shape, both sources normalized into it):
        id (str), source ("campus" | "tuition_class"), title, body,
        is_pinned, created_at, posted_by {id, username, first_name,
        last_name}, context_label (campus name / classroom title),
        scope_label — campus rows only: "section"/"class"/
        "department"/"campus" (mirrors `Notice.scopeLabel` already on
        the Flutter side, `campus_models.dart`); always null for
        tuition_class rows, which have no broader scope than "classroom".

    Sorted pinned-first, then newest-first — same ordering
    `NoticesScreen` (campus) and `NoticeViewSet` (tuitionclass) each
    already apply within their own single source.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        merged = []

        try:
            from campus.models import Notice as CampusNotice
            from campus.views import get_my_campus_ids

            campus_qs = CampusNotice.objects.filter(
                campus_id__in=get_my_campus_ids(user)
            ).select_related("posted_by", "campus")

            for n in campus_qs:
                if n.section_id:
                    scope_label = "section"
                elif n.school_class_id:
                    scope_label = "class"
                elif n.department_id:
                    scope_label = "department"
                else:
                    scope_label = "campus"
                merged.append({
                    "id": str(n.id),
                    "source": "campus",
                    "title": n.title,
                    "body": n.body,
                    "is_pinned": bool(n.pin_until and n.pin_until > timezone.now()),
                    "created_at": n.created_at,
                    "posted_by": _minimal_user(n.posted_by),
                    "context_label": n.campus.name,
                    "scope_label": scope_label,
                })
        except Exception:
            # One source failing must not take down the whole board —
            # same degrade-not-crash posture SearchView above takes per
            # source.
            logger.exception("NoticeBoardView: skipping source %r (build failed).", "campus")

        try:
            from tuitionclass.models import Notice as TuitionClassNotice
            from tuitionclass.views import _accessible_classroom_ids

            live_qs = TuitionClassNotice.objects.filter(
                classroom_id__in=_accessible_classroom_ids(user),
            ).filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
            ).select_related("posted_by", "classroom")

            for n in live_qs:
                merged.append({
                    "id": str(n.id),
                    "source": "tuition_class",
                    "title": n.title,
                    "body": n.message,
                    "is_pinned": n.is_pinned,
                    "created_at": n.created_at,
                    "posted_by": _minimal_user(n.posted_by),
                    "context_label": n.classroom.title,
                    "scope_label": None,
                })
        except Exception:
            logger.exception("NoticeBoardView: skipping source %r (build failed).", "tuition_class")

        merged.sort(key=lambda item: (item["is_pinned"], item["created_at"]), reverse=True)

        try:
            limit = min(max(int(request.query_params.get("limit", 30)), 1), 100)
        except (TypeError, ValueError):
            limit = 30
        try:
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except (TypeError, ValueError):
            offset = 0

        page = merged[offset: offset + limit]
        return Response({"count": len(merged), "results": page})

# ============================================================
# TASK G18 (growth_and_feature_tasks.md — Empty states & first-time-user
# onboarding).
#
# A brand-new user with zero follows/campus/tests sees mostly blank
# screens today. `OnboardingSuggestionsView` powers the 3-step flow the
# Flutter side runs right after signup (see `onboarding/onboarding_screen
# .dart`): pick interests (already served by `post.UserInterestsAPIView`
# — TASK 3, this view does NOT duplicate that), suggested people +
# campuses to look at, and one sample test to try.
#
# Same cross-app posture as `SearchView`/`NoticeBoardView` above: this
# is a pure read layer over each app's own models, built here because
# no single owning app should have to know about the other three just
# to answer "what should a new user see first" — it never invents a
# new access rule, and every suggestion is something the user could
# already reach through that app's normal screens.
# ============================================================
class OnboardingSuggestionsView(APIView):
    """GET core/onboarding/suggestions/

    Returns:
        {
          "suggested_users": [{id, username, first_name, last_name,
                                profile_photo}, ...],   # up to 8, not
                                                          # already followed,
                                                          # self excluded
          "suggested_campuses": [{id, name, type}, ...], # up to 5,
                                                           # approved + active
          "sample_test_series": [<PublicSeriesSerializer row>, ...]  # up
                                                           # to 3, free,
                                                           # published,
                                                           # highest-rated
        }

    Suggested users are ranked by `followers_count` (a simple, existing
    denormalized popularity signal — same field `user_profile` already
    maintains, see login.User's own field docstring) among users this
    caller doesn't already follow. When the caller has picked interests
    already (`post.UserInterest` — this endpoint is meant to run AFTER
    that step, but doesn't require it), users who've posted in one of
    those categories are boosted to the front instead of pure
    popularity, so "suggested people" actually reflects what the user
    just said they care about.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        suggested_users = self._suggested_users(user)
        suggested_campuses = self._suggested_campuses()
        sample_test_series = self._sample_test_series(user=user)

        return Response(
            {
                "suggested_users": suggested_users,
                "suggested_campuses": suggested_campuses,
                "sample_test_series": sample_test_series,
            }
        )

    def _suggested_users(self, user, limit: int = 8):
        # P8-BE — the ranking/exclusion rules moved to user_profile.discovery so this
        # view and GET profile/<username>/similar/ can never drift apart. Behaviour
        # change vs. the old inline version: blocked users (either direction) and users
        # the caller restricted are no longer suggested, and the "posted in my interests"
        # boost only counts public, approved, non-deleted posts.
        from user_profile.discovery import suggested_users_queryset
        from user_profile.serializers import UserSearchSerializer

        return UserSearchSerializer(suggested_users_queryset(user)[:limit], many=True).data

    def _suggested_campuses(self, limit: int = 5):
        try:
            from campus.models import Campus
        except Exception:  # pragma: no cover — campus app not installed
            return []

        campuses = Campus.objects.filter(
            is_active=True, verification_status=Campus.VerificationStatus.APPROVED
        ).order_by("-created_at")[:limit]

        return [{"id": c.id, "name": c.name, "type": c.type} for c in campuses]

    def _sample_test_series(self, limit: int = 3, user=None):
        try:
            from django.db.models import Count, F

            from testseries.models import TestSeries
            from testseries.serializers import PublicSeriesSerializer
        except Exception:  # pragma: no cover — testseries app not installed
            return []

        # `.with_rating()` (Task G9, testseries/models.py) — DB-level
        # subquery annotation, same reasoning that method's own
        # docstring gives for why sorting on the `avg_rating` PROPERTY
        # directly (one query per row, and un-sortable at the DB level
        # in the first place) isn't an option here.
        series = (
            TestSeries.objects.filter(status=TestSeries.Status.PUBLISHED, price_coins=0)
            .exclude(source=TestSeries.Source.CAMPUS)
            .select_related("creator")
            .annotate(q_count=Count("questions", distinct=True))
            .with_rating()
            .order_by(F("rating").desc(nulls_last=True), "-id")
        )

        # Quick-start (core/onboarding_quickstart.py): series matching the
        # user's chosen exam go first, the rest fill up by rating.
        exam = getattr(user, "target_exam", "") if user is not None else ""
        if exam:
            from .onboarding_quickstart import prefer_exam

            first = list(prefer_exam(series, exam)[:limit])
            if len(first) < limit:
                taken = {s.pk for s in first}
                first += [s for s in series.exclude(pk__in=taken)[: limit - len(first)]]
            return PublicSeriesSerializer(first, many=True).data

        return PublicSeriesSerializer(series[:limit], many=True).data


class OnboardingCompleteView(APIView):
    """POST core/onboarding/complete/   body: {"skipped": false}  (optional,
    default false)

    Marks the caller's onboarding flow as finished — either genuinely
    completed (tapped "Finish" on the last step) or explicitly skipped
    (tapped "Skip" at any point). Either way, the Flutter side (see
    `main.dart`'s post-signup navigation, `signup_screen.dart` /
    `complete_profile_screen.dart`) won't route back into the flow for
    this user again.

    GET is also supported, returning the caller's current state — used
    by the Flutter side to decide whether to show the flow at all for a
    user who's already been through it on another device.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        progress = OnboardingProgress.objects.filter(user=request.user).first()
        return Response(
            {
                "completed": bool(progress and progress.completed),
                "skipped": bool(progress and progress.skipped),
            }
        )

    def post(self, request):
        skipped = bool(request.data.get("skipped", False))
        progress, _ = OnboardingProgress.objects.get_or_create(user=request.user)
        progress.completed = not skipped
        progress.skipped = skipped
        progress.completed_at = timezone.now()
        progress.save(update_fields=["completed", "skipped", "completed_at"])
        return Response({"completed": progress.completed, "skipped": progress.skipped})