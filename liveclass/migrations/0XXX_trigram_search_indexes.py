# liveclass/migrations/0XXX_trigram_search_indexes.py
#
# RENAME THIS FILE before running: replace "0XXX" with the next sequence
# number after your actual latest migration in liveclass/migrations/
# (e.g. if your last one is 0012_xxx.py, this becomes 0013_trigram_search_indexes.py).
# Set `dependencies` below to that latest migration's (app_label, name) tuple.
#
# Postgres-only. If liveclass ever needs to run on a non-Postgres backend
# (e.g. sqlite in a lightweight test setting), guard this migration with
# `if connection.vendor == "postgresql"` in a RunPython, or keep a
# Postgres-only settings/test profile — TrigramExtension and GinIndex are
# both Postgres-specific and will fail migrate on any other backend.

from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations, models
from django.contrib.postgres.indexes import GinIndex


class Migration(migrations.Migration):

    dependencies = [
        # ("liveclass", "00XX_previous_migration"),  # <-- fill this in
    ]

    operations = [
        # Enables Postgres's trigram similarity functions/operators — needed
        # before a GIN...gin_trgm_ops index can be created. Requires the
        # DB user running migrate to have CREATE EXTENSION privileges
        # (superuser, or the extension pre-created by a DBA/RDS parameter
        # group on managed Postgres like AWS RDS — see note below).
        TrigramExtension(),
        migrations.AddIndex(
            model_name="classroom",
            index=GinIndex(fields=["title"], name="classroom_title_trgm", opclasses=["gin_trgm_ops"]),
        ),
        migrations.AddIndex(
            model_name="classroom",
            index=GinIndex(fields=["subject"], name="classroom_subject_trgm", opclasses=["gin_trgm_ops"]),
        ),
        migrations.AddIndex(
            model_name="classroom",
            index=GinIndex(
                fields=["description"], name="classroom_desc_trgm", opclasses=["gin_trgm_ops"]
            ),
        ),
    ]

# NOTE — AWS RDS / managed Postgres: TrigramExtension() runs `CREATE
# EXTENSION IF NOT EXISTS pg_trgm`, which needs superuser on vanilla
# Postgres. RDS's master user IS allowed to create this specific extension
# (it's on RDS's allowlist), so this works out-of-the-box on RDS without
# extra setup. On a self-managed Postgres box, run once as a superuser
# before `migrate`, or grant migrate's DB user superuser for this one
# deploy:
#     psql -d <dbname> -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
# then `python manage.py migrate liveclass` will see the extension already
# exists and just add the indexes.