from django.contrib import admin

# Register your models here.
from .models import *
admin.site.register(Conversation)
admin.site.register(ConversationParticipant)
admin.site.register(Group)
admin.site.register(GroupMember)
admin.site.register(Message)
admin.site.register(Presentation)
admin.site.register(GroupMedia)
admin.site.register(MessageStatus)
admin.site.register(MessageReaction)
admin.site.register(CallSession)
admin.site.register(CallParticipant)
admin.site.register(UserPresence)
admin.site.register(BlockedUser)

