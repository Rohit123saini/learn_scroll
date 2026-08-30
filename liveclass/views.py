"""
liveclass/views.py

DRF ViewSets for the liveclass app.

Design notes:
    - Every viewset requires authentication (IsAuthenticated).
    - "Owner" fields (teacher/student/sender/created_by/uploaded_by/user) are
      always taken from request.user in perform_create — never from the payload.
    - A few models need business logic beyond plain CRUD, exposed as @action
      endpoints: ClassSession.join, ClassJoinRequest.accept/reject/cancel,
      LivePoll.vote, AssignmentSubmission.grade, SessionParticipant.leave.
    - Querysets are scoped where it matters for privacy (e.g. a student should
      not see another student's pass purchases or wallet ledger).
"""

import hashlib
import logging
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone
from rest_framework import mixins, pagination, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import (
    Assignment,
    AssignmentSubmission,
    BreakoutRoom,
    Certificate,
    ChatMessage,
    ClassHoliday,
    ClassJoinRequest,
    ClassMaterial,
    ClassPass,
    ClassQuery,
    ClassReminder,
    ClassSchedule,
    ClassSession,
    Classroom,
    ClassroomBan,
    ClassroomReport,
    ClassroomReview,
    ClassroomStaff,
    ClassroomWishlist,
    CoinTransaction,
    Coupon,
    LivePoll,
    Notice,
    Notification,
    PassDailyCharge,
    PassPurchase,
    PollResponse,
    Referral,
    SessionParticipant,
    SessionWaitlist,
    create_bulk_notifications,
    create_notification,
    get_classroom_list_cache_version,
    referral_code_for_user,
    referral_code_to_user_id,
)
from .livekit_utils import (
    LIVEKIT_URL,
    LiveKitError,
    ParticipantRole,
    ensure_room,
    generate_livekit_token,
    remove_participant,
    set_participant_audio_muted,
    start_room_recording,
    stop_room_recording,
    verify_webhook_event,
)
from .serializers import (
    AssignmentGradeSerializer,
    AssignmentSerializer,
    AssignmentSubmissionSerializer,
    BreakoutRoomSerializer,
    CertificateIssueSerializer,
    CertificateSerializer,
    ChatMessageSerializer,
    ClassHolidaySerializer,
    ClassJoinRequestDecisionSerializer,
    ClassJoinRequestSerializer,
    ClassMaterialSerializer,
    ClassPassSerializer,
    ClassQueryAnswerSerializer,
    ClassQuerySerializer,
    ClassReminderSerializer,
    ClassScheduleSerializer,
    ClassSessionSerializer,
    ClassroomBanSerializer,
    ClassroomReportSerializer,
    ClassroomReviewSerializer,
    ClassroomSerializer,
    ClassroomStaffSerializer,
    ClassroomStatsSerializer,
    ClassroomWishlistSerializer,
    CoinTransactionSerializer,
    CouponSerializer,
    LivePollSerializer,
    MyReferralCodeSerializer,
    NoticeSerializer,
    NotificationSerializer,
    PassPurchaseSerializer,
    PollResponseSerializer,
    ReferralRedeemSerializer,
    ReferralSerializer,
    SessionParticipantSerializer,
    SessionRecordingSerializer,
    SessionWaitlistSerializer,
    TeacherEarningsSerializer,
)


class LiveClassPagination(pagination.PageNumberPagination):
    """NOTE (production): this project's DRF settings weren't in scope here,
    so there's no visibility into whether DEFAULT_PAGINATION_CLASS is set
    globally. Applied explicitly on the two endpoints most likely to grow
    unbounded over a classroom's lifetime (chat history, coin ledger) so
    neither can return a multi-thousand-row response in one call regardless
    of the project-wide default. If a global default IS already configured
    in settings.py, this override is harmless (page_size still applies);
    if it isn't, every other list endpoint in this file should get one too
    — see the settings snippet delivered alongside this file.
    """

    page_size = 30
    page_size_query_param = "page_size"
    max_page_size = 100


def _safe_delay(task, *args, **kwargs):
    """Queue a Celery task without ever letting a broker problem hang or
    fail the request that triggered it.

    NOTE (fix): every notify_*.delay() call site in this file used to call
    .delay() directly and unguarded — unlike the equivalent calls in
    signals.py, which are always wrapped in try/except (see that module's
    docstring, "PRODUCTION-HARDENING NOTES"). If the Celery broker
    (Redis/RabbitMQ) isn't reachable — common in local/dev setups where the
    broker just isn't running — .delay() blocks on the connection attempt
    (or raises) INSIDE the request-response cycle, since these calls aren't
    deferred to transaction.on_commit() either. From the client's
    perspective this looks exactly like a hung/spinning request even though
    the actual DB write (the join request, the purchase, the accept) already
    landed successfully before this call — a broker outage should never be
    able to make a successful write look like a failed/stuck one. Mirrors
    signals.py's existing philosophy: notification dispatch is best-effort
    and must never block or break the caller.
    """
    try:
        task.delay(*args, **kwargs)
    except Exception:
        logging.getLogger(__name__).exception(
            "Failed to queue Celery task %s (broker unreachable?) — request still succeeds.",
            getattr(task, "name", task),
        )


