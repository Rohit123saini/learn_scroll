# core/admin.py
from django.contrib import admin

from .models import (
    AppConfig, ChannelConsent, FeatureFlag, ChannelSendLog, ChannelSuppression, FunnelEvent, Notification, NotificationPreference,
    NotificationScopeMute, OnboardingProgress,
)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "notif_type", "title", "classroom", "session", "is_read", "created_at")
    list_filter = ("notif_type", "is_read")
    search_fields = ("recipient__username", "title", "message")
    autocomplete_fields = ["recipient", "classroom", "session"]
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "push_enabled", "email_enabled", "sms_enabled", "whatsapp_enabled", "digest_frequency")
    list_filter = ("push_enabled", "email_enabled", "sms_enabled", "whatsapp_enabled", "digest_frequency")
    search_fields = ("user__username",)
    autocomplete_fields = ["user"]
    readonly_fields = ("updated_at",)


@admin.register(NotificationScopeMute)
class NotificationScopeMuteAdmin(admin.ModelAdmin):
    list_display = ("user", "scope_type", "scope_id", "label", "muted_until", "hide_bell", "created_at")
    list_filter = ("scope_type",)
    search_fields = ("user__username", "scope_id", "label")
    autocomplete_fields = ["user"]
    readonly_fields = ("created_at",)


@admin.register(OnboardingProgress)
class OnboardingProgressAdmin(admin.ModelAdmin):
    # Task G18
    list_display = ("user", "completed", "skipped", "completed_at", "created_at")
    list_filter = ("completed", "skipped")
    search_fields = ("user__username",)
    autocomplete_fields = ["user"]
    readonly_fields = ("created_at",)


@admin.register(AppConfig)
class AppConfigAdmin(admin.ModelAdmin):
    # T1.8 — force-update / maintenance switch (core/app_config.py)
    list_display = ("platform", "min_supported_version", "latest_version", "maintenance_mode", "updated_at")
    list_filter = ("platform", "maintenance_mode")
    readonly_fields = ("updated_at",)


@admin.register(FeatureFlag)
class FeatureFlagAdmin(admin.ModelAdmin):
    """P-J2 - the kill-switch panel. Flip `enabled` (list_editable) to switch a feature off for everyone, or lower
    `rollout_percent` to stage it. Every change is logged with the staff user (logger `learnscroll.feature_flags`) and the
    row remembers who touched it last. Takes effect within seconds (cache is cleared on save)."""

    list_display = ("key", "platform", "enabled", "rollout_percent", "min_app_version", "note", "updated_by", "updated_at")
    list_editable = ("enabled", "rollout_percent")
    list_filter = ("key", "platform", "enabled")
    search_fields = ("key", "note")
    readonly_fields = ("updated_by", "updated_at")
    actions = ("switch_off", "switch_on")

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        self._audit(request, obj, "changed" if change else "created")

    def delete_model(self, request, obj):
        self._audit(request, obj, "deleted")
        super().delete_model(request, obj)

    @staticmethod
    def _audit(request, obj, what):
        import logging

        logging.getLogger("learnscroll.feature_flags").warning(
            "feature flag %s by %s: key=%s platform=%s enabled=%s rollout=%s%% min_version=%s note=%r",
            what, getattr(request.user, "pk", None), obj.key, obj.platform, obj.enabled, obj.rollout_percent,
            obj.min_app_version or "-", obj.note,
        )

    @admin.action(description="KILL: switch the selected flags OFF")
    def switch_off(self, request, queryset):
        for obj in queryset:
            obj.enabled = False
            obj.updated_by = request.user
            obj.save()  # per row: fires the cache-clearing signal + audit log
            self._audit(request, obj, "switched OFF")

    @admin.action(description="Switch the selected flags back ON")
    def switch_on(self, request, queryset):
        for obj in queryset:
            obj.enabled = True
            obj.updated_by = request.user
            obj.save()
            self._audit(request, obj, "switched ON")


@admin.register(FunnelEvent)
class FunnelEventAdmin(admin.ModelAdmin):
    # T3.8 -- read-only audit view; the aggregated numbers live at core/analytics/funnel/dashboard/
    list_display = ("event", "user", "platform", "app_version", "created_at")
    list_filter = ("event", "platform")
    search_fields = ("user__username", "device_id")
    readonly_fields = ("event", "user", "device_id", "platform", "app_version", "props", "dedupe_key", "created_at")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# --- N8 -----------------------------------------------------------------------------------------------------------
@admin.register(ChannelConsent)
class ChannelConsentAdmin(admin.ModelAdmin):
    """Read-only audit trail (DPDP): nobody edits or deletes a consent row."""

    list_display = ("user", "channel", "granted", "address", "text_version", "source", "created_at")
    list_filter = ("channel", "granted", "source")
    search_fields = ("user__username", "address")
    readonly_fields = [f.name for f in ChannelConsent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ChannelSuppression)
class ChannelSuppressionAdmin(admin.ModelAdmin):
    list_display = ("channel", "address", "reason", "soft_bounces", "created_at")
    list_filter = ("channel", "reason")
    search_fields = ("address",)


@admin.register(ChannelSendLog)
class ChannelSendLogAdmin(admin.ModelAdmin):
    list_display = ("user", "channel", "notif_type", "created_at")
    list_filter = ("channel",)
    search_fields = ("user__username",)
    readonly_fields = ("created_at",)
