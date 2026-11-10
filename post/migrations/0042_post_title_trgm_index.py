# P-F7: trigram GIN index on posts.title for the typo-tolerant unified search (core/unified_search.py).
# Hand-written (nothing was run): `python manage.py makemigrations --check --dry-run` must report no changes.
# TrigramExtension is idempotent (CREATE EXTENSION IF NOT EXISTS) and a no-op on non-Postgres backends.
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("post", "0041_rename_post_doubt__subject_5c1a0e_idx_post_doubt__subject_5f6387_idx_and_more"),
    ]

    operations = [
        TrigramExtension(),
        migrations.AddIndex(
            model_name="post",
            index=GinIndex(fields=["title"], name="post_title_trgm", opclasses=["gin_trgm_ops"]),
        ),
    ]
