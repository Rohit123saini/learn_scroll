from django.contrib import admin

# Register your models here.
from .models import *
admin.site.register(Conversation)
admin.site.register(ConversationParticipant)
admin.site.register(Group)
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