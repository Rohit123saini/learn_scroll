"""
core/management/commands/seed_smoke.py  (P-J1)

Fixtures for scripts/smoke_test.sh -- STAGING ONLY.

Creates two throw-away adults (`smoke_user_a`, `smoke_user_b`) that share one password, plus their private
conversation (needed by the live-call token step). Idempotent: running it again re-uses the rows and re-writes the
fixtures file.

    python manage.py seed_smoke --confirm-staging --out /tmp/smoke_fixtures.json
    python manage.py seed_smoke --cleanup --confirm-staging

The fixtures JSON holds only what the shell script needs: usernames, the password, user ids and the private
conversation id. The smoke test still performs a REAL password login (POST /login/) -- tokens are not minted here.

SAFETY: refuses without --confirm-staging and refuses when DJANGO_ENV/ENVIRONMENT looks like production
(override: --i-know-this-is-production, do not use it).  Everything it creates is prefixed `smoke_user_`.
Written by reading the code only -- not run by the author.
"""
import argparse
import json
import os
from datetime import date
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

USER_PREFIX = "smoke_user_"
# Not a secret: the accounts only exist on staging and are deleted with --cleanup.
DEFAULT_PASSWORD = "SmokeTest!2026-not-a-secret"


def _has_field(model, name):
    return any(f.name == name for f in model._meta.get_fields())


class Command(BaseCommand):
    help = "Create (or --cleanup) the two smoke-test users + their private chat for scripts/smoke_test.sh."

    def add_arguments(self, parser):
        parser.add_argument("--confirm-staging", action="store_true",
                            help="required: you confirm this database is NOT production")
        parser.add_argument("--i-know-this-is-production", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--cleanup", action="store_true", help="delete the smoke users and their conversation")
        parser.add_argument("--out", default="smoke_fixtures.json", help="where to write the fixtures JSON")
        parser.add_argument("--password", default=os.environ.get("SMOKE_PASSWORD", DEFAULT_PASSWORD))

    def handle(self, *args, **opts):
        if not opts["confirm_staging"]:
            raise CommandError("Refusing to run without --confirm-staging.")
        env = (os.environ.get("DJANGO_ENV") or os.environ.get("ENVIRONMENT") or "").lower()
        if env in ("production", "prod") and not opts["i_know_this_is_production"]:
            raise CommandError("Environment looks like production. Run this against staging only.")
        if opts["cleanup"]:
            return self._cleanup()
        self._seed(Path(opts["out"]), opts["password"])

    # ------------------------------------------------------------------
    def _cleanup(self):
        from message.models import Conversation, ConversationParticipant

        User = get_user_model()
        with transaction.atomic():
            conv_ids = list(ConversationParticipant.objects.filter(user__username__startswith=USER_PREFIX)
                            .values_list("conversation_id", flat=True))
            Conversation.objects.filter(pk__in=conv_ids).delete()
            deleted, _ = User.objects.filter(username__startswith=USER_PREFIX).delete()
        self.stdout.write(self.style.SUCCESS("Cleanup done (%d rows incl. cascades)." % deleted))

    def _seed(self, out_path, password):
        from message.models import Conversation

        User = get_user_model()
        hashed = make_password(password)
        users = []
        for suffix in ("a", "b"):
            username = "%s%s" % (USER_PREFIX, suffix)
            user = User.objects.filter(username=username).first()
            if user is None:
                extra = {}
                if _has_field(User, "date_of_birth"):
                    extra["date_of_birth"] = date(2000, 1, 1)  # adult: minor-safety rules must not skew the run
                user = User.objects.create(username=username, email="%s@smoke.invalid" % username,
                                           password=hashed, **extra)
            else:
                # keep the password in sync with --password / SMOKE_PASSWORD
                user.password = hashed
                user.save(update_fields=["password"])
            users.append(user)

        conv, _created = Conversation.get_or_create_private(users[0], users[1])
        data = {
            "password": password,
            "user_a": {"id": users[0].pk, "username": users[0].username},
            "user_b": {"id": users[1].pk, "username": users[1].username},
            "conversation_id": str(conv.pk),
        }
        out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS("Smoke fixtures written to %s" % out_path))
