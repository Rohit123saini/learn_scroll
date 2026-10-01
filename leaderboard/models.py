"""
leaderboard/models.py

TASK G7 (growth_and_feature_tasks.md — Leaderboards): "Leaderboards at
multiple scopes — per test series (score-based), per campus/section
(attendance/academics), per app (engagement) — weekly reset so newcomers
can compete, not just all-time toppers."

Single table, one row per (board, user). A "board" is the tuple
(scope_type, scope_id, period_type, period_key):
  - scope_type=test_series,     scope_id=TestSeries.id
  - scope_type=campus_section,  scope_id=campus.Section.id
  - scope_type=engagement,      scope_id=None (one app-wide board)
  - period_type=all_time,  period_key="all"
  - period_type=weekly,    period_key=ISO week, e.g. "2026-W39"

Rows are written ONLY by `leaderboard.tasks.recompute_*` (never from a
request) — the request path (`views.py`) only ever reads this table via
`services.get_board`, matching the "background job owns the writes, the API
just reads what it wrote" split `campus`/`testseries` already use for their
own heavy aggregates (attendance %-summary, report cards: see those models'
own module-level notes). "Weekly reset" falls out of `period_key` alone —
a new ISO week is a brand-new set of rows built from that week's activity,
so newcomers start even; last week's rows are kept as history rather than
deleted (nothing here prunes them), so a "last week" board stays viewable.
"""
from django.conf import settings
from django.db import models


class LeaderboardEntry(models.Model):
    class ScopeType(models.TextChoices):
        TEST_SERIES = "test_series", "Test Series"
        CAMPUS_SECTION = "campus_section", "Campus Section"
        ENGAGEMENT = "engagement", "App-wide Engagement"

    class PeriodType(models.TextChoices):
        WEEKLY = "weekly", "Weekly"
        ALL_TIME = "all_time", "All Time"

    scope_type = models.CharField(max_length=20, choices=ScopeType.choices, db_index=True)
    # UUID for TEST_SERIES (testseries.TestSeries.id) / CAMPUS_SECTION
    # (campus.Section.id) — both real UUID pks in this codebase. Null for
    # ENGAGEMENT, the one scope with no sub-board.
    scope_id = models.UUIDField(null=True, blank=True, db_index=True)

    period_type = models.CharField(max_length=10, choices=PeriodType.choices, db_index=True)
    # "all" for ALL_TIME; an ISO-8601 week string ("2026-W39") for WEEKLY —
    # see services.current_week_key()/services.week_bounds().
    period_key = models.CharField(max_length=20, db_index=True)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="leaderboard_entries"
    )
    score = models.FloatField(default=0)
    rank = models.PositiveIntegerField(db_index=True)

    # Small denormalised "why this score" context so the API/frontend never
    # needs a per-scope join just to explain a number, e.g.
    # {"attempts": 3, "best_score": 91} for a test-series row, or
    # {"attendance_pct": 94.0, "academic_pct": 81.5} for a campus-section
    # row, or {"posts": 4, "likes_received": 40} for engagement. Written
    # only by tasks.py — never accepted from a client.
    metadata = models.JSONField(default=dict, blank=True)

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scope_type", "scope_id", "period_type", "period_key", "user"],
                name="unique_leaderboard_row",
            ),
        ]
        indexes = [
            # The one query the read path (views.LeaderboardAPIView) needs:
            # "give me board X, ordered by rank".
            models.Index(fields=["scope_type", "scope_id", "period_type", "period_key", "rank"]),
            # "what's MY rank" (profile widget / result-screen CTA) —
            # one indexed lookup, no board-wide scan.
            models.Index(fields=["user", "period_type"]),
        ]
        ordering = ["rank"]

    def __str__(self):
        return (
            f"{self.scope_type}:{self.scope_id or '-'} "
            f"[{self.period_type}:{self.period_key}] user={self.user_id} #{self.rank} ({self.score})"
        )
