"""
social_learning/views.py - REST API of P-F5.  Everything is under /social/ (see urls.py). All views need login and are
behind SOCIAL_LEARNING_ENABLED (off => 403 {"code": "feature_disabled", "feature": "social_learning"}).

Error body is always {"detail": "...", "code": "<machine code>"} so the app can translate it.
"""
import logging
import uuid

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common import minor_policy
from message import study_room_roles as sr_roles
from message.models import Conversation, ConversationParticipant, ConversationType
from message.study_room_session import get_active_session
from message.user_display import get_display_name, get_profile_photo_url

from . import accountability as acc
from . import conf, pomodoro, seasons
from .models import AccountabilityPair, SeasonReward, SeasonStanding, StudySeason
from .throttles import (
    SocialNudgeThrottle,
    SocialPartnerRequestThrottle,
    SocialReadThrottle,
    SocialWriteThrottle,
)

logger = logging.getLogger(__name__)
User = get_user_model()


class FeatureDisabled(APIException):
    status_code = 403
    default_detail = {"detail": "Social learning is switched off right now.", "code": "feature_disabled", "feature": "social_learning"}


def _err(detail, code, status=400):
    return Response({"detail": detail, "code": code}, status=status)


def _uuid_or_none(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _bool(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


class SocialView(APIView):
    permission_classes = [IsAuthenticated]
    write_throttle = SocialWriteThrottle

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not conf.enabled():
            raise FeatureDisabled()

    def get_throttles(self):
        if self.request is not None and self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [SocialReadThrottle()]
        return [self.write_throttle()]


def _person(user, request):
    return {
        "user_id": user.id,
        "username": user.username,
        "name": get_display_name(user),
        "photo": get_profile_photo_url(user, request),
    }


# ======================================================================================================================
# Pomodoro (study room)
# ======================================================================================================================
def _room_ctx(request, conversation_id):
    """(conversation, role) or an error Response. Must be an active member of the conversation and not removed."""
    conversation = Conversation.objects.filter(id=conversation_id).first()
    if conversation is None:
        return None, None, _err("Room not found.", "room_not_found", 404)
    if not ConversationParticipant.objects.filter(conversation=conversation, user=request.user, left_at__isnull=True).exists():
        return None, None, _err("You are not in this room.", "not_in_room", 403)
    role, _policy = sr_roles.socket_role_info(conversation.id, request.user.id)
    if role is None:
        return None, None, _err("You cannot use this room right now.", "removed_from_room", 403)
    return conversation, role, None


def _can_control(conversation, role) -> bool:
    if sr_roles.is_manager(role):
        return True
    if role == sr_roles.Role.VIEWER:
        return False
    if conversation.type != ConversationType.GROUP:        # 1:1 study-with-me: both people share the timer
        return True
    return conf.pomodoro_participants_can_control()


def _timer_response(conversation, role, timer, extra=None):
    body = {
        "timer": pomodoro.payload(timer, focusing_now=pomodoro.focusing_now(conversation.id)),
        "can_control": _can_control(conversation, role),
    }
    if extra:
        body.update(extra)
    return Response(body)


class PomodoroStateView(SocialView):
    """GET /social/pomodoro/<conversation_id>/ - the shared timer (rolls a due phase forward first)."""

    def get(self, request, conversation_id):
        conversation, role, error = _room_ctx(request, conversation_id)
        if error:
            return error
        timer = pomodoro.read_state(conversation)
        return _timer_response(conversation, role, timer)


class PomodoroControlView(SocialView):
    """POST /social/pomodoro/<conversation_id>/<action>/   action = start | pause | resume | skip | stop
    start also takes optional {focus_minutes, short_break_minutes, long_break_minutes, cycles_before_long}."""

    ACTIONS = ("start", "pause", "resume", "skip", "stop")

    def post(self, request, conversation_id, action):
        if action not in self.ACTIONS:
            return _err("Unknown action.", "bad_action", 404)
        conversation, role, error = _room_ctx(request, conversation_id)
        if error:
            return error
        if not _can_control(conversation, role):
            return _err("Only the host can control the timer in this room.", "not_allowed", 403)
        try:
            if action == "start":
                if get_active_session(conversation.id) is None:
                    return _err("Start the study room first.", "no_active_session", 409)
                timer = pomodoro.start(conversation.id, request.user, request.data)
            elif action == "pause":
                timer = pomodoro.pause(conversation.id, request.user)
            elif action == "resume":
                timer = pomodoro.resume(conversation.id, request.user)
            elif action == "skip":
                timer = pomodoro.skip(conversation.id, request.user)
            else:
                timer = pomodoro.stop(conversation.id, request.user)
        except pomodoro.PomodoroError as exc:
            return _err(exc.detail, exc.code, exc.http_status)
        return _timer_response(conversation, role, timer)


class PomodoroClaimView(SocialView):
    """POST /social/pomodoro/claim/ {round_id} - claim credit for a finished focus round (idempotent)."""

    def post(self, request):
        round_id = _uuid_or_none(request.data.get("round_id"))
        if round_id is None:
            return _err("round_id is required.", "bad_round", 400)
        try:
            credited, reason, completion = pomodoro.claim_round(request.user, round_id)
        except pomodoro.PomodoroError as exc:
            return _err(exc.detail, exc.code, exc.http_status)
        return Response({"credited": credited, "reason": reason, "minutes": completion.minutes if completion else 0,
                         "stats": pomodoro.my_stats(request.user)})


class PomodoroStatsView(SocialView):
    """GET /social/pomodoro/stats/ - my pomodoros today / this week / total."""

    def get(self, request):
        return Response(pomodoro.my_stats(request.user))


# ======================================================================================================================
# Accountability partner
# ======================================================================================================================
def _pair_card(pair, viewer, request, with_days=False):
    other = pair.partner if str(pair.requester_id) == str(viewer.id) else pair.requester
    card = {
        "id": str(pair.id),
        "status": pair.status,
        "direction": "outgoing" if str(pair.requester_id) == str(viewer.id) else "incoming",
        "daily_goal_minutes": pair.daily_goal_minutes,
        "partner": _person(other, request),
        "created_at": pair.created_at,
    }
    if pair.status == AccountabilityPair.Status.ACTIVE:
        ov = acc.overview(pair, viewer)
        if not with_days:
            ov = {k: v for k, v in ov.items() if k != "days"}
        card.update(ov)
        card["nudge_templates"] = list(acc.NUDGE_TEMPLATES)
    return card


def _pair_or_404(request, pair_id):
    pair = AccountabilityPair.objects.select_related("requester", "partner").filter(id=pair_id).first()
    if pair is None or not pair.includes(request.user.id):
        return None
    from user_profile.services import is_blocked_pair

    if is_blocked_pair(request.user, pair.other_user_id(request.user.id)):
        return None
    return pair


class PartnerListView(SocialView):
    """GET /social/partners/ - {active: [...], incoming: [...], outgoing: [...], limits}."""

    def get(self, request):
        S = AccountabilityPair.Status
        active, incoming, outgoing = [], [], []
        for pair in acc.pairs_for(request.user):
            card = _pair_card(pair, request.user, request)
            if pair.status == S.ACTIVE:
                active.append(card)
            elif card["direction"] == "incoming":
                incoming.append(card)
            else:
                outgoing.append(card)
        prefs = acc.prefs_for(request.user.id)
        return Response({
            "active": active, "incoming": incoming, "outgoing": outgoing,
            "limits": {"max_active": conf.partner_max_active(), "max_pending": conf.partner_max_pending_out(),
                       "goal_min": acc.GOAL_MIN, "goal_max": acc.GOAL_MAX, "manual_max_minutes": conf.partner_manual_max_minutes()},
            "accepts_requests": prefs.accepts_partner_requests,
        })


class PartnerSuggestionsView(SocialView):
    """GET /social/partners/suggestions/?q= - people I may send a request to (accepted follow link, rules already applied)."""

    def get(self, request):
        from user_profile.models import Follow
        from user_profile.services import blocked_user_ids

        me = request.user
        ids = set()
        for a, b in Follow.objects.filter(status=Follow.Status.ACCEPTED).filter(Q(follower=me) | Q(following=me)).values_list("follower_id", "following_id")[:400]:
            ids.add(b if str(a) == str(me.id) else a)
        ids.discard(me.id)
        ids -= set(blocked_user_ids(me))
        live = AccountabilityPair.objects.filter(status__in=["pending", "active"]).filter(Q(requester=me) | Q(partner=me))
        for r, p in live.values_list("requester_id", "partner_id"):
            ids.discard(r)
            ids.discard(p)
        from .models import SocialPrefs

        ids -= set(SocialPrefs.objects.filter(user_id__in=list(ids), accepts_partner_requests=False).values_list("user_id", flat=True))
        qs = User.objects.filter(id__in=list(ids), is_active=True)
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))
        return Response({"results": [_person(u, request) for u in qs.order_by("first_name", "username")[:50]]})


