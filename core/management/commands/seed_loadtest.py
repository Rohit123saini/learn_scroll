"""
core/management/commands/seed_loadtest.py — T3.6 load-test fixtures.

Creates, on a STAGING database only:
  * N users  `lt_user_0001 …`  (all share one pre-hashed password)
  * ONE group chat containing every user         -> chat WebSocket target
  * ONE classroom + free pass for every user + ONE LIVE session
                                                 -> live-session join target
  * a fixtures JSON with a valid access token per user (minted through
    login.token_issuance, so sliding-session rows exist exactly like a real
    login) + the ids the load scripts need.

Why tokens are minted here instead of logging in 1,000 times from the load
generator: the login endpoint is rate-limited and password hashing would
dominate the test; this isolates what we actually want to measure
(feed / chat / live-join).

    python manage.py seed_loadtest --users 1000 --confirm-staging --out loadtest/fixtures.json
    python manage.py seed_loadtest --cleanup --confirm-staging          # remove everything it made

SAFETY: refuses to run unless --confirm-staging is passed AND the process is not
flagged production (DJANGO_ENV/ENVIRONMENT=production) unless --i-know-this-is-production.
Everything it creates is prefixed `lt_` / "LOADTEST" so --cleanup can find it.
"""
import json
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

USER_PREFIX = "lt_user_"
GROUP_NAME = "LOADTEST group chat"
CLASSROOM_TITLE = "LOADTEST live classroom"
PASSWORD = "LoadTest!2026-not-a-secret"


def _has_field(model, name):
    return any(f.name == name for f in model._meta.get_fields())


class Command(BaseCommand):
    help = "Create (or --cleanup) load-test users, a group chat, a classroom and a live session."

    def add_arguments(self, parser):
        parser.add_argument("--users", type=int, default=1000)
        parser.add_argument("--out", default="loadtest/fixtures.json")
        parser.add_argument("--cleanup", action="store_true")
        parser.add_argument("--confirm-staging", action="store_true", help="Required: acknowledges this writes test data.")
        parser.add_argument("--i-know-this-is-production", action="store_true")

    # ------------------------------------------------------------------
    def handle(self, *args, **opts):
        if not opts["confirm_staging"]:
            raise CommandError("Refusing to run without --confirm-staging (this writes thousands of rows).")
        env = (os.environ.get("DJANGO_ENV") or os.environ.get("ENVIRONMENT") or "").lower()
        if env in ("production", "prod") and not opts["i_know_this_is_production"]:
            raise CommandError("Environment looks like production. Use a staging database.")

        if opts["cleanup"]:
            return self._cleanup()
        if not 1 <= opts["users"] <= 20000:
            raise CommandError("--users must be between 1 and 20000.")
        self._seed(opts["users"], Path(opts["out"]))

    # ------------------------------------------------------------------
    def _cleanup(self):
        from tuitionclass.models import Classroom
        from message.models import Conversation, Group

        User = get_user_model()
        with transaction.atomic():
            Classroom.objects.filter(title=CLASSROOM_TITLE).delete()
            convs = list(Group.objects.filter(name=GROUP_NAME).values_list("conversation_id", flat=True))
            Group.objects.filter(name=GROUP_NAME).delete()
            Conversation.objects.filter(pk__in=convs).delete()
            deleted, _ = User.objects.filter(username__startswith=USER_PREFIX).delete()
        self.stdout.write(self.style.SUCCESS(f"Cleanup done ({deleted} rows incl. cascades from users)."))

    # ------------------------------------------------------------------
    def _seed(self, n, out_path):
        from login.token_issuance import issue_tokens_for_user
        from message.models import Conversation, ConversationParticipant, ConversationType, Group, GroupMember
        from tuitionclass.models import ClassPass, ClassSession, Classroom, PassPurchase

        User = get_user_model()
        hashed = make_password(PASSWORD)  # hash ONCE; PBKDF2 per user would take minutes

        users = []
        for i in range(1, n + 1):
            username = f"{USER_PREFIX}{i:04d}"
            user = User.objects.filter(username=username).first()
            if user is None:
                extra = {}
                if _has_field(User, "date_of_birth"):
                    extra["date_of_birth"] = date(2000, 1, 1)  # adult: avoids minor-safety restrictions skewing results
                user = User.objects.create(username=username, email=f"{username}@loadtest.invalid", password=hashed, **extra)
            users.append(user)
        teacher = users[0]
        self.stdout.write(f"{len(users)} users ready.")

        with transaction.atomic():
            # ---- group chat --------------------------------------------------
            group = Group.objects.filter(name=GROUP_NAME).first()
            if group is None:
                conv = Conversation.objects.create(type=ConversationType.GROUP)
                group = Group.objects.create(conversation=conv, name=GROUP_NAME, created_by=teacher)
            conv = group.conversation
            have = set(GroupMember.objects.filter(group=group).values_list("user_id", flat=True))
            GroupMember.objects.bulk_create([
                GroupMember(group=group, user=u, role=GroupMember.Role.ADMIN if u == teacher else GroupMember.Role.MEMBER)
                for u in users if u.pk not in have
            ], batch_size=500)
            have_p = set(ConversationParticipant.objects.filter(conversation=conv).values_list("user_id", flat=True))
            ConversationParticipant.objects.bulk_create([
                ConversationParticipant(conversation=conv, user=u) for u in users if u.pk not in have_p
            ], batch_size=500)
            if _has_field(Group, "members_count"):
                Group.objects.filter(pk=group.pk).update(members_count=len(users))

            # ---- classroom + live session ------------------------------------
            classroom = Classroom.objects.filter(title=CLASSROOM_TITLE).first()
            if classroom is None:
                kwargs = {"teacher": teacher, "title": CLASSROOM_TITLE}
                if _has_field(Classroom, "max_participants"):
                    kwargs["max_participants"] = max(n + 100, 1000)  # don't let the waitlist hide the real join cost
                classroom = Classroom.objects.create(**kwargs)
            class_pass = ClassPass.objects.filter(classroom=classroom).first() or ClassPass.objects.create(
                classroom=classroom, pass_type=ClassPass.PassType.FREE, validity_days=365,
            )
            owned = set(PassPurchase.objects.filter(class_pass=class_pass).values_list("student_id", flat=True))
            now = timezone.now()
            PassPurchase.objects.bulk_create([
                PassPurchase(
                    student=u, class_pass=class_pass, amount_paid=Decimal("0"),
                    expires_at=now + timedelta(days=365), status=PassPurchase.Status.SUCCESS,
                )
                for u in users[1:] if u.pk not in owned
            ], batch_size=500)

            session = ClassSession.objects.filter(classroom=classroom, status=ClassSession.Status.LIVE).first()
            if session is None:
                session = ClassSession.objects.create(
                    classroom=classroom, scheduled_start=now - timedelta(minutes=5),
                    scheduled_end=now + timedelta(hours=6), status=ClassSession.Status.LIVE,
                )

        tokens = []
        for u in users:
            t = issue_tokens_for_user(u)
            tokens.append({"username": u.username, "user_id": str(u.pk), "access": t["access"], "refresh": t["refresh"]})

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({
            "generated_at": timezone.now().isoformat(),
            "conversation_id": str(conv.pk),
            "group_id": str(group.pk),
            "classroom_id": classroom.pk,
            "session_id": session.pk,
            "users": tokens,
        }, indent=1), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(
            f"Wrote {out_path} — {len(tokens)} tokens, conversation={conv.pk}, session={session.pk}. "
            f"Access tokens are valid for the configured ACCESS_TOKEN_LIFETIME (1 day). KEEP THIS FILE PRIVATE."
        ))
