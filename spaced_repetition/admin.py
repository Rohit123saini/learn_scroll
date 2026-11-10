from django.contrib import admin

from .models import RevisionCard, RevisionReview


@admin.register(RevisionCard)
class RevisionCardAdmin(admin.ModelAdmin):
    list_display = ("user", "post", "interval_days", "ease", "repetitions", "lapses", "next_review_at")
    list_filter = ("relearning",)
    search_fields = ("user__username",)
    raw_id_fields = ("user", "post")
    readonly_fields = ("created_at", "updated_at")


@admin.register(RevisionReview)
class RevisionReviewAdmin(admin.ModelAdmin):
    list_display = ("user", "grade", "reviewed_at", "interval_before", "interval_after", "was_new")
    list_filter = ("grade", "was_new")
    raw_id_fields = ("user", "card")
