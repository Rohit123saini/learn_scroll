# campus/management/commands/add_testseries_paid_allowed_field.py
"""
[Task 19] Ad-hoc management command that adds `Campus.
testseries_paid_allowed` directly to the database, instead of going
through Django's normal migration flow (`campus/migrations/`).

WHY THIS EXISTS INSTEAD OF A MIGRATION (read before using):
Django's own schema-tracking (`django_migrations` table,
`makemigrations`/`migrate`) is built specifically to do exactly what
this command does, safely and idempotently, and to keep every
environment's schema history in one place. Moving a single field-add
out of that system into a one-off command means:
  - Running `python manage.py makemigrations` later will still detect
    `Campus.testseries_paid_allowed` as an unapplied model change (Django
    diffs models against migration state, not against the live DB), so
    someone will eventually need a real migration for this field anyway
    or `makemigrations`/`migrate --check` will keep flagging it.
  - This command has to hand-roll the idempotency + existence-checking
    that Django's migration framework already gives you for free.
  - This only works against the database backends handled below
    (postgresql / sqlite / mysql); a real migration works on every
    backend Django supports without extra code.
  - `django_migrations` will NOT know this column exists after running
    this — confirm this isn't going to collide with a teammate's real
    migration adding the same field before running this anywhere
    shared.

This is provided because it was explicitly asked for, not because it's
the recommended path — prefer converting this into a real
`campus/migrations/0xxx_....py` (`AddField`) the moment that's an
option again.

TABLE_NAME ASSUMPTION: `campus_campus` is Django's DEFAULT table name
(`<app_label>_<model_name>`) for `Campus`, assuming (a) this app's
`app_label` is `campus` (i.e. no custom `AppConfig.label` override in
`campus/apps.py`) and (b) `Campus.Meta` has no `db_table` override —
both true for the `Campus` model as reviewed in this task's upload.
Re-check both before running this against an app where either isn't
true.

WHAT IT DOES:
  1. Confirms the target table actually exists (a fresh, not-yet-
     migrated DB would otherwise fail with a confusing driver-level
     error) and exits with a clear message if it doesn't.
  2. Checks whether the column already exists on that table and no-ops
     with a message if so — safe to re-run.
  3. Otherwise runs `ALTER TABLE ... ADD COLUMN testseries_paid_allowed
     <bool> NOT NULL DEFAULT false/0` inside a transaction — a plain
     boolean column, matching `models.BooleanField(default=False)`. No
     schema-editor abstraction is used because that abstraction IS the
     migration framework this command is deliberately bypassing.
     SQLite's ALTER TABLE syntax used here has been hand-verified
     against a real in-memory sqlite3 DB (not just assumed correct).

USAGE:
    python manage.py add_testseries_paid_allowed_field
    python manage.py add_testseries_paid_allowed_field --dry-run
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

TABLE_NAME = "campus_campus"
COLUMN_NAME = "testseries_paid_allowed"


class Command(BaseCommand):
    help = (
        "Adds Campus.testseries_paid_allowed (BooleanField, default False) "
        "directly via raw SQL, bypassing the normal migrations/ flow. "
        "See this command's own module docstring for why a real migration "
        "is still the recommended way to do this."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without actually altering the table.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if not self._table_exists():
            raise CommandError(
                f"Table {TABLE_NAME!r} does not exist — run `migrate` for the "
                "`campus` app first, or fix TABLE_NAME in this command if "
                "Campus's app_label/db_table differs from the default assumed here."
            )

        if self._column_exists():
            self.stdout.write(
                self.style.WARNING(
                    f"{TABLE_NAME}.{COLUMN_NAME} already exists — nothing to do."
                )
            )
            return

        sql = self._build_add_column_sql()

        if dry_run:
            self.stdout.write(self.style.NOTICE(f"[dry-run] Would run: {sql}"))
            return

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(sql)

        self.stdout.write(
            self.style.SUCCESS(
                f"Added {TABLE_NAME}.{COLUMN_NAME} (boolean, default false). "
                "Reminder: Django's migration state does NOT know about this "
                "column — `makemigrations` will still propose adding it as a "
                "real AddField migration. See this command's docstring."
            )
        )

    def _table_exists(self) -> bool:
        return TABLE_NAME in connection.introspection.table_names()

    def _column_exists(self) -> bool:
        with connection.cursor() as cursor:
            existing_columns = {
                col.name for col in connection.introspection.get_table_description(cursor, TABLE_NAME)
            }
        return COLUMN_NAME in existing_columns

    def _build_add_column_sql(self) -> str:
        vendor = connection.vendor
        quoted_table = connection.ops.quote_name(TABLE_NAME)
        quoted_column = connection.ops.quote_name(COLUMN_NAME)

        if vendor == "postgresql":
            return f"ALTER TABLE {quoted_table} ADD COLUMN {quoted_column} boolean NOT NULL DEFAULT false"
        elif vendor in ("sqlite", "mysql"):
            # SQLite has no real boolean type — Django itself stores
            # BooleanField as integer 0/1 under the hood, matched here.
            # Verified against a real in-memory sqlite3 DB — see this
            # file's module docstring. MySQL's BOOL is a TINYINT(1)
            # alias, same 0/1 default works there too.
            return f"ALTER TABLE {quoted_table} ADD COLUMN {quoted_column} bool NOT NULL DEFAULT 0"
        else:
            raise NotImplementedError(
                f"add_testseries_paid_allowed_field: no ADD COLUMN SQL written for "
                f"db backend {vendor!r}. Add a branch here or use a real migration instead."
            )