from django.contrib import admin, messages

from . import services
from .models import BugReport, FeatureRequest, FeatureVote, SupportMessage, SupportTicket


class SupportMessageInline(admin.TabularInline):
    """Thread view. To REPLY, add a row here and save: it is sent as staff
    (not as the user), the ticket becomes 'answered' and the user gets a
    notification. Existing messages are read-only."""

    model = SupportMessage
    extra = 1
    fields = ("created_at", "is_staff", "body")
    readonly_fields = ("created_at", "is_staff")

    def has_change_permission(self, request, obj=None):
        return False  # a sent message is never edited

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("subject", "user", "category", "status", "has_unread_reply", "last_message_at")
    list_filter = ("status", "category")
    search_fields = ("subject", "user__username", "user__email", "messages__body")
    raw_id_fields = ("user",)
    readonly_fields = ("user", "created_at", "last_message_at", "has_unread_reply")
    inlines = [SupportMessageInline]
    actions = ["mark_resolved"]

    def save_formset(self, request, form, formset, change):
        if formset.model is not SupportMessage:
            return super().save_formset(request, form, formset, change)
        for obj in formset.save(commit=False):
            if obj.pk is None and obj.body.strip():
                services.add_staff_reply(form.instance, request.user, obj.body.strip())
        formset.save_m2m()

    @admin.action(description="Mark selected as resolved")
    def mark_resolved(self, request, queryset):
        n = queryset.exclude(status=SupportTicket.Status.CLOSED).update(status=SupportTicket.Status.RESOLVED)
        self.message_user(request, f"{n} ticket(s) marked resolved.", messages.SUCCESS)


@admin.register(BugReport)
class BugReportAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "status", "platform", "app_version", "screen", "created_at")
    list_filter = ("status", "platform")
    search_fields = ("title", "description", "user__username")
    raw_id_fields = ("user",)
    list_editable = ("status",)
    readonly_fields = ("user", "created_at")


@admin.register(FeatureRequest)
class FeatureRequestAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "votes_count", "is_hidden", "author", "created_at")
    list_filter = ("status", "is_hidden")
    search_fields = ("title", "description", "author__username")
    raw_id_fields = ("author",)
    list_editable = ("status", "is_hidden")
    readonly_fields = ("votes_count", "created_at")


@admin.register(FeatureVote)
class FeatureVoteAdmin(admin.ModelAdmin):
    list_display = ("request", "user", "created_at")
    raw_id_fields = ("request", "user")
