"""
liveclass/admin.py

Admin site registrations for every model in liveclass/models.py.
"""

from django.contrib import admin
from django.contrib.auth import get_user_model

from .models import (
    Assignment,
    AssignmentSubmission,
    BreakoutRoom,
    Certificate,
    ChatMessage,
    ClassHoliday,
    ClassJoinRequest,
    ClassMaterial,
    ClassPass,
    ClassQuery,
    ClassReminder,
    ClassSchedule,
    ClassSession,
    Classroom,
    ClassroomBan,
    ClassroomReport,
    ClassroomReview,
    ClassroomStaff,
    ClassroomWishlist,
    CoinTransaction,
    Coupon,
    LivePoll,
    Notice,
    Notification,
    PassDailyCharge,
    PassPurchase,
    PollResponse,
    Referral,
    SessionParticipant,
    SessionWaitlist,
)


# ---------------------------------------------------------------------------
# FIX (production readiness audit — admin.E040 on `makemigrations`/`runserver`):
# every ModelAdmin below that autocompletes a User FK (student, teacher,
# sender, banned_by, decided_by, referrer, ...) is only valid if the User
# model's OWN registered ModelAdmin defines `search_fields` — Django's
# autocomplete widget searches the *target* model's admin, not the one
# declaring `autocomplete_fields`. That's genuinely not this file's model
# (whichever app registers the project's User model, e.g. accounts/admin.py,
# isn't part of this file), so it can't be fixed by editing the classes
# below — they're already correct. Rather than requiring that other app's
# admin.py to also be edited (and risking a second, unrelated
# AlreadyRegistered crash if this ran before that admin.py's own
# `@admin.register(User)` executes), this patches whatever ModelAdmin ends
# up registered for the User model in place, right here, at import time:
#   - if User is already registered (the normal case) and its ModelAdmin
#     has no search_fields, this adds one without replacing the class, so
#     any other customization on that admin (list_display, inlines, etc.)
#     is untouched.
#   - if User isn't registered at all yet, this registers a minimal admin
#     for it so the autocomplete_fields below have something to search.
# Adjust the field list if the project's User model doesn't use
# username/email/first_name/last_name.
# ---------------------------------------------------------------------------
_User = get_user_model()
_user_search_fields = ["username", "email", "first_name", "last_name"]

if admin.site.is_registered(_User):
    _user_admin = admin.site._registry[_User]
    if not getattr(_user_admin, "search_fields", None):
        _user_admin.search_fields = _user_search_fields
else:
    @admin.register(_User)
    class _AutoUserAdmin(admin.ModelAdmin):
        search_fields = _user_search_fields


# ---------------------------------------------------------------------------
# Inlines — small related tables shown directly on their parent's page
# ---------------------------------------------------------------------------
class ClassScheduleInline(admin.TabularInline):
    model = ClassSchedule
    extra = 0
    fields = ("recurrence_type", "start_date", "end_date", "start_time", "duration_minutes", "is_active")
    show_change_link = True


class ClassPassInline(admin.TabularInline):
    model = ClassPass
    extra = 0
    fields = ("pass_type", "title", "price", "validity_days", "max_classes", "is_active")
    show_change_link = True


class ClassroomStaffInline(admin.TabularInline):
    model = ClassroomStaff
    extra = 0
    autocomplete_fields = ["user"]


class PassDailyChargeInline(admin.TabularInline):
    model = PassDailyCharge
    extra = 0
    fields = ("date", "amount", "session", "created_at")
    readonly_fields = ("date", "amount", "session", "created_at")
    can_delete = False
    show_change_link = True


