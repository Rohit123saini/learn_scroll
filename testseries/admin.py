# testseries/admin.py
from django.contrib import admin

from .models import Question, QuestionResponse, TestAttempt, TestSeries, TestSeriesPurchase


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