class PartnerRequestView(SocialView):
    """POST /social/partners/request/ {user_id, daily_goal_minutes?}"""

    write_throttle = SocialPartnerRequestThrottle

    def post(self, request):
        target_id = request.data.get("user_id")
        try:
            target = User.objects.filter(pk=target_id).first()
        except (TypeError, ValueError):
            target = None
        try:
            pair = acc.request_pair(request.user, target, request.data.get("daily_goal_minutes"))
        except acc.PartnerError as exc:
            return _err(exc.detail, exc.code, exc.http_status)
        return Response(_pair_card(pair, request.user, request), status=201)


class PartnerDetailView(SocialView):
    """GET /social/partners/<pair_id>/ - one pair with the last 14 days for both."""

    def get(self, request, pair_id):
        pair = _pair_or_404(request, pair_id)
        if pair is None:
            return _err("Pair not found.", "not_found", 404)
        return Response(_pair_card(pair, request.user, request, with_days=True))


class PartnerRespondView(SocialView):
    """POST /social/partners/<pair_id>/respond/ {accept: bool}"""

    def post(self, request, pair_id):
        pair = _pair_or_404(request, pair_id)
        if pair is None:
            return _err("Pair not found.", "not_found", 404)
        try:
            pair = acc.respond(pair, request.user, _bool(request.data.get("accept")))
        except acc.PartnerError as exc:
            return _err(exc.detail, exc.code, exc.http_status)
        return Response(_pair_card(pair, request.user, request))


