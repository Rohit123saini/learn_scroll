"""
python manage.py recount_follow_counts [--dry-run] [--username NAME] [--chunk 1000]

P10 - counts consistency. `User.followers_count` / `following_count` are kept right by the Follow signals
(user_profile/signals.py); this is the manual "make them right NOW" button for after a bulk import, a restore, or when
the daily `reconcile_follow_counts` task reported drift.

  --dry-run     only report how many users are wrong (and the first few), change nothing
  --username    check / fix one account
  --chunk       users per UPDATE (default 1000)

Uses the SAME single-statement recount as the signals (`signals.recompute_follow_counts`), so cache subscribers of
`follow_counts_recomputed` are told too. posts_count is NOT touched here (its rules - archived / reels / soft-delete -
live in post/signals.py).
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Count, F, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce

from user_profile.models import Follow
from user_profile.signals import recompute_follow_counts


def drifted_users(username=None):
    """Queryset of users whose stored counters differ from the real ACCEPTED Follow rows (annotated true_*)."""
    User = get_user_model()
    accepted = Follow.objects.filter(status=Follow.Status.ACCEPTED).order_by()
    followers_sq = accepted.filter(following_id=OuterRef("pk")).values("following_id").annotate(c=Count("pk")).values("c")
    following_sq = accepted.filter(follower_id=OuterRef("pk")).values("follower_id").annotate(c=Count("pk")).values("c")
    qs = User.objects.annotate(
        true_followers=Coalesce(Subquery(followers_sq, output_field=IntegerField()), 0),
        true_following=Coalesce(Subquery(following_sq, output_field=IntegerField()), 0),
    )
    if username:
        qs = qs.filter(username__iexact=username)
    # NOT (both equal)  ==  at least one of the two is wrong
    return qs.exclude(followers_count=F("true_followers"), following_count=F("true_following"))


class Command(BaseCommand):
    help = "Recompute followers_count / following_count from the real accepted Follow rows."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report only, change nothing.")
        parser.add_argument("--username", default=None, help="Only this account.")
        parser.add_argument("--chunk", type=int, default=1000)

    def handle(self, *args, **opts):
        chunk = max(1, int(opts["chunk"]))
        dry = bool(opts["dry_run"])
        total = 0
        last_id = 0
        shown = 0
        while True:
            rows = list(
                drifted_users(opts["username"]).filter(id__gt=last_id).order_by("id")
                .values_list("id", "username", "followers_count", "true_followers", "following_count", "true_following")[:chunk]
            )
            if not rows:
                break
            last_id = rows[-1][0]
            total += len(rows)
            for uid, name, fc, tf, gc, tg in rows:
                if shown < 10:
                    shown += 1
                    self.stdout.write(f"  @{name}: followers {fc} -> {tf}, following {gc} -> {tg}")
            if not dry:
                recompute_follow_counts([r[0] for r in rows])
        verb = "would fix" if dry else "fixed"
        self.stdout.write(self.style.SUCCESS(f"recount_follow_counts: {verb} {total} user(s)."))
