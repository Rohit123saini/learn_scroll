# core/admin.py
from django.contrib import admin

from .models import Notification, NotificationPreference


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