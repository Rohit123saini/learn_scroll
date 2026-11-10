"""
Erase claimant personal data whose retention period is over (DPDP Act 2023) - the same job Celery runs daily.

  python manage.py purge_claimant_data --dry-run     # only count what would be erased
  python manage.py purge_claimant_data               # do it (one batch)
  python manage.py purge_claimant_data --batch 5000  # bigger batch for a first catch-up run

Numbers and rules: copyrights/retention.py, settings COPYRIGHT_PII_RETENTION_DAYS / _UPHELD_DAYS /
COPYRIGHT_EMAIL_CODE_RETENTION_DAYS.
"""
from django.core.management.base import BaseCommand

from copyrights import retention


class Command(BaseCommand):
    help = "Erase claimant personal data that is past its retention period."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="count only, change nothing")
        parser.add_argument("--batch", type=int, default=None, help="max claims in this run")

    def handle(self, *args, **opts):
        days = retention.retention_days()
        self.stdout.write(f"retention: claims {days['claim']}d, upheld/bad-faith {days['upheld']}d, "
                          f"e-mail codes {days['email_code']}d")
        res = retention.purge_personal_data(batch=opts["batch"], dry_run=opts["dry_run"])
        label = "would erase" if opts["dry_run"] else "erased"
        self.stdout.write(self.style.SUCCESS(
            f"{label}: {res['claims_erased']} claim(s), {res['evidence_files']} evidence file(s), "
            f"{res['email_codes']} e-mail code(s), {res['appeal_contacts']} appeal contact(s); "
            f"skipped {res['claims_skipped']}"))