# ---------------------------------------------------------------------------
# 1. Classroom
# ---------------------------------------------------------------------------
@admin.register(Classroom)
class ClassroomAdmin(admin.ModelAdmin):
    list_display = (
        "title", "teacher", "classroom_type", "language",
        "rating_avg", "rating_count", "enrolled_count",
        "is_active", "is_flagged", "is_deleted", "created_at",
    )
    list_filter = ("classroom_type", "language", "is_active", "is_flagged", "is_deleted")
    search_fields = ("title", "subject", "organisation_name", "teacher__username", "teacher__email")
    autocomplete_fields = ["teacher"]
    readonly_fields = ("rating_avg", "rating_count", "enrolled_count", "created_at", "updated_at", "deleted_at")
    date_hierarchy = "created_at"
    inlines = [ClassScheduleInline, ClassPassInline, ClassroomStaffInline]
    actions = ["flag_classrooms", "unflag_classrooms"]

    @admin.action(description="Flag selected classrooms (hide from Explore)")
    def flag_classrooms(self, request, queryset):
        queryset.update(is_flagged=True)

    @admin.action(description="Unflag selected classrooms")
    def unflag_classrooms(self, request, queryset):
        queryset.update(is_flagged=False)


# ---------------------------------------------------------------------------
# 2. ClassSchedule
# ---------------------------------------------------------------------------
@admin.register(ClassSchedule)
class ClassScheduleAdmin(admin.ModelAdmin):
    list_display = ("classroom", "recurrence_type", "start_date", "end_date", "start_time", "duration_minutes", "is_active")
    list_filter = ("recurrence_type", "is_active")
    search_fields = ("classroom__title",)
    autocomplete_fields = ["classroom"]


# ---------------------------------------------------------------------------
# 3. ClassSession
# ---------------------------------------------------------------------------
@admin.register(ClassSession)
class ClassSessionAdmin(admin.ModelAdmin):
    list_display = ("classroom", "status", "scheduled_start", "scheduled_end", "actual_start", "actual_end", "room_id")
    list_filter = ("status",)
    search_fields = ("classroom__title", "room_id")
    autocomplete_fields = ["classroom", "schedule"]
    readonly_fields = ("room_id", "created_at")
    date_hierarchy = "scheduled_start"


# ---------------------------------------------------------------------------
# 4. ClassPass
# ---------------------------------------------------------------------------
@admin.register(ClassPass)
class ClassPassAdmin(admin.ModelAdmin):
    list_display = ("classroom", "pass_type", "title", "price", "validity_days", "max_classes", "is_active")
    list_filter = ("pass_type", "is_active")
    search_fields = ("classroom__title", "title")
    autocomplete_fields = ["classroom"]


# ---------------------------------------------------------------------------
# 5. PassPurchase
# ---------------------------------------------------------------------------
@admin.register(PassPurchase)
class PassPurchaseAdmin(admin.ModelAdmin):
    list_display = (
        "student", "class_pass", "status", "payment_method",
        "amount_paid", "coins_spent", "coins_released", "remaining_balance_display",
        "purchased_at", "expires_at", "is_active",
    )
    list_filter = ("status", "payment_method", "is_active")
    search_fields = ("student__username", "student__email", "class_pass__classroom__title", "transaction_id")
    autocomplete_fields = ["student", "class_pass", "coupon"]
    readonly_fields = ("purchased_at", "per_day_rate", "coins_released", "last_charge_date")
    date_hierarchy = "purchased_at"
    inlines = [PassDailyChargeInline]
    actions = ["reverse_purchases"]

    @admin.display(description="Remaining balance")
    def remaining_balance_display(self, obj):
        return obj.remaining_balance

    @admin.action(description="Reverse/refund selected purchases")
    def reverse_purchases(self, request, queryset):
        for purchase in queryset:
            purchase.reverse()


# ---------------------------------------------------------------------------
# 5A. PassDailyCharge
# ---------------------------------------------------------------------------
@admin.register(PassDailyCharge)
class PassDailyChargeAdmin(admin.ModelAdmin):
    list_display = ("purchase", "date", "amount", "session", "created_at")
    list_filter = ("date",)
    search_fields = ("purchase__student__username", "purchase__class_pass__classroom__title")
    autocomplete_fields = ["purchase", "session"]
    readonly_fields = ("created_at",)
    date_hierarchy = "date"


