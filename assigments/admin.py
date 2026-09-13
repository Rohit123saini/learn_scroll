# assigments/admin.py
from django.contrib import admin

from .models import assigments, assigmentsAnswer, assigmentsQuestion, assigmentsSubmission


class assigmentsQuestionInline(admin.TabularInline):
    """Lets staff add/edit structured questions straight from the
    assigments admin page instead of a separate screen — matches how
    small the §2a question shape is (no reason to force a second page
    load per question)."""

    model = assigmentsQuestion
    extra = 0
    ordering = ["order"]


@admin.register(assigments)
class assigmentsAdmin(admin.ModelAdmin):
    list_display = [
        "title", "source", "context_type", "posted_by", "due_date",
        "has_structured_questions", "total_marks", "created_at",
    ]
    list_filter = ["source", "has_structured_questions", "context_type"]
    search_fields = ["title", "posted_by__username", "posted_by__email"]
    # total_marks is auto-derived (recompute_total_marks(), see models.py)
    # for the structured path — editing it directly in admin for a
    # structured assigments would just get silently overwritten on the
    # next question add/remove, so make that non-obviousness explicit
    # rather than letting an admin user "fix" a value that won't stick.
    readonly_fields = ["total_marks"]
    inlines = [assigmentsQuestionInline]


class assigmentsAnswerInline(admin.TabularInline):
    """Read-mostly — grading a `text` answer from here bypasses
    `assigmentsSubmission.mark_answer_and_maybe_finalize()`, which means
    the submission's own `status`/`total_marks_awarded` recompute would
    NOT run. `marks_awarded`/`reviewer_feedback` are left editable for
    emergency/manual correction only; the reviewed_by/reviewed_at pair
    stays read-only so an admin edit here never silently misattributes a
    review to whichever staff account happened to be logged into admin.
    Prefer the API's review endpoint (§7) for normal grading."""

    model = assigmentsAnswer
    extra = 0
    fields = ["question", "answer_data", "is_auto_graded", "is_correct", "marks_awarded", "reviewer_feedback"]
    readonly_fields = ["question", "answer_data", "is_auto_graded", "is_correct"]


@admin.register(assigmentsSubmission)
class assigmentsSubmissionAdmin(admin.ModelAdmin):
    list_display = [
        "student", "assigments", "status", "roll_number", "enrollment_no",
        "grade", "total_marks_awarded", "submitted_at", "checked_at",
    ]
    list_filter = ["status"]
    search_fields = ["student__username", "student__email", "roll_number", "enrollment_no"]
    # public_slug is only ever set via `.publish()` (which mints a fresh
    # token) — editing it by hand in admin would let someone hand out a
    # guessable/predictable public URL, defeating the whole point of
    # `secrets.token_urlsafe()`.
    readonly_fields = ["public_slug", "total_marks_awarded"]
    inlines = [assigmentsAnswerInline]


@admin.register(assigmentsQuestion)
class assigmentsQuestionAdmin(admin.ModelAdmin):
    """Registered standalone too (in addition to the inline above) for
    the case where staff need to find/fix one question across
    assigmentss rather than always going in through its parent."""

    list_display = ["assigments", "order", "question_type", "marks"]
    list_filter = ["question_type"]
    search_fields = ["text", "assigments__title"]