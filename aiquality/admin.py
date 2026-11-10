from django.contrib import admin
from django.utils import timezone

from .models import AiCallLog, AiFeedback


@admin.register(AiFeedback)
class AiFeedbackAdmin(admin.ModelAdmin):
    list_display = ("created_at", "surface", "feature", "prompt_version", "rating", "reported_wrong", "reason", "status")
    list_filter = ("status", "rating", "reported_wrong", "surface", "feature", "prompt_version", "reason")
    search_fields = ("comment", "excerpt", "ref_id", "user__username", "staff_note")
    raw_id_fields = ("user", "call", "reviewed_by")
    readonly_fields = ("created_at", "updated_at", "dedupe_key")
    actions = ["mark_confirmed_wrong", "mark_fixed", "mark_dismissed"]

    def _mark(self, request, queryset, status):
        n = queryset.update(status=status, reviewed_by=request.user, reviewed_at=timezone.now())
        self.message_user(request, f"{n} feedback row(s) marked {status}.")

    @admin.action(description="Mark as confirmed wrong")
    def mark_confirmed_wrong(self, request, queryset):
        self._mark(request, queryset, AiFeedback.Status.CONFIRMED_WRONG)

    @admin.action(description="Mark as fixed (prompt / data)")
    def mark_fixed(self, request, queryset):
        self._mark(request, queryset, AiFeedback.Status.FIXED)

    @admin.action(description="Dismiss (answer was fine)")
    def mark_dismissed(self, request, queryset):
        self._mark(request, queryset, AiFeedback.Status.DISMISSED)


@admin.register(AiCallLog)
class AiCallLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "surface", "feature", "prompt_version", "prompt_hash", "model", "ok", "latency_ms",
                    "tokens_in", "tokens_out", "cost_micro_usd")
    list_filter = ("surface", "feature", "ok", "prompt_version", "model")
    raw_id_fields = ("user",)
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
