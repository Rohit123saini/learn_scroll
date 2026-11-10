"""
manage.py backfill_hls_low - P-LEFT (P-E2 leftover).

Old videos that already have an HLS ladder but were encoded before `metadata["hls"]["rungs"]` existed get `hls_low_url = null`
in the API, so the app skips the quick low-rung first frame. NO re-encoding is needed: the rungs are listed in the master
playlist that is already in storage. This command reads each master.m3u8, sorts the variants by BANDWIDTH (lowest first) and
stores the folder names in `metadata["hls"]["rungs"]` - exactly what post.tasks._attach_hls writes for new uploads.

    python manage.py backfill_hls_low --report            # counts only
    python manage.py backfill_hls_low --dry-run --limit 20 # what would be written
    python manage.py backfill_hls_low                      # do it (idempotent; rows that already have rungs are skipped)
    python manage.py backfill_hls_low --media-id <uuid>

Videos with NO hls_playlist at all are a different job (`process_videos --hls`) and are only counted here.
"""
import re

from django.core.management.base import BaseCommand

from ...models import PostMedia

_BW = re.compile(r"BANDWIDTH=(\d+)")


def parse_master_rungs(text: str):
    """Pure: rung folder names of an HLS master playlist, LOWEST bandwidth first. Bad / empty input -> [].
    Variant URIs look like `360p/index.m3u8`; anything with `..`, a scheme or more than one folder level is ignored."""
    out = []
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        if not ln.startswith("#EXT-X-STREAM-INF"):
            continue
        m = _BW.search(ln)
        bw = int(m.group(1)) if m else 0
        uri = next((x for x in lines[i + 1:] if not x.startswith("#")), "")
        parts = uri.split("/")
        if len(parts) != 2 or not parts[0] or ".." in uri or ":" in uri:
            continue
        out.append((bw, parts[0]))
    out.sort(key=lambda t: t[0])
    seen, names = set(), []
    for _, name in out:
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


class Command(BaseCommand):
    help = "Fill metadata['hls']['rungs'] of old HLS videos from their master playlist so the API can send hls_low_url."

    def add_arguments(self, parser):
        parser.add_argument("--report", action="store_true", help="Only print counts.")
        parser.add_argument("--dry-run", action="store_true", help="Show what would be written, change nothing.")
        parser.add_argument("--media-id", help="Only this PostMedia id.")
        parser.add_argument("--limit", type=int, default=0, help="Stop after N rows (0 = no limit).")

    def handle(self, *args, **opts):
        videos = PostMedia.objects.filter(media_type="video")
        with_hls = videos.exclude(hls_playlist="")
        todo = [pk for pk, meta in with_hls.values_list("pk", "metadata")
                if not (((meta or {}).get("hls") or {}).get("rungs") if isinstance(meta, dict) else None)]
        if opts["media_id"]:
            todo = [pk for pk in todo if str(pk) == str(opts["media_id"])]
        if opts["report"]:
            self.stdout.write(
                f"videos={videos.count()} with_hls={with_hls.count()} missing_rungs={len(todo)} "
                f"no_hls_at_all={videos.filter(hls_playlist='').count()} (that last group: process_videos --hls)")
            return
        if opts["limit"] > 0:
            todo = todo[: opts["limit"]]
        storage = PostMedia._meta.get_field("file").storage
        ok = skipped = failed = 0
        for pk in todo:
            media = PostMedia.objects.filter(pk=pk).only("id", "hls_playlist", "metadata").first()
            if media is None:
                continue
            try:
                with storage.open(media.hls_playlist) as fh:
                    text = fh.read().decode("utf-8", "replace")
                rungs = parse_master_rungs(text)
            except Exception as exc:  # missing file / storage down: leave the row alone, report it
                self.stderr.write(f"{pk}: cannot read master playlist ({exc.__class__.__name__})")
                failed += 1
                continue
            if not rungs:
                self.stderr.write(f"{pk}: no usable variants in the master playlist")
                skipped += 1
                continue
            self.stdout.write(f"{pk}: rungs={rungs}  (low = {rungs[0]})")
            if not opts["dry_run"]:
                meta = dict(media.metadata or {})
                hls = dict(meta.get("hls") or {})
                hls["rungs"] = rungs
                meta["hls"] = hls
                PostMedia.objects.filter(pk=pk).update(metadata=meta)
            ok += 1
        self.stdout.write(self.style.SUCCESS(
            f"{'Would update' if opts['dry_run'] else 'Updated'}: {ok}, skipped: {skipped}, failed: {failed}"))
