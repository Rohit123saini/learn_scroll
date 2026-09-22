# testseries/views_advanced.py
"""
Advanced test-series endpoints, kept out of `views.py` so the original
viewsets stay readable. They are attached to the existing viewsets as
MIXINS (`SeriesAdvancedActionsMixin`, `AttemptAdvancedActionsMixin`) — same
router, same permission classes, same `get_object()` access rules — plus a
handful of stand-alone views (public preview, certificate verify, LiveKit
webhook).

URL map (all under the app's mount, i.e. `/testseries/`):

  Series   POST   testseries/{id}/questions-bulk/        JSON answer-key upload
           POST   testseries/{id}/questions-import/      CSV answer-key upload
           GET    testseries/{id}/answer-key/            is the key complete?
           POST   testseries/{id}/release-results/       manual result release
           GET    testseries/{id}/leaderboard/
           GET    testseries/{id}/certificates/          (creator)
           POST   testseries/{id}/revoke-certificate/    (creator)
           POST   testseries/{id}/live-start/            host goes live (+records)
           POST   testseries/{id}/live-end/
           POST   testseries/{id}/live-token/            host token
           GET    testseries/{id}/recordings/
  Attempt  PATCH  attempts/{id}/save/                    server-side autosave
           GET    attempts/{id}/solutions/
           GET    attempts/{id}/analytics/
           GET    attempts/{id}/certificate/
           GET    attempts/{id}/certificate-pdf/
           POST   attempts/{id}/live-token/               student: viewer + proctor tokens
           POST   attempts/{id}/proctor-events/
           GET    attempts/{id}/integrity/                (creator / reviewer)
           POST   attempts/{id}/proctor-watch/            (creator / reviewer)
  Public   GET    testseries/public/{slug}/               share-link preview
           GET    testseries/certificates/verify/{code}/
           GET    testseries/certificates/mine/
           POST   testseries/livekit-webhook/
"""
import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Max
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from login.models import User

from . import live, policy
from .access import user_can_access_series
from .certificate_pdf import PdfUnavailable, render_pdf
from .csv_import import parse_csv
from .models import (
    Question, TestAttempt, TestCertificate, TestLiveSession, TestProctorEvent,
    TestRecording, TestSeries,
)
from .permissions import user_can_review_attempt
from .serializers import (
    CertificateVerifySerializer, ProctorEventSerializer, ProgressSaveSerializer,
    PublicSeriesSerializer, QuestionSerializer, TestCertificateSerializer, TestRecordingSerializer,
    display_name,
)
from .throttling import CertificateVerifyThrottle, TestSeriesPublicPageThrottle

logger = logging.getLogger(__name__)

MAX_BULK_QUESTIONS = 500
MAX_CSV_BYTES = 1024 * 1024
MAX_PROCTOR_EVENTS_PER_ATTEMPT = 500
LEADERBOARD_DEFAULT_SIZE = 50


def _is_creator(user, series) -> bool:
    return series.creator_id == getattr(user, "id", None)


def _file_url(request, field):
    if not field:
        return None
    try:
        return request.build_absolute_uri(field.url)
    except ValueError:  # file field without a file
        return None


def _finished(attempt) -> bool:
    return attempt.status != TestAttempt.Status.IN_PROGRESS


def _require_creator(user, series, message="Only the creator can do this."):
    if not _is_creator(user, series):
        raise PermissionDenied(message)