class PartnerGoalView(SocialView):
    """POST /social/partners/<pair_id>/goal/ {daily_goal_minutes}"""

    def post(self, request, pair_id):
        pair = _pair_or_404(request, pair_id)
        if pair is None:
            return _err("Pair not found.", "not_found", 404)
        try:
            pair = acc.set_goal(pair, request.user, request.data.get("daily_goal_minutes"))
        except acc.PartnerError as exc:
            return _err(exc.detail, exc.code, exc.http_status)
        return Response(_pair_card(pair, request.user, request))


class PartnerNudgeView(SocialView):
    """POST /social/partners/<pair_id>/nudge/ {template}  - fixed templates only (no free text)."""

    write_throttle = SocialNudgeThrottle

    def post(self, request, pair_id):
        pair = _pair_or_404(request, pair_id)
        if pair is None:
            return _err("Pair not found.", "not_found", 404)
        try:
            acc.send_nudge(pair, request.user, request.data.get("template") or "keep_going")
        except acc.PartnerError as exc:
            return _err(exc.detail, exc.code, exc.http_status)
        return Response({"sent": True})


class PartnerEndView(SocialView):
    """POST /social/partners/<pair_id>/end/ - either side, any time (also cancels a pending request / declines)."""

    def post(self, request, pair_id):
        pair = _pair_or_404(request, pair_id)
        if pair is None:
            return _err("Pair not found.", "not_found", 404)
        try:
            acc.end_pair(pair, request.user)
        except acc.PartnerError as exc:
            return _err(exc.detail, exc.code, exc.http_status)
        return Response({"ended": True})


