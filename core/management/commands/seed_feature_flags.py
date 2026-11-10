"""P-J2 - make the kill-switch panel usable on day one.

    python manage.py seed_feature_flags            # create the missing rows (ON, 100%, platform=all); never touches existing rows
    python manage.py seed_feature_flags --status   # print every flag as the app will see it (no changes)

Why a command and not a data migration: "no row" already means "default" (everything ON, cash-out OFF), so seeding is only a
convenience - the admin then sees one line per feature and flips a switch instead of creating a row under pressure during an
incident. A migration would also put rows into every test database, which would break the 'no row = default' tests.

Safe to run on every deploy (idempotent). A seeded `cashout` row is ON but harmless: cash-out stays OFF until the environment
says COIN_CASHOUT_ENABLED=true AND the row is ON (see deploy/COIN_CASHOUT_REENABLE.md).
"""
from django.core.management.base import BaseCommand

from common import feature_flags as ff
from core.models import FeatureFlag


class Command(BaseCommand):
    help = "Create the missing Feature flag rows (live, reels_upload, coin_purchase, ai_features, cashout) so admin can toggle them."

    def add_arguments(self, parser):
        parser.add_argument("--status", action="store_true", help="Only print the current state of every flag.")

    def handle(self, *args, **options):
        if not options["status"]:
            created = 0
            for key in ff.ALL_FLAGS:
                _, was_created = FeatureFlag.objects.get_or_create(
                    key=key,
                    platform=FeatureFlag.Platform.ALL,
                    defaults={"enabled": True, "rollout_percent": 100, "note": "seeded by seed_feature_flags"},
                )
                created += 1 if was_created else 0
            ff.invalidate_cache()
            self.stdout.write(self.style.SUCCESS(f"seed_feature_flags: {created} row(s) created, {len(ff.ALL_FLAGS) - created} already existed."))

        for key in ff.ALL_FLAGS:
            state = ff.evaluate(key)  # platform-less view: the 'all' row + environment
            mark = self.style.SUCCESS("ON ") if state.enabled else self.style.ERROR("OFF")
            self.stdout.write(f"  {key:<14} {mark}  ({state.reason})")