# =====================================================================
# SERIES actions
# =====================================================================
class SeriesAdvancedActionsMixin:
    """Mixed into `TestSeriesViewSet`. `self.get_object()` already enforces
    `IsSeriesCreatorOrReadOnly`, so every POST below is creator-only by
    construction; GET actions that are creator-only say so explicitly."""

    # ------------------------------------------------------ answer key
    @action(detail=True, methods=["get"], url_path="answer-key")
    def answer_key(self, request, pk=None):
        """Is the answer key complete? The publish action refuses to publish
        while it isn't, because an objective question with no correct answer
        would silently mark every student wrong."""
        series = self.get_object()
        _require_creator(request.user, series)
        missing = _questions_missing_answer_key(series)
        return Response({"complete": not missing, "missing_question_orders": missing})

    @action(detail=True, methods=["post"], url_path="questions-bulk")
    def questions_bulk(self, request, pk=None):
        """Body: `{"questions": [ {question_type, text, options, correct_answer,
        marks, negative_marks, topic, difficulty, explanation, order?}, ... ]}`.
        All-or-nothing: one invalid question rejects the whole batch with a
        per-index error report, so a half-imported test can never exist."""
        series = self.get_object()
        _require_creator(request.user, series)
        _require_draft(series)
        payload = request.data.get("questions")
        if not isinstance(payload, list) or not payload:
            raise ValidationError({"questions": "Send a non-empty list."})
        if len(payload) > MAX_BULK_QUESTIONS:
            raise ValidationError({"questions": f"At most {MAX_BULK_QUESTIONS} questions per request."})
        return _create_questions(request, series, payload)

    @action(
        detail=True, methods=["post"], url_path="questions-import",
        parser_classes=[MultiPartParser, FormParser],
    )
    def questions_import(self, request, pk=None):
        """multipart `file` = CSV (see `csv_import.py` for the columns). The
        answer key travels IN the sheet (`correct` column), so every
        objective question is auto-graded the moment a student submits."""
        series = self.get_object()
        _require_creator(request.user, series)
        _require_draft(series)
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError({"file": "Attach a CSV file."})
        if upload.size > MAX_CSV_BYTES:
            raise ValidationError({"file": "CSV is larger than 1 MB."})

        next_order = (series.questions.aggregate(m=Max("order"))["m"] or 0) + 1
        result = parse_csv(upload.read(), start_order=next_order)
        if result.errors:
            return Response(
                {"detail": "The CSV has problems — nothing was imported.",
                 "errors": [{"row": row, "message": msg} for row, msg in result.errors]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return _create_questions(request, series, result.questions)

    # ------------------------------------------------------ results
    @action(detail=True, methods=["post"], url_path="release-results")
    def release_results(self, request, pk=None):
        series = self.get_object()
        _require_creator(request.user, series)
        if series.results_released_at is None:
            series.results_released_at = timezone.now()
            series.save(update_fields=["results_released_at"])
        return Response({"results_released_at": series.results_released_at})

    @action(detail=True, methods=["get"])
    def leaderboard(self, request, pk=None):
        """Best checked attempt per student, highest first (ties share a rank).
        `?limit=` (max 100). The caller's own row is always appended so a
        student outside the top N still sees where they stand."""
        series = self.get_object()
        if not (_is_creator(request.user, series) or user_can_access_series(request.user, series)):
            raise PermissionDenied("You don't have access to this test series.")
        if not _is_creator(request.user, series) and not series.results_visible():
            raise PermissionDenied("Results have not been released yet.")

        try:
            limit = max(1, min(100, int(request.query_params.get("limit", LEADERBOARD_DEFAULT_SIZE))))
        except ValueError:
            limit = LEADERBOARD_DEFAULT_SIZE

        best = list(
            TestAttempt.objects.filter(
                series=series, status=TestAttempt.Status.CHECKED, final_score__isnull=False
            )
            .values("student")
            .annotate(best=Max("final_score"))
            .order_by("-best", "student")
        )
        rows, last_score, last_rank = [], None, 0
        for index, row in enumerate(best, start=1):
            rank = last_rank if row["best"] == last_score else index
            last_score, last_rank = row["best"], rank
            rows.append({"rank": rank, "student_id": row["student"], "score": row["best"]})

        mine = next((r for r in rows if r["student_id"] == request.user.id), None)
        top = rows[:limit]
        if mine is not None and mine not in top:
            top = top + [mine]

        names = {u.id: display_name(u) for u in User.objects.filter(id__in=[r["student_id"] for r in top])}
        total = series.total_marks or 0
        data = [
            {
                "rank": r["rank"],
                "student_name": names.get(r["student_id"], ""),
                "score": r["score"],
                "percentage": policy.percentage(r["score"], total),
                "is_me": r["student_id"] == request.user.id,
            }
            for r in top
        ]
        return Response({"total_ranked": len(rows), "results": data})

    # ------------------------------------------------------ certificates
    @action(detail=True, methods=["get"])
    def certificates(self, request, pk=None):
        series = self.get_object()
        _require_creator(request.user, series)
        qs = series.certificates.select_related("student", "series")
        return Response(TestCertificateSerializer(qs, many=True, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="revoke-certificate")
    def revoke_certificate(self, request, pk=None):
        series = self.get_object()
        _require_creator(request.user, series)
        code = str(request.data.get("code", "")).strip().upper()
        cert = get_object_or_404(series.certificates, code=code)
        if cert.revoked_at is None:
            cert.revoke(str(request.data.get("reason", "")))
        return Response(TestCertificateSerializer(cert, context={"request": request}).data)

    # ------------------------------------------------------ live video
    @action(detail=True, methods=["post"], url_path="live-start")
    def live_start(self, request, pk=None):
        """Host goes live: creates the LiveKit room, starts recording (if
        `record_live`), and returns the host's join token."""
        series = self.get_object()
        _require_creator(request.user, series)
        if series.delivery_mode != TestSeries.DeliveryMode.LIVE:
            raise ValidationError("This test series is not a live test (delivery_mode != 'live').")
        if series.status != TestSeries.Status.PUBLISHED:
            raise ValidationError("Publish the test series before going live.")
        if series.window_state() == policy.ENDED:
            raise ValidationError("The test window has already ended.")

        session, _ = TestLiveSession.objects.get_or_create(
            series=series,
            defaults={"room_name": live.live_room_name(series.id), "host": request.user},
        )
        live.ensure_room(session.room_name)

        if series.record_live and not series.recordings.filter(
            kind=TestRecording.Kind.LIVE_SESSION, status=TestRecording.Status.RECORDING
        ).exists():
            egress_id = live.start_recording(session.room_name)
            TestRecording.objects.create(
                series=series, kind=TestRecording.Kind.LIVE_SESSION,
                room_name=session.room_name, egress_id=egress_id,
            )

        session.status = TestLiveSession.Status.LIVE
        session.started_at = session.started_at or timezone.now()
        session.ended_at = None
        session.host = request.user
        session.save(update_fields=["status", "started_at", "ended_at", "host"])
        return Response(_host_payload(request.user, series, session))

    @action(detail=True, methods=["post"], url_path="live-end")
    def live_end(self, request, pk=None):
        series = self.get_object()
        _require_creator(request.user, series)
        session = getattr(series, "live_session", None)
        if session is None:
            raise ValidationError("This test has no live session.")

        for recording in series.recordings.filter(
            kind=TestRecording.Kind.LIVE_SESSION, status=TestRecording.Status.RECORDING
        ):
            try:
                live.stop_recording(recording.egress_id)
            except live.TestLiveError:
                # The room close below makes LiveKit stop the egress anyway.
                logger.warning("Could not stop egress %s explicitly.", recording.egress_id)
        session.status = TestLiveSession.Status.ENDED
        session.ended_at = timezone.now()
        session.save(update_fields=["status", "ended_at"])
        live.end_room(session.room_name)  # never raises for an already-closed room
        return Response({"status": session.status, "ended_at": session.ended_at})

    @action(detail=True, methods=["post"], url_path="live-token")
    def live_token(self, request, pk=None):
        """Fresh host token (e.g. after the host's app restarted)."""
        series = self.get_object()
        _require_creator(request.user, series)
        session = getattr(series, "live_session", None)
        if session is None:
            raise ValidationError("Go live first.")
        return Response(_host_payload(request.user, series, session))

    @action(detail=True, methods=["get"])
    def recordings(self, request, pk=None):
        """Creator: every recording (live + proctor). Students who have
        attempted the series: the READY live-session recording only, so they
        can replay the class alongside the test. Proctor footage is never
        exposed to students."""
        series = self.get_object()
        if _is_creator(request.user, series):
            qs = series.recordings.all()
        else:
            if not TestAttempt.objects.filter(series=series, student=request.user).exists():
                raise PermissionDenied("Attempt this test to watch its recording.")
            qs = series.recordings.filter(
                kind=TestRecording.Kind.LIVE_SESSION, status=TestRecording.Status.READY
            )
        return Response(TestRecordingSerializer(qs, many=True).data)


def _require_draft(series):
    if series.status != TestSeries.Status.DRAFT:
        raise ValidationError("Questions can only be added while the series is a draft.")


def _questions_missing_answer_key(series):
    """Orders of objective questions whose `correct_answer` is empty."""
    missing = []
    for q in series.questions.all():
        if q.question_type == Question.QuestionType.TEXT:
            continue
        if not q.correct_answer:
            missing.append(q.order)
    return missing


def _host_payload(user, series, session):
    return {
        "room": session.room_name,
        "url": live.livekit_url(),
        "token": live.issue_token(
            room_name=session.room_name, user_id=user.id, user_name=display_name(user), role=live.Role.HOST
        ),
        "status": session.status,
        "recording": series.record_live,
    }


@transaction.atomic
def _create_questions_atomic(series, payload, request):
    """Validate every item through the SAME `QuestionSerializer` a single
    create uses (so shape rules are never duplicated), then create them all."""
    used = set(series.questions.values_list("order", flat=True))
    next_order = (max(used) if used else 0) + 1
    serializers_, errors = [], []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            errors.append({"index": index, "errors": {"non_field_errors": ["Each question must be an object."]}})
            continue
        item = dict(item)
        if item.get("order") in (None, ""):
            while next_order in used:
                next_order += 1
            item["order"] = next_order
        order = item["order"]
        if order in used:
            errors.append({"index": index, "errors": {"order": [f"Order {order} is already taken."]}})
            continue
        used.add(order)
        ser = QuestionSerializer(data=item, context={"request": request})
        if ser.is_valid():
            serializers_.append(ser)
        else:
            errors.append({"index": index, "errors": ser.errors})
    if errors:
        raise ValidationError({"detail": "Nothing was created — fix the listed questions.", "errors": errors})
    created = [ser.save(series=series) for ser in serializers_]
    series.recompute_total_marks()
    return created


def _create_questions(request, series, payload):
    try:
        created = _create_questions_atomic(series, payload, request)
    except DjangoValidationError as exc:
        raise ValidationError({"detail": exc.messages})
    except IntegrityError:
        raise ValidationError({"detail": "A question with the same order already exists."})
    return Response(
        {
            "created": len(created),
            "total_marks": series.total_marks,
            "questions": QuestionSerializer(created, many=True, context={"request": request}).data,
        },
        status=status.HTTP_201_CREATED,
    )


# =====================================================================
# ATTEMPT actions
# =====================================================================
class AttemptAdvancedActionsMixin:
    """Mixed into `TestAttemptViewSet` (`self.get_object()` = own attempt OR
    permitted reviewer)."""

    def _owner_only(self, request, attempt, message="You can only do this on your own attempt."):
        if attempt.student_id != request.user.id:
            raise PermissionDenied(message)

    def _reviewer_only(self, request, attempt):
        if not user_can_review_attempt(request.user, attempt):
            raise PermissionDenied("You are not permitted to review this attempt.")

    def _results_gate(self, request, attempt):
        """Owner must wait for the release policy; creator/reviewer never."""
        if attempt.student_id == request.user.id and not user_can_review_attempt(request.user, attempt):
            if not _finished(attempt):
                raise ValidationError("Submit the test first.")
            if not attempt.series.results_visible():
                raise PermissionDenied("Results have not been released yet.")

    # ------------------------------------------------------ autosave
    @action(detail=True, methods=["patch", "post"], url_path="save")
    def save_progress(self, request, pk=None):
        """Server-side autosave: survives app kill / device change, and is the
        source the auto-submit task grades if the student never presses Submit."""
        attempt = self.get_object()
        self._owner_only(request, attempt)
        if attempt.status != TestAttempt.Status.IN_PROGRESS:
            raise ValidationError("This attempt has already been submitted.")
        if policy.is_past_deadline(now=timezone.now(), deadline=attempt.deadline_at):
            raise ValidationError({"detail": "Time is up.", "code": "deadline_passed"})
        ser = ProgressSaveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        attempt.save_progress(ser.validated_data["answers"])
        return Response(
            {"saved_at": attempt.draft_saved_at, "server_time": timezone.now(), "deadline_at": attempt.deadline_at}
        )

    # ------------------------------------------------------ solutions
    @action(detail=True, methods=["get"])
    def solutions(self, request, pk=None):
        attempt = self.get_object()
        self._results_gate(request, attempt)
        if attempt.student_id == request.user.id and not attempt.series.show_solutions \
                and not user_can_review_attempt(request.user, attempt):
            raise PermissionDenied("The creator has turned solutions off for this test.")

        responses = attempt.responses.select_related("question").order_by("question__order")
        data = []
        for r in responses:
            q = r.question
            data.append(
                {
                    "question_id": q.id, "order": q.order, "question_type": q.question_type,
                    "text": q.text, "attachment": _file_url(request, q.attachment),
                    "options": q.options, "marks": q.marks, "negative_marks": q.negative_marks,
                    "topic": q.topic, "difficulty": q.difficulty,
                    "your_answer": r.answer_data, "your_attachment": _file_url(request, r.answer_attachment),
                    "correct_answer": q.correct_answer or None, "explanation": q.explanation,
                    "is_correct": r.is_correct, "marks_awarded": r.marks_awarded, "penalty": r.penalty,
                    "reviewer_feedback": r.reviewer_feedback, "time_spent_seconds": r.time_spent_seconds,
                }
            )
        return Response({"attempt_id": attempt.id, "results": data})

    # ------------------------------------------------------ analytics
    @action(detail=True, methods=["get"])
    def analytics(self, request, pk=None):
        """Rank / percentile / class average / per-topic accuracy / time."""
        attempt = self.get_object()
        self._results_gate(request, attempt)
        series = attempt.series

        out = {
            "attempt_id": attempt.id, "status": attempt.status,
            "score": attempt.final_score if attempt.final_score is not None else attempt.auto_score,
            "total_marks": series.total_marks, "percentage": attempt.percentage, "passed": attempt.passed,
            "submitted_late": attempt.submitted_late,
        }

        # ---- standing among all students (best checked attempt each)
        if attempt.status == TestAttempt.Status.CHECKED and attempt.final_score is not None:
            per_student = list(
                TestAttempt.objects.filter(series=series, status=TestAttempt.Status.CHECKED, final_score__isnull=False)
                .values("student").annotate(best=Max("final_score"))
            )
            scores = [row["best"] for row in per_student]
            mine = max(
                (row["best"] for row in per_student if row["student"] == attempt.student_id),
                default=attempt.final_score,
            )
            total = len(scores)
            out["standing"] = {
                "rank": 1 + sum(1 for s in scores if s > mine),
                "total_students": total,
                "percentile": round(100.0 * sum(1 for s in scores if s < mine) / total, 1) if total else None,
                "average_score": round(sum(scores) / total, 2) if total else None,
                "top_score": max(scores) if scores else None,
            }

        # ---- topics + time
        topics, per_question, total_seconds = {}, [], 0
        for r in attempt.responses.select_related("question").order_by("question__order"):
            q = r.question
            bucket = topics.setdefault(q.topic or "General", {"topic": q.topic or "General", "questions": 0,
                                                              "correct": 0, "marks": 0, "max_marks": 0})
            bucket["questions"] += 1
            bucket["correct"] += 1 if r.is_correct else 0
            bucket["marks"] += r.marks_awarded or 0
            bucket["max_marks"] += q.marks
            if r.time_spent_seconds is not None:
                total_seconds += r.time_spent_seconds
            per_question.append(
                {"question_id": q.id, "order": q.order, "seconds": r.time_spent_seconds,
                 "is_correct": r.is_correct, "topic": q.topic}
            )
        for bucket in topics.values():
            bucket["accuracy"] = round(100.0 * bucket["marks"] / bucket["max_marks"], 1) if bucket["max_marks"] else None
        out["topics"] = sorted(topics.values(), key=lambda b: (b["accuracy"] is None, b["accuracy"] or 0))
        out["time"] = {"total_seconds": total_seconds, "per_question": per_question}
        return Response(out)

    # ------------------------------------------------------ certificate
    def _certificate_for(self, request, attempt):
        self._results_gate(request, attempt)
        cert = getattr(attempt, "certificate", None)
        if cert is None:
            raise NotFound("No certificate was issued for this attempt.")
        return cert

    @action(detail=True, methods=["get"])
    def certificate(self, request, pk=None):
        attempt = self.get_object()
        cert = self._certificate_for(request, attempt)
        return Response(TestCertificateSerializer(cert, context={"request": request}).data)

    @action(detail=True, methods=["get"], url_path="certificate-pdf")
    def certificate_pdf(self, request, pk=None):
        attempt = self.get_object()
        cert = self._certificate_for(request, attempt)
        if not cert.is_valid:
            raise PermissionDenied("This certificate has been revoked.")
        try:
            pdf = render_pdf(
                student_name=display_name(cert.student), title=cert.title, series_title=cert.series.title,
                score=cert.score, total_marks=cert.total_marks, percentage=cert.percentage,
                code=cert.code, issued_at=cert.issued_at,
                verify_url=request.build_absolute_uri(f"/testseries/certificates/verify/{cert.code}/"),
            )
        except PdfUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_501_NOT_IMPLEMENTED)
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="certificate-{cert.code}.pdf"'
        return response

    # ------------------------------------------------------ live / proctor
    @action(detail=True, methods=["post"], url_path="live-token")
    def live_token(self, request, pk=None):
        """Student: everything the client needs to join the video side of THIS
        attempt — a subscribe-only token for the host's live room (live tests)
        and/or a publish-only token for the attempt's own proctor room (camera
        proctoring), starting that room's recording exactly once."""
        attempt = self.get_object()
        self._owner_only(request, attempt)
        if attempt.status != TestAttempt.Status.IN_PROGRESS:
            raise ValidationError("This attempt is not in progress.")
        series = attempt.series
        payload = {}

        if series.delivery_mode == TestSeries.DeliveryMode.LIVE:
            session = getattr(series, "live_session", None)
            room = session.room_name if session else live.live_room_name(series.id)
            payload["live"] = {
                "room": room, "url": live.livekit_url(),
                "session_status": session.status if session else TestLiveSession.Status.SCHEDULED,
                "token": live.issue_token(
                    room_name=room, user_id=request.user.id, user_name=display_name(request.user),
                    role=live.Role.VIEWER,
                ),
            }

        if series.proctoring == TestSeries.Proctoring.CAMERA:
            room = live.proctor_room_name(attempt.id)
            live.ensure_room(room)
            with transaction.atomic():
                # Row lock: two quick taps / a retry must not start two recordings.
                locked = TestAttempt.objects.select_for_update().get(pk=attempt.pk)
                if not locked.recordings.filter(kind=TestRecording.Kind.PROCTOR).exists():
                    egress_id = live.start_recording(room)
                    TestRecording.objects.create(
                        series=series, attempt=locked, kind=TestRecording.Kind.PROCTOR,
                        room_name=room, egress_id=egress_id,
                    )
            payload["proctor"] = {
                "room": room, "url": live.livekit_url(),
                "token": live.issue_token(
                    room_name=room, user_id=request.user.id, user_name=display_name(request.user),
                    role=live.Role.CANDIDATE,
                ),
            }

        if not payload:
            raise ValidationError("This test has no live video or proctoring.")
        return Response(payload)

    @action(detail=True, methods=["post"], url_path="proctor-events")
    def proctor_events(self, request, pk=None):
        """Client-reported integrity signal (tab switch, face missing, ...).
        Advisory: it feeds the creator's review list, it never auto-fails a
        student. Capped per attempt so it can't be used to flood the table."""
        attempt = self.get_object()
        self._owner_only(request, attempt)
        if attempt.status != TestAttempt.Status.IN_PROGRESS:
            raise ValidationError("This attempt is not in progress.")
        ser = ProctorEventSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if attempt.proctor_events.count() >= MAX_PROCTOR_EVENTS_PER_ATTEMPT:
            return Response(status=status.HTTP_204_NO_CONTENT)
        event = ser.save(attempt=attempt)
        TestAttempt.objects.filter(pk=attempt.pk).update(integrity_flags=F("integrity_flags") + 1)
        return Response(ProctorEventSerializer(event).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def integrity(self, request, pk=None):
        """Creator / reviewer: flags + the proctor recording(s) for this attempt."""
        attempt = self.get_object()
        self._reviewer_only(request, attempt)
        events = attempt.proctor_events.all()
        summary = {row["event_type"]: row["n"] for row in events.values("event_type").annotate(n=Count("id"))}
        return Response(
            {
                "flags": attempt.integrity_flags,
                "submitted_late": attempt.submitted_late,
                "summary": summary,
                "events": ProctorEventSerializer(events, many=True).data,
                "recordings": TestRecordingSerializer(
                    attempt.recordings.filter(kind=TestRecording.Kind.PROCTOR), many=True
                ).data,
            }
        )

    @action(detail=True, methods=["post"], url_path="proctor-watch")
    def proctor_watch(self, request, pk=None):
        """Creator / reviewer: a subscribe-only token to WATCH this candidate live."""
        attempt = self.get_object()
        self._reviewer_only(request, attempt)
        if attempt.series.proctoring != TestSeries.Proctoring.CAMERA:
            raise ValidationError("This test is not proctored.")
        room = live.proctor_room_name(attempt.id)
        return Response(
            {
                "room": room, "url": live.livekit_url(),
                "token": live.issue_token(
                    room_name=room, user_id=request.user.id, user_name=display_name(request.user),
                    role=live.Role.VIEWER,
                ),
            }
        )


# =====================================================================
# Stand-alone views
# =====================================================================
class PublicSeriesView(generics.RetrieveAPIView):
    """`GET /testseries/public/<slug>/` — unauthenticated share-link preview.

    Marketing info only (title, price, duration, rating, certificate?) — no
    questions, options or answers. Campus series are NEVER exposed here (they
    belong to one institution); individual and live-class series are, so a
    creator can share a link on WhatsApp / social and the app can deep-link it.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [TestSeriesPublicPageThrottle]
    serializer_class = PublicSeriesSerializer
    lookup_field = "share_slug"
    lookup_url_kwarg = "slug"

    def get_queryset(self):
        return (
            TestSeries.objects.filter(status=TestSeries.Status.PUBLISHED)
            .exclude(source=TestSeries.Source.CAMPUS)
            .exclude(share_slug__isnull=True)
            .select_related("creator")
            .annotate(q_count=Count("questions", distinct=True))
        )


class CertificateVerifyView(generics.RetrieveAPIView):
    """`GET /testseries/certificates/verify/<code>/` — public, throttled. Lets
    an employer / institute confirm a certificate is genuine (and not revoked)
    without an account. Returns 404 for unknown codes; never enumerates."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [CertificateVerifyThrottle]
    serializer_class = CertificateVerifySerializer
    lookup_field = "code"
    queryset = TestCertificate.objects.select_related("student", "series")

    def get_object(self):
        code = str(self.kwargs["code"]).strip().upper()
        return get_object_or_404(self.get_queryset(), code=code)


class MyCertificatesView(generics.ListAPIView):
    """`GET /testseries/certificates/mine/` — the caller's own certificates."""

    permission_classes = [IsAuthenticated]
    serializer_class = TestCertificateSerializer

    def get_queryset(self):
        return TestCertificate.objects.filter(student=self.request.user).select_related("student", "series")


class LiveKitWebhookView(APIView):
    """`POST /testseries/livekit-webhook/` — LiveKit tells us an egress
    (recording) finished. Signature-verified (that IS the access control, so no
    auth / throttle — same reasoning as liveclass's own webhook). Events for
    egress ids that are not ours are ignored with 200 so LiveKit doesn't retry."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    def post(self, request):
        event = live.verify_webhook(request.body, request.headers.get("Authorization", ""))
        if event.event != "egress_ended":
            return Response(status=200)

        info = event.egress_info
        recording = TestRecording.objects.filter(egress_id=info.egress_id).first()
        if recording is None:
            return Response(status=200)

        results = list(info.file_results)
        url, seconds = "", None
        if results:
            first = results[0]
            url = getattr(first, "location", "") or getattr(first, "filename", "") or ""
            duration_ns = getattr(first, "duration", 0) or 0
            seconds = int(duration_ns / 1_000_000_000) if duration_ns else None

        recording.url = url[:500]
        recording.status = TestRecording.Status.READY if url else TestRecording.Status.FAILED
        recording.ended_at = timezone.now()
        recording.duration_seconds = seconds
        recording.save(update_fields=["url", "status", "ended_at", "duration_seconds"])
        return Response(status=200)


def close_proctor_room(attempt) -> None:
    """Best-effort: when a proctored attempt ends (submit, or the auto-submit
    task), stop its recording and close the candidate's room. Never raises —
    the attempt is already saved, and LiveKit closes an empty room by itself
    after `EMPTY_ROOM_TIMEOUT_SECS` anyway."""
    if attempt.series.proctoring != TestSeries.Proctoring.CAMERA:
        return
    try:
        for recording in attempt.recordings.filter(
            kind=TestRecording.Kind.PROCTOR, status=TestRecording.Status.RECORDING
        ):
            live.stop_recording(recording.egress_id)
        live.end_room(live.proctor_room_name(attempt.id))
    except Exception:  # noqa: BLE001
        logger.warning("Could not close proctor room for attempt %s", attempt.id, exc_info=True)
