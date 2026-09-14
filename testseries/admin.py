# testseries/admin.py
from django.contrib import admin

from .models import Question, QuestionResponse, TestAttempt, TestSeries, TestSeriesPurchase, TestSeriesReview


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0
    fields = ["order", "question_type", "text", "marks"]
    ordering = ["order"]


@admin.register(TestSeries)
class TestSeriesAdmin(admin.ModelAdmin):
    list_display = ["title", "source", "creator", "is_paid", "price_coins", "status", "total_marks", "created_at"]
    list_filter = ["source", "status", "is_paid"]
    search_fields = ["title", "creator__username", "creator__email"]
    readonly_fields = ["total_marks", "created_at", "updated_at"]
    inlines = [QuestionInline]


@admin.register(TestSeriesPurchase)
class TestSeriesPurchaseAdmin(admin.ModelAdmin):
    # Payouts/refunds are exposed read-only here on purpose — the actual
    # state transitions (release()/refund()) go through the model
    # methods (CoinLedger side-effects), never a raw admin field edit.
    list_display = ["series", "buyer", "coins_spent", "status", "created_at", "released_at", "refunded_at"]
    list_filter = ["status"]
    search_fields = ["series__title", "buyer__username"]
    readonly_fields = [f.name for f in TestSeriesPurchase._meta.fields]

    def has_add_permission(self, request):
        return False


class QuestionResponseInline(admin.TabularInline):
    model = QuestionResponse
    extra = 0
    fields = ["question", "is_auto_graded", "is_correct", "answer_attachment", "marks_awarded", "reviewed_by", "reviewed_at"]
    readonly_fields = ["question", "is_auto_graded", "is_correct"]


@admin.register(TestAttempt)
class TestAttemptAdmin(admin.ModelAdmin):
    list_display = ["series", "student", "status", "auto_score", "final_score", "submitted_at", "checked_at"]
    list_filter = ["status", "series__source"]
    search_fields = ["series__title", "student__username", "roll_number", "enrollment_no"]
    inlines = [QuestionResponseInline]


# TASK 35 — Reviews are now inspectable/moderatable from admin.
@admin.register(TestSeriesReview)
class TestSeriesReviewAdmin(admin.ModelAdmin):
    """
    Add is disabled on purpose, same reasoning as
    `TestSeriesPurchaseAdmin.has_add_permission` above:
    `TestSeriesReview.create_review()` is the model's own documented
    "single creation entrypoint", specifically so the
    `TESTSERIES_REVIEW_RECEIVED` notify-the-creator step can never be
    skipped at a call site. A raw admin-created row would still pass
    `clean()`'s validation (that runs from `save()` regardless of entry
    point — defense-in-depth, not the primary gate), but it would
    silently skip that notify, leaving the series creator never told
    about a review that exists. Disabling add here keeps
    `create_review()` the only path, exactly like Purchase keeps
    `release()`/`refund()` the only path for its own state changes.

    `series`/`student`/`attempt` are read-only for the same "identity
    fields shouldn't move under an existing row" reasoning
    `TestSeriesPurchaseAdmin` applies to its own FKs — moderation here
    means editing/removing `rating`/`comment` content (e.g. an abusive
    review), not rewiring which attempt/series/student a review row is
    attached to.
    """
    list_display = ["series", "student", "rating", "attempt", "created_at"]
    list_filter = ["rating"]
    search_fields = ["series__title", "student__username", "comment"]
    readonly_fields = ["series", "student", "attempt", "created_at", "updated_at"]

    def has_add_permission(self, request):
        return False