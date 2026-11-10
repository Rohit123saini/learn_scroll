"""
manage.py backfill_copyright_fingerprints [--dry-run] [--limit N]

Content that was removed BEFORE perceptual fingerprints existed only has a SHA-256 (BlockedMediaHash).
This command fingerprints those files from quarantine (TargetHold.media_manifest) so a re-encoded /
resized / mirrored copy of them is caught too. Safe to run repeatedly (same file + algo is not duplicated).
Run it once after deploying, ideally off-peak: it reads and decodes every quarantined file.
Needs the same tools as the live check (ffmpeg, fpcalc, pypdf) - kinds whose tool is missing are skipped.
"""

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from copyrights import fingerprint, media
from copyrights.models import TargetHold


class Command(BaseCommand):
    help = "Fingerprint already-quarantined (removed) files so near-copies are detected."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="only count what would be fingerprinted")
        parser.add_argument("--limit", type=int, default=0, help="stop after this many files (0 = all)")

    def handle(self, *args, **opts):
        done = skipped = 0
        limit, dry = opts["limit"], opts["dry_run"]
        for hold in TargetHold.objects.filter(media_quarantined=True).iterator():
            if limit and done >= limit:
                break
            claim_id, order_id = media._blocked_source(hold)
            changed = False
            for m in hold.media_manifest or []:
                if limit and done >= limit:
                    break
                q = m.get("quarantine")
                if not q or fingerprint.kind_for_name(m.get("name", "")) is None or not default_storage.exists(q):
                    skipped += 1
                    continue
                if dry:
                    done += 1
                    continue
                # fingerprint the quarantined copy, register it under the ORIGINAL sha256
                ids = fingerprint.register_removed(q, claim_id=claim_id, legal_order_id=order_id,
                                                   sha256=m.get("sha256", ""))
                if ids:
                    m["fp_ids"] = sorted(set((m.get("fp_ids") or []) + ids))
                    changed = True
                    done += 1
                else:
                    skipped += 1
            if changed:
                hold.save(update_fields=["media_manifest", "updated_at"])
        self.stdout.write(self.style.SUCCESS(
            f"{'would fingerprint' if dry else 'fingerprinted'} {done} file(s), skipped {skipped}"))
