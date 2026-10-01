from django.contrib import admin

from .models import LeaderboardEntry


@admin.register(LeaderboardEntry)
class LeaderboardEntryAdmin(admin.ModelAdmin):
    list_display = ("scope_type", "scope_id", "period_type", "period_key", "rank", "user", "score", "computed_at")
    list_filter = ("scope_type", "period_type")
    search_fields = ("user__username", "user__email", "scope_id")
    ordering = ("scope_type", "scope_id", "period_type", "period_key", "rank")
    # Written only by leaderboard.tasks recompute jobs — admin is read-only
    # so a manual edit can't silently drift from what the next recompute
    # (which fully replaces the board) will overwrite anyway.
    readonly_fields = [f.name for f in LeaderboardEntry._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
