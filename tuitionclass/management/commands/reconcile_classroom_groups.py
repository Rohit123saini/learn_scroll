"""T3 — compare every classroom's real participants with its chat group and fix
the difference.

    python manage.py reconcile_classroom_groups                 # all classrooms
    python manage.py reconcile_classroom_groups --classroom 12  # one classroom
    python manage.py reconcile_classroom_groups --dry-run       # report only
"""
from django.core.management.base import BaseCommand

from core.classroom_chat_bridge import reconcile_classroom_group
from tuitionclass.models import Classroom


class Command(BaseCommand):
    help = "Make each classroom's chat group members equal to its actual participants."

    def add_arguments(self, parser):
        parser.add_argument("--classroom", type=int, help="Only this classroom id.")
        parser.add_argument("--dry-run", action="store_true", help="Print what would change, write nothing.")

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        qs = Classroom.objects.filter(is_active=True, is_deleted=False, chat_group_enabled=True).select_related("teacher")
        if opts.get("classroom"):
            qs = qs.filter(pk=opts["classroom"])
        totals = {"classrooms": 0, "created": 0, "added": 0, "removed": 0, "role_fixed": 0, "errors": 0}
        for classroom in qs.iterator(chunk_size=200):
            try:
                r = reconcile_classroom_group(classroom, dry_run=dry)
            except Exception as exc:  # noqa: BLE001 — keep sweeping
                totals["errors"] += 1
                self.stderr.write(f"classroom {classroom.pk}: ERROR {exc}")
                continue
            totals["classrooms"] += 1
            totals["created"] += int(bool(r["created"] or (dry and r["group_missing"])))
            totals["added"] += len(r["added"])
            totals["removed"] += len(r["removed"])
            totals["role_fixed"] += len(r["role_fixed"])
            if r["group_missing"] or r["added"] or r["removed"] or r["role_fixed"] or r["capacity_mismatch"]:
                self.stdout.write(
                    f"classroom {classroom.pk} ({classroom.title[:40]}): "
                    f"{'GROUP MISSING ' if r['group_missing'] else ''}"
                    f"+{len(r['added'])} -{len(r['removed'])} roles:{len(r['role_fixed'])}"
                    f"{' CAPACITY>max_participants' if r['capacity_mismatch'] else ''}"
                )
        label = "DRY-RUN " if dry else ""
        self.stdout.write(self.style.SUCCESS(f"{label}done: {totals}"))
