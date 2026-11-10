"""
tuitionclass/views/classrooms.py

Split out of the former monolithic views.py (T3.2) — see views/__init__.py.
"""

import hashlib
import logging
from core.models import Notification
from core.services import create_notification
from decimal import Decimal, InvalidOperation
from django.core.cache import cache
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Q, Sum, Value, When
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from ..livekit_utils import LiveKitError, remove_participant
from ..models import (
    ClassJoinRequest,
    ClassSchedule,
    ClassSession,
    Classroom,
    ClassroomBan,
    ClassroomReport,
    ClassroomReview,
    ClassroomShare,
    ClassroomStaff,
    ClassroomTrialAccess,
    ClassroomWishlist,
    PassPurchase,
    SessionParticipant,
    get_classroom_list_cache_version,
    referral_code_for_user,
)
from ..realtime import broadcast_to_session
from ..serializers import (
    ClassScheduleSerializer,
    ClassSessionSerializer,
    ClassroomBanSerializer,
    ClassroomMyShareSerializer,
    ClassroomReferralDashboardSerializer,
    ClassroomReportSerializer,
    ClassroomReviewSerializer,
    ClassroomSerializer,
    ClassroomShareLogSerializer,
    ClassroomShareResultSerializer,
    ClassroomShareSerializer,
    ClassroomStaffSerializer,
    ClassroomStatsSerializer,
    ClassroomWishlistSerializer,
    ReferLinkResultSerializer,
    SessionRecordingSerializer,
)

from ._common import (
    TuitionClassPagination,
    _is_truthy,
    _safe_broadcast_to_user,
    _safe_delay,
)
from .access import (
    AccessLevel,
    _access_level,
    _accessible_classroom_ids,
    _can_manage_classroom,
    _can_view_classroom_internals,
)
from .sessions import _perform_join, _perform_trial_join


