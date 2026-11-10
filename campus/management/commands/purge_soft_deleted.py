"""Really erase campus records that have been in the trash longer than the retention window.

    python manage.py purge_soft_deleted                      # DRY RUN: only reports what would be erased
    python manage.py purge_soft_deleted --execute            # erase for real
    python manage.py purge_soft_deleted --days 30            # retention override (default: settings
                                                             #   CAMPUS_SOFT_DELETE_RETENTION_DAYS, 90)
    python manage.py purge_soft_deleted --campus <uuid>      # one campus only
    python manage.py purge_soft_deleted --model Section --model Room
    python manage.py purge_soft_deleted --limit 2000         # at most this many rows per run

Safety rules (see campus/soft_delete.py):
- Dry run is the default. Nothing is erased without --execute.
- Only rows with `is_deleted=True` AND `deleted_at` older than the retention window are touched.
- `Campus` rows are skipped unless --include-campus (erasing a campus erases everything under it).
- `FeeInvoice` rows are skipped unless --include-invoices (an invoice number must never be re-used,
  and a fee structure is never erased while any of its invoices is kept).
- A row is skipped while it still has an alive child, a child trashed more recently than the cutoff, or a
  child of a skipped model: a hard delete cascades, and it must never take a record the admin can still see
  or restore. Such rows are picked up by a later run, once their children have aged out.
- Every erased row writes a `<model>.purge` audit row (not for Campus: its audit rows go with it).
- Each row is erased in its own transaction; a row the database refuses (PROTECT / FK) is skipped, not fatal.
"""
from collections import Counter
from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError, RestrictedError
from django.utils import timezone

from campus.audit import _campus_id_of, log_action
from campus.soft_delete import SoftDeleteModel

DEFAULT_RETENTION_DAYS = 90
DEFAULT_LIMIT = 5000
MAX_PASSES = 20  # a parent can only go after its children, so we sweep a few times


def soft_delete_models():
    """Every concrete model that is soft-deletable."""
    return [m for m in apps.get_models() if issubclass(m, SoftDeleteModel) and not m._meta.abstract]


def campus_id_for(obj):
    """Campus a row belongs to (a Campus row belongs to itself)."""
    if obj._meta.model_name == "campus":
        return obj.pk
    return _campus_id_of(obj)


def blocker_for(obj, cutoff, kept_models):
    """Why this row must NOT be hard-deleted yet (a short text), or None when it is safe.

    A hard delete cascades to the row's children, so we look at every soft-deletable CASCADE child:
    alive, trashed after the cutoff, or belonging to a model this run never erases -> keep the parent.
    """
    for related, fk in obj._soft_children():
        children = related.all_objects.filter(**{fk: obj.pk})
        if related in kept_models and children.exists():
            return f"has {related.__name__} rows that are kept"
        if children.filter(is_deleted=False).exists():
            return f"has alive {related.__name__} rows"
        if children.filter(is_deleted=True, deleted_at__gte=cutoff).exists():
            return f"has recently deleted {related.__name__} rows"
    return None