def _is_truthy(value) -> bool:
    """Parse a query-string flag like ?mine=1 / ?mine=true properly.

    NOTE (fix): several endpoints below used bare `if request.query_params.get("flag")`
    to decide a boolean, which means ANY non-empty value — including the very
    literal string "false" or "0" — was treated as True. `?mine=false` silently
    behaved like `?mine=true`, and `?include_expired=false` silently behaved
    like `?include_expired=true`. This helper treats only "0"/"false"/"no"/""
    (case-insensitive) as False, absence of the param as False, and everything
    else — including bare presence, e.g. "?mine" — as True.
    """
    if value is None:
        return False
    return str(value).strip().lower() not in ("", "0", "false", "no")


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
    # explicit pagination here, same class of gap the LiveClassPagination
    # docstring already calls out for chat/coin-ledger. Applied directly so
    # this can't return every classroom on the platform in one response
    # regardless of whether a project-wide default is configured.
    pagination_class = LiveClassPagination
    # NOTE (UX): the Explore/search list had no sort control — a client
    # could only ever get the model's default order (newest first). Adds
    # opt-in ?ordering=rating_avg / ?ordering=-enrolled_count / etc. without
    # touching the default behavior for existing callers that don't pass it.
    filter_backends = [OrderingFilter]
    ordering_fields = ["created_at", "rating_avg", "enrolled_count", "title"]
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
        cache_key = f"liveclass:classroom_list:{version}:{query_fingerprint}"

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
        serializer.save(teacher=self.request.user)

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

    @action(detail=True, methods=["get"])
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
                                doubts, assignments, reviews, etc.) but
                                CANNOT enter a live session — needs a fresh
                                join request to renew.
            "none"          -> never purchased a pass (or their only join
                                request is still pending/rejected/cancelled).
                                Sees only the public listing info + reviews.

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

        # legacy "status" naming kept exactly as before ("owner" not "admin")
        # so existing frontend code doesn't break.
        status_label = "owner" if level == AccessLevel.ADMIN else level

        return Response(
            {
                "access_level": level,
                "status": status_label,
                "has_access": level in (AccessLevel.ADMIN, AccessLevel.ACTIVE),
                "can_view_internals": level != AccessLevel.NONE,
                "can_enter_class": level in (AccessLevel.ADMIN, AccessLevel.ACTIVE),
                "expires_at": latest.expires_at if latest else None,
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
                    {"detail": "There is no live class right now.", "no_session": True},
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
                    "weekly_timing": classroom.weekly_timing_summary(),
                    "upcoming_holidays": classroom.upcoming_holidays(),
                }
            ).data
        )

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

        with transaction.atomic():
            ban, created = ClassroomBan.objects.get_or_create(
                classroom=classroom,
                student=student,
                defaults={
                    "banned_by": request.user,
                    "reason": serializer.validated_data.get("reason", ""),
                },
            )
            if not created:
                # Already banned — idempotent, just return the existing row
                # instead of erroring on a double-tap or a retried request.
                return Response(ClassroomBanSerializer(ban).data, status=200)

            active_purchases = list(
                PassPurchase.objects.select_for_update().filter(
                    class_pass__classroom=classroom,
                    student=student,
                    status=PassPurchase.Status.SUCCESS,
                    is_active=True,
                    expires_at__gt=timezone.now(),
                )
            )
            for purchase in active_purchases:
                purchase.reverse()

            ClassJoinRequest.objects.filter(
                classroom=classroom, student=student, status=ClassJoinRequest.Status.PENDING
            ).update(status=ClassJoinRequest.Status.REJECTED)

        # Best-effort LiveKit kick, deliberately OUTSIDE the transaction
        # above (network call — same reasoning as everywhere else in this
        # file that talks to LiveKit) and never allowed to fail the ban
        # itself, same pattern as ClassSessionViewSet.kick().
        live_participant = (
            SessionParticipant.objects.filter(
                session__classroom=classroom, user=student, left_at__isnull=True
            )
            .select_related("session")
            .first()
        )
        if live_participant is not None:
            try:
                remove_participant(str(live_participant.session.room_id), identity=str(student.id))
            except LiveKitError:
                logging.getLogger(__name__).warning(
                    "LiveKit remove_participant failed while banning student %s from classroom %s "
                    "(room already gone / LiveKit unreachable).", student.id, classroom.id,
                )
            now = timezone.now()
            SessionParticipant.objects.filter(pk=live_participant.pk).update(left_at=now, kicked_at=now)

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
    # unbounded as reports accumulate — same gap the LiveClassPagination
    # docstring already calls out. See views.py module docstring.
    pagination_class = LiveClassPagination
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
        from .tasks import notify_report_reviewed

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
    pagination_class = LiveClassPagination
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

    def perform_update(self, serializer):
        if serializer.instance.classroom.teacher_id != self.request.user.id:
            raise PermissionDenied("Only the classroom's teacher can edit a schedule.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.classroom.teacher_id != self.request.user.id:
            raise PermissionDenied("Only the classroom's teacher can remove a schedule.")
        instance.delete()


# ---------------------------------------------------------------------------
# 3. SESSION
# ---------------------------------------------------------------------------
def _org_staff_role(classroom, user):
    """Staff row for this user on this classroom, or None. Separate lookup
    (instead of inlining everywhere) since both role-resolution and the
    org-staff host-access rule below need it."""
    return (
        ClassroomStaff.objects.filter(classroom=classroom, user=user)
        .values_list("role", flat=True)
        .first()
    )


def _has_room_access_no_pass(classroom, user) -> bool:
    """Who gets into the room WITHOUT needing a pass, beyond the teacher
    (Classroom.has_access already covers teacher + valid PassPurchase).

    Organisation classrooms: ANY staff row (TA included) is the
    organisation's own staff, not a paying student — they shouldn't need to
    buy a pass to enter their own org's class. Individual (solo teacher)
    classrooms keep the tighter rule: only CO_TEACHER/MODERATOR get
    host-tier treatment (see _resolve_session_roles); a plain TA on an
    individual classroom still needs a pass like any student.
    """
    if classroom.classroom_type != Classroom.ClassroomType.ORGANISATION:
        return False
    return _org_staff_role(classroom, user) is not None


def _has_room_access(classroom, user) -> bool:
    """Combined gate used by join()/token(): valid pass (or teacher) OR
    org-staff bypass above."""
    return classroom.has_access(user) or _has_room_access_no_pass(classroom, user)


def _resolve_session_roles(session, user):
    """Map a user to (livekit_role, participant_role) for a given session.

    - Classroom.teacher                                    -> HOST
    - ClassroomStaff (co_teacher / moderator), any classroom -> CO_HOST on
      LiveKit (moderation power), recorded as SessionParticipant.Role.HOST
      since that model only distinguishes HOST/STUDENT.
    - ClassroomStaff (ta), ORGANISATION classroom only     -> CO_HOST too —
      org staff are staff, not students, even in the TA seat.
    - ClassroomStaff (ta) on an INDIVIDUAL classroom, or anyone else -> STUDENT
    """
    classroom = session.classroom
    if user.id == classroom.teacher_id:
        return ParticipantRole.HOST, SessionParticipant.Role.HOST

    staff_role = _org_staff_role(classroom, user)
    if staff_role in (ClassroomStaff.Role.CO_TEACHER, ClassroomStaff.Role.MODERATOR):
        return ParticipantRole.CO_HOST, SessionParticipant.Role.HOST
    if staff_role is not None and classroom.classroom_type == Classroom.ClassroomType.ORGANISATION:
        return ParticipantRole.CO_HOST, SessionParticipant.Role.HOST

    return ParticipantRole.STUDENT, SessionParticipant.Role.STUDENT


def _can_moderate_session(session, user) -> bool:
    """Teacher, co-teacher, or moderator can end the session / remove
    participants — and, on an organisation classroom, any staff row (TA
    included), same host-tier bypass as _resolve_session_roles above."""
    classroom = session.classroom
    if user.id == classroom.teacher_id:
        return True
    staff_role = _org_staff_role(classroom, user)
    if staff_role in (ClassroomStaff.Role.CO_TEACHER, ClassroomStaff.Role.MODERATOR):
        return True
    return staff_role is not None and classroom.classroom_type == Classroom.ClassroomType.ORGANISATION


def _can_manage_classroom(classroom, user) -> bool:
    """Teacher, co-teacher, or moderator can post notices / mark off-days.
    (Plain TAs on an individual classroom can't — same split as above; org
    TAs can, via the same org-staff bypass.)"""
    if user.id == classroom.teacher_id:
        return True
    staff_role = _org_staff_role(classroom, user)
    if staff_role in (ClassroomStaff.Role.CO_TEACHER, ClassroomStaff.Role.MODERATOR):
        return True
    return staff_role is not None and classroom.classroom_type == Classroom.ClassroomType.ORGANISATION


def _can_view_classroom_internals(classroom, user) -> bool:
    """Gate for everything that is NOT part of the public Explore/listing
    card: schedule timings, generated sessions, off-days, the notice board,
    materials, doubts, assignments, etc. Before a join request is ever
    accepted (see ClassJoinRequestViewSet), a non-participant only ever gets
    the classroom's public description and its reviews — nothing else.

    True for the teacher/any staff row (_can_manage_classroom), OR anyone
    who has EVER held a successful pass for this classroom — active or
    expired (classroom.is_enrolled). Deliberately broader than has_access():
    a lapsed pass should still unlock the classroom's general content, it
    should only lose the ability to actually enter a live session (that
    tighter boundary is _has_room_access, used by join()/token()/chat/polls
    below)."""
    return _can_manage_classroom(classroom, user) or classroom.is_enrolled(user)


# The 4 user tiers this app recognises for a given classroom, from most to
# least privileged. Used by ClassroomViewSet.my_pass (the "class details"
# access-status endpoint the frontend drives its UI off of) and anywhere
# else that needs a single, authoritative answer to "who is this user, for
# this classroom".
class AccessLevel:
    ADMIN = "admin"      # teacher, or co-teacher/moderator/org-staff — full manage rights
    ACTIVE = "active"    # currently valid (unexpired) pass — full access, CAN enter a live session
    EXPIRED = "expired"  # held a pass before, it lapsed — full access EXCEPT entering a live session
    NONE = "none"        # never held a pass / no accepted join request yet — public listing + reviews only


def _access_level(classroom, user) -> str:
    """Single source of truth for the 4-tier access model:
        admin   -> _can_manage_classroom (teacher / co-teacher / moderator /
                    org staff of any role)
        active  -> classroom.has_access (currently valid pass)
        expired -> classroom.is_enrolled but NOT has_access (pass lapsed)
        none    -> everything else (no pass ever purchased, or their only
                    join request is still pending/rejected/cancelled)
    """
    if _can_manage_classroom(classroom, user):
        return AccessLevel.ADMIN
    if classroom.has_access(user):
        return AccessLevel.ACTIVE
    if classroom.is_enrolled(user):
        return AccessLevel.EXPIRED
    return AccessLevel.NONE


def _accessible_classroom_ids(user):
    """Classroom ids `user` is allowed to see internals for: classrooms they
    teach or staff, plus every classroom where they have EVER held a
    successful pass (active or expired — see is_enrolled). Used to scope
    list endpoints that are called WITHOUT a ?classroom= filter, so e.g.
    GET /schedules/ never leaks every classroom's schedule on the whole
    platform to an unrelated signed-in user, while still showing a lapsed
    student the classrooms they were once enrolled in."""
    managed = Classroom.objects.filter(
        Q(teacher=user) | Q(staff__user=user)
    ).values_list("id", flat=True)
    enrolled = PassPurchase.objects.filter(
        student=user,
        status=PassPurchase.Status.SUCCESS,
        is_active=True,
    ).values_list("class_pass__classroom_id", flat=True)
    return set(managed) | set(enrolled)


class ClassSessionViewSet(viewsets.ModelViewSet):
    """NOTE (fix): same gap as ClassScheduleViewSet above — a session's
    scheduled_start/room_id/etc is internal detail, not public listing
    info. Gated behind _can_view_classroom_internals; the actual seat/
    LiveKit-access check on join()/token() (_has_room_access) is unchanged
    and stays the real security boundary for entering the room."""

    serializer_class = ClassSessionSerializer
    permission_classes = [IsAuthenticated]
    # NOTE (fix): a long-running weekly classroom accumulates ClassSession
    # rows indefinitely (one per past occurrence, forever) — same
    # unbounded-growth class as chat/coin-ledger. Explicit pagination here
    # for the same reason.
    pagination_class = LiveClassPagination
    # NOTE (fix): join()/token() below pass throttle_scope="..." as an
    # extra @action kwarg (DRF's documented per-action ScopedRateThrottle
    # pattern). DRF's ViewSetMixin.as_view() validates every such extra
    # kwarg against `hasattr(cls, key)` at ROUTER-REGISTRATION time (i.e.
    # on every server start / URL import), not per-request — so without a
    # base `throttle_scope` attribute existing on this class, the whole
    # app fails to boot with "ClassSessionViewSet() received an invalid
    # keyword 'throttle_scope'" the moment urls.py is imported. The value
    # here is irrelevant (each action overrides it via its own
    # throttle_scope=... kwarg at request time) — only its *existence* as
    # a class attribute matters for this check to pass.
    throttle_scope = None
    # NOTE (perf): classroom_title is a dotted source ("classroom.title") on
    # every row — select_related avoids one extra query per session listed.
    queryset = ClassSession.objects.select_related("classroom")

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

    # NOTE (fix, CRITICAL): this viewset is a plain ModelViewSet with NO
    # perform_create/perform_update/perform_destroy override at all — every
    # other "classroom internals" viewset in this file (schedules,
    # materials, assignments, notices, holidays...) locks writes behind
    # _can_manage_classroom, but sessions never got that treatment. As
    # shipped, ANY authenticated user could:
    #   - POST a brand-new, immediately-joinable ClassSession onto ANY
    #     classroom (not just one they teach), bypassing schedule
    #     generation entirely;
    #   - PATCH someone else's session — including flipping `status` to
    #     COMPLETED/CANCELLED, which the post_save signal in signals.py
    #     treats as a real end-of-class event: it tears down the LiveKit
    #     room, force-closes every open poll, force-checks-out every
    #     participant, and wipes the waitlist. That's a one-request DoS
    #     against a live class run by anyone with an account, not just a
    #     bug — kicking out an active teacher's students mid-lecture;
    #   - overwrite `recording_url` with an arbitrary link shown to every
    #     student as "the class recording" (a phishing vector), or
    #     silently DELETE the session outright.
    # Locked down to the same _can_manage_classroom check every sibling
    # viewset already uses. Manual creation stays available for a teacher
    # adding an ad-hoc session outside their normal recurrence rule; the
    # normal path is still schedule-driven generation.
    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        if not _can_manage_classroom(classroom, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can create a session.")
        # NOTE (fix): nothing stopped a session being created on a classroom
        # that's inactive/closed (see ClassroomViewSet.close — refunds every
        # active pass, then deactivates) or soft-deleted. A "closed" or
        # deleted classroom should never gain a fresh joinable session —
        # students who were just refunded could otherwise be shown a live
        # class to join with no valid pass path back in, and a soft-deleted
        # classroom's history should stay frozen, not keep growing.
        if not classroom.is_active or classroom.is_deleted:
            raise ValidationError("Can't create a session on an inactive or deleted classroom.")
        serializer.save()

    def perform_update(self, serializer):
        if not _can_manage_classroom(serializer.instance.classroom, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can edit this session.")
        # NOTE (fix): a teacher flipping a session's status to CANCELLED via
        # PATCH previously told nobody — students who had it on their
        # schedule (and anyone with a ClassReminder queued for it) only
        # found out by opening the app and noticing the session was gone
        # from their list. Compared before/after save so this only fires on
        # an actual transition into CANCELLED, not on every unrelated edit
        # (title/description/etc.) to an already-cancelled session.
        was_cancelled = serializer.instance.status == ClassSession.Status.CANCELLED
        session = serializer.save()
        if session.status == ClassSession.Status.CANCELLED and not was_cancelled:
            from .tasks import notify_session_cancelled

            _safe_delay(notify_session_cancelled,
                classroom_id=session.classroom_id,
                classroom_title=session.classroom.title,
                session_id=session.id,
                scheduled_start_iso=timezone.localtime(session.scheduled_start).strftime("%d %b, %I:%M %p"),
                exclude_user_id=self.request.user.id,
            )

    def perform_destroy(self, instance):
        if not _can_manage_classroom(instance.classroom, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can delete this session.")
        # NOTE (fix): same gap as the CANCELLED-status case above, for the
        # DELETE path — captured before the row is gone since the task
        # can't look a deleted session back up by id.
        classroom_id = instance.classroom_id
        classroom_title = instance.classroom.title
        session_id = instance.id
        scheduled_start_iso = timezone.localtime(instance.scheduled_start).strftime("%d %b, %I:%M %p")
        instance.delete()

        from .tasks import notify_session_cancelled

        _safe_delay(notify_session_cancelled,
            classroom_id=classroom_id,
            classroom_title=classroom_title,
            session_id=session_id,
            scheduled_start_iso=scheduled_start_iso,
            exclude_user_id=self.request.user.id,
        )

    # NOTE (perf/abuse): join/token both call out to LiveKit's server API
    # (ensure_room + a fresh JWT). Neither was rate-limited, so a buggy
    # client (retry loop on a flaky network) or a deliberate abuser could
    # hammer LiveKit's Room Service through us with no backpressure. Scoped
    # throttle here — add matching rates to DRF's DEFAULT_THROTTLE_RATES in
    # settings.py, e.g. {"session_join": "20/min", "session_token": "30/min"}.
    @action(detail=True, methods=["post"], throttle_classes=[ScopedRateThrottle], throttle_scope="session_join")
    def join(self, request, pk=None):
        """Gate joining behind Classroom.has_access(); handles waitlist overflow;
        ensures the LiveKit room exists and issues a role-scoped join token.

        The seat-count check + participant creation happen inside a row-locked
        transaction (select_for_update on the session) so two students hitting
        /join/ at the same instant can't both slip in past max_participants —
        the second request's lock acquisition waits for the first to commit,
        then re-reads the count. LiveKit network calls stay outside the lock
        so we're not holding a DB row lock open for the duration of an HTTP
        call to LiveKit's API.

        NOTE (fix — simplification): the actual join logic now lives in the
        module-level `_perform_join()` below, shared with
        `ClassroomViewSet.start_or_join()` — that endpoint is the one call
        the app makes to figure out "is there a session, and can I get into
        it" (previously 2-4 separate client-side calls: list live sessions,
        list scheduled ones, sort them client-side, maybe create an ad-hoc
        one, then join — see classroom_detail_screen.dart's old
        _enterClass/_offerStartClassNow/_startClassNow, now collapsed into
        one round trip). This action stays as a thin wrapper so directly
        joining a KNOWN session id still works exactly as before.
        """
        return _perform_join(self.get_object(), request.user)



    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        """Teacher/co-teacher/moderator: end the live session.

        This stays a thin status transition on purpose — room teardown,
        closing open polls, and force-checkout of remaining participants are
        handled by the post_save signal on ClassSession (see liveclass/signals.py).
        That way the exact same cleanup fires no matter which path marks a
        session COMPLETED: this action, the Django admin, or a future
        scheduled task that auto-completes sessions past their scheduled_end.

        NOTE (fix — pass escrow was charging the wrong date): actual_end
        was declared on the model and PassPurchase.charge_for_session()
        already read it (falling back to scheduled_start only when it's
        None) to decide WHICH calendar day of the pass's escrow to release
        — but nothing on any path ever actually set it, so that fallback
        was silently the only thing that ever ran. Harmless the vast
        majority of the time (a class usually ends the same calendar day
        it was scheduled to start), but wrong the moment a session runs
        past midnight or a teacher ends it well after scheduled_start's
        date — the escrow charge landed on the WRONG day, which matters
        because charging is per-calendar-day (see the unique
        (purchase, date) constraint on PassDailyCharge). Stamped here,
        before the save that flips status (and therefore before the
        post_save signal that reads it), so charge_for_session sees the
        real end time on its very first attempt.
        """
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can end the session.")

        session.status = ClassSession.Status.COMPLETED
        session.actual_end = timezone.now()
        session.save(update_fields=["status", "actual_end"])

        return Response({"detail": "Session ended.", "status": session.status})

    @action(detail=True, methods=["post"], throttle_classes=[ScopedRateThrottle], throttle_scope="session_token")
    def token(self, request, pk=None):
        """Issue a fresh LiveKit token WITHOUT creating a SessionParticipant
        row or flipping session status/seat-count — for reconnecting a
        dropped call, joining from a second device, or manually testing a
        LiveKit client against this room. Gated behind the same access rule
        as join(); shown on class details as a "Generate token" action.
        """
        session = self.get_object()
        user = request.user

        if not _has_room_access(session.classroom, user):
            raise PermissionDenied("A valid pass is required to join this class.")
        # NOTE (fix): same host/staff time-window exemption as join() above
        # — resolve role first so is_joinable() knows whether to skip the
        # scheduled_start/scheduled_end window for this caller.
        livekit_role, participant_role = _resolve_session_roles(session, user)
        is_host = participant_role == SessionParticipant.Role.HOST
        if not session.is_joinable(is_host=is_host):
            raise ValidationError("This session is not joinable right now.")
        if session.participants.filter(user=user, kicked_at__isnull=False).exists():
            raise PermissionDenied("You've been removed from this session by a moderator and can't rejoin it.")

        room_name = str(session.room_id)
        # NOTE (fix — was `except LiveKitError as exc: return Response(...,
        # status=503)`): LiveKitError is now a DRF APIException (see
        # livekit_utils.py), so simply letting it propagate gives the
        # standard {"detail", "code"} envelope via liveclass_exception_handler
        # instead of a hand-built Response that bypassed it. No try/except
        # needed here — nothing to clean up on failure in token()'s case
        # (unlike _perform_join(), this doesn't create a participant row).
        ensure_room(room_name=room_name, max_participants=session.classroom.max_participants)
        livekit_token = generate_livekit_token(
            room_name=room_name,
            user_id=user.id,
            user_name=user.get_full_name() or user.username,
            role=livekit_role,
        )

        return Response(
            {
                "room_id": room_name,
                "role": participant_role,
                "livekit_role": livekit_role,
                "livekit_url": LIVEKIT_URL,
                "livekit_token": livekit_token,
            },
            status=200,
        )

    @action(detail=True, methods=["post"], url_path="kick/(?P<user_id>[^/.]+)")
    def kick(self, request, pk=None, user_id=None):
        """Teacher/co-teacher/moderator: remove a disruptive participant from
        the live LiveKit room AND block them from rejoining THIS session.

        NOTE (fix): this used to only call remove_participant() + mark
        left_at — nothing stopped the same user calling /join/ or /token/
        again immediately after. Now stamps kicked_at, which join()/token()
        check and refuse re-entry on. Scoped to this one session (not the
        whole classroom) — a heated moment in one class shouldn't
        permanently lock someone out of a classroom they've paid for; a
        teacher who wants that can still deactivate/refund the pass.
        """
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can remove participants.")

        # NOTE (fix): let LiveKitError (a DRF APIException) propagate
        # instead of catching it just to hand-build a Response — see
        # token()'s NOTE above for why.
        remove_participant(str(session.room_id), identity=str(user_id))

        now = timezone.now()
        session.participants.filter(user_id=user_id, left_at__isnull=True).update(
            left_at=now, kicked_at=now
        )
        # NOTE (fix): a kick frees a seat exactly like a normal leave does —
        # without this, that seat just sits empty until the host happens to
        # open the waitlist tab and notices. See _try_promote_from_waitlist's
        # own docstring below for the full reasoning.
        _try_promote_from_waitlist(session)
        return Response({"detail": "Participant removed and blocked from rejoining this session."})

    @action(detail=True, methods=["post"], url_path="mute/(?P<user_id>[^/.]+)")
    def mute(self, request, pk=None, user_id=None):
        """Teacher/co-teacher/moderator: force-mute (or release the mute on)
        a participant's microphone WITHOUT removing them from the room.

        NOTE (fix — daily-use classroom control was missing): kick() is a
        blunt instrument for the extremely common "please mute, I can hear
        an echo/dog barking/etc." moment — it drops the student's whole
        connection and blocks them from rejoining this session. This just
        flips the participant's already-published mic track's muted flag
        server-side via LiveKit's Room Service API; LiveKit pushes that down
        as a TrackMutedEvent to the student's own client, so their mic
        button/UI updates to reflect it immediately, same as it would if
        they'd muted themselves. Nothing here touches SessionParticipant or
        kicked_at — this is a live audio toggle, not a moderation record.

        Body: {"muted": true|false} — defaults to true (mute) when omitted,
        so a bare POST with no body is the common "mute them" case; the
        host calls this again with {"muted": false} to hand the mic back.
        """
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can mute participants.")

        muted = request.data.get("muted", True)
        if isinstance(muted, str):
            muted = muted.strip().lower() not in ("false", "0", "")

        # NOTE (fix): let LiveKitError propagate — see token()'s NOTE above.
        set_participant_audio_muted(str(session.room_id), identity=str(user_id), muted=muted)

        return Response({"detail": "Microphone muted." if muted else "Microphone unmuted."})

    @action(detail=True, methods=["post"], url_path="hand")
    def raise_hand(self, request, pk=None):
        """Any current participant (student or host) toggles THEIR OWN hand.

        NOTE (feature): the only "I want to say something right now" signal
        in the app used to be ClassQuery — a written doubt meant to be
        answered whenever, not a live in-the-moment gesture. This is that
        gesture: a plain timestamp on the caller's own SessionParticipant
        row for this session, cleared either by the student themselves
        (calling this again with {"raised": false}) or by the host via
        hand/{user_id}/lower/ below after acknowledging it.

        Body: {"raised": true|false} — defaults to true (raise) so a bare
        POST with no body is the common case.
        """
        session = self.get_object()
        participant = (
            session.participants.filter(user=request.user, left_at__isnull=True).order_by("-joined_at").first()
        )
        if participant is None:
            raise PermissionDenied("You're not currently an active participant in this session.")

        raised = request.data.get("raised", True)
        if isinstance(raised, str):
            raised = raised.strip().lower() not in ("false", "0", "")

        participant.hand_raised_at = timezone.now() if raised else None
        participant.save(update_fields=["hand_raised_at"])
        return Response({"detail": "Hand raised." if raised else "Hand lowered.", "hand_raised": raised})

    @action(detail=True, methods=["post"], url_path="hand/(?P<user_id>[^/.]+)/lower")
    def lower_hand(self, request, pk=None, user_id=None):
        """Teacher/co-teacher/moderator: clear someone ELSE's raised hand —
        the normal "okay, I've seen you, go ahead / next question" action
        once they've been acknowledged, without waiting for the student to
        lower it themselves.
        """
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can lower another participant's hand.")

        updated = session.participants.filter(
            user_id=user_id, left_at__isnull=True, hand_raised_at__isnull=False
        ).update(hand_raised_at=None)
        if not updated:
            raise NotFound("That participant's hand wasn't raised.")
        return Response({"detail": "Hand lowered."})

    @action(detail=True, methods=["post"], url_path="start-recording")
    def start_recording(self, request, pk=None):
        """Teacher/co-teacher/moderator: start a LiveKit Room Composite
        Egress recording of this session.

        NOTE (gap fix): Classroom.recording_enabled and
        ClassSession.recording_url already existed — a teacher could see
        "Recording" as a classroom setting and there was a field to hold
        the finished link — but nothing anywhere ever called LiveKit's
        Egress API, so recording_url could never actually get filled in.
        This is that missing engine call.

        Refuses to start a second recording on top of one already running
        (egress_id already set) rather than silently losing track of the
        first job's ID. The file only actually appears at recording_url
        once LiveKit's `egress_ended` webhook lands (see
        LiveKitWebhookView) — starting this call does not itself return a
        playable URL, since the recording doesn't exist yet.
        """
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can start recording.")
        if not session.classroom.recording_enabled:
            raise ValidationError("Recording is turned off for this classroom.")
        if session.egress_id:
            raise ValidationError("A recording is already in progress for this session.")

        # NOTE (fix): let LiveKitError propagate — see token()'s NOTE above.
        egress_id = start_room_recording(str(session.room_id))

        session.egress_id = egress_id
        session.save(update_fields=["egress_id"])
        return Response({"detail": "Recording started.", "egress_id": egress_id})

    @action(detail=True, methods=["post"], url_path="stop-recording")
    def stop_recording(self, request, pk=None):
        """Teacher/co-teacher/moderator: stop the in-progress recording.

        Clears egress_id immediately so the UI stops showing "REC" right
        away; recording_url still fills in asynchronously via the
        `egress_ended` webhook once LiveKit finishes uploading the file —
        stopping the egress job and the file being ready are two different
        moments, and this endpoint only guarantees the former.
        """
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can stop recording.")
        if not session.egress_id:
            raise ValidationError("No recording is currently in progress for this session.")

        # NOTE (fix): let LiveKitError propagate — see token()'s NOTE above.
        egress_id = session.egress_id
        stop_room_recording(egress_id)

        session.egress_id = ""
        session.save(update_fields=["egress_id"])
        return Response({"detail": "Recording stopped. The file will appear here once processing finishes."})

    # FEATURE: breakout rooms. Backend for live_session_screen.dart's
    # breakout UI, which shipped assuming exactly this REST shape (see that
    # file's header comment) — `LiveClassApi.breakoutRooms` on the Flutter
    # side was still hitting endpoints that didn't exist until now.
    @action(detail=True, methods=["get", "post"], url_path="breakout")
    def breakout(self, request, pk=None):
        """GET: the session's current breakout-room layout — an empty list
        means no breakout is running. Readable by anyone with room access
        (same _has_room_access gate as chat/polls), not just the host, so a
        student who reconnects mid-breakout finds out which room they're in
        from the server rather than a peer-to-peer signal they may have
        missed.

        POST {"room_count": int}: teacher/co-teacher/moderator only.
        Creates exactly `room_count` empty numbered rooms (1..room_count).
        Refused if a breakout is already running for this session — call
        breakout/close/ first, so a stray double-tap can't silently double
        the room count or leave half the class assigned to rooms nobody's
        tracking the size of anymore.
        """
        session = self.get_object()

        if request.method == "GET":
            if not _has_room_access(session.classroom, request.user):
                raise PermissionDenied("A valid pass is required to view this session's breakout rooms.")
            rooms = session.breakout_rooms.all()
            return Response(BreakoutRoomSerializer(rooms, many=True).data)

        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can start breakout rooms.")
        if session.breakout_rooms.exists():
            raise ValidationError("Breakout rooms are already running for this session — close them first.")

        try:
            room_count = int(request.data.get("room_count"))
        except (TypeError, ValueError):
            raise ValidationError("room_count must be a positive integer.")
        if not (1 <= room_count <= 50):
            raise ValidationError("room_count must be between 1 and 50.")

        BreakoutRoom.objects.bulk_create(
            [BreakoutRoom(session=session, room_number=n) for n in range(1, room_count + 1)]
        )
        rooms = session.breakout_rooms.all()
        return Response(BreakoutRoomSerializer(rooms, many=True).data, status=201)

    @action(detail=True, methods=["post"], url_path="breakout/assign")
    def breakout_assign(self, request, pk=None):
        """Teacher/co-teacher/moderator: put one participant into a room, or
        back into the main room with `{"room": null}`.

        Body: {"participant_id": int, "room": int|null}. [participant_id]
        is the SessionParticipant row id — same convention every other
        moderation action in this file uses (kick/mute/lower-hand all key
        off a participant/user id the same way) — not the room-scoped
        LiveKit identity string; BreakoutRoomSerializer does that
        conversion on the way out so the client never has to.
        """
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can assign breakout rooms.")

        participant = session.participants.filter(
            pk=request.data.get("participant_id"), left_at__isnull=True
        ).first()
        if participant is None:
            raise ValidationError("That participant isn't currently active in this session.")

        room_number = request.data.get("room")
        if room_number is None:
            participant.breakout_room = None
        else:
            try:
                room_number = int(room_number)
            except (TypeError, ValueError):
                raise ValidationError("room must be an integer or null.")
            room = session.breakout_rooms.filter(room_number=room_number).first()
            if room is None:
                raise ValidationError(f"Breakout room {room_number} doesn't exist for this session.")
            participant.breakout_room = room
        participant.save(update_fields=["breakout_room"])

        rooms = session.breakout_rooms.all()
        return Response(BreakoutRoomSerializer(rooms, many=True).data)

    @action(detail=True, methods=["post"], url_path="breakout/close")
    def breakout_close(self, request, pk=None):
        """Teacher/co-teacher/moderator: end the breakout. Deletes every
        BreakoutRoom row for this session — SessionParticipant.breakout_room
        is on_delete=SET_NULL, so this one delete also clears everyone's
        room assignment in a single query, no per-participant loop needed.
        """
        session = self.get_object()
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can close breakout rooms.")
        deleted, _ = session.breakout_rooms.all().delete()
        if not deleted:
            raise ValidationError("No breakout rooms are currently running for this session.")
        return Response({"detail": "Breakout rooms closed — everyone's back in the main room."})


# ---------------------------------------------------------------------------
# LIVEKIT WEBHOOK — server-to-server, not a user-facing endpoint.
#
# FEATURE (recording, part 2): start_recording()/stop_recording() above
# control the egress JOB, but the actual output file only exists once
# LiveKit finishes encoding + uploading it — which happens asynchronously,
# sometimes well after stop_recording() returns. LiveKit's own webhook
# mechanism is how it tells us that's done. Point your LiveKit project's
# webhook URL (in the LiveKit Cloud/self-host dashboard) at this endpoint;
# verify_webhook_event() checks the Authorization header's signature against
# LIVEKIT_API_KEY/SECRET so a request claiming to be this event can't be
# forged by anyone who doesn't hold those credentials — this is why the
# view skips normal user auth (LiveKit's server has no Django session/token)
# but is NOT unauthenticated in any meaningful sense.
# ---------------------------------------------------------------------------


def _perform_join(session, user):
    """Shared join implementation — see ClassSessionViewSet.join()'s
    docstring above and ClassroomViewSet.start_or_join() below for the two
    call sites. Moved out of the action method so both can share it without
    duplicating the seat-locking transaction / LiveKit error handling.
    """
    # NOTE (fix — ClassroomBan had no teeth in the join path): checked
    # explicitly (not just relying on Classroom.has_access() returning
    # False for a banned student) so a banned student gets a clear "you've
    # been banned" message instead of the generic "no valid pass" one —
    # they may well still hold what looks like a valid pass if the refund
    # from the ban hasn't landed yet.
    if session.classroom.bans.filter(student_id=user.id).exists():
        raise PermissionDenied("You've been banned from this classroom by the teacher.")
    if not _has_room_access(session.classroom, user):
        raise PermissionDenied("A valid pass is required to join this class.")
    # NOTE (fix): resolved BEFORE the is_joinable() check (was resolved
    # again later, inside the transaction, for the same purpose) so the
    # host/staff time-window exemption in is_joinable() has a role to
    # check against. See is_joinable()'s docstring for why this matters.
    livekit_role, participant_role = _resolve_session_roles(session, user)
    is_host = participant_role == SessionParticipant.Role.HOST
    if not session.is_joinable(is_host=is_host):
        raise ValidationError("This session is not joinable right now.")
    # NOTE (fix): block re-entry for anyone kicked from THIS session —
    # see SessionParticipant.kicked_at / kick() above for why this
    # check didn't exist before (moderation had no teeth without it).
    if session.participants.filter(user=user, kicked_at__isnull=False).exists():
        raise PermissionDenied("You've been removed from this session by a moderator and can't rejoin it.")

    # NOTE (fix): PassPurchase.sync_missed_charges()'s own docstring
    # already promised this call site ("called opportunistically
    # whenever a student actually shows up") but nothing ever actually
    # called it — the per-day escrow charge otherwise depends entirely
    # on the post_save signal on ClassSession, which is fine for the
    # normal case but leaves no safety net if that signal ever misses
    # a session (a stuck task, a status flip via some other path).
    # Best-effort and outside the seat-locking transaction below: a
    # failure here must never block the student from actually joining
    # the class they're trying to enter right now.
    purchase = PassPurchase.objects.filter(
        student=user,
        class_pass__classroom=session.classroom,
        status=PassPurchase.Status.SUCCESS,
        is_active=True,
    ).first()
    if purchase is not None:
        try:
            purchase.sync_missed_charges()
        except Exception:
            logging.getLogger(__name__).exception(
                "sync_missed_charges failed for purchase %s on join of session %s",
                purchase.id, session.id,
            )

    with transaction.atomic():
        session = (
            ClassSession.objects.select_for_update().select_related("classroom").get(pk=session.pk)
        )

        # NOTE: livekit_role/participant_role already resolved above,
        # before the is_joinable() check — no need to resolve again here.

        # NOTE (fix): this used to unconditionally .create() a new
        # SessionParticipant row on every call, then check capacity
        # first. A double-tap / retried request (or rejoining after a
        # dropped connection without ever calling leave()) created a
        # second "still in room" (left_at IS NULL) row for the same
        # user, which (a) double-counted them against max_participants,
        # shrinking real capacity over time, (b) could waitlist someone
        # who already has a seat, and (c) left an orphan row that never
        # gets closed out, so attendance/waitlist-promotion logic in
        # signals.py never fires for it. Reuse the user's existing open
        # row for this session if one exists, and skip the capacity
        # check entirely in that case — they already hold a seat.
        participant = session.participants.filter(user=user, left_at__isnull=True).first()
        newly_created = participant is None
        if newly_created:
            current_count = session.participants.filter(left_at__isnull=True).count()
            if current_count >= session.classroom.max_participants:
                SessionWaitlist.objects.get_or_create(session=session, student=user)
                return Response(
                    {"detail": "Session is full — you've been added to the waitlist."}, status=202
                )
            participant = SessionParticipant.objects.create(
                session=session, user=user, role=participant_role
            )

            # NOTE (fix — max_classes cap was silently dead): a
            # "N-class pack" pass's classes_attended never got
            # incremented anywhere in the codebase, so
            # Classroom.has_access()'s max_classes exclusion (see that
            # method's own NOTE (fix)) had nothing to actually compare
            # against and the cap never tripped. Count exactly one
            # class per SESSION per student — gated on newly_created,
            # not on every /join/ call, so a reconnect/retry against a
            # session the student is already seated in never
            # double-counts. Row-locked and done with select_for_update
            # so two students' concurrent joins against a
            # nearly-exhausted pack can't both slip in past the cap
            # (has_access() re-reads this same row on their NEXT join
            # attempt, so the cap is enforced going forward, not
            # retroactively on the join already in progress here).
            attendance_counted = False
            if purchase is not None:
                PassPurchase.objects.select_for_update().filter(pk=purchase.pk).update(
                    classes_attended=F("classes_attended") + 1
                )
                attendance_counted = True

        went_live = False
        if session.status == ClassSession.Status.SCHEDULED:
            session.status = ClassSession.Status.LIVE
            session.actual_start = session.actual_start or timezone.now()
            session.save(update_fields=["status", "actual_start"])
            went_live = True

    if went_live:
        # NOTE (fix): a session going LIVE was previously only ever
        # surfaced to students who had set an explicit ClassReminder
        # (send_due_reminders in tasks.py) — anyone who didn't set one
        # had no way to know class had actually started short of
        # opening the app and checking. Queued after the transaction
        # above has already committed the status change, so a
        # slow/failing push provider can't add latency here; excludes
        # whoever's own join() call just triggered this (the teacher,
        # normally) since they don't need a push about a class they're
        # already in.
        from .tasks import notify_session_live

        _safe_delay(notify_session_live, session.id, exclude_user_id=user.id)

    room_name = str(session.room_id)
    try:
        ensure_room(room_name=room_name, max_participants=session.classroom.max_participants)
        livekit_token = generate_livekit_token(
            room_name=room_name,
            user_id=user.id,
            user_name=user.get_full_name() or user.username,
            role=livekit_role,
        )
    except LiveKitError:
        # If the room service call fails, only roll back a newly-created
        # participant record (so the join can be retried) — if the user
        # was already in the room (reused row), don't mistakenly mark
        # them as "left".
        if newly_created:
            participant.delete()
            # NOTE (fix): the classes_attended bump above happens
            # before this LiveKit call, on the assumption the join
            # succeeds. If it doesn't, the student never actually got
            # into the room — undo the count along with the
            # participant row, or a flaky LiveKit call would silently
            # burn a class off someone's pack for a class they were
            # never let into.
            if attendance_counted:
                PassPurchase.objects.filter(pk=purchase.pk).update(
                    classes_attended=F("classes_attended") - 1
                )
        # NOTE (fix — was `return Response({"detail": str(exc)},
        # status=503)`): that hand-built Response bypassed
        # liveclass_exception_handler entirely (it only normalises RAISED
        # exceptions), so this path came back without a "code" field
        # unlike every other error in the app. `exc` is already a
        # LiveKitError — a DRF APIException carrying the right
        # user-safe detail and status_code (see livekit_utils.py) — so a
        # bare re-raise after the cleanup above is all that's needed; DRF
        # + liveclass_exception_handler take it from here.
        raise

    return Response(
        {
            "room_id": room_name,
            "participant_id": participant.id,
            "role": participant_role,
            "livekit_role": livekit_role,
            "livekit_url": LIVEKIT_URL,
            "livekit_token": livekit_token,
        },
        status=200,
    )


def _try_promote_from_waitlist(session):
    """Called immediately whenever a seat frees up on a session — a normal
    /leave/ (SessionParticipantViewSet.leave) or a host /kick/
    (ClassSessionViewSet.kick) — so the next waitlisted student gets pulled
    in automatically instead of the seat just sitting empty until a host
    happens to open the waitlist tab and hits /promote/ manually.

    NOTE (fix — real product gap): SessionWaitlistViewSet.promote() already
    existed as a host-triggered path, but nothing on the /leave/ or /kick/
    side ever called anything like it — a seat freeing up produced no
    signal to anyone. This is that missing trigger. promote() itself stays
    available unchanged for a host who wants to hand-pick a specific
    student out of order; this is just the "nobody has to babysit the
    waitlist" default that fires on every seat-freeing path automatically.

    Best-effort and swallows its own errors — a failure here must never
    break the /leave/ or /kick/ call that triggered it.
    """
    try:
        if session.status not in (ClassSession.Status.LIVE, ClassSession.Status.SCHEDULED):
            return

        student = None
        with transaction.atomic():
            current_count = session.participants.filter(left_at__isnull=True).count()
            if current_count >= session.classroom.max_participants:
                return  # still full — e.g. someone else already took the seat

            entry = (
                SessionWaitlist.objects.select_for_update()
                .select_related("student")
                .filter(session=session)
                .order_by("joined_at")
                .first()
            )
            if entry is None:
                return

            # Never resurrect someone THIS session explicitly kicked — same
            # guard as the manual promote() path.
            if session.participants.filter(user=entry.student, kicked_at__isnull=False).exists():
                entry.delete()
                return

            _, participant_role = _resolve_session_roles(session, entry.student)
            SessionParticipant.objects.create(
                session=session, user=entry.student, role=participant_role
            )
            student = entry.student
            entry.delete()
    except Exception:
        logging.getLogger(__name__).exception(
            "Failed to auto-promote waitlist for session %s", session.pk
        )
        return

    if student is None:
        return

    create_notification(
        recipient=student,
        notif_type=Notification.NotifType.WAITLIST_PROMOTED,
        title="You're in!",
        message=f"A seat opened up in '{session.classroom.title}' — you've been moved in from the waitlist.",
        classroom=session.classroom,
        session=session,
    )
    from .tasks import notify_waitlist_promotion

    _safe_delay(notify_waitlist_promotion, student.id, session.id)


class LiveKitWebhookView(APIView):
    permission_classes = []
    authentication_classes = []
    # NOTE (fix — legitimate webhook deliveries could get 429'd): without
    # this, the view inherits REST_FRAMEWORK's DEFAULT_THROTTLE_CLASSES
    # (AnonRateThrottle, since authentication_classes=[] means every
    # request here is anonymous) — 20/min, bucketed by source IP. LiveKit's
    # webhook sender always hits this from the same IP, and every
    # concurrent recording that ends around the same time fires one
    # `egress_ended` POST each; a busy platform can easily burst past
    # 20/min. Throttling runs in dispatch() BEFORE post() even executes,
    # so a tripped limit would silently drop the signature check and the
    # recording_url update with it — no user-facing symptom, just a
    # recording that never gets its playable link. This endpoint is
    # already protected by verify_webhook_event()'s signature check
    # (see livekit_utils.py), which is the real access control here, not
    # the shared per-IP rate limit meant for browser/app clients.
    throttle_classes = []

    def post(self, request):
        # NOTE (fix — was `except LiveKitError as exc: return Response(...,
        # status=401)`): verify_webhook_event() raises LiveKitError with
        # status_code=401 for a bad/missing signature (see
        # livekit_utils.py) — now that LiveKitError is a DRF APIException,
        # letting it propagate gives the same 401 with the standard
        # {"detail", "code"} envelope instead of a hand-built Response
        # that skipped liveclass_exception_handler. Signature-failure
        # detail is still logged inside verify_webhook_event(); the
        # caller here (LiveKit's servers, not a browser) doesn't need
        # more than the plain 401 + detail it already gets.
        event = verify_webhook_event(request.body, request.headers.get("Authorization", ""))

        if event.event == "egress_ended":
            egress_info = event.egress_info
            session = ClassSession.objects.filter(egress_id=egress_info.egress_id).first()
            if session is not None:
                file_results = list(egress_info.file_results)
                if file_results:
                    # `location` is the final playable URL/path for
                    # cloud-storage outputs; some SDK versions instead only
                    # populate `filename` for local/file-only outputs — try
                    # both rather than assuming one.
                    url = getattr(file_results[0], "location", "") or getattr(file_results[0], "filename", "")
                    if url:
                        session.recording_url = url
                # Egress job is over one way or another (success or
                # failure) — always clear egress_id so the UI's "REC"
                # indicator can't get stuck on if stop_recording() was
                # never explicitly called (e.g. the room's empty_timeout
                # closed the room and LiveKit auto-stopped the egress).
                session.egress_id = ""
                session.save(update_fields=["recording_url", "egress_id"])

        # 200 with no body is all LiveKit's webhook sender expects — it
        # just needs to know delivery succeeded so it doesn't retry.
        return Response(status=200)


# ---------------------------------------------------------------------------
# 4. PASS
# ---------------------------------------------------------------------------
class ClassPassViewSet(viewsets.ModelViewSet):
    serializer_class = ClassPassSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = LiveClassPagination
    queryset = ClassPass.objects.filter(is_active=True)
    # NOTE (UX): lets the buy-pass screen sort tiers by price or newest
    # instead of only ever getting the model's default (price ascending).
    filter_backends = [OrderingFilter]
    ordering_fields = ["price", "validity_days", "created_at"]
    ordering = ["price"]

    def get_queryset(self):
        # NOTE (fix): there was no get_queryset() override here at all, so
        # ?classroom=<id> — which BuyPassScreen sends on every load via
        # LiveClassApiService.getPasses(classroomId: ...) — was silently
        # ignored by the backend. GET passes/?classroom=X returned every
        # active pass for every classroom on the platform, not just X's;
        # the buy-pass screen would then show other classrooms' pricing
        # tiers mixed in with (or instead of) the one the student actually
        # opened. Passes are meant to be publicly browsable pre-purchase
        # (no _can_view_classroom_internals gate needed, unlike schedules/
        # materials), so this just applies the filter when given.
        #
        # NOTE (fix): the base `queryset = ClassPass.objects.filter(is_active=True)`
        # also silently doubled as the detail (get_object) queryset. Once a
        # teacher paused a pass (is_active=False) — e.g. to stop new signups
        # without deleting pricing history — GET/PATCH on that pass's own
        # detail URL 404'd for them too, so there was no way to edit it or
        # ever flip is_active back to True again. Same fix as ClassroomViewSet:
        # public/other-teachers' passes still only show active ones; the
        # classroom's own teacher can always see and manage all of theirs.
        qs = ClassPass.objects.filter(Q(is_active=True) | Q(classroom__teacher=self.request.user))
        classroom_id = self.request.query_params.get("classroom")
        if classroom_id:
            qs = qs.filter(classroom_id=classroom_id)
        return qs

    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        if classroom.teacher_id != self.request.user.id:
            raise PermissionDenied("Only the classroom's teacher can create a pass.")
        serializer.save()

    def perform_update(self, serializer):
        instance = serializer.instance
        if instance.classroom.teacher_id != self.request.user.id:
            raise PermissionDenied("Only the classroom's teacher can edit a pass.")

        # NOTE (fix): this is the pass-level twin of the create-sell-vanish
        # gap Classroom.perform_destroy was hardened against — just reached
        # via PATCH instead of DELETE. A pass with paying holders used to be
        # freely editable: nothing stopped a teacher from selling a pass at
        # its listed price/validity/max_classes, then quietly raising the
        # price (misleading future buyers about what current holders paid)
        # or — worse — cutting validity_days/max_classes on a pass people
        # ALREADY paid coins for. PassPurchase snapshots its own expires_at
        # at purchase time, but per-purchase attendance limits are checked
        # against this pass's live max_classes, so shrinking it after the
        # fact can silently lock a currently-paying student out of classes
        # they already paid for — coins taken, value clawed back after the
        # fact, no refund. Block any such downgrade while at least one
        # currently active, paid purchase is outstanding on this pass; the
        # teacher can still edit freely once every purchase on it has
        # lapsed or been refunded, and can always pause new sales via
        # is_active without touching the terms existing holders paid for.
        #
        # NOTE (fix, race): the has_active_purchases check + the save() used
        # to run unlocked, back to back but not atomically — a join request
        # could be accept()-ed (creating a fresh active PassPurchase) in the
        # gap between this check reading "no active purchases yet" and the
        # save() actually landing, letting a downgrade slip through against
        # a pass that, by the time the edit committed, already had a paying
        # holder. Locking the row here serializes against
        # ClassJoinRequestViewSet.accept()'s own select_for_update on the
        # same ClassPass row (see that view) — whichever transaction gets
        # there first now makes the other wait, instead of both reading a
        # stale "safe" snapshot.
        with transaction.atomic():
            instance = ClassPass.objects.select_for_update().get(pk=instance.pk)
            has_active_purchases = PassPurchase.objects.filter(
                class_pass=instance,
                status=PassPurchase.Status.SUCCESS,
                is_active=True,
            ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())).exists()

            if has_active_purchases:
                data = serializer.validated_data
                if "price" in data and data["price"] is not None and data["price"] > instance.price:
                    raise ValidationError(
                        "Can't raise the price on a pass with active holders. Create a new pass instead."
                    )
                for field in ("validity_days", "max_classes"):
                    if field in data and data[field] is not None:
                        old_value = getattr(instance, field)
                        if old_value is not None and data[field] < old_value:
                            raise ValidationError(
                                f"Can't reduce {field.replace('_', ' ')} on a pass with active "
                                "holders — that would retroactively cut access students already "
                                "paid coins for."
                            )
                if "pass_type" in data and data["pass_type"] != instance.pass_type:
                    raise ValidationError("Can't change the pass type on a pass with active holders.")

            serializer.instance = instance
            serializer.save()

    def perform_destroy(self, instance):
        if instance.classroom.teacher_id != self.request.user.id:
            raise PermissionDenied("Only the classroom's teacher can remove a pass.")

        # NOTE (fix): same create-sell-vanish gap as Classroom.perform_destroy
        # used to have, one level down — this used to be a bare
        # instance.delete() regardless of whether anyone had ever actually
        # paid coins for this pass. Deleting a pass that's been purchased
        # risks silently taking the purchase record (and the coin-spend +
        # access history it represents) down with it, and breaks every
        # screen that resolves a purchase back to its pass (buy_pass_screen,
        # wallet history, refund lookups). Once a pass has ever been sold,
        # deleting it is no longer a "clean up an unused pricing tier"
        # operation, so it's blocked outright — the supported way to retire
        # a sold pass is PATCH is_active=False (pause: stops new signups,
        # existing holders keep full access, refund/close flows are
        # untouched), which get_queryset() above already keeps visible to
        # the teacher for exactly this.
        #
        # NOTE (fix, race): same reasoning as perform_update above — lock
        # the row so this can't slip a delete through in the gap between an
        # in-flight accept() creating the pass's first-ever PassPurchase and
        # that transaction committing.
        with transaction.atomic():
            instance = ClassPass.objects.select_for_update().get(pk=instance.pk)
            if PassPurchase.objects.filter(class_pass=instance).exists():
                raise ValidationError(
                    "This pass has been purchased before and can't be deleted — set it to "
                    "inactive (pause) instead so existing holders keep their access and the "
                    "purchase history stays intact."
                )
            instance.delete()


# ---------------------------------------------------------------------------
# 5. PASS PURCHASE (read-only — a PassPurchase is only ever created as the
#    side effect of ClassJoinRequestViewSet.accept(); there is no direct
#    "buy" endpoint, on purpose — see ClassJoinRequest's docstring)
# ---------------------------------------------------------------------------
class PassPurchaseViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Own purchases only by default. Pass ?classroom=<id> as that
    classroom's teacher/co-teacher/moderator to see every purchase on it
    instead — see get_queryset()."""

    serializer_class = PassPurchaseSerializer
    permission_classes = [IsAuthenticated]
    # NOTE (fix): an active student's purchase history grows with every
    # renewal/repurchase across every classroom they've ever joined —
    # unbounded over time, same as the session/chat/ledger lists above.
    pagination_class = LiveClassPagination

    def get_queryset(self):
        # NOTE (fix): this used to unconditionally filter to
        # student=request.user with no way for a classroom's teacher/
        # co-teacher/moderator to ever list purchases on their OWN
        # classroom. The refund() action below has always allowed them to
        # refund a specific purchase, but with no listing path there was no
        # way to discover a purchase's id in the first place — the action
        # existed but was practically unreachable from the app. ?classroom=
        # opts into "every purchase on that classroom" once
        # _can_manage_classroom confirms the caller actually manages it;
        # same opt-in pattern as SessionWaitlistViewSet's ?session= above.
        # No ?classroom= (or failing that check) falls straight through to
        # the original "own purchases only" behaviour.
        base = PassPurchase.objects.select_related("student", "coupon", "class_pass__classroom")
        classroom_id = self.request.query_params.get("classroom")
        if classroom_id:
            classroom = Classroom.objects.filter(pk=classroom_id).first()
            if classroom and _can_manage_classroom(classroom, self.request.user):
                return base.filter(class_pass__classroom_id=classroom_id)
        return base.filter(student=self.request.user)

    @action(detail=True, methods=["post"])
    def refund(self, request, pk=None):
        """Teacher/co-teacher/moderator/staff-only reversal of a single
        purchase — e.g. resolving one student's complaint or a report.
        NOT usable by the student themselves; that's what cancel() below
        is for.

        NOTE (fix): this used to call the old lump-sum _refund_purchase(),
        which refunded the student's FULL coins_spent and clawed that
        same amount back out of the teacher's wallet — including coins
        the teacher had already legitimately earned for days actually
        taught. Now delegates to PassPurchase.reverse(), which only
        refunds remaining_balance (whatever is still sitting in escrow,
        un-earned) and leaves coins_released alone. See reverse()'s
        docstring for why no teacher clawback is needed at all under the
        escrow design. reverse() also queues the refund notification
        itself, so there's no separate create_notification() call here
        any more.

        Deliberately not scoped through get_queryset() (which is "own
        purchases only"): the caller here needs to act on someone ELSE's
        purchase, so the object is fetched directly and the permission
        check is explicit, same pattern as ClassJoinRequestViewSet.accept.
        """
        purchase = get_object_or_404(PassPurchase, pk=pk)
        classroom = purchase.class_pass.classroom
        if not (_can_manage_classroom(classroom, request.user) or request.user.is_staff):
            raise PermissionDenied(
                "Only the classroom's teacher/co-teacher/moderator, or platform staff, can refund this pass."
            )
        if purchase.status != PassPurchase.Status.SUCCESS:
            raise ValidationError(f"This purchase is already {purchase.get_status_display().lower()}.")

        with transaction.atomic():
            purchase = PassPurchase.objects.select_for_update().get(pk=purchase.pk)
            if purchase.status != PassPurchase.Status.SUCCESS:
                raise ValidationError(f"This purchase is already {purchase.get_status_display().lower()}.")
            purchase.reverse()

        return Response(PassPurchaseSerializer(purchase).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Student self-service: cancel your OWN currently-active pass
        whenever you want, without needing the teacher to do anything.

        NOTE (fix): this endpoint didn't exist at all before, even though
        PassPurchase.reverse()'s docstring already documented it as one of
        the two callers ("the student themselves ... a student no longer
        has to just keep paying for a pass they don't want to finish
        using"). Only remaining_balance (whatever's still in escrow, i.e.
        days nobody has taught yet) is refunded — coins already released
        to the teacher for classes actually held stay with the teacher.
        Scoped to the requester's own purchase (student=request.user), the
        same as get_queryset()'s default "own purchases only" — but
        fetched directly here (not via get_queryset()) so a ?classroom=
        query param a teacher might have set for a different call on this
        viewset can never accidentally widen this lookup.
        """
        purchase = get_object_or_404(PassPurchase, pk=pk, student=request.user)
        if purchase.status != PassPurchase.Status.SUCCESS:
            raise ValidationError(f"This purchase is already {purchase.get_status_display().lower()}.")

        with transaction.atomic():
            purchase = PassPurchase.objects.select_for_update().get(pk=purchase.pk)
            if purchase.status != PassPurchase.Status.SUCCESS:
                raise ValidationError(f"This purchase is already {purchase.get_status_display().lower()}.")
            purchase.reverse()

        return Response(PassPurchaseSerializer(purchase).data)


def _charge_and_create_purchase(student, class_pass, coupon_code):
    """Shared coin-debit + PassPurchase-creation logic, used ONLY by
    ClassJoinRequestViewSet.accept() below. Coin-only marketplace: the
    student is charged out of their User.coin balance, and the
    PassPurchase row is created ONLY if they have enough coins for the
    (post-coupon) price — if not, we raise and nothing is written, so a
    pass can never exist without the coins actually being debited first.

    payment_method is NOT accepted from the client — there is no card/UPI
    path in this app. It's decided from the final price: 0 -> FREE (no
    coin debit), otherwise -> COIN_WALLET (mandatory coin debit).

    Must be called from inside a transaction.atomic() block by the caller,
    with row locks on the coupon (max_uses check) and the student's wallet
    (coin balance) — without this, two concurrent accepts on two different
    pending requests for the same coupon/student could both pass the "is
    coupon still valid" / "is balance sufficient" checks before either
    write lands, over-redeeming a limited coupon or letting a wallet
    balance go negative.
    """
    coupon = None
    if coupon_code:
        coupon = Coupon.objects.select_for_update().filter(code__iexact=coupon_code).first()
        if not coupon or not coupon.is_valid():
            raise ValidationError({"coupon_code": "Invalid or expired coupon."})
        # NOTE (fix): coupon codes are globally unique, but nothing tied a
        # coupon to the classroom/teacher it was actually meant for — a
        # coupon created by Teacher A (for their own classroom, or scoped
        # to one specific classroom of theirs) could be redeemed against
        # ANY other teacher's pass, discounting (or zeroing out) revenue
        # they never agreed to give away. A coupon is only usable against
        # the classroom it's scoped to (coupon.classroom), or — if
        # classroom is null ("usable across all of this teacher's
        # classrooms") — against any classroom created_by actually teaches.
        classroom = class_pass.classroom
        scoped_to_this_classroom = coupon.classroom_id == classroom.id
        scoped_to_creators_other_classroom = coupon.classroom_id is None and coupon.created_by_id == classroom.teacher_id
        if not (scoped_to_this_classroom or scoped_to_creators_other_classroom):
            raise ValidationError({"coupon_code": "This coupon isn't valid for this classroom."})

    price = class_pass.price
    if coupon:
        if coupon.discount_percent:
            price -= price * coupon.discount_percent / 100
        if coupon.discount_amount:
            price -= coupon.discount_amount
        price = max(price, 0)

    # NOTE (fix — systematic underpayment): plain int(price) always
    # TRUNCATES toward zero, so any coupon discount that lands on a
    # fractional coin amount (e.g. 20% off a 49-coin pass = 39.2) always
    # rounded in the STUDENT's favor (39, never 39.2 -> 39 is fine, but
    # 49.6 would truncate to 49 instead of the correct 50) — a small but
    # completely predictable and exploitable leak against the teacher on
    # every single discounted purchase. Round to nearest instead (banker's-
    # rounding-safe via Decimal quantize) so neither side is systematically
    # favored; ROUND_HALF_UP matches how a price is normally displayed.
    coins_spent = int(price.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    if coins_spent > 0:
        # Coins are the ONLY way to pay -> lock the student's row, verify
        # balance, and refuse to create the pass at all if it's short. The
        # request stays PENDING so the student can top up and the teacher
        # can retry accept() later — a failed accept is not a rejection.
        user = type(student).objects.select_for_update().get(pk=student.pk)
        if user.coin < coins_spent:
            raise ValidationError({"coin": "Student has insufficient coin balance to accept this request."})
        user.coin -= coins_spent
        user.save(update_fields=["coin"])
        CoinTransaction.objects.create(
            user=user,
            txn_type=CoinTransaction.TxnType.DEBIT,
            reason=CoinTransaction.Reason.PASS_PURCHASE,
            amount=coins_spent,
            balance_after=user.coin,
            reference_id=f"class_pass:{class_pass.id}",
        )

        # NOTE (fix — "pay only for classes actually held"): this used to
        # credit the teacher the full coins_spent right here, the moment
        # accept() ran — before a single class of this pass's validity
        # window had even happened. That's exactly the loss case a
        # student hits when a teacher stops teaching mid-pass: the money
        # for every un-taught day was already gone from escrow and sitting
        # in the teacher's wallet, so closing/refunding the pass could
        # only claw it back if the teacher hadn't already spent/withdrawn
        # it (see the clamp that used to live in _refund_purchase).
        #
        # The coins still leave the student's wallet here (unchanged —
        # that's the escrow debit, and the balance/coupon checks above
        # already ran once), but they are NOT paid to the teacher yet.
        # They sit in escrow on this PassPurchase and are released to the
        # teacher one calendar day at a time, only for a day this
        # classroom actually held a class — see
        # PassPurchase.charge_for_session(), fired by the post_save
        # signal on ClassSession in models.py. per_day_rate is the
        # snapshot this purchase releases against; frozen here so a later
        # price edit on the ClassPass can never retroactively change what
        # an already-purchased pass charges per day.
        payment_method = PassPurchase.PaymentMethod.COIN_WALLET
    else:
        payment_method = PassPurchase.PaymentMethod.FREE

    purchase = PassPurchase.objects.create(
        student=student,
        class_pass=class_pass,
        coupon=coupon,
        payment_method=payment_method,
        # NOTE (fix): record what was actually debited (coins_spent), not
        # the pre-rounding fractional price — otherwise amount_paid and
        # coins_spent could disagree on this exact row (e.g. 49.6 vs 50),
        # which is exactly the kind of mismatch a dispute/audit trail must
        # not have.
        amount_paid=Decimal(coins_spent),
        coins_spent=coins_spent,
        # Escrow release rate — see the NOTE above payment_method for why
        # the teacher is no longer paid coins_spent upfront. Decimal
        # division here (not int) is deliberate: charge_for_session()
        # rounds each day's charge itself, and hands the final day
        # whatever fractional remainder is left rather than stranding it.
        per_day_rate=Decimal(coins_spent) / class_pass.validity_days,
        status=PassPurchase.Status.SUCCESS,
        expires_at=timezone.now() + timezone.timedelta(days=class_pass.validity_days),
    )
    if coupon:
        Coupon.objects.filter(pk=coupon.pk).update(used_count=F("used_count") + 1)

    return purchase


# NOTE (fix): the old module-level _refund_purchase() (lump-sum refund +
# clawback out of the teacher's wallet) used to live here. It's gone now —
# PassPurchase.reverse() in models.py replaces it entirely under the
# per-day escrow design (refunds only remaining_balance, no teacher
# clawback, notification queued from inside the model). Both call sites
# (ClassroomViewSet.close() and PassPurchaseViewSet.refund(), plus the new
# PassPurchaseViewSet.cancel()) now call purchase.reverse() directly.


# ---------------------------------------------------------------------------
# 5B. CLASS JOIN REQUEST (the only door in — see ClassJoinRequest's
#     docstring in models.py. A student never buys a pass directly: they
#     raise a request here, and the classroom's teacher/co-teacher/
#     moderator accepts or rejects it. Coins are charged, and the
#     PassPurchase that actually grants access is created, ONLY inside
#     accept() below.)
# ---------------------------------------------------------------------------
class ClassJoinRequestViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = ClassJoinRequestSerializer
    permission_classes = [IsAuthenticated]
    # NOTE (fix): a popular classroom's join-request inbox grows unbounded
    # over the classroom's lifetime (every request ever raised against it,
    # accepted/rejected/cancelled included) — same class of gap as the
    # session/purchase/chat/ledger lists above.
    pagination_class = LiveClassPagination
    # NOTE (perf): student/classroom_title/class_pass_title/class_pass_price
    # are all nested or dotted-source fields on every row.
    queryset = ClassJoinRequest.objects.select_related("student", "classroom", "class_pass")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user

        # NOTE (fix): this filtering is only correct for the LIST action.
        # It used to run unconditionally, which meant get_object() (used by
        # retrieve/accept/reject/cancel) filtered through it too. Detail
        # calls like POST join-requests/{id}/accept/ don't carry a
        # ?classroom= query param, so classroom_id was always None on those
        # calls, and every request fell through to `qs.filter(student=user)`
        # — which is never true for the teacher accepting/rejecting someone
        # ELSE's request. Result: accept()/reject() 404'd ("not found") on
        # requests that genuinely existed and that the teacher/co-teacher/
        # moderator had every right to act on, even though the explicit
        # _can_manage_classroom() permission check further down in accept()/
        # reject() would have allowed it. Scoping this filter to `list` only
        # fixes that; detail actions now get the full queryset and rely on
        # the explicit permission checks (added to retrieve() below, and
        # already present in accept()/reject()/cancel()) instead.
        if self.action != "list":
            return qs

        classroom_id = self.request.query_params.get("classroom")

        if classroom_id:
            qs = qs.filter(classroom_id=classroom_id)
            classroom = Classroom.objects.filter(pk=classroom_id).first()
            # Teacher/co-teacher/moderator reviewing their own classroom's
            # inbox sees every request raised against it; anyone else only
            # ever sees the ones they personally raised.
            if classroom and _can_manage_classroom(classroom, user):
                status_filter = self.request.query_params.get("status")
                return qs.filter(status=status_filter) if status_filter else qs
            return qs.filter(student=user)

        # No ?classroom= filter -> "my requests" across every classroom.
        return qs.filter(student=user)

    def retrieve(self, request, *args, **kwargs):
        # NOTE (fix): get_queryset() no longer restricts detail lookups (see
        # above), so this view is now the only thing standing between "any
        # authenticated user" and "any join request by id" — it previously
        # relied entirely on the list-style queryset filtering for that,
        # which is exactly what broke accept()/reject(). Explicit check here:
        # the student who raised it, or a manager of that classroom, only.
        instance = self.get_object()
        user = request.user
        if instance.student_id != user.id and not _can_manage_classroom(instance.classroom, user):
            raise PermissionDenied("You don't have permission to view this join request.")
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        class_pass = serializer.validated_data["class_pass"]
        user = self.request.user

        # NOTE (fix): neither classroom.is_active nor class_pass.is_active
        # was checked here — a student could raise (and a teacher could
        # still accept/charge for) a join request against a deactivated
        # classroom or a pass the teacher had paused/retired.
        if not classroom.is_active:
            raise ValidationError("This classroom is no longer active.")
        if not class_pass.is_active:
            raise ValidationError("This pass is no longer available.")
        if classroom.teacher_id == user.id:
            raise ValidationError("You can't raise a join request against your own classroom.")
        # NOTE (fix — ClassroomBan had no teeth in the join-request path):
        # without this a banned student could just raise a fresh join
        # request against a NEW pass immediately after being banned — the
        # ban only ever meant "your current access is revoked", not
        # "you can never come back".
        if classroom.bans.filter(student_id=user.id).exists():
            raise ValidationError("You've been banned from this classroom by the teacher.")
        if classroom.has_access(user):
            raise ValidationError("You already have access to this classroom.")
        if ClassJoinRequest.objects.filter(
            classroom=classroom, student=user, status=ClassJoinRequest.Status.PENDING
        ).exists():
            raise ValidationError("You already have a pending request for this classroom.")

        # NOTE (fix — race): the exists() check above and serializer.save()
        # below aren't atomic against each other — two identical "raise a
        # join request" calls fired at the same instant (double-tap on a
        # slow connection, a retried request) could both pass this check
        # before either insert lands, and the second one would then hit the
        # DB's own one-pending-per-classroom-per-student unique constraint
        # as a raw IntegrityError -> unhandled 500 instead of the same
        # clean "you already have a pending request" 400 the first caller
        # would have gotten. Caught and converted here.
        try:
            join_request = serializer.save(student=user)
        except IntegrityError:
            raise ValidationError("You already have a pending request for this classroom.")

        create_notification(
            recipient=classroom.teacher,
            notif_type=Notification.NotifType.JOIN_REQUEST_RECEIVED,
            title="New join request",
            message=f"{user.get_full_name() or user.username} wants to join '{classroom.title}'.",
            classroom=classroom,
        )
        # NOTE (fix): in-app row only got created above — the teacher got no
        # push if the app wasn't open. Queued so a slow/failing push provider
        # never adds latency here (same reasoning as every other
        # notify_*.delay() call site in this file).
        from .tasks import notify_join_request_received

        _safe_delay(notify_join_request_received, join_request.id)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        """Teacher/co-teacher/moderator only. Charges the student's coin
        wallet (if the pass isn't free) and creates the PassPurchase that
        actually grants access — nothing is charged and nobody becomes a
        participant until this runs."""
        join_request = self.get_object()
        if not _can_manage_classroom(join_request.classroom, request.user):
            raise PermissionDenied(
                "Only the classroom's teacher, co-teacher, or moderator can accept a join request."
            )
        if join_request.status != ClassJoinRequest.Status.PENDING:
            raise ValidationError(f"This request is already {join_request.get_status_display().lower()}.")

        decision = ClassJoinRequestDecisionSerializer(data=request.data)
        decision.is_valid(raise_exception=True)
        note = decision.validated_data.get("note", "")

        with transaction.atomic():
            join_request = ClassJoinRequest.objects.select_for_update().get(pk=join_request.pk)
            if join_request.status != ClassJoinRequest.Status.PENDING:
                raise ValidationError(f"This request is already {join_request.get_status_display().lower()}.")

            # NOTE (fix): class_pass itself wasn't locked — only the join
            # request and (inside _charge_and_create_purchase) the coupon
            # and wallets were. That left a real window for a teacher's
            # ClassPassViewSet.perform_update/perform_destroy to race a
            # student's accept() here: e.g. the pass gets edited (or
            # deactivated) in between this view reading join_request's
            # class_pass and the price actually being charged, so the
            # student could be charged a stale price, or a purchase could
            # land against a pass mid-edit. Row-locking it here means
            # ClassPassViewSet's own select_for_update (see that view) waits
            # for this transaction to commit before it can decide whether
            # "active purchases" exist, and vice versa — the two paths can
            # no longer interleave.
            class_pass = ClassPass.objects.select_for_update().get(pk=join_request.class_pass_id)
            if not class_pass.is_active:
                raise ValidationError("This pass is no longer available.")

            # NOTE (fix — charge-after-close fraud): perform_create() checks
            # classroom.is_active when the REQUEST is first raised, but
            # accept() itself never re-checked it. A teacher could call
            # classrooms/{id}/close/ (refunds every current student, marks
            # the classroom inactive — the supported "I'm done running
            # this" path) and then still accept() any join requests left
            # pending from before the close, charging a brand-new student
            # real coins for a classroom the teacher has already declared
            # they've stopped running.
            #
            # Row-locked the same way class_pass/coupon/wallets already are
            # in this function (see the earlier NOTE (fix, race) above) so
            # close()'s is_active write can't land in the gap between this
            # read and the charge actually committing — whichever of
            # close()/accept() gets here first now makes the other wait,
            # instead of both racing off a stale "still active" snapshot.
            classroom = Classroom.objects.select_for_update().get(pk=join_request.classroom_id)
            if not classroom.is_active or classroom.is_deleted:
                raise ValidationError("This classroom is no longer active — the request can't be accepted.")

            purchase = _charge_and_create_purchase(
                student=join_request.student,
                class_pass=class_pass,
                coupon_code=join_request.coupon_code,
            )
            join_request.status = ClassJoinRequest.Status.ACCEPTED
            join_request.pass_purchase = purchase
            join_request.decided_by = request.user
            join_request.decision_note = note
            join_request.decided_at = timezone.now()
            join_request.save(
                update_fields=["status", "pass_purchase", "decided_by", "decision_note", "decided_at"]
            )

        create_notification(
            recipient=join_request.student,
            notif_type=Notification.NotifType.JOIN_REQUEST_ACCEPTED,
            title="Join request accepted",
            message=f"You now have access to '{join_request.classroom.title}'.",
            classroom=join_request.classroom,
        )
        # NOTE (fix): student got no push telling them access just landed —
        # queued the same way every other notify_*.delay() call site in this
        # file is.
        from .tasks import notify_join_request_accepted

        _safe_delay(notify_join_request_accepted, join_request.id)
        return Response(ClassJoinRequestSerializer(join_request).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """Teacher/co-teacher/moderator only. No charge, no PassPurchase."""
        join_request = self.get_object()
        if not _can_manage_classroom(join_request.classroom, request.user):
            raise PermissionDenied(
                "Only the classroom's teacher, co-teacher, or moderator can reject a join request."
            )
        if join_request.status != ClassJoinRequest.Status.PENDING:
            raise ValidationError(f"This request is already {join_request.get_status_display().lower()}.")

        decision = ClassJoinRequestDecisionSerializer(data=request.data)
        decision.is_valid(raise_exception=True)

        join_request.status = ClassJoinRequest.Status.REJECTED
        join_request.decided_by = request.user
        join_request.decision_note = decision.validated_data.get("note", "")
        join_request.decided_at = timezone.now()
        join_request.save(update_fields=["status", "decided_by", "decision_note", "decided_at"])

        create_notification(
            recipient=join_request.student,
            notif_type=Notification.NotifType.JOIN_REQUEST_REJECTED,
            title="Join request rejected",
            message=f"Your request to join '{join_request.classroom.title}' was rejected.",
            classroom=join_request.classroom,
        )
        from .tasks import notify_join_request_rejected

        _safe_delay(notify_join_request_rejected, join_request.id)
        return Response(ClassJoinRequestSerializer(join_request).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """The requesting student can withdraw their own still-pending
        request (e.g. changed their mind, or want to re-request against a
        different pass — cancelling frees up the one-pending-per-classroom
        slot for a fresh request)."""
        join_request = self.get_object()
        if join_request.student_id != request.user.id:
            raise PermissionDenied("You can only cancel your own join request.")
        if join_request.status != ClassJoinRequest.Status.PENDING:
            raise ValidationError(f"This request is already {join_request.get_status_display().lower()}.")

        join_request.status = ClassJoinRequest.Status.CANCELLED
        join_request.decided_at = timezone.now()
        join_request.save(update_fields=["status", "decided_at"])

        return Response(ClassJoinRequestSerializer(join_request).data)


# ---------------------------------------------------------------------------
# 6. SESSION PARTICIPANT
# ---------------------------------------------------------------------------
class SessionParticipantViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """NOTE (fix): ?session=<id> used to return EVERY participant row for
    that session (i.e. the full attendance list, including who joined and
    when) to ANY authenticated user — not just the classroom's teacher/
    staff. Now gated behind _can_view_classroom_internals, same as
    ClassSessionViewSet; anyone else, filtered or not, only ever sees
    their own participation rows."""

    serializer_class = SessionParticipantSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = LiveClassPagination

    def get_queryset(self):
        qs = SessionParticipant.objects.select_related("user")
        session_id = self.request.query_params.get("session")
        user = self.request.user
        if session_id:
            session = ClassSession.objects.filter(pk=session_id).select_related("classroom").first()
            if session and _can_view_classroom_internals(session.classroom, user):
                return qs.filter(session_id=session_id)
            return qs.filter(session_id=session_id, user=user)
        return qs.filter(user=user)

    @action(detail=True, methods=["post"])
    def leave(self, request, pk=None):
        participant = self.get_object()
        if participant.user_id != request.user.id:
            raise PermissionDenied("You can only end your own participation.")
        participant.left_at = timezone.now()
        participant.save(update_fields=["left_at"])
        # NOTE (fix): see _try_promote_from_waitlist's docstring (views.py,
        # right after _perform_join) — a seat freeing up here previously had
        # no path to the waitlist at all; a student had to wait for the
        # host to manually notice and hit /promote/.
        _try_promote_from_waitlist(participant.session)
        return Response({"left_at": participant.left_at})


# ---------------------------------------------------------------------------
# 7. CLASS MATERIAL
# ---------------------------------------------------------------------------
class ClassMaterialViewSet(viewsets.ModelViewSet):
    """NOTE (fix): this previously had two gaps —
    1. perform_create only stamped uploaded_by; ANY authenticated user
       could upload a material row onto ANY classroom, not just its
       teacher/co-teacher/moderator. update/destroy had no checks either,
       so anyone could edit or delete anyone else's materials.
    2. get_queryset never restricted materials to people who actually have
       access to the classroom (has_access — a valid pass, or manage
       rights) — every signed-in user could read (and download the file
       for) ANY classroom's paid materials, whether or not they'd bought a
       pass, and listing with no ?classroom= leaked every material on the
       whole platform in one call.
    Both are fixed below, matching the has_access() gating already used by
    ClassQueryViewSet/ClassroomReviewViewSet for other paid content."""

    serializer_class = ClassMaterialSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = LiveClassPagination
    queryset = ClassMaterial.objects.all()

    def get_queryset(self):
        classroom_id = self.request.query_params.get("classroom")
        if not classroom_id:
            return ClassMaterial.objects.none()
        qs = ClassMaterial.objects.filter(classroom_id=classroom_id).select_related("uploaded_by")
        classroom = Classroom.objects.filter(pk=classroom_id).first()
        if not classroom or not _can_view_classroom_internals(classroom, self.request.user):
            return ClassMaterial.objects.none()
        return qs

    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        if not _can_manage_classroom(classroom, self.request.user):
            raise PermissionDenied(
                "Only the classroom's teacher, co-teacher, or moderator can upload materials."
            )
        serializer.save(uploaded_by=self.request.user)

    def perform_update(self, serializer):
        if not _can_manage_classroom(serializer.instance.classroom, self.request.user):
            raise PermissionDenied(
                "Only the classroom's teacher, co-teacher, or moderator can edit this material."
            )
        serializer.save()

    def perform_destroy(self, instance):
        if not _can_manage_classroom(instance.classroom, self.request.user):
            raise PermissionDenied(
                "Only the classroom's teacher, co-teacher, or moderator can remove this material."
            )
        instance.delete()


# ---------------------------------------------------------------------------
# 8. CHAT MESSAGE
# ---------------------------------------------------------------------------
class ChatMessageViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    """NOTE (fix): ?session=<id> previously returned EVERY chat message for
    that session to ANY authenticated user, and create() let anyone post a
    message into any session's chat — no check that the caller actually
    holds a valid pass for (or manages) that session's classroom. Both are
    now gated behind _has_room_access (teacher/org-staff bypass, or a
    currently valid pass) — same boundary used to gate joining the room
    itself in ClassSessionViewSet.join()."""

    serializer_class = ChatMessageSerializer
    permission_classes = [IsAuthenticated]
    # NOTE (production): a long-running weekly classroom accumulates chat
    # history indefinitely — with no pagination this endpoint would return
    # every message ever sent in one response. Chronological ordering (see
    # ChatMessage.Meta) plus paging keeps a mobile client from having to
    # download months of chat just to open a session.
    pagination_class = LiveClassPagination

    # NOTE (fix — spam/flood protection): posting a chat message had no
    # rate limit at all, unlike every other write action in this file
    # (session join/token, coupon validate). A single participant could
    # flood a live session's chat as fast as the network allows — annoying
    # at best, a denial-of-service on that session's chat for everyone else
    # at worst. Scoped (not global) so it only throttles the create()
    # action, never list(). Add e.g. {"chat_message_create": "20/min"} to
    # DRF's DEFAULT_THROTTLE_RATES in settings.py for this to take effect —
    # same pattern as session_join/session_token/coupon_validate below.
    def get_throttles(self):
        if self.action == "create":
            self.throttle_scope = "chat_message_create"
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def _session_or_none(self, session_id):
        return ClassSession.objects.filter(pk=session_id).select_related("classroom").first()

    def get_queryset(self):
        qs = ChatMessage.objects.filter(is_deleted=False).select_related("sender")
        session_id = self.request.query_params.get("session")
        if not session_id:
            return qs.none()
        session = self._session_or_none(session_id)
        if not session or not _has_room_access(session.classroom, self.request.user):
            return qs.none()
        return qs.filter(session_id=session_id)

    def perform_create(self, serializer):
        session = serializer.validated_data.get("session")
        if not session or not _has_room_access(session.classroom, self.request.user):
            raise PermissionDenied("A valid pass is required to chat in this session.")
        serializer.save(sender=self.request.user)

    def perform_destroy(self, instance):
        # Sender can remove their own message; a host can moderate anyone's.
        if instance.sender_id != self.request.user.id and not _can_moderate_session(
            instance.session, self.request.user
        ):
            raise PermissionDenied("You can only delete your own messages.")
        instance.is_deleted = True  # soft delete
        instance.save(update_fields=["is_deleted"])


# ---------------------------------------------------------------------------
# 9. LIVE POLL + RESPONSE
# ---------------------------------------------------------------------------
class LivePollViewSet(viewsets.ModelViewSet):
    """NOTE (fix): several gaps here previously —
    1. ?session=<id> returned every poll (question/options/result_counts)
       for that session to ANY authenticated user, and no filter at all
       returned EVERY poll on the whole platform. List is now gated
       behind _has_room_access, same boundary as ChatMessageViewSet.
    2. perform_create let any signed-in user create a poll on any
       session — now restricted to the session's teacher/co-teacher/
       moderator via _can_moderate_session.
    3. retrieve/update/destroy had no object-level access check at all
       (any authenticated user could GET/PATCH/DELETE any poll by id) —
       get_object() below now enforces the same rule as list, and
       perform_update/perform_destroy are host-only.
    4. vote()/close() look the poll up directly rather than through
       get_object(), since get_queryset() below only applies the
       ?session= filter for the 'list' action — using it for detail
       routes too would 404 every vote/close call (no ?session= query
       param is sent on those POSTs).
    """

    serializer_class = LivePollSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = LiveClassPagination
    # NOTE (perf): get_result_counts() below reads obj.responses per poll —
    # prefetch_related turns that from one query per poll into one query
    # total for the whole list.
    queryset = LivePoll.objects.prefetch_related("responses")

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action != "list":
            # Detail routes (retrieve/update/destroy) authorize via
            # get_object()/perform_* below instead of this queryset.
            return qs
        session_id = self.request.query_params.get("session")
        if not session_id:
            return qs.none()
        session = ClassSession.objects.filter(pk=session_id).select_related("classroom").first()
        if not session or not _has_room_access(session.classroom, self.request.user):
            return qs.none()
        return qs.filter(session_id=session_id)

    def get_object(self):
        obj = super().get_object()
        if self.action == "retrieve" and not _has_room_access(obj.session.classroom, self.request.user):
            raise PermissionDenied("A valid pass is required to view this poll.")
        return obj

    def perform_create(self, serializer):
        session = serializer.validated_data["session"]
        if not _can_moderate_session(session, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can start a poll.")
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        if not _can_moderate_session(serializer.instance.session, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can edit this poll.")
        serializer.save()

    def perform_destroy(self, instance):
        if not _can_moderate_session(instance.session, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can delete this poll.")
        instance.delete()

    @action(detail=True, methods=["post"])
    def vote(self, request, pk=None):
        poll = LivePoll.objects.select_related("session__classroom").filter(pk=pk).first()
        if poll is None:
            raise NotFound("Poll not found.")
        if not _has_room_access(poll.session.classroom, request.user):
            raise PermissionDenied("A valid pass is required to vote in this session.")
        if not poll.is_active:
            raise ValidationError("This poll is closed.")

        idx = request.data.get("selected_option_index")
        serializer = PollResponseSerializer(data={"poll": poll.id, "selected_option_index": idx})
        serializer.is_valid(raise_exception=True)

        # NOTE (fix): PollResponse has unique_together = ("poll", "student"),
        # but `student` is read-only on this serializer (set below, not from
        # the client), so DRF's automatic UniqueTogetherValidator never
        # fires. A student changing their answer (a second vote/() call on
        # the same poll) previously hit an unhandled IntegrityError -> 500
        # instead of either being rejected cleanly or, more usefully,
        # letting them change their vote. Upsert instead: create on first
        # vote, update the choice on any later call.
        response, _created = PollResponse.objects.update_or_create(
            poll=poll,
            student=request.user,
            defaults={"selected_option_index": serializer.validated_data["selected_option_index"]},
        )
        return Response(PollResponseSerializer(response).data, status=201)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        # Looked up directly (not via get_queryset/get_object), same as
        # vote() above — get_queryset only accepts ?session= for the list
        # route; detail actions authorize explicitly instead.
        #
        # NOTE (fix): this used to only allow the poll's own `created_by`
        # to close it — inconsistent with perform_update/perform_destroy
        # right above, which correctly use _can_moderate_session (any
        # teacher/co-teacher/moderator on that session). As shipped, if a
        # co-teacher started a poll, the classroom's own TEACHER could not
        # close it themselves — a moderation gap where the most senior
        # person in the room was locked out of an action anyone with
        # host-tier access should be able to take. Aligned with the same
        # host-tier check used everywhere else in this viewset.
        poll = LivePoll.objects.select_related("session__classroom").filter(pk=pk).first()
        if poll is None:
            raise NotFound("Poll not found.")
        if not _can_moderate_session(poll.session, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can close this poll.")
        poll.is_active = False
        poll.closed_at = timezone.now()
        poll.save(update_fields=["is_active", "closed_at"])
        return Response(LivePollSerializer(poll).data)


# ---------------------------------------------------------------------------
# 10. ASSIGNMENT + SUBMISSION
# ---------------------------------------------------------------------------
class AssignmentViewSet(viewsets.ModelViewSet):
    """NOTE (fix): this previously had no perform_create/update/destroy
    checks at all — any authenticated user (not just the classroom's
    teacher/co-teacher/moderator) could create, edit, or delete an
    assignment on ANY classroom. Locked down to _can_manage_classroom,
    matching the pattern used by ClassHoliday/Notice/ClassQuery.

    NOTE (fix): get_queryset() also had no access-control gate at all —
    unlike every sibling "classroom internals" endpoint (materials,
    schedules, sessions, notices, holidays), a bare GET /assignments/ with
    no ?classroom= returned EVERY assignment on the whole platform, and
    ?classroom=<id> returned that classroom's assignments (titles, due
    dates, attachment files) to any signed-in user, pass or no pass. Now
    gated behind _can_view_classroom_internals, same pattern as
    ClassScheduleViewSet/ClassSessionViewSet: an explicit ?classroom=
    filter is checked against that one classroom, and a bare list (or a
    detail lookup by id, which never carries a ?classroom= param) is scoped
    to every classroom the caller can already see internals for."""

    serializer_class = AssignmentSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = LiveClassPagination
    queryset = Assignment.objects.all()

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
        if not _can_manage_classroom(classroom, self.request.user):
            raise PermissionDenied(
                "Only the classroom's teacher, co-teacher, or moderator can create an assignment."
            )
        assignment = serializer.save()
        # NOTE (fix): posting an assignment previously produced no
        # notification at all — students found out only by happening to
        # open the Assignments tab, same class of silent-fan-out gap
        # NoticeViewSet already fixed for urgent notices. bulk_create keeps
        # the in-app fan-out to one INSERT regardless of classroom size.
        student_ids = list(
            PassPurchase.objects.filter(
                class_pass__classroom=classroom,
                status=PassPurchase.Status.SUCCESS,
                is_active=True,
                expires_at__gt=timezone.now(),
            ).values_list("student_id", flat=True).distinct()
        )
        create_bulk_notifications(
            recipients=student_ids,
            notif_type=Notification.NotifType.ASSIGNMENT_POSTED,
            title="New assignment posted",
            message=f"'{assignment.title}' was posted in '{classroom.title}'.",
            classroom=classroom,
        )
        from .tasks import notify_assignment_posted

        _safe_delay(notify_assignment_posted, assignment.id, student_ids)

    def perform_update(self, serializer):
        if not _can_manage_classroom(serializer.instance.classroom, self.request.user):
            raise PermissionDenied(
                "Only the classroom's teacher, co-teacher, or moderator can edit this assignment."
            )
        serializer.save()

    def perform_destroy(self, instance):
        if not _can_manage_classroom(instance.classroom, self.request.user):
            raise PermissionDenied(
                "Only the classroom's teacher, co-teacher, or moderator can delete this assignment."
            )
        instance.delete()


class AssignmentSubmissionViewSet(viewsets.ModelViewSet):
    serializer_class = AssignmentSubmissionSerializer
    permission_classes = [IsAuthenticated]
    # NOTE (fix): a teacher's ?assignment= view returns every student's
    # submission for that assignment — for a large batch this is unbounded
    # in the same way the lists above are.
    pagination_class = LiveClassPagination

    def get_queryset(self):
        # NOTE (perf): student is nested (UserMiniSerializer) on every row.
        qs = AssignmentSubmission.objects.select_related("student", "assignment__classroom")
        assignment_id = self.request.query_params.get("assignment")
        if assignment_id:
            qs = qs.filter(assignment_id=assignment_id)

        # A ?assignment= filter used to return EVERY submission for that
        # assignment to whoever asked — any signed-in student could read
        # classmates' submissions/grades just by passing the id. Now: only
        # the classroom's teacher/co-teacher/moderator (org staff included,
        # via _can_manage_classroom) gets the full list; everyone else,
        # filtered or not, only ever sees their own.
        user = self.request.user
        is_manager = False
        if assignment_id:
            assignment = Assignment.objects.filter(pk=assignment_id).select_related("classroom").first()
            is_manager = bool(assignment) and _can_manage_classroom(assignment.classroom, user)
        if not is_manager:
            qs = qs.filter(student=user)
        return qs

    def perform_create(self, serializer):
        # NOTE (fix): no check that the submitter actually holds access to
        # the assignment's classroom — anyone signed in could submit
        # (and, since (assignment, student) is unique_together, squat on)
        # any assignment across the platform, pass or no pass.
        assignment = serializer.validated_data["assignment"]
        user = self.request.user
        if not _can_view_classroom_internals(assignment.classroom, user):
            raise PermissionDenied("A pass (active or expired) is required to submit this assignment.")
        # NOTE (fix): same class of bug as ClassroomReview above —
        # AssignmentSubmission has unique_together = ("assignment",
        # "student") but `student` is read-only here, so the automatic
        # UniqueTogetherValidator never applies and a resubmission attempt
        # raised an unhandled IntegrityError -> 500 instead of a clear
        # error telling the student to update their existing submission.
        if AssignmentSubmission.objects.filter(assignment=assignment, student=user).exists():
            raise ValidationError(
                "You've already submitted this assignment — update your existing submission instead."
            )
        submission = serializer.save(student=user)
        # NOTE (fix): the teacher-facing counterpart to ASSIGNMENT_GRADED
        # below didn't exist — a submission coming in produced no signal at
        # all for the teacher, who'd only find out by manually reopening
        # the grading queue for every assignment they'd ever posted.
        create_notification(
            recipient=assignment.classroom.teacher,
            notif_type=Notification.NotifType.SUBMISSION_RECEIVED,
            title="New submission to grade",
            message=(
                f"{user.get_full_name() or user.username} submitted "
                f"'{assignment.title}' in '{assignment.classroom.title}'."
            ),
            classroom=assignment.classroom,
        )
        from .tasks import notify_submission_received

        _safe_delay(notify_submission_received, submission.id)

    # NOTE (fix — grading integrity): AssignmentSubmissionViewSet is a plain
    # ModelViewSet with no perform_update/perform_destroy override, so once
    # a submission existed ANY of these were silently possible:
    #   - the submitting student could swap out their file (or the "score"/
    #     "feedback" fields, which are only meant to be teacher-writable via
    #     grade()) AFTER being graded — grade the easy version, quietly
    #     replace it with someone else's work, keep the mark;
    #   - the submitting student could DELETE a low-scoring submission
    #     outright to force a "resubmission" and game the due-date/is_late
    #     check on a second attempt;
    #   - a classroom manager (teacher/co-teacher/moderator), who get the
    #     full queryset for grading purposes, could edit/delete a student's
    #     submission with no ownership check at all.
    # Locked down: only the submitting student may edit/delete their own
    # row, and never once graded_at is set — a graded submission is frozen,
    # exactly like a paid PassPurchase or an accepted join request.
    def perform_update(self, serializer):
        instance = serializer.instance
        if instance.student_id != self.request.user.id:
            raise PermissionDenied("You can only edit your own submission.")
        if instance.graded_at:
            raise ValidationError("This submission has already been graded and can no longer be edited.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.student_id != self.request.user.id:
            raise PermissionDenied("You can only delete your own submission.")
        if instance.graded_at:
            raise ValidationError("This submission has already been graded and can no longer be deleted.")
        instance.delete()

    @action(detail=True, methods=["post"])
    def grade(self, request, pk=None):
        submission = self.get_object()
        if submission.assignment.classroom.teacher_id != request.user.id:
            raise PermissionDenied("Only the classroom's teacher can grade submissions.")
        serializer = AssignmentGradeSerializer(submission, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(graded_at=timezone.now())
        create_notification(
            recipient=submission.student,
            notif_type=Notification.NotifType.ASSIGNMENT_GRADED,
            title="Assignment graded",
            message=f"'{submission.assignment.title}' was graded — score: {submission.score}.",
            classroom=submission.assignment.classroom,
        )
        # NOTE (fix): grading previously only produced a silent bell-icon row.
        from .tasks import notify_assignment_graded

        _safe_delay(notify_assignment_graded, submission.id)
        return Response(AssignmentSubmissionSerializer(submission).data)


# ---------------------------------------------------------------------------
# 11. CLASSROOM REVIEW
# ---------------------------------------------------------------------------
class ClassroomReviewViewSet(viewsets.ModelViewSet):
    serializer_class = ClassroomReviewSerializer
    permission_classes = [IsAuthenticated]
    # NOTE (fix): a popular classroom accumulates one review per student
    # ever enrolled — unbounded over time, same class of gap as above.
    pagination_class = LiveClassPagination
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
        from .tasks import notify_review_posted

        _safe_delay(notify_review_posted, review.id)


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
    pagination_class = LiveClassPagination

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
# 12. COUPON
# ---------------------------------------------------------------------------
class CouponViewSet(viewsets.ModelViewSet):
    """NOTE (fix): this was wide open —
    1. No get_queryset override at all: GET /coupons/ returned EVERY
       coupon on the whole platform (code, discount, classroom, created_by)
       to any signed-in user — competitors could enumerate each other's
       discount codes.
    2. perform_create only stamped created_by; there was no check that the
       caller actually owns the target classroom (or is null/'usable
       across all of my classrooms' for their own account only) — anyone
       could create a coupon against someone else's classroom.
    3. There were no perform_update/perform_destroy checks whatsoever — any
       authenticated user could edit ANY coupon (e.g. set discount_percent
       to 100, making every pass on that classroom free) or delete anyone
       else's coupons. This is a direct financial exploit, fixed by
       restricting every write to the coupon's own creator.
    List is now scoped to "coupons I created" (optionally narrowed by
    ?classroom=<id>, still only within the caller's own coupons) — a
    classroom's *listing* page doesn't need to show live coupon codes
    (that's what /coupons/validate/ is for); only the teacher managing
    their own promos does.
    """

    serializer_class = CouponSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = LiveClassPagination
    # NOTE (fix): same as ClassSessionViewSet above — the `validate` action
    # below passes throttle_scope="coupon_validate" as an @action kwarg,
    # which DRF's router validates against `hasattr(cls, key)` at
    # url-import time. Without this base attribute the whole app fails to
    # boot. Value is irrelevant — the action overrides it per-request.
    throttle_scope = None
    # NOTE (fix — N+1 query): CouponSerializer nests `created_by` as a full
    # UserMiniSerializer (read_only), not a plain FK id — without
    # select_related, DRF fires one extra user lookup per coupon row while
    # serializing. The list here is already scoped to the caller's own
    # coupons (small), but a teacher managing several classrooms' promos at
    # once can still have enough rows for this to add up, and it's free —
    # folds the same lookup into the original query via a JOIN.
    queryset = Coupon.objects.select_related("created_by")

    def get_queryset(self):
        qs = Coupon.objects.filter(created_by=self.request.user).select_related("created_by", "classroom")
        classroom_id = self.request.query_params.get("classroom")
        return qs.filter(classroom_id=classroom_id) if classroom_id else qs

    def perform_create(self, serializer):
        classroom = serializer.validated_data.get("classroom")
        # classroom may legitimately be null (a coupon usable across every
        # classroom the caller teaches) — only block creating one against
        # a classroom the caller does NOT teach/co-teach/moderate.
        if classroom is not None and not _can_manage_classroom(classroom, self.request.user):
            raise PermissionDenied("You can only create coupons for classrooms you teach or manage.")
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        if serializer.instance.created_by_id != self.request.user.id:
            raise PermissionDenied("Only the coupon's creator can edit it.")
        classroom = serializer.validated_data.get("classroom", serializer.instance.classroom)
        if classroom is not None and not _can_manage_classroom(classroom, self.request.user):
            raise PermissionDenied("You can only assign coupons to classrooms you teach or manage.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.created_by_id != self.request.user.id:
            raise PermissionDenied("Only the coupon's creator can delete it.")
        instance.delete()

    # NOTE (abuse): coupon codes are short, guessable strings — without a
    # rate limit this endpoint is a free oracle for brute-forcing valid
    # codes (every guess returns a clean 200/404 with no cost). Scoped
    # throttle; add e.g. {"coupon_validate": "20/min"} to
    # DEFAULT_THROTTLE_RATES in settings.py.
    @action(
        detail=False,
        methods=["get"],
        throttle_classes=[ScopedRateThrottle],
        throttle_scope="coupon_validate",
    )
    def validate(self, request):
        """GET coupons/validate/?code=XYZ&classroom=<id> — checks a code
        without spending it, so the frontend can show the discount before
        buyPass() commits. Registered as a list-level action so it resolves
        before the coupons/{pk}/ detail route.

        NOTE (fix): accepts an OPTIONAL ?classroom= now, checked against the
        same classroom-scoping rule enforced at actual redemption time in
        _charge_and_create_purchase (see that function's NOTE (fix)) — kept
        optional (rather than required) so this doesn't break existing
        frontend callers that only ever sent ?code=; but any caller that
        does pass ?classroom= gets an accurate preview instead of seeing
        "valid" for a coupon that accept() would then actually reject.
        """
        code = request.query_params.get("code")
        classroom_id = request.query_params.get("classroom")
        if not code:
            raise ValidationError({"code": "This field is required."})
        coupon = Coupon.objects.filter(code__iexact=code, is_active=True).first()
        if not coupon or not coupon.is_valid():
            raise NotFound("Invalid or expired coupon code.")
        if classroom_id:
            classroom = Classroom.objects.filter(pk=classroom_id).first()
            if not classroom:
                raise NotFound("Classroom not found.")
            scoped_to_this_classroom = coupon.classroom_id == classroom.id
            scoped_to_creators_other_classroom = (
                coupon.classroom_id is None and coupon.created_by_id == classroom.teacher_id
            )
            if not (scoped_to_this_classroom or scoped_to_creators_other_classroom):
                raise NotFound("This coupon isn't valid for this classroom.")
        return Response(CouponSerializer(coupon).data)


# ---------------------------------------------------------------------------
# 13. COIN TRANSACTION (read-only; own ledger only)
# ---------------------------------------------------------------------------
class CoinTransactionViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = CoinTransactionSerializer
    permission_classes = [IsAuthenticated]
    # NOTE (production): an active user's coin ledger grows on every single
    # purchase/refund/credit — unpaginated, a heavy user's "wallet history"
    # screen would eventually have to download thousands of rows at once.
    pagination_class = LiveClassPagination

    def get_queryset(self):
        return CoinTransaction.objects.filter(user=self.request.user)

    @action(detail=False, methods=["get"])
    def balance(self, request):
        """Real, authoritative coin balance — reads User.coin directly
        instead of asking the client to derive it from the latest ledger
        row. This is the source of truth even for a user with coins but
        zero CoinTransaction rows (e.g. a signup bonus credited without a
        ledger entry), which the "latest transaction's balance_after"
        approach used elsewhere in the app cannot represent correctly.
        Documented in urls.py; was previously undocumented-but-missing."""
        return Response({"coin": request.user.coin})


# ---------------------------------------------------------------------------
# 13B. REFERRAL PROGRAM
#
# GAP THIS CLOSES: CoinTransaction.Reason.REFERRAL_BONUS existed as an enum
# choice with nothing behind it — no code path ever created a transaction
# with that reason. See Referral / referral_code_for_user /
# referral_code_to_user_id in models.py for the encode/decode scheme, and
# REFERRAL_BONUS_COINS / REFERRAL_REDEEM_WINDOW_DAYS in settings.py for the
# two tunables.
# ---------------------------------------------------------------------------
class ReferralViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """GET /liveclass/referrals/ -> people the caller has successfully
    referred (own ledger only, same privacy posture as coin-transactions/).
    `my-code` and `redeem` live on this same viewset since they're the
    same feature, even though they aren't list/retrieve-shaped."""

    serializer_class = ReferralSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = LiveClassPagination

    def get_queryset(self):
        return Referral.objects.filter(referrer=self.request.user).select_related("referred")

    @action(detail=False, methods=["get"], url_path="my-code")
    def my_code(self, request):
        """Own referral code + a running tally of how many people have
        redeemed it and how many coins that's earned so far."""
        user = request.user
        earned = CoinTransaction.objects.filter(
            user=user, reason=CoinTransaction.Reason.REFERRAL_BONUS
        ).aggregate(total=Sum("amount"))["total"] or 0
        data = {
            "code": referral_code_for_user(user.id),
            "referral_count": Referral.objects.filter(referrer=user).count(),
            "total_bonus_earned": earned,
            "bonus_per_referral": django_settings.REFERRAL_BONUS_COINS,
        }
        return Response(MyReferralCodeSerializer(data).data)

    @action(detail=False, methods=["post"])
    def redeem(self, request):
        """Redeem someone else's referral code — one-time, new-account-only
        (Referral.referred is a OneToOneField, and the signup-window check
        below is the second, independent guard against farming). Credits
        REFERRAL_BONUS_COINS to BOTH the referrer and the redeeming user.
        Body: {"code": "R..."}.
        """
        serializer = ReferralRedeemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data["code"]
        user = request.user

        referrer_id = referral_code_to_user_id(code)
        if referrer_id is None:
            raise ValidationError({"code": "Invalid referral code."})
        if referrer_id == user.id:
            raise ValidationError({"code": "You can't redeem your own referral code."})
        if hasattr(user, "referral_used"):
            raise ValidationError("You've already redeemed a referral code.")

        window = timezone.timedelta(days=django_settings.REFERRAL_REDEEM_WINDOW_DAYS)
        if timezone.now() - user.date_joined > window:
            raise ValidationError(
                f"Referral codes can only be redeemed within "
                f"{django_settings.REFERRAL_REDEEM_WINDOW_DAYS} day(s) of signing up."
            )

        referrer = get_user_model().objects.filter(pk=referrer_id).first()
        if referrer is None:
            raise ValidationError({"code": "Invalid referral code."})

        bonus = django_settings.REFERRAL_BONUS_COINS
        with transaction.atomic():
            # Lock both wallets lowest-id-first — a fixed lock order across
            # every concurrent redeem prevents a lock-ordering deadlock
            # between two redeems that happen to touch the same two users
            # in opposite order.
            user_ids = sorted([referrer.id, user.id])
            locked = {
                u.id: u
                for u in get_user_model().objects.select_for_update().filter(pk__in=user_ids)
            }
            referrer_locked = locked[referrer.id]
            user_locked = locked[user.id]

            try:
                referral = Referral.objects.create(
                    referrer=referrer_locked, referred=user_locked, bonus_amount=bonus
                )
            except IntegrityError:
                # referred is a OneToOneField — this is the DB-level
                # backstop against a race between the hasattr() check
                # above and this create() (same double-tap class of race
                # every other unique-constraint check in this app guards
                # against the same way).
                raise ValidationError("You've already redeemed a referral code.")

            referrer_locked.coin += bonus
            referrer_locked.save(update_fields=["coin"])
            CoinTransaction.objects.create(
                user=referrer_locked,
                txn_type=CoinTransaction.TxnType.CREDIT,
                reason=CoinTransaction.Reason.REFERRAL_BONUS,
                amount=bonus,
                balance_after=referrer_locked.coin,
                reference_id=f"referral:{referral.id}:referrer",
            )

            user_locked.coin += bonus
            user_locked.save(update_fields=["coin"])
            CoinTransaction.objects.create(
                user=user_locked,
                txn_type=CoinTransaction.TxnType.CREDIT,
                reason=CoinTransaction.Reason.REFERRAL_BONUS,
                amount=bonus,
                balance_after=user_locked.coin,
                reference_id=f"referral:{referral.id}:referred",
            )

        return Response(
            {"detail": f"Referral redeemed — {bonus} coins credited to both accounts.", "bonus": bonus},
            status=201,
        )


# ---------------------------------------------------------------------------
# TEACHER EARNINGS DASHBOARD
#
# GAP THIS CLOSES: ClassroomViewSet.stats (above) is a public listing-card
# summary (rating/timing/enrolled_count) — it has nothing about money.
# PassDailyCharge is the actual per-day escrow release ledger (see
# PassPurchase.charge_for_session in models.py), so it's the right source
# to aggregate a teacher's real earnings from, instead of re-deriving it
# from CoinTransaction (which mixes class earnings with refunds/referral
# bonuses/etc. for the SAME user and would need reason-filtering + could
# still double count if a teacher is also someone else's student).
# ---------------------------------------------------------------------------
class TeacherEarningsView(APIView):
    """GET /liveclass/my-earnings/ — optionally ?classroom=<id> to scope to
    one classroom (must be a classroom the caller teaches; a co-teacher/
    moderator does not themselves earn anything under the current escrow
    design — see PassPurchase.charge_for_session, which pays
    classroom.teacher only — so this is teacher-only, not staff-visible)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        charges = PassDailyCharge.objects.filter(purchase__class_pass__classroom__teacher=user)

        classroom_id = request.query_params.get("classroom")
        if classroom_id:
            classroom = Classroom.objects.filter(pk=classroom_id, teacher=user).first()
            if classroom is None:
                raise PermissionDenied("You don't teach this classroom.")
            charges = charges.filter(purchase__class_pass__classroom=classroom)

        totals = charges.aggregate(total=Sum("amount"), sessions=Count("id"))
        total_earned = totals["total"] or 0
        total_sessions_charged = totals["sessions"] or 0

        month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        this_month_earned = (
            charges.filter(date__gte=month_start).aggregate(total=Sum("amount"))["total"] or 0
        )

        thirty_days_ago = timezone.now().date() - timezone.timedelta(days=29)
        daily = (
            charges.filter(date__gte=thirty_days_ago)
            .values("date")
            .annotate(amount=Sum("amount"))
            .order_by("date")
        )

        by_classroom = (
            charges.values(
                classroom_id=F("purchase__class_pass__classroom_id"),
                classroom_title=F("purchase__class_pass__classroom__title"),
            )
            .annotate(total_earned=Sum("amount"), sessions_charged=Count("id"))
            .order_by("-total_earned")
        )

        data = {
            "total_earned": total_earned,
            "total_sessions_charged": total_sessions_charged,
            "this_month_earned": this_month_earned,
            "last_30_days": list(daily),
            "by_classroom": list(by_classroom),
        }
        return Response(TeacherEarningsSerializer(data).data)


# ---------------------------------------------------------------------------
# 14. CLASSROOM STAFF
# ---------------------------------------------------------------------------
class ClassroomStaffViewSet(viewsets.ModelViewSet):
    """Anyone can read a classroom's staff/team list (?classroom=<id> —
    shown on the detail page, e.g. an organisation's team); only the
    classroom's own teacher can add, edit, or remove a staff member."""

    serializer_class = ClassroomStaffSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = LiveClassPagination
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
        from .tasks import notify_staff_added

        _safe_delay(notify_staff_added, staff.id)

    def perform_update(self, serializer):
        if serializer.instance.classroom.teacher_id != self.request.user.id:
            raise PermissionDenied("Only the classroom's teacher can edit a staff member's role.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.classroom.teacher_id != self.request.user.id:
            raise PermissionDenied("Only the classroom's teacher can remove a staff member.")
        instance.delete()


# ---------------------------------------------------------------------------
# 15. WAITLIST
# ---------------------------------------------------------------------------
class SessionWaitlistViewSet(
    mixins.ListModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    """Own waitlist entries only by default (list/leave). Pass ?session=<id>
    as that session's teacher/co-teacher/moderator to see every entry
    waiting on it instead, oldest first — the listing `promote` needs an
    id from, since it has no other way to discover who's waiting. `promote`
    itself stays a separate, host-only path that bypasses the "own
    entries" queryset on purpose — a teacher/co-teacher/moderator needs to
    promote a STUDENT's entry, not their own."""

    serializer_class = SessionWaitlistSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = LiveClassPagination

    def get_queryset(self):
        base = SessionWaitlist.objects.select_related("student", "session__classroom")

        # NOTE (fix): there was no way for a teacher/co-teacher/moderator to
        # list who's waiting on their own session — get_queryset only ever
        # returned the CALLER's own entries, so the host had no id to pass
        # into promote/{id}/ short of guessing. ?session=<id> switches to
        # every entry on that session (still oldest-first, via the model's
        # Meta.ordering = ["joined_at"] — matches who should logically be
        # promoted next) once _can_moderate_session confirms the caller
        # actually hosts it. No ?session= (or failing that check) falls
        # straight through to the original "own entries" behaviour, so
        # nothing that already relies on the current shape breaks.
        session_id = self.request.query_params.get("session")
        if session_id:
            session = ClassSession.objects.filter(pk=session_id).select_related("classroom").first()
            if session and _can_moderate_session(session, self.request.user):
                return base.filter(session_id=session_id)

        return base.filter(student=self.request.user)

    @action(detail=True, methods=["post"])
    def promote(self, request, pk=None):
        """Host action — pulls this waitlisted student into the live room
        (e.g. after someone else leaves and a seat frees up). Looked up
        directly (not via get_queryset/get_object) since the caller is the
        host, not the student who owns the waitlist entry."""
        entry = SessionWaitlist.objects.filter(pk=pk).select_related("session__classroom").first()
        if entry is None:
            raise NotFound("Waitlist entry not found.")

        session = entry.session
        if not _can_moderate_session(session, request.user):
            raise PermissionDenied(
                "Only the classroom's teacher, co-teacher, or moderator can promote from the waitlist."
            )

        current_count = session.participants.filter(left_at__isnull=True).count()
        if current_count >= session.classroom.max_participants:
            raise ValidationError("Session is still full — no seat to promote into.")

        # NOTE (fix): join()/token() both refuse re-entry to anyone with a
        # kicked_at stamp on THIS session (see SessionParticipant.kicked_at),
        # but promote() — a separate host-only path that bypasses join()
        # entirely — never checked that. A host removing a disruptive
        # student, then promoting them straight back in from the waitlist
        # (or another host doing so without realizing), silently undid the
        # kick with no error and no way for the original moderator to know.
        if session.participants.filter(user=entry.student, kicked_at__isnull=False).exists():
            # NOTE (fix — was a hand-built Response, bypassing
            # liveclass_exception_handler and coming back without a
            # "code" field): the entry.delete() side effect still needs
            # to happen before we fail the request, so raise after it
            # instead of before.
            entry.delete()
            raise PermissionDenied("This student was removed from this session and can't be promoted back in.")

        livekit_role, participant_role = _resolve_session_roles(session, entry.student)
        participant = SessionParticipant.objects.create(
            session=session, user=entry.student, role=participant_role
        )

        create_notification(
            recipient=participant.user,
            notif_type=Notification.NotifType.WAITLIST_PROMOTED,
            title="You're in!",
            message=f"A seat opened up in '{session.classroom.title}' — you've been moved in from the waitlist.",
            classroom=session.classroom,
            session=session,
        )
        # NOTE (fix — CRITICAL, production bug): notify_waitlist_promotion
        # already existed as a Celery task, but nothing anywhere ever
        # called it — a promoted student got the silent in-app bell row
        # above and NOTHING else. If their app was closed/backgrounded they
        # had no idea a seat had opened until they happened to check back,
        # defeating the entire point of a waitlist promotion (the seat can
        # fill back up while they're not looking). Passes plain ids instead
        # of the waitlist entry's own pk — see notify_waitlist_promotion's
        # docstring for why (entry.delete() below would otherwise race it).
        from .tasks import notify_waitlist_promotion

        _safe_delay(notify_waitlist_promotion, entry.student_id, session.id)
        entry.delete()
        return Response(SessionParticipantSerializer(participant).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# 16. CERTIFICATE (read-only; issued by server-side logic elsewhere)
# ---------------------------------------------------------------------------
class CertificateViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    permission_classes = [IsAuthenticated]
    # NOTE (fix): a certificate list grows unbounded over a user's lifetime
    # on the platform (every certificate they've ever earned, or every one
    # issued across a classroom they teach) — same unbounded-list gap
    # already fixed on sessions/purchases/chat/join-requests above.
    pagination_class = LiveClassPagination

    def get_serializer_class(self):
        return CertificateIssueSerializer if self.action == "create" else CertificateSerializer

    def get_queryset(self):
        base = Certificate.objects.select_related("student", "classroom")
        classroom_id = self.request.query_params.get("classroom")
        user = self.request.user
        if classroom_id:
            classroom = Classroom.objects.filter(pk=classroom_id).first()
            # Teacher/co-teacher/moderator (org staff included) browsing their
            # own classroom's page sees every certificate issued there —
            # everyone else, filtered or not, only ever sees their own.
            if classroom and _can_manage_classroom(classroom, user):
                return base.filter(classroom_id=classroom_id)
            return base.filter(classroom_id=classroom_id, student=user)
        return base.filter(student=user)

    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        student = serializer.validated_data["student"]
        if not _can_manage_classroom(classroom, self.request.user):
            raise PermissionDenied(
                "Only the classroom's teacher, co-teacher, or moderator can issue a certificate."
            )
        # NOTE (fix): nothing checked that the recipient ever actually held
        # a pass for this classroom — a teacher (compromised account, or a
        # co-teacher/moderator acting maliciously) could mint a legitimate-
        # looking "completion" certificate for ANY user on the platform,
        # including one who never took a single class here. Certificate is
        # unique_together (classroom, student), so this also stops a
        # duplicate re-issue attempt from silently failing with a 500
        # instead of a clear error.
        if not classroom.is_enrolled(student):
            raise ValidationError(
                "Can't issue a certificate to a user who has never held a pass for this classroom."
            )
        if Certificate.objects.filter(classroom=classroom, student=student).exists():
            raise ValidationError("A certificate has already been issued to this student for this classroom.")
        serializer.save(certificate_id=uuid.uuid4().hex, issued_at=timezone.now())
        create_notification(
            recipient=student,
            notif_type=Notification.NotifType.CERTIFICATE_ISSUED,
            title="Certificate issued",
            message=f"You've been issued a certificate for '{classroom.title}'.",
            classroom=classroom,
        )
        # NOTE (fix): "you earned a certificate" is exactly the kind of
        # moment worth an actual push, not just a silent bell-icon row.
        from .tasks import notify_certificate_issued

        _safe_delay(notify_certificate_issued, serializer.instance.id)


# ---------------------------------------------------------------------------
# 17. CLASS REMINDER
# ---------------------------------------------------------------------------
class ClassReminderViewSet(viewsets.ModelViewSet):
    serializer_class = ClassReminderSerializer
    permission_classes = [IsAuthenticated]
    # NOTE (fix): same unbounded-list gap as certificates/sessions/purchases
    # above — a user active for a long time can accumulate a large number
    # of reminders across every classroom they've ever engaged with.
    pagination_class = LiveClassPagination

    def get_queryset(self):
        return ClassReminder.objects.filter(user=self.request.user).select_related(
            "user", "session__classroom"
        )

    def perform_create(self, serializer):
        # NOTE (fix): no check that the caller has access to the session's
        # classroom — low severity (rows are already scoped to `user` on
        # read, see get_queryset), but still lets someone create reminder
        # rows against sessions they can't otherwise see. Gated the same
        # way as everywhere else that reads session internals.
        session = serializer.validated_data["session"]
        if not _can_view_classroom_internals(session.classroom, self.request.user):
            raise PermissionDenied("You don't have access to this session.")
        serializer.save(user=self.request.user)


# ---------------------------------------------------------------------------
# 18. CLASS HOLIDAY / OFF-DAY
# ---------------------------------------------------------------------------
class ClassHolidayViewSet(viewsets.ModelViewSet):
    """NOTE (fix): off-days are classroom "internals" like schedules/
    materials — a user with NO pass at all (access_level "none") shouldn't
    be able to read them, only the teacher/staff or anyone who has ever
    held a pass (active or expired). Gated behind
    _can_view_classroom_internals; only the teacher/co-teacher/moderator can
    mark or remove one."""

    serializer_class = ClassHolidaySerializer
    permission_classes = [IsAuthenticated]
    # NOTE (fix): consistent with every other list endpoint in this app —
    # holiday counts are naturally small per classroom, but a classroom
    # running for years can still accumulate enough rows to be worth capping.
    pagination_class = LiveClassPagination
    # NOTE (perf): created_by is nested (UserMiniSerializer) on every row.
    queryset = ClassHoliday.objects.select_related("created_by", "classroom", "schedule")

    def get_queryset(self):
        qs = super().get_queryset()
        classroom_id = self.request.query_params.get("classroom")
        if not classroom_id:
            return qs.none()
        classroom = Classroom.objects.filter(pk=classroom_id).first()
        if not classroom or not _can_view_classroom_internals(classroom, self.request.user):
            return qs.none()
        return qs.filter(classroom_id=classroom_id)

    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        if not _can_manage_classroom(classroom, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can mark an off-day.")
        serializer.save(created_by=self.request.user)

    def perform_destroy(self, instance):
        if not _can_manage_classroom(instance.classroom, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can remove an off-day.")
        instance.delete()


# ---------------------------------------------------------------------------
# 19. NOTICE BOARD
# ---------------------------------------------------------------------------
class NoticeViewSet(viewsets.ModelViewSet):
    """NOTE (fix): notices are classroom "internals" like schedules/
    materials — a user with NO pass at all (access_level "none") shouldn't
    be able to read them, only the teacher/staff or anyone who has ever
    held a pass (active or expired). Reads (?classroom=<id>, newest/pinned
    first, expired ones hidden by default) are now gated behind
    _can_view_classroom_internals; only the teacher/co-teacher/moderator can
    post, pin, or delete a notice."""

    serializer_class = NoticeSerializer
    permission_classes = [IsAuthenticated]
    # NOTE (fix): a long-running classroom's notice board grows unbounded
    # (every announcement ever posted) — same class of gap as above.
    pagination_class = LiveClassPagination
    # NOTE (perf): posted_by is nested (UserMiniSerializer) on every row.
    queryset = Notice.objects.select_related("posted_by", "classroom")

    def get_queryset(self):
        qs = super().get_queryset()
        classroom_id = self.request.query_params.get("classroom")
        if not classroom_id:
            return qs.none()
        classroom = Classroom.objects.filter(pk=classroom_id).first()
        if not classroom or not _can_view_classroom_internals(classroom, self.request.user):
            return qs.none()
        qs = qs.filter(classroom_id=classroom_id)
        if not _is_truthy(self.request.query_params.get("include_expired")):
            qs = qs.exclude(expires_at__lt=timezone.now())
        return qs

    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        if not _can_manage_classroom(classroom, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can post a notice.")
        notice = serializer.save(posted_by=self.request.user)
        # Only URGENT notices fan out an in-app notification to every
        # currently-enrolled student — routine notices would otherwise spam
        # a notification row per student per post. bulk_create keeps this
        # to one INSERT regardless of classroom size.
        if notice.priority == Notice.Priority.URGENT:
            student_ids = list(
                PassPurchase.objects.filter(
                    class_pass__classroom=classroom,
                    status=PassPurchase.Status.SUCCESS,
                    is_active=True,
                    expires_at__gt=timezone.now(),
                ).values_list("student_id", flat=True).distinct()
            )
            create_bulk_notifications(
                recipients=student_ids,
                notif_type=Notification.NotifType.NOTICE_POSTED,
                title=f"Urgent: {notice.title}",
                message=f"New urgent notice in '{classroom.title}'.",
                classroom=classroom,
            )
            # NOTE (fix): the bulk in-app fan-out above never pushed —
            # an "urgent" notice sitting unseen in the bell icon until the
            # student happens to open the app defeats the point of marking
            # it urgent. Queued as one task (not one task per student) so a
            # large classroom's fan-out doesn't flood the queue with
            # thousands of individual jobs; the task itself loops
            # best-effort per recipient.
            from .tasks import notify_notice_posted

            _safe_delay(notify_notice_posted, notice.id, student_ids)

    def perform_update(self, serializer):
        if not _can_manage_classroom(serializer.instance.classroom, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can edit this notice.")
        serializer.save()

    def perform_destroy(self, instance):
        if not _can_manage_classroom(instance.classroom, self.request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can delete this notice.")
        instance.delete()

    @action(detail=True, methods=["post"])
    def pin(self, request, pk=None):
        notice = self.get_object()
        if not _can_manage_classroom(notice.classroom, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can pin a notice.")
        notice.is_pinned = not notice.is_pinned
        notice.save(update_fields=["is_pinned"])
        return Response(NoticeSerializer(notice).data)


# ---------------------------------------------------------------------------
# 20. CLASS QUERY / DOUBT (student asks, teacher/co-teacher/moderator answers)
# ---------------------------------------------------------------------------
class ClassQueryViewSet(viewsets.ModelViewSet):
    """Students ask a question/doubt about a classroom (optionally scoped to
    one session); the classroom's teacher, co-teacher, or moderator answers
    it via the `answer` action.

    Visibility:
        - GET .../queries/?classroom=<id> — if the caller manages that
          classroom (teacher/co-teacher/moderator), they see every query for
          it; anyone else only sees the ones they personally asked.
        - GET .../queries/ (no classroom filter) — a "my doubts" list: every
          query the caller has asked, across all classrooms.
    """

    serializer_class = ClassQuerySerializer
    permission_classes = [IsAuthenticated]
    # NOTE (fix): a classroom's doubts/queries list grows unbounded over its
    # lifetime — same class of gap as above.
    pagination_class = LiveClassPagination
    # NOTE (perf): asked_by/answered_by are both nested (UserMiniSerializer).
    queryset = ClassQuery.objects.select_related("asked_by", "answered_by", "classroom")

    def get_queryset(self):
        qs = super().get_queryset()
        classroom_id = self.request.query_params.get("classroom")
        if classroom_id:
            qs = qs.filter(classroom_id=classroom_id)
            classroom = Classroom.objects.filter(pk=classroom_id).first()
            if classroom and _can_manage_classroom(classroom, self.request.user):
                return qs
        return qs.filter(asked_by=self.request.user)

    def perform_create(self, serializer):
        classroom = serializer.validated_data["classroom"]
        if not _can_view_classroom_internals(classroom, self.request.user):
            raise PermissionDenied("A pass (active or expired) is required to ask a question in this classroom.")
        serializer.save(asked_by=self.request.user)

    @action(detail=True, methods=["post"])
    def answer(self, request, pk=None):
        query = self.get_object()
        if not _can_manage_classroom(query.classroom, request.user):
            raise PermissionDenied("Only the classroom's teacher, co-teacher, or moderator can answer a query.")
        serializer = ClassQueryAnswerSerializer(query, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(
            answered_by=request.user, answered_at=timezone.now(), status=ClassQuery.Status.ANSWERED
        )
        create_notification(
            recipient=query.asked_by,
            notif_type=Notification.NotifType.QUERY_ANSWERED,
            title="Your doubt was answered",
            message=f"'{query.question[:60]}' has been answered in '{query.classroom.title}'.",
            classroom=query.classroom,
            session=query.session,
        )
        from .tasks import notify_query_answered

        _safe_delay(notify_query_answered, query.id)
        return Response(ClassQuerySerializer(query).data)

# ---------------------------------------------------------------------------
# HOME DASHBOARD — single-call summary for the app's home/landing screen.
#
# WHY THIS EXISTS: without it, a mobile home screen needing "what's next"
# has to fire N separate requests (upcoming sessions, my classrooms,
# certificates earned, wishlist, pending requests) and stitch them together
# client-side — slower first paint, more battery/data on mobile, and N
# separate chances for one of them to fail. This is the same "one call for
# the whole screen" pattern classrooms/{id}/stats/ already uses for the
# classroom detail page, applied to the app's home screen instead.
# Everything here is a cheap COUNT/LIMIT query — no N+1, nothing here
# duplicates another endpoint's full detail (the client still hits
# /sessions/, /certificates/, etc. for full lists — this is a summary only).
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 22. NOTIFICATION (read-only; rows are only ever created server-side — see
# create_notification()/create_bulk_notifications() in models.py)
#
# NOTE (fix — unreachable feature): Notification, NotificationSerializer, and
# every notif_type-producing call site (join-request decisions, grading,
# certificate issuance, waitlist promotion, classroom flagging, notices,
# doubt answers, ...) already existed, but nothing ever exposed them over
# the API — no ViewSet, no route. Every user had a growing pile of
# notification rows created on their behalf with absolutely no way to
# read, count, or clear them from the client. This viewset is the missing
# other half of that pipeline.
# ---------------------------------------------------------------------------
class NotificationViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Own notifications only — nobody can read or clear anyone else's.

    GET    notifications/                 list, newest first (?is_read=true/false to filter)
    GET    notifications/{id}/            retrieve one
    DELETE notifications/{id}/            clear one (own only)
    GET    notifications/unread-count/    badge count for the bell icon
    POST   notifications/{id}/mark-read/  mark one as read
    POST   notifications/mark-all-read/   mark every unread one as read
    """

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = LiveClassPagination

    def get_queryset(self):
        qs = Notification.objects.filter(recipient=self.request.user).select_related("classroom", "session")
        is_read = self.request.query_params.get("is_read")
        if is_read is not None:
            qs = qs.filter(is_read=_is_truthy(is_read))
        return qs

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        count = Notification.objects.filter(recipient=request.user, is_read=False).count()
        return Response({"unread_count": count})

    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        notification.mark_read()
        return Response(NotificationSerializer(notification).data)

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        # NOTE (perf): bulk UPDATE instead of looping + calling .mark_read()
        # per row — a user with hundreds of unread notifications shouldn't
        # cost hundreds of UPDATE statements for one "clear my badge" tap.
        updated = Notification.objects.filter(recipient=request.user, is_read=False).update(
            is_read=True, read_at=timezone.now()
        )
        return Response({"marked_read": updated})


class MyDashboardView(APIView):
    """GET /liveclass/dashboard/ — the logged-in user's home-screen summary:
    next few upcoming sessions they can join, how many classrooms they
    teach vs are currently enrolled in, certificates earned, wishlist size,
    and any join requests still awaiting a teacher's decision."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        now = timezone.now()
        accessible_ids = _accessible_classroom_ids(user)

        upcoming_sessions = (
            ClassSession.objects.filter(
                classroom_id__in=accessible_ids,
                status__in=[ClassSession.Status.SCHEDULED, ClassSession.Status.LIVE],
                scheduled_start__lte=now + timezone.timedelta(days=7),
                scheduled_end__gte=now,
            )
            .select_related("classroom")
            .order_by("scheduled_start")[:5]
        )

        teaching_count = Classroom.objects.filter(teacher=user, is_deleted=False).count()

        enrolled_count = (
            PassPurchase.objects.filter(
                student=user,
                status=PassPurchase.Status.SUCCESS,
                is_active=True,
                expires_at__gt=now,
            )
            .values("class_pass__classroom_id")
            .distinct()
            .count()
        )

        return Response(
            {
                "upcoming_sessions": ClassSessionSerializer(upcoming_sessions, many=True).data,
                "teaching_classrooms_count": teaching_count,
                "enrolled_classrooms_count": enrolled_count,
                "certificates_count": Certificate.objects.filter(student=user).count(),
                "wishlist_count": ClassroomWishlist.objects.filter(user=user).count(),
                "pending_join_requests_count": ClassJoinRequest.objects.filter(
                    student=user, status=ClassJoinRequest.Status.PENDING
                ).count(),
                "unread_notifications_count": Notification.objects.filter(
                    recipient=user, is_read=False
                ).count(),
            }
        )


# ---------------------------------------------------------------------------
# HEALTH CHECK (load balancer / uptime monitoring)
# ---------------------------------------------------------------------------
class HealthCheckView(APIView):
    """GET /liveclass/healthz/ — unauthenticated liveness/readiness probe for
    a load balancer or uptime monitor (Render health check, UptimeRobot, a
    k8s readiness probe, etc.).

    Deliberately OUTSIDE IsAuthenticated: infra that has no credentials
    can't hit an authenticated endpoint, so a health check that requires
    auth is useless to the thing that's supposed to be checking it.

    Each dependency below is checked independently and wrapped in its own
    try/except so one broken dependency reports its own status instead of
    an unhandled 500 taking the whole endpoint down — that distinction
    ("the app process itself is dead" vs "the app is up but its DB/cache
    is unreachable") is exactly the thing a status page/alert needs to be
    able to tell apart.

    Checks:
        database — a trivial `SELECT 1` against the primary DB connection.
        cache    — a round-trip set/get against Django's configured cache
                   backend (Redis in production — see settings.py).

    The HTTP status code is what the load balancer/monitor actually acts
    on: 200 only if every check passes, 503 if any fails. The JSON body
    (which check failed, and why) is for a human looking at a dashboard or
    debugging an alert — never assume the caller parses it.
    """

    permission_classes = []
    authentication_classes = []

    def get(self, request):
        checks = {}
        healthy = True

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            checks["database"] = "ok"
        except Exception as exc:
            healthy = False
            checks["database"] = f"error: {exc}"

        try:
            probe_key = "liveclass:healthz:probe"
            cache.set(probe_key, "1", timeout=5)
            if cache.get(probe_key) != "1":
                raise RuntimeError("cache round-trip returned an unexpected value")
            checks["cache"] = "ok"
        except Exception as exc:
            healthy = False
            checks["cache"] = f"error: {exc}"

        return Response(
            {"status": "ok" if healthy else "error", "checks": checks},
            status=200 if healthy else 503,
        )