# ---------------------------------------------------------------------------
# 1. CLASSROOM
# ---------------------------------------------------------------------------
class ClassroomViewSet(viewsets.ModelViewSet):
    serializer_class = ClassroomSerializer
    permission_classes = [IsAuthenticated]
    # NOTE (fix — N+1 query): ClassroomSerializer nests `teacher` as a full
    # UserMiniSerializer (read_only), not a plain FK id — so without
    # select_related, DRF fires one extra `SELECT ... FROM login_user WHERE
    # id=...` per row while serializing. On a paginated list that's fine
    # (20 rows = 20 extra queries), but the Explore/search endpoint is the
    # highest-traffic read path in this app (cached, ordered, unbounded
    # growth per the pagination note below) — select_related folds the
    # teacher lookup into the original query via a JOIN instead.
    queryset = Classroom.objects.select_related("teacher")
    # NOTE (fix): the platform-wide Explore/search list has no natural
    # upper bound — it grows with every classroom ever created — and had no
    # explicit pagination here, same class of gap the TuitionClassPagination
    # docstring already calls out for chat/coin-ledger. Applied directly so
    # this can't return every classroom on the platform in one response
    # regardless of whether a project-wide default is configured.
    pagination_class = TuitionClassPagination
    # NOTE (UX): the Explore/search list had no sort control — a client
    # could only ever get the model's default order (newest first). Adds
    # opt-in ?ordering=rating_avg / ?ordering=-enrolled_count / etc. without
    # touching the default behavior for existing callers that don't pass it.
    filter_backends = [OrderingFilter]
    # NOTE (feature): share_count added as a sort option — flagged as a
    # deliberately-excluded follow-up in the Pass 6 audit note ("share_count
    # as an Explore sort option"). ?ordering=-share_count surfaces the
    # most-shared/trending classrooms; ?ordering=share_count is rarely
    # useful but kept symmetric with every other field here.
    ordering_fields = ["created_at", "rating_avg", "enrolled_count", "share_count", "title"]
    ordering = ["-created_at"]

    # How long a cached Explore/search page can live even without any
    # invalidating write — belt-and-braces on top of the version bump (see
    # get_classroom_list_cache_version in models.py), in case a row ever
    # changes by some path that doesn't go through Classroom.save()/delete()
    # (a raw bulk .update(), a data migration, etc).
    LIST_CACHE_TTL_SECONDS = 120

    def list(self, request, *args, **kwargs):
        # NOTE (perf): only the public Explore/search view (?mine is not
        # set) is cached. "mine" results are small (one teacher's own
        # classrooms) and already fast, and caching them per-user would
        # multiply cache entries for near-zero benefit — this endpoint's
        # cost is dominated by the platform-wide public listing, not the
        # per-teacher one.
        if _is_truthy(request.query_params.get("mine")):
            return super().list(request, *args, **kwargs)

        version = get_classroom_list_cache_version()
        # Cache key must be sensitive to every param that changes the
        # response: search/language/ordering/pagination all vary the result
        # set, so they all go into the key. Hashed because raw query
        # strings (free-text ?search=) can contain characters/length that
        # aren't safe/bounded as a cache key.
        query_fingerprint = hashlib.sha256(
            request.get_full_path().encode("utf-8")
        ).hexdigest()
        cache_key = f"tuitionclass:classroom_list:{version}:{query_fingerprint}"

        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        response = super().list(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            cache.set(cache_key, response.data, timeout=self.LIST_CACHE_TTL_SECONDS)
        return response

    def get_queryset(self):
        user = self.request.user
        mine = _is_truthy(self.request.query_params.get("mine"))
        if mine:
            qs = Classroom.objects.filter(teacher=user)
        else:
            # NOTE (fix): this used to be a flat `.filter(is_active=True)`,
            # which is right for the public Explore/search list but also
            # doubled as the queryset behind get_object() for retrieve/
            # update/destroy/has_access/my_pass/stats — so once a teacher
            # deactivated their OWN classroom (is_active=False), they could
            # never again GET, PATCH, or DELETE it via .../classrooms/{id}/
            # (404, "not found") — there was no way back to reactivate it or
            # even view its own detail page. Now: public/other-users' rows
            # still require is_active=True, but a classroom's own teacher can
            # always see and act on it regardless of active state.
            #
            # NOTE (fix): also exclude is_flagged rows from anyone but the
            # teacher — a classroom sitting on enough pending
            # ClassroomReports (see _auto_flag_classroom in models.py)
            # shouldn't keep surfacing in Explore/search while it's under
            # review, even though it's still technically is_active.
            qs = Classroom.objects.filter(Q(is_active=True, is_flagged=False) | Q(teacher=user))

        # NOTE (fix): a soft-deleted classroom (see perform_destroy below)
        # must disappear everywhere, including from its own teacher's
        # "mine" list — it's gone from the teacher's point of view, the row
        # only survives for purchase/report history and audit.
        qs = qs.filter(is_deleted=False)

        # X12 L-C: the hidden personal classroom of a standalone live never appears in a list (Explore, "my
        # classes", search); it is still reachable by id (retrieve / join) like any classroom.
        # (every collection route - list, recommended, search - has no pk; detail routes keep working.)
        if not self.kwargs.get("pk"):
            qs = qs.filter(is_personal=False)

        # ?search= — free-text match across title/subject/description and the
        # teacher's name, e.g. GET /classrooms/?search=python
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(
                Q(title__icontains=search)
                | Q(subject__icontains=search)
                | Q(description__icontains=search)
                | Q(teacher__first_name__icontains=search)
                | Q(teacher__last_name__icontains=search)
            )

        # ?language= — filter by the language the class is taught in, e.g.
        # GET /classrooms/?language=Hindi
        language = self.request.query_params.get("language")
        if language:
            qs = qs.filter(language__iexact=language)

        # ?subject= — category/subject facet, e.g. GET /classrooms/?subject=Python
        # Separate from ?search= above: ?search= is a broad free-text match
        # across title/subject/description/teacher name (any of them can
        # match); ?subject= is the exact "browse by category" facet a
        # marketplace filter UI needs (a dropdown of subjects, not a
        # search box) — kept case-insensitive but exact, not icontains, so
        # "Math" doesn't also match a classroom whose subject is
        # "Advanced Mathematics".
        subject = self.request.query_params.get("subject")
        if subject:
            qs = qs.filter(subject__iexact=subject)

        # ?min_rating= — e.g. GET /classrooms/?min_rating=4 for "4 stars & up".
        # Invalid/non-numeric values are ignored rather than 400ing — a
        # malformed filter param shouldn't break the whole listing.
        min_rating = self.request.query_params.get("min_rating")
        if min_rating:
            try:
                qs = qs.filter(rating_avg__gte=Decimal(min_rating))
            except (InvalidOperation, ValueError):
                pass

        # ?min_price= / ?max_price= — price lives on ClassPass (a classroom
        # can have several passes at different price points), not on
        # Classroom itself, so this matches classrooms that have AT LEAST
        # ONE active pass inside the given range — e.g. a classroom with a
        # free trial pass AND a paid monthly pass still shows up under
        # ?max_price=0 (someone browsing for free classes) as well as
        # under a paid price-range filter. `.distinct()` is needed because
        # the passes__price filter joins through ClassPass, and a
        # classroom with multiple matching passes would otherwise appear
        # once per matching pass.
        min_price = self.request.query_params.get("min_price")
        max_price = self.request.query_params.get("max_price")
        if min_price or max_price:
            price_filter = Q(passes__is_active=True)
            try:
                if min_price:
                    price_filter &= Q(passes__price__gte=Decimal(min_price))
                if max_price:
                    price_filter &= Q(passes__price__lte=Decimal(max_price))
                qs = qs.filter(price_filter).distinct()
            except (InvalidOperation, ValueError):
                pass

        # NOTE (perf): `teacher` is nested (UserMiniSerializer) on every row
        # — without select_related, listing N classrooms fired N extra
        # queries just to resolve it. One JOIN instead.
        return qs.select_related("teacher")

    def perform_create(self, serializer):
        classroom = serializer.save(teacher=self.request.user)

        # TASK 4: notify the teacher's followers that a new classroom
        # went up. Queued via _safe_delay (see its docstring above) —
        # a popular teacher's follower list can run into the thousands,
        # so the actual user_profile.Follow read + notification fan-out
        # happens entirely inside notify_followers_new_classroom, off
        # this request's path, same reasoning as every other
        # notify_*.delay() call site in this file.
        from ..tasks import notify_followers_new_classroom

        _safe_delay(notify_followers_new_classroom, classroom.id)

    def perform_update(self, serializer):
        # Only the classroom's own teacher can edit its info — co-teachers/
        # moderators/TAs can run sessions but do not own the listing itself.
        if serializer.instance.teacher_id != self.request.user.id:
            raise PermissionDenied("Only the classroom's teacher can edit this classroom.")
        serializer.save()

    def perform_destroy(self, instance):
        # NOTE (fix): this used to be a bare instance.delete() — any
        # teacher, at any time, however many hours old the classroom was,
        # however many students had just paid coins for a still-active
        # pass. That's exactly the create-sell-vanish scam: sell passes,
        # then DELETE the classroom, cascading away every PassPurchase/
        # ClassJoinRequest/etc. along with it, leaving the student with no
        # access and no trace to dispute. Now gated by
        # Classroom.can_be_deleted() (30-day minimum age + no active paid
        # pass outstanding — see that method's docstring) and turned into a
        # SOFT delete so the row and its full history survive regardless.
        if instance.teacher_id != self.request.user.id:
            raise PermissionDenied("Only the classroom's teacher can delete this classroom.")
        can_delete, reason = instance.can_be_deleted()
        if not can_delete:
            raise ValidationError(reason)
        instance.is_active = False
        instance.is_deleted = True
        instance.deleted_at = timezone.now()
        instance.save(update_fields=["is_active", "is_deleted", "deleted_at"])

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        """Teacher-only. The supported way to stop running a classroom
        before it clears the can_be_deleted() bar (or if it never will,
        because passes keep getting bought): reverses every currently
        active, unexpired, paid PassPurchase (see PassPurchase.reverse())
        and deactivates the classroom.

        NOTE (fix): this used to call the old lump-sum _refund_purchase(),
        which credited the student the FULL coins_spent and clawed the
        same amount back out of the teacher's wallet. That was correct
        under the old "pay the teacher everything upfront" design, but
        under the current per-day escrow design (see PassPurchase's NOTE
        (fix — "pay only for classes actually held")) the teacher was
        never paid for days that haven't happened yet — only
        coins_released, one taught day at a time. Calling the old
        function here would have wrongly clawed back money the teacher
        had *actually earned* for classes already held. reverse() refunds
        only remaining_balance (what's still sitting in escrow) and
        leaves coins_released untouched, which is the correct behaviour
        now that escrow — not the teacher's wallet — is holding the
        un-taught days' money.

        Still does NOT soft-delete the classroom — that still needs a
        separate DELETE once the 30-day/no-active-pass bar is clear, so
        the closure itself stays on the record.
        """
        classroom = self.get_object()
        if classroom.teacher_id != request.user.id:
            raise PermissionDenied("Only the classroom's teacher can close this classroom.")

        with transaction.atomic():
            # NOTE (fix, race): locked so this can't interleave with
            # ClassJoinRequestViewSet.accept()'s own select_for_update on
            # this same Classroom row (see that view's matching NOTE) —
            # whichever transaction gets here first now makes the other
            # wait, instead of accept() charging a student for a classroom
            # that's mid-close.
            classroom = Classroom.objects.select_for_update().get(pk=classroom.pk)
            active_purchases = list(
                PassPurchase.objects.select_for_update().filter(
                    class_pass__classroom=classroom,
                    status=PassPurchase.Status.SUCCESS,
                    is_active=True,
                    expires_at__gt=timezone.now(),
                )
            )
            for purchase in active_purchases:
                purchase.reverse()

            classroom.is_active = False
            classroom.save(update_fields=["is_active"])

        return Response({"closed": True, "passes_refunded": len(active_purchases)})

    @action(detail=True, methods=["get"], url_path="has-access")
    def has_access(self, request, pk=None):
        classroom = self.get_object()
        return Response({"has_access": classroom.has_access(request.user)})

    @action(detail=True, methods=["get"], url_path="my-pass")
    def my_pass(self, request, pk=None):
        """The single call the class-details screen should make to decide
        what to show: the "class admin" panel, the "Enter Class" button, a
        "Recharge pass" prompt, or just the read-only public view.

        status / access_level (same value, "status" kept for backward compat):
            "owner"/"admin" -> teacher, co-teacher, moderator, or org staff.
                                Full manage rights on the classroom.
            "active"        -> holds a currently-valid PassPurchase. Full
                                access, INCLUDING entering a live session.
            "expired"       -> held a pass before, it lapsed. Full access to
                                classroom content (materials, notices,
                                doubts, assigmentss, reviews, etc.) but
                                CANNOT enter a live session — needs a fresh
                                join request to renew.
            "pending"       -> not enrolled, but has a ClassJoinRequest
                                against this classroom still awaiting the
                                teacher's decision. `pending_request_id` is
                                set so the frontend can cancel it
                                (JoinRequestApi.cancel) without a separate
                                lookup. Flips to "active" (accepted) or
                                "none" (rejected) the moment the teacher
                                decides — see ClassJoinRequestViewSet.accept/
                                reject, which push a `join_request.decided`
                                event over the caller's user-scoped
                                WebSocket channel so the frontend doesn't
                                have to poll this endpoint to notice.
            "none"          -> never purchased a pass (no pending request
                                either; or their only past request was
                                rejected/cancelled). Sees only the public
                                listing info + reviews.

        can_view_internals -> True for admin/active/expired.
        can_enter_class    -> True for admin/active only — this is exactly
                               the gate join()/token() enforce, so the
                               frontend can trust it 1:1 for the "Enter
                               Class" button.
        """
        classroom = self.get_object()
        user = request.user
        level = _access_level(classroom, user)

        latest = None
        if level in (AccessLevel.ACTIVE, AccessLevel.EXPIRED):
            latest = (
                PassPurchase.objects.filter(
                    student=user, class_pass__classroom=classroom, status=PassPurchase.Status.SUCCESS
                )
                .order_by("-purchased_at")
                .first()
            )

        pending_request_id = None
        if level == AccessLevel.PENDING:
            pending_request_id = (
                classroom.join_requests.filter(student=user, status=ClassJoinRequest.Status.PENDING)
                .values_list("id", flat=True)
                .first()
            )

        # legacy "status" naming kept exactly as before ("owner" not "admin")
        # so existing frontend code doesn't break.
        status_label = "owner" if level == AccessLevel.ADMIN else level

        # FEATURE (trial/demo access): per-student trial state lives HERE
        # (not on ClassroomSerializer, whose list output is cached and
        # shared across users). Only never-enrolled students (none /
        # pending) can trial; owners, pass holders and lapsed-pass
        # students get "unavailable".
        trial_state = "unavailable"
        trial_expires_at = None
        if classroom.is_trial_enabled and classroom.trial_password and level in (
            AccessLevel.NONE, AccessLevel.PENDING,
        ):
            existing_trial = ClassroomTrialAccess.objects.filter(classroom=classroom, user=user).first()
            if existing_trial is None:
                trial_state = "available"
            elif existing_trial.is_active():
                trial_state = "active"
                trial_expires_at = existing_trial.expires_at
            else:
                trial_state = "used"

        return Response(
            {
                "access_level": level,
                "status": status_label,
                "has_access": level in (AccessLevel.ADMIN, AccessLevel.ACTIVE),
                "can_view_internals": level != AccessLevel.NONE,
                "can_enter_class": level in (AccessLevel.ADMIN, AccessLevel.ACTIVE),
                "expires_at": latest.expires_at if latest else None,
                "pending_request_id": pending_request_id,
                "trial_state": trial_state,  # available | active | used | unavailable
                "trial_available": trial_state in ("available", "active"),
                "trial_duration_minutes": classroom.trial_duration_minutes,
                "trial_expires_at": trial_expires_at,
            }
        )

    @action(detail=True, methods=["post"], url_path="start-or-join")
    def start_or_join(self, request, pk=None):
        """One call that replaces the old client-side dance of: list LIVE
        sessions, if none list SCHEDULED ones and sort them, if none of
        those are joinable either offer the teacher a confirm dialog to
        create an ad-hoc one, then finally call join() on whatever was
        picked (see classroom_detail_screen.dart's old _enterClass /
        _offerStartClassNow / _startClassNow, which this is designed to
        let the frontend delete). Both students and the classroom's
        teacher/co-teacher/moderator/org-staff hit this same endpoint —
        the response tells them what happened:

            200/202 -> exactly _perform_join()'s normal join/waitlist
                       response (room_id/livekit_token/... or waitlisted).
            404, {"no_session": true} -> nothing to join right now. For a
                       student this is the end state (show the schedule).
                       Never returned to a manager — see below.
            201, {"session": {...}, "started_new": true} -> ONLY for a
                       manager (teacher/co-teacher/moderator/org-staff)
                       with no live/joinable session to enter: a short
                       (60-minute) ad-hoc session was created AND joined
                       in the same call, exactly like tapping "Start Class
                       Now" used to, minus the extra confirmation round
                       trip and the separate create-then-join calls.

        A manager never sees 404 — for them "nothing to join" always means
        "start one", same behavior as before, just server-side and in one
        request instead of three.
        """
        classroom = self.get_object()
        user = request.user
        is_manager = _can_manage_classroom(classroom, user)

        session = (
            classroom.sessions.filter(status=ClassSession.Status.LIVE)
            .order_by("-actual_start", "-scheduled_start")
            .first()
        )
        if session is None:
            # Soonest first — a manager can start any of these early (same
            # is_host bypass _perform_join()/is_joinable() already grant);
            # a non-manager only sees ones already inside the join window.
            upcoming = classroom.sessions.filter(
                status=ClassSession.Status.SCHEDULED
            ).order_by("scheduled_start")
            for candidate in upcoming:
                if is_manager or candidate.is_joinable():
                    session = candidate
                    break

        started_new = False
        if session is None:
            if not is_manager:
                return Response(
                    {"detail": "There is no tuition class right now.", "no_session": True},
                    status=404,
                )
            # NOTE: same 60-minute ad-hoc window _startClassNow used to
            # create client-side — kept here so behavior doesn't change,
            # just where it happens.
            now = timezone.now()
            session = ClassSession.objects.create(
                classroom=classroom,
                scheduled_start=now,
                scheduled_end=now + timezone.timedelta(minutes=60),
                status=ClassSession.Status.SCHEDULED,
            )
            started_new = True

        response = _perform_join(session, user)
        # NOTE (fix): the frontend needs the actual ClassSession id/data no
        # matter which path got it here — room_id in _perform_join()'s
        # payload is the LiveKit room identifier, not this session's
        # primary key, and there'd be no other way for the caller to know
        # which session it just landed in. Attach it on every successful
        # response (200 join, 202 waitlisted), not just the started_new
        # (201) case.
        if response.status_code in (200, 202):
            response.data["session"] = ClassSessionSerializer(session, context={"request": request}).data
            if started_new:
                response.data["started_new"] = True
                response.status_code = 201
        return response

    @action(detail=True, methods=["post"], url_path="trial-join")
    def trial_join(self, request, pk=None):
        """POST /tuitionclass/classrooms/<id>/trial-join/  {"trial_password": "..."}

        Gateway for a not-yet-enrolled student to sample a LIVE session for
        `classroom.trial_duration_minutes` before buying a pass. Separate
        from start-or-join / sessions/<id>/join on purpose: those still
        demand a real pass (see _has_full_room_access) and are unchanged.

        200 -> same shape as a normal join (room_id, participant_id,
               livekit_url, livekit_token, session) PLUS
               `is_trial_session: true` and `trial: {expires_at,
               duration_minutes, remaining_seconds, server_time}`. The
               LiveKit token is subscribe/data-only and expires with the
               trial; the server also removes the student when the trial
               ends (tasks.expire_trial_access) — the client countdown is
               just UX.
        400 already_has_access | 403 trial_not_enabled / trial_used /
        trial_password_invalid | 404 no_session | 409 session_full |
        429 trial_locked (too many wrong codes).
        """
        classroom = self.get_object()
        return _perform_trial_join(
            classroom, request.user, request.data.get("trial_password"), request
        )

    @action(detail=True, methods=["get"])
    def stats(self, request, pk=None):
        """One-call summary for the classroom's public 'about' card: rating,
        how many students currently hold a pass, plain-English weekly
        timing, and any upcoming off-days — everything the listing/detail
        page needs without the frontend stitching together reviews +
        purchases + schedules + holidays itself."""
        classroom = self.get_object()
        return Response(
            ClassroomStatsSerializer(
                {
                    "rating_avg": classroom.rating_avg,
                    "rating_count": classroom.rating_count,
                    "enrolled_count": classroom.enrolled_count,
                    "share_count": classroom.share_count,
                    "weekly_timing": classroom.weekly_timing_summary(),
                    "upcoming_holidays": classroom.upcoming_holidays(),
                }
            ).data
        )

    # -----------------------------------------------------------------
    # FEATURE (share): two ways to spread a classroom around, both behind
    # one endpoint —
    #   - IN-APP (app ke andar): pass to_user_id (and/or channel="in_app")
    #     to share with another platform user. Creates the in-app
    #     Notification bell row synchronously, same as every other
    #     create_notification() call site in this file, then queues a push
    #     via notify_classroom_shared (tasks.py) the same way
    #     join-request/waitlist/etc. notifications do.
    #   - OUTSIDE THE APP (app ke bhar): omit to_user_id. Nothing is sent
    #     to anyone from this server — the actual "send" (WhatsApp/SMS/
    #     email/whatever) happens on the user's own device, via their OS's
    #     native share sheet. This endpoint's job there is just to hand
    #     back a stable web_url (works even if the recipient doesn't have
    #     the app) and deep_link (opens straight into the app if they do),
    #     plus a ready-to-use share_text, so every client on every
    #     platform shares the exact same link/wording instead of each
    #     building its own.
    # Either way, one ClassroomShare row is logged and Classroom.share_count
    # is bumped — see share-stats below for the teacher-facing view of that.
    # -----------------------------------------------------------------
    # NOTE (fix — abuse/spam protection): share() had no rate limit at all,
    # unlike every other write action in this file (session join/token,
    # coupon validate, chat message create). A script could hammer this
    # endpoint to inflate share_count / spam in-app share notifications to
    # arbitrary to_user_id targets — flagged as a deliberately-excluded
    # follow-up in the Pass 6 audit note ("per-user share-rate limiting").
    # Scoped so it only throttles this one action, same pattern as
    # ChatMessageViewSet.get_throttles(). Add "classroom_share": "<rate>"
    # to DEFAULT_THROTTLE_RATES in settings.py for this to take effect.
    def get_throttles(self):
        if self.action == "share":
            self.throttle_scope = "classroom_share"
            return [ScopedRateThrottle()]
        if self.action == "trial_join":
            # Guessing a short teacher-set code is the abuse case here.
            # Add "classroom_trial_join": "10/min" to DEFAULT_THROTTLE_RATES
            # (settings.py). _perform_trial_join also has its own
            # per-(user, classroom) failed-attempt lockout on top of this.
            self.throttle_scope = "classroom_trial_join"
            return [ScopedRateThrottle()]
        return super().get_throttles()

    @action(detail=True, methods=["post"])
    def share(self, request, pk=None):
        # get_object() already applies get_queryset()'s visibility rules
        # (public+active+unflagged, or the requester's own) — if you can't
        # otherwise see this classroom, you can't share it either.
        classroom = self.get_object()

        serializer = ClassroomShareSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        to_user = serializer.validated_data.get("to_user")
        channel = serializer.validated_data["channel"]

        if to_user is not None and to_user.id == request.user.id:
            raise ValidationError("You can't share a classroom with yourself.")

        share_log = ClassroomShare.objects.create(
            classroom=classroom, shared_by=request.user, shared_with=to_user, channel=channel,
        )
        classroom.record_share()

        if to_user is not None:
            teacher_name = request.user.get_full_name() or request.user.username
            create_notification(
                recipient=to_user,
                notif_type=Notification.NotifType.CLASSROOM_SHARED,
                title="A class was shared with you",
                message=f"{teacher_name} shared '{classroom.title}' with you.",
                classroom=classroom,
            )
            # NOTE: in-app row only got created above — same "queue the
            # push separately so a slow/failing provider never adds
            # latency here" reasoning as every other notify_*.delay() call
            # site in this file (see _safe_delay's docstring).
            from ..tasks import notify_classroom_shared

            _safe_delay(notify_classroom_shared, share_log.id)

        web_url, deep_link = classroom.share_urls()
        return Response(
            ClassroomShareResultSerializer(
                {
                    "share_id": share_log.id,
                    "web_url": web_url,
                    "deep_link": deep_link,
                    "share_text": f"Check out '{classroom.title}' — {web_url}",
                    "shared_with": to_user,
                    "share_count": classroom.share_count,
                }
            ).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"], url_path="share-stats")
    def share_stats(self, request, pk=None):
        """Teacher/co-teacher/moderator only: who's sharing this classroom,
        via which channel, and when — the breakdown behind the single
        share_count number on the stats/list card."""
        classroom = self.get_object()
        if not _can_manage_classroom(classroom, request.user):
            raise PermissionDenied(
                "Only the classroom's teacher, co-teacher, or moderator can view share stats."
            )
        qs = classroom.shares.select_related("shared_by", "shared_with")
        by_channel = qs.values("channel").annotate(count=Count("id")).order_by("-count")
        # Not run through pagination_class: this is a small summary payload
        # (aggregate counts + a recent-activity slice), not a bare list, so
        # DRF's page/results envelope doesn't fit it. Capped to the newest
        # 50 rather than paginated — plenty for "who's been sharing this
        # lately"; a teacher wanting the full history can be given a
        # dedicated paginated endpoint later if that's ever actually asked
        # for.
        recent = ClassroomShareLogSerializer(qs[:50], many=True).data
        return Response(
            {
                "share_count": classroom.share_count,
                "by_channel": {row["channel"]: row["count"] for row in by_channel},
                "recent": recent,
            }
        )

    # -----------------------------------------------------------------
    # FEATURE (my-shares): share-stats above is teacher/staff-facing (who's
    # sharing MY classroom). This is the other side — a student/sharer's
    # own history across every classroom they've ever shared, e.g. for a
    # personal "your shares" screen or a referral-credit reconciliation
    # view. Flagged as a deliberately-excluded Pass 6 follow-up ("a 'my
    # shares' endpoint").
    # -----------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="my-shares")
    def my_shares(self, request):
        qs = ClassroomShare.objects.filter(shared_by=request.user).select_related(
            "classroom", "shared_with"
        )
        page = self.paginate_queryset(qs)
        serializer = ClassroomMyShareSerializer(page if page is not None else qs, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    # -----------------------------------------------------------------
    # FEATURE (refer & earn): the class-level counterpart to share() above
    # — share() just logs word-of-mouth, this hands back a link that
    # ClassJoinRequestViewSet.perform_create can trace back to whoever
    # generated it, and that PassPurchase.charge_for_session then pays a
    # daily commission against (see Classroom.referral_enabled /
    # referral_commission_percent for the on/off + rate the teacher
    # controls). Deliberately open to ANY authenticated user who can see
    # this classroom, not just its enrolled students — same "teacher
    # decides once, anyone can then refer" model requested for this
    # feature; get_object() already applies get_queryset()'s visibility
    # rule (public+active+unflagged, or the requester's own), so a
    # classroom nobody can browse to can't be referred either.
    # -----------------------------------------------------------------
    @action(detail=True, methods=["get"], url_path="refer-link")
    def refer_link(self, request, pk=None):
        classroom = self.get_object()
        if not classroom.referral_active:
            raise ValidationError("This classroom doesn't have referrals enabled.")

        web_url, deep_link = classroom.referral_urls(request.user)
        return Response(
            ReferLinkResultSerializer(
                {
                    "referral_code": referral_code_for_user(request.user.id),
                    "web_url": web_url,
                    "deep_link": deep_link,
                    "share_text": (
                        f"Join '{classroom.title}' using my link — {web_url}"
                    ),
                    "commission_percent": classroom.referral_commission_percent,
                }
            ).data
        )

    # -----------------------------------------------------------------
    # NEW (task 65 — referral-dashboard, "aapne itne log invite kiye"):
    # per-classroom view of how this user's own refer-link (refer_link
    # above) has performed for THIS classroom specifically — distinct from
    # ReferralViewSet.class_referral_summary (views.py), which aggregates
    # the same PassPurchase.referred_by data across EVERY classroom the
    # caller has ever referred someone into. Open to the same audience as
    # refer_link (anyone who can see the classroom), since a zero-referral
    # dashboard for a classroom you haven't shared yet is a valid, harmless
    # answer, not something to gate.
    # -----------------------------------------------------------------
    @action(detail=True, methods=["get"], url_path="referral-dashboard")
    def referral_dashboard(self, request, pk=None):
        classroom = self.get_object()
        referred_qs = PassPurchase.objects.filter(
            referred_by=request.user, class_pass__classroom=classroom
        )
        commission_earned = referred_qs.aggregate(
            total=Sum("referral_coins_released")
        )["total"] or 0
        pending_commission = sum(p.referral_remaining_balance for p in referred_qs)
        data = {
            "classroom_id": classroom.id,
            "referred_count": referred_qs.values("student_id").distinct().count(),
            "commission_earned": commission_earned,
            "commission_pending": pending_commission,
            "referral_code": referral_code_for_user(request.user.id),
        }
        return Response(ClassroomReferralDashboardSerializer(data).data)

    # -----------------------------------------------------------------
    # FEATURE (personalized discovery): Explore used to be pure manual
    # filtering (search/language/subject/price/rating — see get_queryset)
    # with no signal from what THIS user actually likes. Content-based,
    # not ML — scores every visible classroom the caller hasn't already
    # purchased a pass for by how well its subject/language match the
    # caller's own purchase + wishlist history, with a small boost for
    # generally well-performing classrooms so a brand-new user with no
    # history still gets a sensible (rating/enrollment-led) list instead
    # of an empty one.
    # -----------------------------------------------------------------
    # -----------------------------------------------------------------
    # P-F11 — "Classes I can edit". `?mine=true` only lists classes the caller OWNS, so a co-teacher / moderator never
    # saw the class in the test-series "Add to a class" picker even though the server lets them create a test there
    # (testseries.access.editable_context_ids -> bridge.user_editable_testseries_context_ids). This list reads the same
    # bridge helper, so what the picker offers and what the server accepts can never drift apart.
    # Owner + co-teacher + moderator only (a TA / student / pass holder gets nothing). Small list -> no cache.
    # Each row carries `my_role` ("teacher" | "co_teacher" | "moderator") so a client can label it.
    # -----------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="editable")
    def editable(self, request):
        from ..bridge import editable_classroom_roles

        roles = editable_classroom_roles(request.user)
        qs = (
            Classroom.objects.select_related("teacher")
            .filter(pk__in=list(roles), is_deleted=False, is_personal=False)
            .order_by("title", "id")
        )
        page = self.paginate_queryset(qs)
        rows = list(page if page is not None else qs)
        data = self.get_serializer(rows, many=True).data
        out = []
        for row, item in zip(rows, data):
            item = dict(item)
            item["my_role"] = roles.get(row.pk, "")
            out.append(item)
        if page is not None:
            return self.get_paginated_response(out)
        return Response(out)

    @action(detail=False, methods=["get"])
    def recommended(self, request):
        user = request.user
        limit = min(int(request.query_params.get("limit", 10) or 10), 50)

        purchased_classroom_ids = PassPurchase.objects.filter(student=user).values_list(
            "class_pass__classroom_id", flat=True
        )
        interest_qs = Classroom.objects.filter(
            Q(pk__in=purchased_classroom_ids) | Q(wishlisted_by__user=user)
        )
        subjects = set(interest_qs.values_list("subject", flat=True))
        languages = set(interest_qs.values_list("language", flat=True))

        candidates = self.get_queryset().exclude(pk__in=purchased_classroom_ids)
        candidates = candidates.annotate(
            _subject_match=(
                Case(When(subject__in=subjects, then=Value(1)), default=Value(0), output_field=IntegerField())
                if subjects else Value(0, output_field=IntegerField())
            ),
            _language_match=(
                Case(When(language__in=languages, then=Value(1)), default=Value(0), output_field=IntegerField())
                if languages else Value(0, output_field=IntegerField())
            ),
        )
        candidates = candidates.order_by(
            "-_subject_match", "-_language_match", "-rating_avg", "-enrolled_count"
        )[:limit]

        page = self.paginate_queryset(candidates)
        serializer = self.get_serializer(page if page is not None else candidates, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    # -----------------------------------------------------------------
    # TASK G11 (growth_and_feature_tasks.md) — "Free trial / sample class
    # booster". Low-friction try-before-you-commit already EXISTED end to
    # end (is_trial_enabled/trial_password/trial_duration_minutes on this
    # model, ClassroomTrialAccess, POST .../trial-join/, TrialAccessSheet
    # + TrialJoinSheet on the Flutter side) but had zero discovery surface
    # — a student could only ever reach a trial if a teacher handed them
    # the code out of band. There was nothing to feature on Explore.
    #
    # This is that missing discovery endpoint: the best trial-enabled
    # classroom per subject (so the row is a spread across subjects, not
    # five trials for the same one), with the caller's own onboarding
    # Interests (post.UserInterest — Task 3 from the production list)
    # used to rank subjects the caller actually selected ahead of
    # everything else. `is_live_now` tells the client whether tapping
    # through leads straight into a live trial or to "next session";
    # `is_recommended_for_you` drives the "Because you're into X" badge.
    # -----------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="trial-classes")
    def trial_classes(self, request):
        limit = min(int(request.query_params.get("limit", 8) or 8), 20)

        # Same base visibility rules as every other public listing here
        # (active, not flagged, not deleted, or your own) — get_queryset()
        # already encodes that — narrowed to classrooms a trial can
        # actually be started on: trial switched on AND a code is set
        # (mirrors the exact "on but no code" guard ClassroomSerializer.
        # validate() enforces on write, so this can't disagree with it).
        trial_qs = self.get_queryset().filter(is_trial_enabled=True).exclude(trial_password="")

        # One query for every candidate's "is a session LIVE right now"
        # flag, same shape as live_now() above, rather than a query per
        # card on the client.
        tuition_classroom_ids = set(
            ClassSession.objects.filter(
                classroom_id__in=trial_qs.values_list("id", flat=True),
                status=ClassSession.Status.LIVE,
            ).values_list("classroom_id", flat=True)
        )

        # Best ONE classroom per subject — highest rated, then most
        # enrolled, then newest — so a subject with several trial-enabled
        # classrooms doesn't crowd the other subjects out of a "spread
        # across subjects" row. Subject is free text (teacher-chosen), so
        # this groups case-insensitively/trimmed; a blank subject still
        # gets its own slot per classroom rather than being merged with
        # every other unlabeled classroom.
        ranked = list(trial_qs.order_by("-rating_avg", "-enrolled_count", "-created_at"))
        best_per_subject = {}
        for c in ranked:
            key = (c.subject or "").strip().lower() or f"__classroom_{c.id}"
            best_per_subject.setdefault(key, c)
        candidates = list(best_per_subject.values())

        # Interest match: there's no direct FK between post.UserInterest
        # (a handful of broad content categories picked at onboarding)
        # and Classroom.subject (free-text, teacher-chosen) — so this is
        # a best-effort case-insensitive text match of the interest
        # category's own display label against the classroom's subject
        # and title, the same `icontains` style get_queryset()'s ?search=
        # already uses, not a real taxonomy join. A caller with no
        # interests selected (or none that match anything live right
        # now) just gets the plain rating/enrollment-led ranking below —
        # never an empty row.
        #
        # Local import: tuitionclass has no other reason to import the post
        # app at module load time.
        from post.models import UserInterest

        interest_categories = UserInterest.objects.filter(user=request.user).values_list(
            "category", flat=True
        )
        category_labels = dict(UserInterest._meta.get_field("category").choices)
        interest_terms = [category_labels.get(cat, cat).lower() for cat in interest_categories]

        def _matches_interest(classroom) -> bool:
            haystack = f"{classroom.subject} {classroom.title}".lower()
            return any(term and term in haystack for term in interest_terms)

        matched_ids = {c.id for c in candidates if _matches_interest(c)}

        candidates.sort(
            key=lambda c: (
                c.id in matched_ids,
                c.id in tuition_classroom_ids,
                float(c.rating_avg),
                c.enrolled_count,
            ),
            reverse=True,
        )
        candidates = candidates[:limit]

        serializer = self.get_serializer(candidates, many=True)
        data = serializer.data
        for row, classroom in zip(data, candidates):
            row["is_live_now"] = classroom.id in tuition_classroom_ids
            row["is_recommended_for_you"] = classroom.id in matched_ids
        return Response(data)

    # -----------------------------------------------------------------
    # FEATURE (classroom-wide ban): see ClassroomBan in models.py for why
    # this is separate from ClassSessionViewSet.kick() (which only blocks
    # re-entry to ONE session).
    # -----------------------------------------------------------------
    @action(detail=True, methods=["post"])
    def ban(self, request, pk=None):
        """Teacher/co-teacher/moderator: permanently ban a student from this
        classroom. Body: {"student_id": <id>, "reason": "<optional>"}.

        Also best-effort revokes whatever access the student currently
        has: kicks them out of any session they're live in right now,
        rejects any still-pending join request, and reverses/refunds any
        active paid PassPurchase for this classroom — a ban should mean
        the student is fully gone, not "banned but still holding a paid
        pass they can never use again".
        """
        classroom = self.get_object()
        if not _can_manage_classroom(classroom, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can ban a student.")

        serializer = ClassroomBanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        student = serializer.validated_data["student"]

        if student.id == classroom.teacher_id:
            raise ValidationError("Can't ban the classroom's own teacher.")

        # X12: the ban logic (ban row, refund of active passes, pending join request, LiveKit removal + realtime
        # event) lives in live_moderation.apply_classroom_ban - the live moderation panel calls the SAME function,
        # so the two paths can never drift. Idempotent: an already banned student returns the existing row (200).
        from .. import live_moderation, live_push

        live_row = (
            SessionParticipant.objects.filter(session__classroom=classroom, user=student, left_at__isnull=True)
            .select_related("session").first()
        )
        ban, created = live_moderation.apply_classroom_ban(
            classroom, student, request.user, serializer.validated_data.get("reason", ""),
        )
        if not created:
            return Response(ClassroomBanSerializer(ban).data, status=200)
        if live_row is not None:
            # The student was in a live room: audit row + the ban notice (same as the panel's "ban" action).
            live_moderation.log_event(
                live_row.session, "ban", actor=request.user, target=student,
                detail=serializer.validated_data.get("reason", ""),
            )
            live_push.notify_user_of_action(live_row.session, student.id, "ban")

        return Response(ClassroomBanSerializer(ban).data, status=201)

    @action(detail=True, methods=["get"])
    def bans(self, request, pk=None):
        """Teacher/co-teacher/moderator only: list every student currently
        banned from this classroom."""
        classroom = self.get_object()
        if not _can_manage_classroom(classroom, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can view bans.")
        qs = classroom.bans.select_related("student", "banned_by")
        return Response(ClassroomBanSerializer(qs, many=True).data)

    @action(detail=True, methods=["post"], url_path="unban/(?P<student_id>[^/.]+)")
    def unban(self, request, pk=None, student_id=None):
        """Teacher/co-teacher/moderator only: lift a ban. Does NOT restore
        the refunded pass — the student is welcome back, but they'd need
        to raise a fresh join request and pay again, same as any other new
        student would."""
        classroom = self.get_object()
        if not _can_manage_classroom(classroom, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can lift a ban.")
        deleted, _ = classroom.bans.filter(student_id=student_id).delete()
        if not deleted:
            raise NotFound("No ban found for this student on this classroom.")
        return Response({"detail": "Ban lifted."})

    # -----------------------------------------------------------------
    # FEATURE (recordings library): recording_url has existed per-session
    # since the LiveKit egress wiring, but there was no browsable "past
    # recordings" list — a student had to already know a session's id.
    # -----------------------------------------------------------------
    @action(detail=True, methods=["get"])
    def recordings(self, request, pk=None):
        """Browsable list of this classroom's past recorded sessions —
        gated behind _can_view_classroom_internals (teacher/staff/anyone
        who has ever held a pass, active or expired — same tier as
        materials/notices, not the public listing card).

        NOTE: recording_url only gets filled in asynchronously once
        LiveKit's egress webhook confirms the uploaded file is ready (see
        LiveKitWebhookView) — a session that was recorded but hasn't
        finished uploading yet simply won't show up here until it does.
        """
        classroom = self.get_object()
        if not _can_view_classroom_internals(classroom, request.user):
            raise PermissionDenied("You need access to this classroom to view its recordings.")
        qs = classroom.sessions.exclude(recording_url="").order_by("-scheduled_start")
        page = self.paginate_queryset(qs)
        serializer = SessionRecordingSerializer(page if page is not None else qs, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# 1B. CLASSROOM REPORTS (student flags a classroom; staff review it)
# ---------------------------------------------------------------------------
class ClassroomReportViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Any authenticated user can file a report against a classroom — the
    student-facing counterpart to Classroom.can_be_deleted(): a teacher who
    stops delivering, or looks like they're about to scam-and-vanish, can be
    flagged even before they try to delete anything. Enough pending reports
    auto-hides the classroom from Explore (see _auto_flag_classroom in
    models.py). Platform staff review and resolve reports via `review`."""

    serializer_class = ClassroomReportSerializer
    permission_classes = [IsAuthenticated]
    # NOTE (production): platform-staff ?status=/no-filter GET can grow
    # unbounded as reports accumulate — same gap the TuitionClassPagination
    # docstring already calls out. See views.py module docstring.
    pagination_class = TuitionClassPagination
    queryset = ClassroomReport.objects.select_related("classroom", "reported_by", "reviewed_by")

    def get_queryset(self):
        user = self.request.user
        if user.is_staff:
            qs = super().get_queryset()
            classroom_id = self.request.query_params.get("classroom")
            if classroom_id:
                qs = qs.filter(classroom_id=classroom_id)
            status_filter = self.request.query_params.get("status")
            return qs.filter(status=status_filter) if status_filter else qs
        # Non-staff only ever see the reports they personally filed.
        return super().get_queryset().filter(reported_by=user)

    def perform_create(self, serializer):
        # NOTE (fix — Sybil/griefing gap): reporting had no enrollment
        # check — ANY authenticated user, including a brand-new account
        # that never held a pass for this classroom, could file a report.
        # Combined with AUTO_FLAG_THRESHOLD=3 (auto-hides the classroom
        # from Explore once 3 distinct users report it — see
        # _auto_flag_classroom in models.py), a competitor could spin up
        # 3 throwaway accounts and knock any classroom out of search with
        # zero real engagement. Requiring is_enrolled() (ever held a pass —
        # same bar ClassroomReviewViewSet already uses for reviews) means
        # an attacker has to actually pay for a pass per fake account,
        # which doesn't stop a determined attacker but raises the cost far
        # above "free API call" and ties every report to a real
        # transaction platform staff can audit.
        classroom = serializer.validated_data["classroom"]
        if not classroom.is_enrolled(self.request.user):
            raise PermissionDenied("Only students who have held a pass for this classroom can report it.")
        serializer.save(reported_by=self.request.user)

    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        """Platform staff only. Moves a report out of PENDING into
        reviewed/action_taken/dismissed, with an optional note. This does
        NOT itself refund anyone or touch the classroom — staff use
        pass-purchases/{id}/refund/ or classrooms/{id}/close/ separately
        once they've decided what "action_taken" actually means here."""
        if not request.user.is_staff:
            raise PermissionDenied("Only platform staff can review a report.")
        report = self.get_object()
        new_status = request.data.get("status")
        valid_statuses = {
            s for s, _ in ClassroomReport.Status.choices if s != ClassroomReport.Status.PENDING
        }
        if new_status not in valid_statuses:
            raise ValidationError({"status": f"Must be one of {sorted(valid_statuses)}."})

        report.status = new_status
        report.reviewed_by = request.user
        report.admin_note = request.data.get("admin_note", "")
        report.reviewed_at = timezone.now()
        report.save(update_fields=["status", "reviewed_by", "admin_note", "reviewed_at"])

        # NOTE (fix): moving a report OUT of pending changes the classroom's
        # live pending-report count, which is exactly what decides
        # is_flagged. Without this call the classroom stayed hidden from
        # Explore forever even once every report against it was cleared —
        # see Classroom.sync_flag_status() for the full story.
        report.classroom.sync_flag_status()

        # NOTE (fix): the student who filed the report previously had no
        # way to know what happened to it short of re-checking their own
        # report list — a resolved report is a natural moment to close the
        # loop with them either way (dismissed or acted on).
        create_notification(
            recipient=report.reported_by,
            notif_type=Notification.NotifType.REPORT_REVIEWED,
            title="Your report was reviewed",
            message=f"Your report on '{report.classroom.title}' was marked '{report.get_status_display()}'.",
            classroom=report.classroom,
        )
        from ..tasks import notify_report_reviewed

        _safe_delay(notify_report_reviewed, report.id)

        return Response(ClassroomReportSerializer(report).data)


# ---------------------------------------------------------------------------
# 2. SCHEDULE
# ---------------------------------------------------------------------------
class ClassScheduleViewSet(viewsets.ModelViewSet):
    """NOTE (fix): schedule timings are classroom "internals", same tier as
    materials/notices — not part of the public listing card. Previously
    ?classroom=<id> returned the schedule to ANY signed-in user, and no
    filter at all returned EVERY classroom's schedule on the platform. Now
    gated behind _can_view_classroom_internals (teacher/staff/valid pass),
    same pattern as ClassMaterialViewSet."""

    serializer_class = ClassScheduleSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = TuitionClassPagination
    queryset = ClassSchedule.objects.all()

    def get_queryset(self):
        qs = super().get_queryset()
        classroom_id = self.request.query_params.get("classroom")
        user = self.request.user
        if classroom_id:
            classroom = Classroom.objects.filter(pk=classroom_id).first()
            if not classroom or not _can_view_classroom_internals(classroom, user):
                return qs.none()
            return qs.filter(classroom_id=classroom_id)
        return qs.filter(classroom_id__in=_accessible_classroom_ids(user))

    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        if classroom.teacher_id != self.request.user.id:
            raise PermissionDenied("Only the classroom's teacher can add a schedule.")
        serializer.save()

    # NOTE (fix — writable-classroom-FK reassigments sweep, item 5): this
    # checked permission against `serializer.instance.classroom` (the OLD
    # value) but ClassScheduleSerializer.classroom is writable, not
    # read_only — a caller who legitimately manages this schedule's
    # classroom could also reassign `classroom` to a DIFFERENT one in the
    # same PATCH, one they may have no rights on at all, since only the
    # old value's permission was ever verified. Same bug-class already
    # fixed on assigments/Notice/ClassHoliday/LivePoll/ClassQuery/
    # PollTemplate/ClassroomStaff/ClassroomReview — schedules aren't
    # meant to move between classrooms after creation either, so blocked
    # outright rather than re-validated against a second classroom.
    def perform_update(self, serializer):
        if serializer.instance.classroom.teacher_id != self.request.user.id:
            raise PermissionDenied("Only the classroom's teacher can edit a schedule.")
        new_classroom = serializer.validated_data.get("classroom")
        if new_classroom is not None and new_classroom.id != serializer.instance.classroom_id:
            raise ValidationError({"classroom": "A schedule can't be moved to a different classroom."})
        serializer.save()

    def perform_destroy(self, instance):
        if instance.classroom.teacher_id != self.request.user.id:
            raise PermissionDenied("Only the classroom's teacher can remove a schedule.")
        instance.delete()


# ---------------------------------------------------------------------------
# 11. CLASSROOM REVIEW
# ---------------------------------------------------------------------------
class ClassroomReviewViewSet(viewsets.ModelViewSet):
    serializer_class = ClassroomReviewSerializer
    permission_classes = [IsAuthenticated]
    # NOTE (fix): a popular classroom accumulates one review per student
    # ever enrolled — unbounded over time, same class of gap as above.
    pagination_class = TuitionClassPagination
    # NOTE (perf): student is nested (UserMiniSerializer) on every row.
    queryset = ClassroomReview.objects.select_related("student")

    def get_queryset(self):
        qs = super().get_queryset()
        classroom_id = self.request.query_params.get("classroom")
        return qs.filter(classroom_id=classroom_id) if classroom_id else qs

    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        user = self.request.user
        if not classroom.is_enrolled(user):
            raise PermissionDenied("Only students who have held a pass for this class can review it.")
        # NOTE (fix): ClassroomReview has unique_together = ("classroom",
        # "student"), but `student` is a read-only nested field on this
        # serializer (set here, not from the client's payload), so DRF's
        # automatic UniqueTogetherValidator can't see it and never fires.
        # A second review from the same student previously fell straight
        # through to the DB and raised an unhandled IntegrityError -> 500.
        # Checked explicitly here and turned into a normal 400.
        if ClassroomReview.objects.filter(classroom=classroom, student=user).exists():
            raise ValidationError("You've already reviewed this classroom — edit your existing review instead.")
        review = serializer.save(student=user)
        # NOTE (fix): a new review previously produced no notification at
        # all — a teacher found out only by opening their own classroom's
        # review list. Worth a nudge either way (good review to celebrate,
        # bad one to respond to), so this is unconditional rather than
        # rating-gated.
        create_notification(
            recipient=classroom.teacher,
            notif_type=Notification.NotifType.REVIEW_POSTED,
            title="New review",
            message=f"{user.get_full_name() or user.username} left a {review.rating}-star review on '{classroom.title}'.",
            classroom=classroom,
        )
        from ..tasks import notify_review_posted

        _safe_delay(notify_review_posted, review.id)

    # NOTE (fix — found during the Phase 3 audit sweep, SEVERE, pre-existing):
    # this viewset had NO perform_update or perform_destroy override at
    # all — a plain ModelViewSet. get_queryset() above applies no ownership
    # filter either (unlike e.g. ClassReminderViewSet, which always scopes
    # to `user=self.request.user`): with no ?classroom= query param, it
    # returns literally every review on the whole platform. Put together,
    # ANY authenticated user could PATCH or DELETE ANY OTHER student's
    # review by id — rewrite someone else's rating/comment, or delete it
    # outright — with zero ownership check, and (same as the other
    # writable-classroom-field gaps found in this sweep)
    # `ClassroomReviewSerializer.classroom` is writable too, so an update
    # could also reassign a review to an unrelated classroom. This was
    # already reachable before this pass — `ReviewApi.update()`/`.delete()`
    # (Dart) already existed — this pass just happened to be the one
    # auditing every perform_update/perform_destroy in this file for the
    # same bug class. Locked down to the review's own student, same shape
    # as assigmentsSubmissionViewSet's ownership check above.
    def perform_update(self, serializer):
        instance = serializer.instance
        if instance.student_id != self.request.user.id:
            raise PermissionDenied("You can only edit your own review.")
        new_classroom = serializer.validated_data.get("classroom")
        if new_classroom is not None and new_classroom.id != instance.classroom_id:
            raise ValidationError({"classroom": "A review can't be moved to a different classroom."})
        serializer.save()

    def perform_destroy(self, instance):
        if instance.student_id != self.request.user.id:
            raise PermissionDenied("You can only delete your own review.")
        instance.delete()


# ---------------------------------------------------------------------------
# 11C. WISHLIST ("save for later" — no enrollment/payment required, unlike
# reviews/reports. A student browsing Explore can bookmark a classroom
# they're considering and come back to it — standard on any mature
# marketplace-style learning app (Udemy, Byju's, etc.) and, until now,
# completely absent here.)
# ---------------------------------------------------------------------------
class ClassroomWishlistViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    """Own wishlist only. POST {"classroom_id": <id>} to save; DELETE
    /wishlist/{id}/ to remove. No ?classroom= filter needed — this is
    always "my saved classrooms", never someone else's."""

    serializer_class = ClassroomWishlistSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = TuitionClassPagination

    def get_queryset(self):
        return ClassroomWishlist.objects.filter(user=self.request.user).select_related(
            "classroom", "classroom__teacher"
        )

    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        user = self.request.user
        # unique_together=(user, classroom) exists at the DB level too, but
        # `user` is set here (not from the client payload) so DRF's
        # UniqueTogetherValidator can't see it — same gap class as reviews/
        # certificates/staff above. Checked explicitly for a clean 400
        # instead of an unhandled IntegrityError.
        if ClassroomWishlist.objects.filter(user=user, classroom=classroom).exists():
            raise ValidationError("Already in your wishlist.")
        serializer.save(user=user)

    def perform_destroy(self, instance):
        if instance.user_id != self.request.user.id:
            raise PermissionDenied("You can only remove your own wishlist entries.")
        instance.delete()


# ---------------------------------------------------------------------------
# 14. CLASSROOM STAFF
# ---------------------------------------------------------------------------
class ClassroomStaffViewSet(viewsets.ModelViewSet):
    """Anyone can read a classroom's staff/team list (?classroom=<id> —
    shown on the detail page, e.g. an organisation's team); only the
    classroom's own teacher can add, edit, or remove a staff member."""

    serializer_class = ClassroomStaffSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = TuitionClassPagination
    # NOTE (perf): user is nested (UserMiniSerializer) on every row.
    queryset = ClassroomStaff.objects.select_related("user", "classroom")

    def get_queryset(self):
        qs = super().get_queryset()
        classroom_id = self.request.query_params.get("classroom")
        return qs.filter(classroom_id=classroom_id) if classroom_id else qs.none()

    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        target_user = serializer.validated_data["user"]
        if classroom.teacher_id != self.request.user.id:
            raise PermissionDenied("Only the classroom's teacher can add staff.")
        if target_user.id == classroom.teacher_id:
            raise ValidationError("The classroom's teacher is already its owner — no need to add them as staff.")
        # NOTE (fix): ClassroomStaff has unique_together = ("classroom",
        # "user"); adding the same person twice previously fell through to
        # an unhandled IntegrityError -> 500 instead of a normal 400.
        if ClassroomStaff.objects.filter(classroom=classroom, user=target_user).exists():
            raise ValidationError("This user is already on the classroom's staff.")
        staff = serializer.save()
        # NOTE (fix): the added user previously had no signal at all that
        # they'd gained manage rights on a classroom — they'd only find out
        # by stumbling onto it in the app (or via a change they suddenly
        # had permission to make).
        create_notification(
            recipient=target_user,
            notif_type=Notification.NotifType.STAFF_ADDED,
            title="You were added as staff",
            message=f"You've been added as {staff.get_role_display()} on '{classroom.title}'.",
            classroom=classroom,
        )
        from ..tasks import notify_staff_added

        _safe_delay(notify_staff_added, staff.id)

        # REALTIME (Phase 4, item 2 — mid-session role promote): mirrors the
        # join_request.created push above (same _safe_broadcast_to_user /
        # per-user WS channel). Without this, a user who's already sitting
        # on ClassroomDetailScreen when they get promoted only picks up
        # manage-tier access (and, with it, the pending-join-request badge)
        # on their NEXT full screen load — the screen has no way to know
        # its own access just changed underneath it. The Flutter side's
        # `staff.added` handler just re-runs `_load()` (same as it already
        # does for `join_request.decided`), which recomputes `_canManage`
        # fresh and fires `_loadPendingRequestCount()` immediately if it's
        # now true — see classroom_detail_screen.dart.
        _safe_broadcast_to_user(
            target_user.id,
            "staff.added",
            {
                "classroom_id": classroom.id,
                "classroom_title": classroom.title,
                "role": staff.role,
            },
        )

    def perform_update(self, serializer):
        if serializer.instance.classroom.teacher_id != self.request.user.id:
            raise PermissionDenied("Only the classroom's teacher can edit a staff member's role.")
        # NOTE (fix — found during the Phase 3 audit sweep, same class of
        # gap as everywhere else in this sweep): both `classroom` and
        # `user` (via `user_id`) are writable on `ClassroomStaffSerializer`.
        # Without this, classroom A's teacher could PATCH one of their own
        # staff rows to move it to classroom B (bypassing B's own teacher
        # check entirely — only A's was ever verified), or reassign it to
        # an arbitrary different user, effectively granting staff access to
        # someone who was never actually added via perform_create's own
        # checks (duplicate-staff guard, teacher-can't-be-staff guard).
        # Both are frozen after creation, same as the classroom-immutability
        # guards added elsewhere in this pass — a role CHANGE for the same
        # person on the same classroom is the only thing update() is for.
        new_classroom = serializer.validated_data.get("classroom")
        if new_classroom is not None and new_classroom.id != serializer.instance.classroom_id:
            raise ValidationError({"classroom": "A staff row can't be moved to a different classroom."})
        new_user = serializer.validated_data.get("user")
        if new_user is not None and new_user.id != serializer.instance.user_id:
            raise ValidationError({"user_id": "A staff row can't be reassigned to a different user."})
        serializer.save()

    def perform_destroy(self, instance):
        if instance.classroom.teacher_id != self.request.user.id:
            raise PermissionDenied("Only the classroom's teacher can remove a staff member.")
        instance.delete()
