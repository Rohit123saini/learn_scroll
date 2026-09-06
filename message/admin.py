from django.contrib import admin

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
admin.site.register(UserPresence)
admin.site.register(BlockedUser)
admin.site.register(DeviceToken)
admin.site.register(StudyRoomState)