from django.contrib import admin, messages

from . import seasons
from .models import (
    AccountabilityPair,
    PomodoroTimer,
    SeasonReward,
    SeasonStanding,
    SocialPrefs,
    StudySeason,
)


@admin.register(StudySeason)
class StudySeasonAdmin(admin.ModelAdmin):
    list_display = ("number", "title", "status", "starts_at", "ends_at", "finalized_at")
    list_filter = ("status",)
    readonly_fields = ("finalized_at", "created_at")
    actions = ["refresh_now", "close_now"]

    @admin.action(description="Recompute the standings now (provisional)")
    def refresh_now(self, request, queryset):
        for season in queryset.exclude(status=StudySeason.Status.CLOSED):
            n = seasons.refresh_standings(season)
            self.message_user(request, f"{season.title}: {n} ranked", messages.SUCCESS)

    @admin.action(description="Close now: freeze standings + give rewards (cannot be undone)")
    def close_now(self, request, queryset):
        for season in queryset.exclude(status=StudySeason.Status.CLOSED):
            summary = seasons.finalize_season(season)
            self.message_user(request, f"{season.title}: {summary}", messages.WARNING)


@admin.register(SeasonStanding)
class SeasonStandingAdmin(admin.ModelAdmin):
    list_display = ("season", "rank", "user", "score", "minutes", "pomodoros", "active_days", "is_final")
    list_filter = ("season", "is_final")
    raw_id_fields = ("user",)
    ordering = ("season", "rank")


@admin.register(SeasonReward)
class SeasonRewardAdmin(admin.ModelAdmin):
    list_display = ("season", "rank", "user", "tier", "badge_code", "freezes_granted", "granted_at")
    list_filter = ("season", "tier")
    raw_id_fields = ("user",)


@admin.register(AccountabilityPair)
class AccountabilityPairAdmin(admin.ModelAdmin):
    list_display = ("id", "requester", "partner", "status", "daily_goal_minutes", "best_streak", "created_at")
    list_filter = ("status",)
    raw_id_fields = ("requester", "partner", "ended_by")


@admin.register(PomodoroTimer)
class PomodoroTimerAdmin(admin.ModelAdmin):
    list_display = ("conversation", "phase", "is_running", "phase_ends_at", "version", "updated_at")
    list_filter = ("phase", "is_running")
    raw_id_fields = ("conversation", "updated_by", "current_round")


@admin.register(SocialPrefs)
class SocialPrefsAdmin(admin.ModelAdmin):
    list_display = ("user", "season_opt_out", "accepts_partner_requests", "partner_reminders")
    raw_id_fields = ("user",)
