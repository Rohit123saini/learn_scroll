"""[T4 §D] Compare each campus section's real roster with its chat group and fix it.

    python manage.py reconcile_section_groups                  # all sections
    python manage.py reconcile_section_groups --section <uuid> # one section
    python manage.py reconcile_section_groups --dry-run        # report only
"""
from django.core.management.base import BaseCommand

from campus.models import Section
from core.classroom_chat_bridge import reconcile_section_group


class Command(BaseCommand):
    help = "Make each section chat group's members equal the section roster (students + teachers)."

    def add_arguments(self, parser):
        parser.add_argument("--section", help="Only this section id (uuid).")
        parser.add_argument("--dry-run", action="store_true", help="Print what would change, write nothing.")

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        qs = Section.objects.filter(chat_group_enabled=True, school_class__campus__is_active=True).select_related(
            "school_class"
        )
        if opts.get("section"):
            qs = qs.filter(pk=opts["section"])
        totals = {"sections": 0, "created": 0, "added": 0, "removed": 0, "role_fixed": 0, "errors": 0}
        for section in qs.iterator(chunk_size=200):
            try:
                r = reconcile_section_group(section, dry_run=dry)
            except Exception as exc:  # noqa: BLE001 - keep sweeping
                totals["errors"] += 1
                self.stderr.write(f"section {section.pk}: ERROR {exc}")
                continue
            totals["sections"] += 1
            totals["created"] += int(bool(r["created"] or (dry and r["group_missing"])))
            totals["added"] += len(r["added"])
            totals["removed"] += len(r["removed"])
            totals["role_fixed"] += len(r["role_fixed"])
            if r["group_missing"] or r["added"] or r["removed"] or r["role_fixed"] or r["capacity_mismatch"]:
                self.stdout.write(
                    f"section {section.pk} ({str(section)[:40]}): "
                    f"{'GROUP MISSING ' if r['group_missing'] else ''}"
                    f"+{len(r['added'])} -{len(r['removed'])} roles:{len(r['role_fixed'])}"
                    f"{' CAPACITY<students' if r['capacity_mismatch'] else ''}"
                )
        label = "DRY-RUN " if dry else ""
        self.stdout.write(self.style.SUCCESS(f"{label}done: {totals}"))
