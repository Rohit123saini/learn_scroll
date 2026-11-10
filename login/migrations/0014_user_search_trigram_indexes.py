import django.contrib.postgres.indexes
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    """P-C5 -- trigram GIN indexes for user search (username / first_name / last_name icontains). (hand-written)

    Index-only, no column changes. TrigramExtension = CREATE EXTENSION IF NOT EXISTS pg_trgm (no-op on SQLite;
    tuitionclass 0001 already enables it, repeating it is harmless). On a big table prefer building the indexes
    CONCURRENTLY by hand during low traffic, then `migrate --fake` this one.
    Check with `python manage.py makemigrations --check --dry-run`.
    """

    dependencies = [
        ("login", "0013_user_profile_photo_blurhash"),
    ]

    operations = [
        TrigramExtension(),
        migrations.AddIndex(
            model_name="user",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["username"], name="user_username_trgm", opclasses=["gin_trgm_ops"]),
        ),
        migrations.AddIndex(
            model_name="user",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["first_name"], name="user_first_name_trgm", opclasses=["gin_trgm_ops"]),
        ),
        migrations.AddIndex(
            model_name="user",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["last_name"], name="user_last_name_trgm", opclasses=["gin_trgm_ops"]),
        ),
    ]
