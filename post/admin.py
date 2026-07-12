from django.contrib import admin

from .models import *

admin.site.register(Post)
admin.site.register(PostMedia)
admin.site.register(PostLike)
admin.site.register(PostComment)
admin.site.register(PostShare)
admin.site.register(PostView)
admin.site.register(PostSave)



