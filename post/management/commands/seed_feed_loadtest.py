"""H2: feed data for the load test (STAGING ONLY).

`seed_loadtest` creates the 1,000 `lt_user_NNNN` accounts but no posts / follows / seen rows, so their feed
would be empty and the measured p95 meaningless. This adds feed-shaped data for exactly those users:

  * posts   `--posts-per-user` per user, spread over the last 30 days (so the 7-day trending window and the 30-day
            recommended window both have rows), random engagement counters, mixed text / image / video
  * follows each user follows `--follows` random others (ACCEPTED) -> the 60 % "following" pool
  * seen    each user has `--seen` PostView rows (is_counted=False, like POST /post/feed/seen/) -> exercises
            get_seen_post_ids and the exclude/Case(id__in=seen) SQL

Rows are created with bulk_create (no signals / notifications / fan-out). Everything is tagged so --cleanup can
remove it: posts carry content starting with "LOADTEST feed post"; follows/views are removed through the lt_ users.

    python manage.py seed_feed_loadtest --confirm-staging                     # 5 posts, 40 follows, 200 seen per user
    python manage.py seed_feed_loadtest --confirm-staging --posts-per-user 20 --seen 400
    python manage.py seed_feed_loadtest --cleanup --confirm-staging

Run `seed_loadtest` first. For honest numbers ALSO load the staging DB up to production-like size (rows in `posts`
and `post_views`): an index/plan that is fine at 5k posts can seq-scan at 2M.
"""
import os
import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

USER_PREFIX = "lt_user_"  # same prefix as core/management/commands/seed_loadtest.py
POST_TAG = "LOADTEST feed post"
CATEGORIES = ["general", "tech", "education", "news", "entertainment", "sports", "lifestyle", "business"]
POST_TYPES = ["text"] * 6 + ["image"] * 3 + ["video"]


class Command(BaseCommand):
    help = "Create (or --cleanup) posts, follows and seen-rows for the lt_user_* load-test accounts."

    def add_arguments(self, parser):
        parser.add_argument("--posts-per-user", type=int, default=5)
        parser.add_argument("--follows", type=int, default=40, help="accounts each user follows")
        parser.add_argument("--seen", type=int, default=200, help="PostView rows per user")
        parser.add_argument("--days", type=int, default=30, help="spread post creation over this many days")
        parser.add_argument("--seed", type=int, default=42, help="RNG seed (same data on every run)")
        parser.add_argument("--cleanup", action="store_true")
        parser.add_argument("--confirm-staging", action="store_true")
        parser.add_argument("--i-know-this-is-production", action="store_true")

    def handle(self, *args, **opts):
        if not opts["confirm_staging"]:
            raise CommandError("Refusing to run without --confirm-staging (this writes many thousands of rows).")
        env = (os.environ.get("DJANGO_ENV") or os.environ.get("ENVIRONMENT") or "").lower()
        if env in ("production", "prod") and not opts["i_know_this_is_production"]:
            raise CommandError("Environment looks like production. Use a staging database.")
        if opts["cleanup"]:
            return self._cleanup()
        self._seed(opts)

    # ------------------------------------------------------------------
    def _cleanup(self):
        from post.models import Post, PostView
        from user_profile.models import Follow

        User = get_user_model()
        lt_ids = list(User.objects.filter(username__startswith=USER_PREFIX).values_list("pk", flat=True))
        with transaction.atomic():
            views, _ = PostView.objects.filter(user_id__in=lt_ids).delete()
            follows, _ = Follow.objects.filter(follower_id__in=lt_ids).delete()
            posts, _ = Post.objects.filter(user_id__in=lt_ids, content__startswith=POST_TAG).delete()
        self.stdout.write(self.style.SUCCESS(f"Cleanup done: {posts} post rows (incl. cascades), {follows} follows, {views} views."))

    # ------------------------------------------------------------------
    def _seed(self, opts):
        from post.models import Post, PostView
        from user_profile.models import Follow

        User = get_user_model()
        users = list(User.objects.filter(username__startswith=USER_PREFIX).order_by("username"))
        if len(users) < 2:
            raise CommandError("No lt_user_* accounts - run `manage.py seed_loadtest --confirm-staging` first.")
        rng = random.Random(opts["seed"])
        now = timezone.now()
        span = max(1, opts["days"]) * 24 * 3600

        # ---- posts --------------------------------------------------------
        existing = Post.objects.filter(user__in=users, content__startswith=POST_TAG).count()
        if existing:
            self.stdout.write(self.style.WARNING(f"{existing} loadtest posts already exist - skipping post creation (use --cleanup first to redo)."))
            posts = list(Post.objects.filter(user__in=users, content__startswith=POST_TAG).values_list("pk", flat=True))
        else:
            new_posts = []
            for user in users:
                for i in range(max(0, opts["posts_per_user"])):
                    likes = int(rng.paretovariate(1.3)) - 1  # long tail: most posts ~0, a few popular
                    new_posts.append(Post(
                        user=user,
                        content=f"{POST_TAG} {user.username} #{i} " + "lorem ipsum dolor sit amet " * rng.randint(2, 12),
                        category=rng.choice(CATEGORIES),
                        post_type=rng.choice(POST_TYPES),
                        visibility="public",
                        likes_count=likes,
                        comments_count=int(likes * rng.random() * 0.4),
                        shares_count=int(likes * rng.random() * 0.1),
                        views_count=likes * rng.randint(5, 30),
                    ))
            Post.objects.bulk_create(new_posts, batch_size=1000)
            # auto_now_add forces created_at = now on INSERT; bulk_update (an UPDATE) lets us spread them out
            for post in new_posts:
                post.created_at = now - timedelta(seconds=rng.randint(0, span))
            Post.objects.bulk_update(new_posts, ["created_at"], batch_size=1000)
            posts = [p.pk for p in new_posts]
            self.stdout.write(f"{len(posts)} posts created.")

        # ---- follows ------------------------------------------------------
        follows = []
        k = min(opts["follows"], len(users) - 1)
        for user in users:
            for other in rng.sample(users, k + 1):
                if other.pk != user.pk:
                    follows.append(Follow(follower=user, following=other, status=Follow.Status.ACCEPTED))
        # `unique_follow` makes re-runs safe; a user that got k+1 picks and drew itself ends up with k
        Follow.objects.bulk_create(follows, batch_size=2000, ignore_conflicts=True)
        self.stdout.write(f"{len(follows)} follow rows submitted (existing pairs ignored).")

        # ---- seen rows ----------------------------------------------------
        views = []
        n_seen = min(opts["seen"], len(posts))
        for user in users:
            for pid in rng.sample(posts, n_seen):
                views.append(PostView(post_id=pid, user=user, is_counted=False))
        PostView.objects.bulk_create(views, batch_size=2000, ignore_conflicts=True)  # uniq_postview_post_user
        # viewed_at is auto_now_add too: spread it so the 30-day window / seen_cutoff logic sees a realistic history.
        # pk is a client-side uuid4, so these objects can be bulk_update'd even though bulk_create ignored conflicts
        # (a row that already existed is simply not matched).
        for v in views:
            v.viewed_at = now - timedelta(seconds=rng.randint(60, span))
        PostView.objects.bulk_update(views, ["viewed_at"], batch_size=1000)
        self.stdout.write(f"{len(views)} seen rows submitted.")
        self.stdout.write(self.style.SUCCESS(
            f"Feed seed done: {len(users)} users, {len(posts)} posts. Run ANALYZE on the staging DB before measuring."
        ))
