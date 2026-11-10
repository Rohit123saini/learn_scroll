"""
core/management/commands/seed_calls_loadtest.py — X2 fixtures: message calls + study rooms (see loadtest/CALLS_STUDYROOM_PLAN.md).

Creates, on a STAGING database only (everything is prefixed `lt_cs_` / "LOADTEST ..." so --cleanup finds it):

  * PAIRS      N disjoint 1:1 pairs (a, b) + their private conversation          -> CL1 / CL2 / CL5 / CL6 / CL7
               (disjoint on purpose: a user can be in ONE call at a time, `call_state.busy_status`)
  * BUSY SETS  K sets of (1 callee + C callers), one private conversation per caller<->callee
                                                                                  -> CL3 (A->B and C->B at the same instant)
  * GROUP CALL one group of `--group-call-size` members (default 51: the call cap is 50, the 51st is the `call_full` probe)
                                                                                  -> CL4
  * ROOMS      R study-room groups of `--room-size` members (default 50), member 0 = group ADMIN = room host
                                                                                  -> SR1 .. SR6
               Groups are PRIVATE by default: a PUBLIC group makes the first join answer 403 `rules_not_accepted`
               (STUDY_ROOM_RULES_REQUIRED_PUBLIC) and puts new sessions behind a waiting room
               (STUDY_ROOM_PUBLIC_GROUP_WAITING_ROOM). Pass --public to test exactly that gate (scenario SR6).
  * a fixtures JSON with one access token per account (minted through login.token_issuance, same reason as
    seed_loadtest: do not log in 1,500 times from the injector)

    python manage.py seed_calls_loadtest --confirm-staging --out loadtest/calls_fixtures.json
    python manage.py seed_calls_loadtest --reset-state --confirm-staging     # between two runs: end leftover calls / rooms
    python manage.py seed_calls_loadtest --cleanup --confirm-staging         # remove everything it made

Re-running tops up (idempotent). Fixture accounts get an ADULT date_of_birth when the User model has that field:
the minor-safety rules (call recording, DM study room, masked names in public rooms) would change what the endpoints
return and skew the measurement.

NOT RUN while writing (read-only authoring round). Field names were checked against message/models.py by reading.
"""
import json
import os
from datetime import date
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

PREFIX = "lt_cs_"
GROUP_CALL_NAME = "LOADTEST call group"
ROOM_NAME = "LOADTEST room {i:02d}"
ROOM_NAME_PREFIX = "LOADTEST room "
PASSWORD = "LoadTest!2026-not-a-secret"


def _has_field(model, name):
    return any(f.name == name for f in model._meta.get_fields())