# ---------------------------------------------------------------------------
# 5B. ClassJoinRequest
# ---------------------------------------------------------------------------
@admin.register(ClassJoinRequest)
class ClassJoinRequestAdmin(admin.ModelAdmin):
    list_display = ("student", "classroom", "class_pass", "status", "decided_by", "requested_at", "decided_at")
    list_filter = ("status",)
    search_fields = ("student__username", "classroom__title", "coupon_code")
    autocomplete_fields = ["classroom", "class_pass", "student", "pass_purchase", "decided_by"]
    readonly_fields = ("requested_at",)
    date_hierarchy = "requested_at"


# ---------------------------------------------------------------------------
# 6B. BreakoutRoom
# ---------------------------------------------------------------------------
@admin.register(BreakoutRoom)
class BreakoutRoomAdmin(admin.ModelAdmin):
    list_display = ("session", "room_number", "created_at")
    search_fields = ("session__classroom__title",)
    autocomplete_fields = ["session"]


# ---------------------------------------------------------------------------
# 6. SessionParticipant
# ---------------------------------------------------------------------------
@admin.register(SessionParticipant)
class SessionParticipantAdmin(admin.ModelAdmin):
    list_display = ("user", "session", "role", "joined_at", "left_at", "kicked_at", "hand_raised_at", "breakout_room")
    list_filter = ("role",)
    search_fields = ("user__username", "session__classroom__title")
    autocomplete_fields = ["session", "user", "breakout_room"]


# ---------------------------------------------------------------------------
# 7. ClassMaterial
# ---------------------------------------------------------------------------
@admin.register(ClassMaterial)
class ClassMaterialAdmin(admin.ModelAdmin):
    list_display = ("title", "classroom", "session", "material_type", "uploaded_by", "uploaded_at")
    list_filter = ("material_type",)
    search_fields = ("title", "classroom__title")
    autocomplete_fields = ["classroom", "session", "uploaded_by"]
    readonly_fields = ("uploaded_at",)


# ---------------------------------------------------------------------------
# 8. ChatMessage
# ---------------------------------------------------------------------------
@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("sender", "session", "short_message", "sent_at", "is_deleted")
    list_filter = ("is_deleted",)
    search_fields = ("sender__username", "message", "session__classroom__title")
    autocomplete_fields = ["session", "sender"]
    readonly_fields = ("sent_at",)

    @admin.display(description="Message")
    def short_message(self, obj):
        return obj.message[:50]


# ---------------------------------------------------------------------------
# 9. LivePoll / PollResponse
# ---------------------------------------------------------------------------
@admin.register(LivePoll)
class LivePollAdmin(admin.ModelAdmin):
    list_display = ("question", "session", "created_by", "is_active", "created_at", "closed_at")
    list_filter = ("is_active",)
    search_fields = ("question", "session__classroom__title")
    autocomplete_fields = ["session", "created_by"]
    readonly_fields = ("created_at",)


@admin.register(PollResponse)
class PollResponseAdmin(admin.ModelAdmin):
    list_display = ("poll", "student", "selected_option_index", "answered_at")
    search_fields = ("student__username", "poll__question")
    autocomplete_fields = ["poll", "student"]
    readonly_fields = ("answered_at",)


# ---------------------------------------------------------------------------
# 10. Assignment / AssignmentSubmission
# ---------------------------------------------------------------------------
@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ("title", "classroom", "session", "due_date", "max_score", "created_at")
    search_fields = ("title", "classroom__title")
    autocomplete_fields = ["classroom", "session"]
    readonly_fields = ("created_at",)
    date_hierarchy = "due_date"


