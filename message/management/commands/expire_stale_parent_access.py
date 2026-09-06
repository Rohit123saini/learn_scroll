# message/management/commands/expire_stale_parent_access.py
"""
Optional periodic cleanup (run via cron / celery-beat, e.g. daily).

NOT load-bearing for security — `HasValidParentToken` already rejects
expired codes/tokens live, on every request, regardless of whether this
command has ever run. This is purely DB hygiene:

  1. Delete `ParentToken` rows that have been inactive past their own
     30-day TTL for a while (grace period before hard-delete, so a
     just-expired device doesn't vanish from the "devices" list the
     instant it crosses the line — the student can still see it did
     exist).
  2. Deactivate `ParentAccessCode`s that expired long ago and were
     never renewed — keeps the "Manage parent access" list from
     accumulating ancient dead codes indefinitely.

Usage: python manage.py expire_stale_parent_access
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from message.models import ParentAccessCode, ParentToken


class Command(BaseCommand):
    help = "Clean up long-stale parent-access tokens/codes (DB hygiene only)."

    # Grace window past each item's own expiry before we actually delete/
    # deactivate — keeps recently-expired items visible in the UI for a
    # bit instead of disappearing the moment they cross the TTL line.
    TOKEN_DELETE_GRACE_DAYS = 14
    CODE_DEACTIVATE_GRACE_DAYS = 30

    def handle(self, *args, **options):
        now = timezone.now()

        stale_token_cutoff = now - timedelta(
            days=ParentToken.INACTIVITY_TTL_DAYS + self.TOKEN_DELETE_GRACE_DAYS
        )
        # last_seen_at can be null (never used after verify) — fall back
        # to created_at in that case, same as `ParentToken.is_expired`.
        stale_tokens = ParentToken.objects.filter(last_seen_at__lt=stale_token_cutoff) | \
            ParentToken.objects.filter(last_seen_at__isnull=True, created_at__lt=stale_token_cutoff)
        deleted_count, _ = stale_tokens.distinct().delete()

        code_cutoff = now - timedelta(days=self.CODE_DEACTIVATE_GRACE_DAYS)
        deactivated_count = ParentAccessCode.objects.filter(
            is_active=True, expires_at__lt=code_cutoff,
        ).update(is_active=False)

        self.stdout.write(self.style.SUCCESS(
            f"Deleted {deleted_count} stale parent tokens, "
            f"deactivated {deactivated_count} long-expired parent codes."
        ))