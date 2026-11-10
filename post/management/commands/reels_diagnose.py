"""Why is Reels empty (or short) for a user? Prints the candidate-pool size after
EVERY filter, then the size of each fallback tier. A big drop between two lines is
the filter that empties the feed.

    python manage.py reels_diagnose --user alice          # username, e-mail or id
    python manage.py reels_diagnose                       # first active user
    python manage.py reels_diagnose --user alice --ignore-seen

Read-only. Uses the same config (settings.FEED_REELS) and the same base queryset
as GET /post/reels/.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from post import feed_mix, reels
from post.views import _home_base_qs, _video_and_velocity_boost

User = get_user_model()


class Command(BaseCommand):
    help = "Print the Reels candidate pool size after each filter (which filter empties Reels)."

    def add_arguments(self, parser):
        parser.add_argument("--user", help="username, e-mail or id of the viewer (default: first active user)")
        parser.add_argument("--ignore-seen", action="store_true", help="do not subtract already-seen videos")

    def _user(self, ident):
        if not ident:
            user = User.objects.filter(is_active=True).order_by("id").first()
            if user is None:
                raise CommandError("No users found.")
            return user
        q = Q(username=ident) | Q(email__iexact=ident)
        if str(ident).isdigit():
            q |= Q(pk=int(ident))
        user = User.objects.filter(q).first()
        if user is None:
            raise CommandError(f"User {ident!r} not found.")
        return user

    def handle(self, *args, **opts):
        user = self._user(opts.get("user"))
        cfg = reels.get_config()
        seen = set() if opts["ignore_seen"] else feed_mix.get_seen_post_ids(user)
        self.stdout.write(self.style.MIGRATE_HEADING(f"Reels diagnose for {user}"))
        self.stdout.write(
            f"config: enabled={cfg['enabled']} min_aspect={cfg['min_aspect']} "
            f"max_duration={cfg['max_duration_seconds']}s fallback={cfg['fallback']} "
            f"min_pool={cfg['min_pool']} pool_cap={cfg['pool_cap']} "
            f"include_own_last_tier={cfg['include_own_last_tier']}  seen={len(seen)}"
        )
        if not cfg["enabled"]:
            self.stdout.write(self.style.WARNING("FEED_REELS['enabled'] is False -> endpoint returns an empty page."))
        prev = None
        for label, count in reels.diagnose(user, _home_base_qs(user), cfg, seen_ids=seen):
            note = ""
            if isinstance(count, int) and isinstance(prev, int) and prev > 0 and count < prev:
                note = f"   (-{prev - count})"
            self.stdout.write(f"{label:<62} {count}{note}")
            prev = count if isinstance(count, int) else None
        pool = reels.build_pool_ids(user, _home_base_qs(user), _video_and_velocity_boost, seen_ids=seen)
        style = self.style.SUCCESS if pool else self.style.ERROR
        self.stdout.write(style(f"\nFinal pool served to the user: {len(pool)} reels"))
        own_total = reels.own_candidate_queryset(user, cfg).count()
        if not pool:
            self.stdout.write(self.style.WARNING(
                "Empty pool. Read the list above top to bottom: the first line whose number drops to 0 is the "
                "filter that removed everything (e.g. '+ not own' = all videos belong to this user)."
            ))
        if own_total and not cfg["include_own_last_tier"]:
            self.stdout.write(self.style.WARNING(
                f"This user has {own_total} own public video(s) that are never shown. Set "
                "FEED_REELS['include_own_last_tier']=True (env REELS_INCLUDE_OWN_LAST_TIER=1) to add them as the last tier."
            ))