@admin.register(AssignmentSubmission)
class AssignmentSubmissionAdmin(admin.ModelAdmin):
    list_display = ("assignment", "student", "submitted_at", "score", "graded_at", "is_late_display")
    search_fields = ("student__username", "assignment__title")
    autocomplete_fields = ["assignment", "student"]
    readonly_fields = ("submitted_at",)

    @admin.display(description="Late?", boolean=True)
    def is_late_display(self, obj):
        return obj.is_late()


# ---------------------------------------------------------------------------
# 11. ClassroomReview / ClassroomWishlist
# ---------------------------------------------------------------------------
@admin.register(ClassroomReview)
class ClassroomReviewAdmin(admin.ModelAdmin):
    list_display = ("classroom", "student", "rating", "created_at")
    list_filter = ("rating",)
    search_fields = ("classroom__title", "student__username", "comment")
    autocomplete_fields = ["classroom", "student"]
    readonly_fields = ("created_at",)


@admin.register(ClassroomWishlist)
class ClassroomWishlistAdmin(admin.ModelAdmin):
    list_display = ("user", "classroom", "created_at")
    search_fields = ("user__username", "classroom__title")
    autocomplete_fields = ["user", "classroom"]
    readonly_fields = ("created_at",)


# ---------------------------------------------------------------------------
# 12. Coupon
# ---------------------------------------------------------------------------
@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = (
        "code", "classroom", "created_by", "discount_percent", "discount_amount",
        "valid_from", "valid_until", "used_count", "max_uses", "is_active",
    )
    list_filter = ("is_active",)
    search_fields = ("code", "classroom__title", "created_by__username")
    autocomplete_fields = ["classroom", "created_by"]
    readonly_fields = ("used_count",)


# ---------------------------------------------------------------------------
# 13. CoinTransaction
# ---------------------------------------------------------------------------
@admin.register(CoinTransaction)
class CoinTransactionAdmin(admin.ModelAdmin):
    list_display = ("user", "txn_type", "reason", "amount", "balance_after", "reference_id", "created_at")
    list_filter = ("txn_type", "reason")
    search_fields = ("user__username", "reference_id")
    autocomplete_fields = ["user"]
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"


# ---------------------------------------------------------------------------
# 14. ClassroomStaff
# ---------------------------------------------------------------------------
@admin.register(ClassroomStaff)
class ClassroomStaffAdmin(admin.ModelAdmin):
    list_display = ("classroom", "user", "role", "added_at")
    list_filter = ("role",)
    search_fields = ("classroom__title", "user__username")
    autocomplete_fields = ["classroom", "user"]
    readonly_fields = ("added_at",)


# ---------------------------------------------------------------------------
# 15. SessionWaitlist
# ---------------------------------------------------------------------------
@admin.register(SessionWaitlist)
class SessionWaitlistAdmin(admin.ModelAdmin):
    list_display = ("session", "student", "joined_at", "notified")
    list_filter = ("notified",)
    search_fields = ("session__classroom__title", "student__username")
    autocomplete_fields = ["session", "student"]
    readonly_fields = ("joined_at",)


# ---------------------------------------------------------------------------
# 15B. ClassroomReport
# ---------------------------------------------------------------------------
@admin.register(ClassroomReport)
class ClassroomReportAdmin(admin.ModelAdmin):
    list_display = ("classroom", "reported_by", "reason", "status", "reviewed_by", "created_at", "reviewed_at")
    list_filter = ("reason", "status")
    search_fields = ("classroom__title", "reported_by__username", "description")
    autocomplete_fields = ["classroom", "reported_by", "reviewed_by"]
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"


# ---------------------------------------------------------------------------
# 16. Certificate
# ---------------------------------------------------------------------------
@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = ("classroom", "student", "certificate_id", "issued_at")
    search_fields = ("classroom__title", "student__username", "certificate_id")
    autocomplete_fields = ["classroom", "student"]
    readonly_fields = ("certificate_id", "issued_at")


