from django.contrib import admin
from django.utils import timezone

# Register your models here.
from .models import *


# 🔧 GAP FIX — `Conversation`/`Group` use `BaseModel`'s `SoftDeleteManager`
# as their default manager (`objects`), and plain `admin.site.register()`
# uses a model's default manager for its changelist queryset — so a
# soft-deleted group (see `GroupViewSet.destroy()` in views.py) simply
# VANISHED from the admin too, with no way for support/ops to find it,
# inspect it, or `.restore()` it during the grace window
# (`GROUP_SOFT_DELETE_GRACE_DAYS`, see tasks.py:
# purge_soft_deleted_conversations for what eventually hard-deletes it).
# These two custom `ModelAdmin`s point the changelist at `all_objects`
# instead so soft-deleted rows stay visible/manageable, plus a bulk
# "restore" action.
class SoftDeleteAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'is_deleted', 'created_at', 'updated_at')
    list_filter = ('is_deleted',)
    actions = ['restore_selected']

    def get_queryset(self, request):
        # `self.model.all_objects` (not `.objects`) so soft-deleted rows
        # show up here instead of being silently hidden.
        return self.model.all_objects.all()

    @admin.action(description='Restore selected (undo soft-delete)')
    def restore_selected(self, request, queryset):
        updated = queryset.update(is_deleted=False)
        self.message_user(request, f"{updated} row(s) restored.")


admin.site.register(Conversation, SoftDeleteAdmin)
admin.site.register(ConversationParticipant)
admin.site.register(Group, SoftDeleteAdmin)
admin.site.register(GroupMember)
# 🔥 GAP FIX — GroupJoinRequest, DeviceToken, StudyRoomState models existed
# but were never registered here, so ops/support had no way to inspect
# pending private-group join requests, debug a user's push-notification
# tokens, or look at a study room's saved whiteboard state without a raw
# DB query. Registered the same way every other model already was.
admin.site.register(GroupJoinRequest)
admin.site.register(Message)
admin.site.register(Presentation)
admin.site.register(GroupMedia)
admin.site.register(MessageStatus)
admin.site.register(MessageReaction)
admin.site.register(CallSession)
admin.site.register(CallParticipant)


@admin.register(CallRecordingConsent)
class CallRecordingConsentAdmin(admin.ModelAdmin):
    """C16 — call recording consent log: sirf padhne ke liye (audit trail, edit / delete nahi)."""
    list_display = ('created_at', 'call', 'user', 'event', 'egress_id', 'notice_version')
    list_filter = ('event',)
    search_fields = ('call__id', 'user__username', 'egress_id')
    readonly_fields = [f.name for f in CallRecordingConsent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(UserPresence)
admin.site.register(BlockedUser)


@admin.register(DeviceToken)   # N3b - push-token health at a glance (who is failing, who is stale, per device)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = ('user', 'platform', 'device_name', 'app_version', 'last_seen_at', 'failure_count', 'last_failure_code')
    list_filter = ('platform', 'last_failure_code')
    search_fields = ('user__username', 'device_name', 'device_id')
    readonly_fields = ('token', 'session_jti', 'last_seen_at', 'last_failure_at')
    ordering = ('-last_seen_at',)
    actions = ('delete_failing',)

    @admin.action(description='Delete selected tokens that are failing (failure_count >= 1)')
    def delete_failing(self, request, queryset):
        deleted, _ = queryset.filter(failure_count__gte=1).delete()
        self.message_user(request, f'{deleted} failing token(s) deleted.')

admin.site.register(StudyRoomState)
admin.site.register(StudyRoomSession)   # S2
admin.site.register(StudyRoomPresence)   # S5
admin.site.register(StudyRoomStateSnapshot)   # S3
admin.site.register(StudyRoomParticipant)   # S4


@admin.register(StudyRoomReport)   # S4 — moderator review (staff API: message/staff/study-room-reports/)
class StudyRoomReportAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'reason', 'status', 'reported_user', 'reporter', 'conversation', 'reviewed_by')
    list_filter = ('status', 'reason')
    search_fields = ('details', 'review_note')
    raw_id_fields = ('conversation', 'session', 'reporter', 'reported_user', 'reviewed_by')
    actions = ('mark_reviewed', 'mark_dismissed')

    def _mark(self, request, queryset, status):
        now = timezone.now()
        return queryset.filter(status=StudyRoomReport.Status.OPEN).update(
            status=status, reviewed_by=request.user, reviewed_at=now, updated_at=now,
        )

    @admin.action(description='Mark selected open reports as reviewed')
    def mark_reviewed(self, request, queryset):
        self.message_user(request, f"{self._mark(request, queryset, StudyRoomReport.Status.REVIEWED)} report(s) reviewed.")

    @admin.action(description='Dismiss selected open reports')
    def mark_dismissed(self, request, queryset):
        self.message_user(request, f"{self._mark(request, queryset, StudyRoomReport.Status.DISMISSED)} report(s) dismissed.")
admin.site.register(GroupRulesAcceptance)   # S8 — room rules accept log
# 🔥 NAYA — sticky notes: soft-deleted (tombstone) rows bhi dikhein, restore action ke saath.
admin.site.register(StudyRoomNote, SoftDeleteAdmin)

# T1.1/T1.3 — Doubt Queue moderation: soft-deleted rows visible, answers inspectable.
admin.site.register(DoubtQuestion, SoftDeleteAdmin)
admin.site.register(DoubtAnswer, SoftDeleteAdmin)
admin.site.register(DoubtUpvote)