class StudyLogView(SocialView):
    """GET/POST /social/study-log/ - today's "I studied offline" minutes (counts for the partner goal only)."""

    def get(self, request):
        from .models import ManualStudyLog

        today = timezone.localdate()
        row = ManualStudyLog.objects.filter(user=request.user, date=today).first()
        return Response({"date": today.isoformat(), "minutes": row.minutes if row else 0, "max_minutes": conf.partner_manual_max_minutes()})

    def post(self, request):
        try:
            row = acc.log_manual_minutes(request.user, request.data.get("minutes"))
        except acc.PartnerError as exc:
            return _err(exc.detail, exc.code, exc.http_status)
        return Response({"date": row.date.isoformat(), "minutes": row.minutes, "max_minutes": conf.partner_manual_max_minutes()})


# ======================================================================================================================
# Seasons
# ======================================================================================================================
def _standing_rows(viewer, request, rows):
    """Rows -> JSON. A minor is masked (first name only, no photo, no id) for adult strangers, same rule as other boards."""
    rows = list(rows)
    masked = minor_policy.masked_ids_for_viewer(viewer, [r.user_id for r in rows], feature=minor_policy.LEADERBOARD)
    out = []
    for r in rows:
        u = r.user
        if r.user_id in masked:
            person = {"user_id": None, **minor_policy.masked_person(u), "masked": True}
            person["name"] = person.pop("full_name")
            person["photo"] = person.pop("profile_picture")
        else:
            person = {**_person(u, request), "masked": False}
        out.append({"rank": r.rank, "score": r.score, "minutes": r.minutes, "pomodoros": r.pomodoros,
                    "active_days": r.active_days, "is_me": str(r.user_id) == str(viewer.id), **person})
    return out


def _season_json(season, now=None):
    now = now or timezone.now()
    return {
        "id": str(season.id), "number": season.number, "title": season.title, "status": season.status,
        "starts_at": season.starts_at, "ends_at": season.ends_at,
        "seconds_left": max(0, int((season.ends_at - now).total_seconds())),
        "rules": seasons.public_rules(season),
    }


def _board(viewer, request, season, limit, offset):
    from user_profile.services import blocked_user_ids

    qs = SeasonStanding.objects.filter(season=season).select_related("user")
    blocked = set(blocked_user_ids(viewer))
    if blocked:
        qs = qs.exclude(user_id__in=blocked)
    return _standing_rows(viewer, request, qs.order_by("rank")[offset:offset + limit])


def _limit_offset(request, default=20, maximum=100):
    try:
        limit = max(1, min(int(request.query_params.get("limit", default)), maximum))
    except (TypeError, ValueError):
        limit = default
    try:
        offset = max(0, int(request.query_params.get("offset", 0)))
    except (TypeError, ValueError):
        offset = 0
    return limit, offset


class SeasonCurrentView(SocialView):
    """GET /social/seasons/current/ - the running season (or the next one), my standing and the top of the board."""

    def get(self, request):
        now = timezone.now()
        season = seasons.current_season(now)
        upcoming = None
        if season is None:
            upcoming = seasons.next_season_after(now)
            if upcoming is None and conf.season_auto_create():
                seasons.tick(now)                            # first ever request on a fresh install
                season = seasons.current_season(now)
                upcoming = seasons.next_season_after(now) if season is None else None
        prefs = acc.prefs_for(request.user.id)
        if season is None:
            return Response({"season": None, "upcoming": _season_json(upcoming, now) if upcoming else None, "opted_out": prefs.season_opt_out})
        limit, _ = _limit_offset(request, default=20)
        return Response({
            "season": _season_json(season, now),
            "me": None if prefs.season_opt_out else seasons.my_standing(season, request.user),
            "top": _board(request.user, request, season, limit, 0),
            "opted_out": prefs.season_opt_out,
            "upcoming": None,
        })