# ---------------------------------------------------------------------------
# 17. ClassReminder
# ---------------------------------------------------------------------------
@admin.register(ClassReminder)
class ClassReminderAdmin(admin.ModelAdmin):
    list_display = ("user", "session", "remind_at", "channel", "is_sent")
    list_filter = ("channel", "is_sent")
    search_fields = ("user__username", "session__classroom__title")
    autocomplete_fields = ["session", "user"]
    date_hierarchy = "remind_at"


# ---------------------------------------------------------------------------
# 18. ClassHoliday
# ---------------------------------------------------------------------------
@admin.register(ClassHoliday)
class ClassHolidayAdmin(admin.ModelAdmin):
    list_display = ("classroom", "schedule", "date", "reason", "created_by", "created_at")
    search_fields = ("classroom__title", "reason")
    autocomplete_fields = ["classroom", "schedule", "created_by"]
    readonly_fields = ("created_at",)
    date_hierarchy = "date"


# ---------------------------------------------------------------------------
# 19. Notice
# ---------------------------------------------------------------------------
@admin.register(Notice)
class NoticeAdmin(admin.ModelAdmin):
    list_display = ("title", "classroom", "posted_by", "priority", "is_pinned", "created_at", "expires_at")
    list_filter = ("priority", "is_pinned")
    search_fields = ("title", "message", "classroom__title")
    autocomplete_fields = ["classroom", "posted_by"]
    readonly_fields = ("created_at",)


# ---------------------------------------------------------------------------
# 20. ClassQuery
# ---------------------------------------------------------------------------
@admin.register(ClassQuery)
class ClassQueryAdmin(admin.ModelAdmin):
    list_display = ("classroom", "session", "asked_by", "status", "answered_by", "created_at", "answered_at")
    list_filter = ("status",)
    search_fields = ("classroom__title", "asked_by__username", "question", "answer")
    autocomplete_fields = ["classroom", "session", "asked_by", "answered_by"]
    readonly_fields = ("created_at",)


# ---------------------------------------------------------------------------
# 21. Notification
# ---------------------------------------------------------------------------
@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "notif_type", "title", "classroom", "session", "is_read", "created_at")
    list_filter = ("notif_type", "is_read")
    search_fields = ("recipient__username", "title", "message")
    autocomplete_fields = ["recipient", "classroom", "session"]
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"


# ---------------------------------------------------------------------------
# 22. Referral
#
# NOTE (fix — was importable, un-admin'd dead-in-the-admin-panel model):
# Referral has existed in models.py (and been used by ReferralViewSet /
# tasks.py's referral-bonus crediting) without any admin registration —
# support staff had no way to look up "did user X's referral actually
# record" without a shell. Read-mostly here on purpose: bonus_amount and
# the referrer/referred pairing are a financial record tied to real
# CoinTransaction rows already created at redemption time, so editing them
# after the fact would desync the two — hence everything but nothing is
# left editable via `readonly_fields` beyond the normal auto_now_add field.
# ---------------------------------------------------------------------------
@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ("referrer", "referred", "bonus_amount", "created_at")
    search_fields = ("referrer__username", "referred__username")
    autocomplete_fields = ["referrer", "referred"]
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"


# ---------------------------------------------------------------------------
# 23. ClassroomBan
#
# NOTE (fix — same gap as Referral above): ClassroomBan gates join-request
# creation, session join, and Classroom.has_access() (see the model's own
# docstring in models.py), but had no admin registration — a support agent
# handling a "why can't I rejoin this classroom" ticket, or a teacher
# dispute over a ban, had no read path into this table without a shell.
# banned_by is included in list_display/search since "who banned this
# person" is exactly what a dispute review needs first.
# ---------------------------------------------------------------------------
@admin.register(ClassroomBan)
class ClassroomBanAdmin(admin.ModelAdmin):
    list_display = ("classroom", "student", "banned_by", "reason", "created_at")
    search_fields = ("classroom__title", "student__username", "banned_by__username", "reason")
    autocomplete_fields = ["classroom", "student", "banned_by"]
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"