class Command(BaseCommand):
    help = "Erase soft-deleted campus records older than the retention window (dry run unless --execute)."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true", help="Really erase. Without it this is a dry run.")
        parser.add_argument("--days", type=int, default=None,
                            help="Retention in days (default: CAMPUS_SOFT_DELETE_RETENTION_DAYS, 90). Minimum 1.")
        parser.add_argument("--campus", help="Only this campus id (uuid).")
        parser.add_argument("--model", action="append", default=[],
                            help="Only this model (class name, repeatable), e.g. --model Section.")
        parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                            help=f"Erase at most this many rows in one run (default {DEFAULT_LIMIT}).")
        parser.add_argument("--include-campus", action="store_true",
                            help="Also erase trashed Campus rows (this erases everything under that campus).")
        parser.add_argument("--include-invoices", action="store_true",
                            help="Also erase trashed FeeInvoice rows (their invoice numbers become free again).")

    # -- helpers ---------------------------------------------------------------------------------
    def _pick_models(self, opts):
        models = soft_delete_models()
        by_name = {m.__name__.lower(): m for m in models}
        if opts["model"]:
            picked = []
            for name in opts["model"]:
                m = by_name.get(name.lower())
                if m is None:
                    raise CommandError(f"Unknown or not soft-deletable model: {name}. "
                                       f"Choose from: {', '.join(sorted(m.__name__ for m in models))}")
                picked.append(m)
            models = picked
        kept = set()
        if not opts["include_campus"]:
            kept |= {m for m in soft_delete_models() if m._meta.model_name == "campus"}
        if not opts["include_invoices"]:
            kept |= {m for m in soft_delete_models() if m._meta.model_name == "feeinvoice"}
        return [m for m in models if m not in kept], kept

    def _candidates(self, model, cutoff, campus):
        qs = model.all_objects.filter(is_deleted=True, deleted_at__lt=cutoff).order_by("deleted_at")
        for obj in list(qs):  # materialised: rows are erased while we loop (SQLite dislikes writes mid-cursor)
            if campus and str(campus_id_for(obj)) != str(campus):
                continue
            yield obj

    # -- main ------------------------------------------------------------------------------------
    def handle(self, *args, **opts):
        days = opts["days"]
        if days is None:
            days = int(getattr(settings, "CAMPUS_SOFT_DELETE_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))
        if days < 1:
            raise CommandError("--days must be at least 1.")
        if opts["limit"] < 1:
            raise CommandError("--limit must be at least 1.")
        execute = opts["execute"]
        cutoff = timezone.now() - timedelta(days=days)
        models, kept = self._pick_models(opts)

        self.stdout.write(
            f"{'EXECUTE' if execute else 'DRY RUN'}: trash older than {days} day(s) (before {cutoff:%Y-%m-%d %H:%M} UTC)"
            + (f", campus {opts['campus']}" if opts["campus"] else "")
        )
        if kept:
            self.stdout.write("  not touched: " + ", ".join(sorted(m.__name__ for m in kept))
                              + " (use --include-campus / --include-invoices)")

        if not execute:
            self._dry_run(models, kept, cutoff, opts)
            return
        self._execute(models, kept, cutoff, days, opts)

    def _dry_run(self, models, kept, cutoff, opts):
        ready, blocked = Counter(), Counter()
        for model in models:
            for obj in self._candidates(model, cutoff, opts["campus"]):
                if blocker_for(obj, cutoff, kept):
                    blocked[model.__name__] += 1
                else:
                    ready[model.__name__] += 1
        for name in sorted(set(ready) | set(blocked)):
            self.stdout.write(f"  {name}: {ready[name]} ready to erase, {blocked[name]} waiting on children")
        total = sum(ready.values())
        self.stdout.write(self.style.SUCCESS(
            f"Would erase {total} row(s) now (rows waiting on children may follow in later runs). Nothing was changed."
        ))

    def _execute(self, models, kept, cutoff, days, opts):
        erased, skipped = Counter(), Counter()
        remaining = opts["limit"]
        for _ in range(MAX_PASSES):
            progress = False
            for model in models:
                if remaining <= 0:
                    break
                for obj in self._candidates(model, cutoff, opts["campus"]):
                    if remaining <= 0:
                        break
                    if blocker_for(obj, cutoff, kept):
                        continue
                    campus_id = campus_id_for(obj)
                    name, pk, label = model.__name__, str(obj.pk), str(obj)[:200]
                    deleted_at = obj.deleted_at
                    try:
                        with transaction.atomic():
                            obj.delete()  # SoftDeleteModel does not override delete(): this is the real one
                    except (ProtectedError, RestrictedError, IntegrityError) as exc:
                        skipped[name] += 1
                        self.stderr.write(f"  skipped {name} {pk}: {exc.__class__.__name__}")
                        continue
                    erased[name] += 1
                    remaining -= 1
                    progress = True
                    if campus_id and model._meta.model_name != "campus":
                        log_action(
                            campus_id, None, f"{model._meta.model_name}.purge",
                            target_type=name, target_id=pk, summary=f"purged {name} after {days} day(s) in trash: {label}",
                            purged=True, retention_days=days, deleted_at=deleted_at,
                        )
            if not progress or remaining <= 0:
                break
        for name in sorted(erased):
            self.stdout.write(f"  {name}: {erased[name]} erased" + (f", {skipped[name]} skipped" if skipped[name] else ""))
        self.stdout.write(self.style.SUCCESS(f"Erased {sum(erased.values())} row(s)."))
        if remaining <= 0:
            self.stdout.write("Limit reached: run again to continue.")