class Command(BaseCommand):
    help = "Create (or --reset-state / --cleanup) load-test users, 1:1 pairs, a group call and study-room groups."

    def add_arguments(self, parser):
        parser.add_argument("--pairs", type=int, default=200, help="1:1 caller/callee pairs (2 users each)")
        parser.add_argument("--busy-sets", type=int, default=10, help="CL3 sets: 1 callee + --callers-per-set callers")
        parser.add_argument("--callers-per-set", type=int, default=3)
        parser.add_argument("--group-call-size", type=int, default=51, help="members of the CL4 group (cap is 50)")
        parser.add_argument("--rooms", type=int, default=20, help="study-room groups")
        parser.add_argument("--room-size", type=int, default=50, help="members per study-room group (member 0 = host)")
        parser.add_argument("--public", action="store_true", help="make the study-room groups PUBLIC (rules gate + waiting room, SR6)")
        parser.add_argument("--out", default="loadtest/calls_fixtures.json")
        parser.add_argument("--reset-state", action="store_true", help="end leftover calls / study-room sessions of lt_cs_ users")
        parser.add_argument("--cleanup", action="store_true")
        parser.add_argument("--confirm-staging", action="store_true", help="Required: acknowledges this writes test data.")
        parser.add_argument("--i-know-this-is-production", action="store_true")

    # ------------------------------------------------------------------
    def handle(self, *args, **o):
        if not o["confirm_staging"]:
            raise CommandError("Refusing to run without --confirm-staging (this writes thousands of rows).")
        env = (os.environ.get("DJANGO_ENV") or os.environ.get("ENVIRONMENT") or "").lower()
        if env in ("production", "prod") and not o["i_know_this_is_production"]:
            raise CommandError("Environment looks like production. Use a staging database.")
        if o["cleanup"]:
            return self._cleanup()
        if o["reset_state"]:
            return self._reset_state()
        ok = (0 <= o["pairs"] <= 5000 and 0 <= o["busy_sets"] <= 500 and 1 <= o["callers_per_set"] <= 20
              and 0 <= o["group_call_size"] <= 500 and 0 <= o["rooms"] <= 200 and 2 <= o["room_size"] <= 500)
        if not ok:
            raise CommandError("--pairs 0..5000, --busy-sets 0..500, --callers-per-set 1..20, "
                               "--group-call-size 0..500, --rooms 0..200, --room-size 2..500")
        self._seed(o, Path(o["out"]))

    # ------------------------------------------------------------------
    def _cleanup(self):
        from message.models import CallSession, Conversation, ConversationParticipant

        User = get_user_model()
        with transaction.atomic():
            conv_ids = list(ConversationParticipant.objects.filter(user__username__startswith=PREFIX)
                            .values_list("conversation_id", flat=True).distinct())
            CallSession.objects.filter(caller__username__startswith=PREFIX).delete()
            # Group.conversation is a OneToOne with CASCADE: deleting the conversation removes the group too.
            Conversation.objects.filter(pk__in=conv_ids).delete()
            deleted, _ = User.objects.filter(username__startswith=PREFIX).delete()
        self.stdout.write(self.style.SUCCESS(f"Cleanup done ({deleted} rows incl. cascades from users)."))

    # ------------------------------------------------------------------
    def _reset_state(self):
        """Between two runs: a call left RINGING / ONGOING makes its users 'busy' (409 already_in_call / callee_busy)
        until the sweeper runs, and an unended study-room session keeps its old host / settings."""
        from message.call_state import TERMINAL_STATUSES
        from message.models import CallParticipant, CallSession, CallStatus, ConversationParticipant, StudyRoomSession

        now = timezone.now()
        conv_ids = list(ConversationParticipant.objects.filter(user__username__startswith=PREFIX)
                        .values_list("conversation_id", flat=True).distinct())
        with transaction.atomic():
            calls = CallSession.objects.filter(caller__username__startswith=PREFIX).exclude(status__in=TERMINAL_STATUSES)
            call_ids = list(calls.values_list("id", flat=True))
            CallParticipant.objects.filter(call_id__in=call_ids, left_at__isnull=True).update(status=CallStatus.ENDED, left_at=now)
            n_calls = CallSession.objects.filter(id__in=call_ids).update(status=CallStatus.ENDED, ended_at=now)
            n_rooms = StudyRoomSession.objects.filter(conversation_id__in=conv_ids, ended_at__isnull=True).update(ended_at=now)
        self.stdout.write(self.style.SUCCESS(f"Reset: {n_calls} open call(s) ended, {n_rooms} study-room session(s) ended."))

    # ------------------------------------------------------------------
    def _users(self, n, offset, hashed):
        """`n` users lt_cs_<offset+1> .. lt_cs_<offset+n> (bulk create what is missing, return them in order)."""
        User = get_user_model()
        names = [f"{PREFIX}{offset + i + 1:05d}" for i in range(n)]
        have = set(User.objects.filter(username__in=names).values_list("username", flat=True))
        make = []
        for name in names:
            if name not in have:
                extra = {"date_of_birth": date(1995, 1, 1)} if _has_field(User, "date_of_birth") else {}
                make.append(User(username=name, email=f"{name}@loadtest.invalid", password=hashed, **extra))
        User.objects.bulk_create(make, batch_size=500)
        by = {u.username: u for u in User.objects.filter(username__in=names)}
        return [by[name] for name in names]

    def _group(self, name, members, public):
        """Group chat with `members[0]` as ADMIN. Returns the Conversation. Top-up safe."""
        from message.models import Conversation, ConversationParticipant, ConversationType, Group, GroupMember

        group = Group.objects.filter(name=name).first()
        if group is None:
            conv = Conversation.objects.create(type=ConversationType.GROUP)
            group = Group.objects.create(conversation=conv, name=name, created_by=members[0], is_private=not public)
        elif group.is_private == public:           # --public flag changed between runs
            Group.objects.filter(pk=group.pk).update(is_private=not public)
        conv = group.conversation
        have = set(GroupMember.objects.filter(group=group).values_list("user_id", flat=True))
        GroupMember.objects.bulk_create([
            GroupMember(group=group, user=u, role=GroupMember.Role.ADMIN if u == members[0] else GroupMember.Role.MEMBER)
            for u in members if u.pk not in have
        ], batch_size=500)
        have_p = set(ConversationParticipant.objects.filter(conversation=conv).values_list("user_id", flat=True))
        ConversationParticipant.objects.bulk_create([
            ConversationParticipant(conversation=conv, user=u) for u in members if u.pk not in have_p
        ], batch_size=500)
        if _has_field(Group, "members_count"):
            Group.objects.filter(pk=group.pk).update(members_count=len(members))
        return conv

    # ------------------------------------------------------------------
    def _seed(self, o, out_path):
        from login.token_issuance import issue_tokens_for_user
        from message.models import Conversation

        hashed = make_password(PASSWORD)  # hash ONCE; PBKDF2 per user would take minutes
        n_pairs, n_busy, per_set = o["pairs"], o["busy_sets"], o["callers_per_set"]
        n_group, n_rooms, room_size = o["group_call_size"], o["rooms"], o["room_size"]

        total = 2 * n_pairs + n_busy * (1 + per_set) + n_group + n_rooms * room_size
        users = self._users(total, 0, hashed)
        self.stdout.write(f"{len(users)} users ready.")
        cursor = 0

        def take(n):
            nonlocal cursor
            chunk = users[cursor:cursor + n]
            cursor += n
            return chunk

        tokens = {}

        def tok(u):
            if u.pk not in tokens:
                t = issue_tokens_for_user(u)
                tokens[u.pk] = {"username": u.username, "user_id": str(u.pk), "access": t["access"], "refresh": t["refresh"]}
            return tokens[u.pk]

        out = {"pairs": [], "busy_sets": [], "group_call": None, "rooms": []}
        with transaction.atomic():
            for _ in range(n_pairs):
                a, b = take(2)
                conv, _created = Conversation.get_or_create_private(a, b)
                out["pairs"].append({"conversation_id": str(conv.pk), "a": a, "b": b})
            for _ in range(n_busy):
                callee, *callers = take(1 + per_set)
                convs = [str(Conversation.get_or_create_private(c, callee)[0].pk) for c in callers]
                out["busy_sets"].append({"callee": callee, "callers": callers, "conversation_ids": convs})
            if n_group:
                members = take(n_group)
                out["group_call"] = {"conversation_id": str(self._group(GROUP_CALL_NAME, members, public=False).pk), "members": members}
            for i in range(n_rooms):
                members = take(room_size)
                conv = self._group(ROOM_NAME.format(i=i + 1), members, public=o["public"])
                out["rooms"].append({"conversation_id": str(conv.pk), "host": members[0], "members": members})

        # tokens AFTER the transaction (token issuance writes its own session rows)
        fx = {
            "generated_at": timezone.now().isoformat(),
            "public_rooms": bool(o["public"]),
            "pairs": [{"conversation_id": p["conversation_id"], "a": tok(p["a"]), "b": tok(p["b"])} for p in out["pairs"]],
            "busy_sets": [{"callee": tok(s["callee"]), "callers": [tok(c) for c in s["callers"]],
                           "conversation_ids": s["conversation_ids"]} for s in out["busy_sets"]],
            "group_call": None if out["group_call"] is None else {
                "conversation_id": out["group_call"]["conversation_id"],
                "caller": tok(out["group_call"]["members"][0]),
                "members": [tok(u) for u in out["group_call"]["members"]],
            },
            "rooms": [{"conversation_id": r["conversation_id"], "host": tok(r["host"]),
                       "members": [tok(u) for u in r["members"]]} for r in out["rooms"]],
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(fx, indent=1), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(
            f"Wrote {out_path} — {len(fx['pairs'])} pairs, {len(fx['busy_sets'])} busy sets, "
            f"group call: {'yes' if fx['group_call'] else 'no'}, {len(fx['rooms'])} rooms x {room_size}. "
            f"Access tokens are valid for the configured ACCESS_TOKEN_LIFETIME. KEEP THIS FILE PRIVATE."
        ))
