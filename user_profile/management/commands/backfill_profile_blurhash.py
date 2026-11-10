"""
python manage.py backfill_profile_blurhash [--dry-run] [--username NAME] [--limit N] [--all]

P10b - give existing accounts the avatar placeholder (`User.profile_photo_blurhash`). New uploads get it automatically
(login.models `_refresh_profile_photo_blurhash`); this is for photos uploaded BEFORE the column existed.

  default      only users that HAVE a photo and an EMPTY hash
  --all        recompute for every user with a photo (use after changing the encoder)
  --username   one account
  --limit      stop after N users (default: no limit)
  --dry-run    count what would be done, change nothing

Safe to re-run. A photo that is missing from storage / cannot be decoded is skipped and counted, never fatal.
Writes only the one column (`update`), so signals / photo-cleanup receivers do not fire.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


def blurhash_for_stored_photo(user):
    """BlurHash string for `user.profile_photo` read from storage, or '' when it cannot be read / decoded."""
    from PIL import Image

    from post.services import blurhash_for_image

    try:
        with user.profile_photo.storage.open(user.profile_photo.name, "rb") as f:
            with Image.open(f) as img:
                return blurhash_for_image(img)[:40]
    except Exception:  # noqa: BLE001 - missing file / not an image
        return ""


class Command(BaseCommand):
    help = "Backfill User.profile_photo_blurhash for accounts whose photo was uploaded before the column existed (P10b)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--all", action="store_true", help="recompute even where a hash already exists")
        parser.add_argument("--username", default=None)
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, **opts):
        User = get_user_model()
        qs = User.objects.exclude(profile_photo="").exclude(profile_photo__isnull=True)
        if not opts["all"]:
            qs = qs.filter(profile_photo_blurhash="")
        if opts["username"]:
            qs = qs.filter(username__iexact=opts["username"])
        qs = qs.order_by("pk").only("pk", "username", "profile_photo", "profile_photo_blurhash")
        if opts["limit"]:
            qs = qs[: opts["limit"]]

        done = skipped = 0
        for user in qs.iterator(chunk_size=200):
            if opts["dry_run"]:
                done += 1
                continue
            h = blurhash_for_stored_photo(user)
            if not h:
                skipped += 1
                continue
            User.objects.filter(pk=user.pk).update(profile_photo_blurhash=h)
            done += 1

        verb = "would update" if opts["dry_run"] else "updated"
        self.stdout.write(f"{verb} {done} user(s); skipped (unreadable photo): {skipped}")