class SeasonStandingsView(SocialView):
    """GET /social/seasons/<season_id>/standings/?limit=&offset="""

    def get(self, request, season_id):
        season = StudySeason.objects.filter(id=season_id).first()
        if season is None:
            return _err("Season not found.", "not_found", 404)
        limit, offset = _limit_offset(request)
        return Response({"season": _season_json(season), "results": _board(request.user, request, season, limit, offset),
                         "me": seasons.my_standing(season, request.user)})


class SeasonHistoryView(SocialView):
    """GET /social/seasons/history/ - closed seasons with my final rank and what I won."""

    def get(self, request):
        closed = list(StudySeason.objects.filter(status=StudySeason.Status.CLOSED).order_by("-starts_at")[:24])
        mine = {s.season_id: s for s in SeasonStanding.objects.filter(user=request.user, season__in=closed)}
        rewards = {r.season_id: r for r in SeasonReward.objects.filter(user=request.user, season__in=closed).select_related("season")}
        out = []
        for s in closed:
            st, rw = mine.get(s.id), rewards.get(s.id)
            out.append({
                "id": str(s.id), "number": s.number, "title": s.title, "starts_at": s.starts_at, "ends_at": s.ends_at,
                "my_rank": st.rank if st else None, "my_score": st.score if st else None,
                "reward": _reward_json(rw) if rw else None,
            })
        return Response({"results": out})


def _reward_json(rw):
    return {"season_id": str(rw.season_id), "season_number": rw.season.number if hasattr(rw, "season") else None,
            "tier": rw.tier, "rank": rw.rank, "score": rw.score, "badge_code": rw.badge_code,
            "freezes_granted": rw.freezes_granted, "granted_at": rw.granted_at, "seen": rw.seen_at is not None}


class SeasonRewardsView(SocialView):
    """GET /social/seasons/rewards/ - my rewards (newest first). POST marks them all as seen (so the 'you won' sheet shows once)."""

    def get(self, request):
        rows = SeasonReward.objects.filter(user=request.user).select_related("season").order_by("-granted_at")[:50]
        return Response({"results": [_reward_json(r) for r in rows], "unseen": sum(1 for r in rows if r.seen_at is None)})

    def post(self, request):
        n = SeasonReward.objects.filter(user=request.user, seen_at__isnull=True).update(seen_at=timezone.now())
        return Response({"marked": n})


class SocialPrefsView(SocialView):
    """GET / PUT /social/prefs/ {season_opt_out?, accepts_partner_requests?, partner_reminders?}"""

    @staticmethod
    def _json(p):
        return {"season_opt_out": p.season_opt_out, "accepts_partner_requests": p.accepts_partner_requests, "partner_reminders": p.partner_reminders}

    def get(self, request):
        return Response(self._json(acc.prefs_for(request.user.id)))

    def put(self, request):
        prefs = acc.prefs_for(request.user.id)
        fields = []
        for key in ("season_opt_out", "accepts_partner_requests", "partner_reminders"):
            if key in request.data:
                setattr(prefs, key, _bool(request.data.get(key)))
                fields.append(key)
        if fields:
            prefs.save(update_fields=fields + ["updated_at"])
        if prefs.season_opt_out:                              # leave the running board at once, not at the next tick
            SeasonStanding.objects.filter(user=request.user, is_final=False).delete()
        return Response(self._json(prefs))

    patch = put


class SocialConfigView(SocialView):
    """GET /social/config/ - what the app needs before it draws the entry points."""

    def get(self, request):
        return Response({"enabled": True, "pomodoro": {"min_focus": pomodoro.MIN_FOCUS, "max_focus": pomodoro.MAX_FOCUS,
                                                         "min_break": pomodoro.MIN_BREAK, "max_break": pomodoro.MAX_BREAK,
                                                         "min_cycles": pomodoro.MIN_CYCLES, "max_cycles": pomodoro.MAX_CYCLES},
                         "partner": {"goal_min": acc.GOAL_MIN, "goal_max": acc.GOAL_MAX, "default_goal": acc.DEFAULT_GOAL}})
