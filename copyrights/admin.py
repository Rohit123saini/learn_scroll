"""
copyrights/admin.py - the staff console, split by LEVEL (see permissions.py).

Everything is read-only; decisions are made with the actions below, which call
copyrights.services (so the same level rules apply here, in the API and in the shell).
Type the reason in the "Note" box next to the action dropdown before you run it.

  L1  take for review, ask claimant for info, reject (simple claims)
  L2  uphold (remove content + strike), reject as bad-faith, restore held content,
      and every claim marked "needs L2" (verified / high-reach / repeat / overdue)
  L3  revoke strikes, reverse an upheld decision, terminate / reinstate accounts
"""

from django import forms
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from . import permissions as perms
from . import services
from .models import (
    CopyrightAuditLog, CopyrightClaim, CopyrightCounterNotice, CopyrightStanding,
    CopyrightStrike, CopyrightTakedown,
)


class NoteActionForm(helpers.ActionForm):
    note = forms.CharField(
        required=False, label="Note",
        widget=forms.TextInput(attrs={"size": 60, "placeholder": "Reason / message (needed for reject, info, restore, revoke)"}),
    )


def _run(modeladmin, request, queryset, fn, ok_msg):
    """Run `fn(obj)` for each row; report successes and the first few failures."""
    done, errors = 0, []
    for obj in queryset:
        try:
            fn(obj)
            done += 1
        except services.ClaimError as exc:
            errors.append(f"{obj}: {exc.message}")
        except PermissionDenied as exc:
            errors.append(str(exc))
            break
    if done:
        modeladmin.message_user(request, f"{ok_msg}: {done}", messages.SUCCESS)
    for e in errors[:5]:
        modeladmin.message_user(request, e, messages.ERROR)


class _StaffOnlyAdmin(admin.ModelAdmin):
    action_form = NoteActionForm
    min_level = perms.L1

    def _level(self, request):
        return perms.level_for(request.user)

    def has_module_permission(self, request):
        return self._level(request) >= self.min_level

    def has_view_permission(self, request, obj=None):
        return self._level(request) >= self.min_level

    # Records are evidence: never created, edited or deleted by hand.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def _note(self, request):
        return (request.POST.get("note") or "").strip()

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]


@admin.register(CopyrightClaim)
class CopyrightClaimAdmin(_StaffOnlyAdmin):
    list_display = ("short_id", "status", "priority", "required_level", "target_type", "claimant_name",
                    "content_owner", "bad_faith", "age", "created_at")
    list_filter = ("status", "priority", "required_level", "target_type", "source", "bad_faith")
    search_fields = ("claimant_name", "claimant_email", "organisation", "target_id", "content_owner__username",
                     "work_description")
    date_hierarchy = "created_at"
    ordering = ("-priority", "created_at")
    actions = ("act_take", "act_info", "act_reject", "act_reject_bad_faith",
               "act_uphold_strike", "act_uphold_no_strike", "act_restore")

    @admin.display(description="id")
    def short_id(self, obj):
        return str(obj.id)[:8]

    @admin.display(description="open for")
    def age(self, obj):
        end = obj.decided_at or timezone.now()
        hours = int((end - obj.created_at).total_seconds() // 3600)
        return f"{hours}h" if hours < 48 else f"{hours // 24}d"

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("content_owner", "claimant")
        if self._level(request) < perms.L2:
            qs = qs.filter(required_level=1)  # L1 only sees the simple claims
        return qs

    @admin.action(description="L1  Take for review")
    def act_take(self, request, queryset):
        _run(self, request, queryset, lambda c: services.take_for_review(c, request.user), "Taken for review")

    @admin.action(description="L1  Ask claimant for more info (note = what is missing)")
    def act_info(self, request, queryset):
        note = self._note(request)
        _run(self, request, queryset, lambda c: services.request_info(c, request.user, note), "Info requested")

    @admin.action(description="L1/L2  Reject claim (note = reason; content comes back)")
    def act_reject(self, request, queryset):
        note = self._note(request)
        _run(self, request, queryset, lambda c: services.reject_claim(c, request.user, note), "Rejected")

    @admin.action(description="L2  Reject as BAD FAITH (false notice, counts against the claimant)")
    def act_reject_bad_faith(self, request, queryset):
        note = self._note(request)
        _run(self, request, queryset,
             lambda c: services.reject_claim(c, request.user, note, bad_faith=True), "Rejected (bad faith)")

    @admin.action(description="L2  UPHOLD - remove content + give a strike")
    def act_uphold_strike(self, request, queryset):
        note = self._note(request)
        _run(self, request, queryset, lambda c: services.uphold_claim(c, request.user, note), "Upheld")

    @admin.action(description="L2  UPHOLD - remove content, NO strike (first-time / minor)")
    def act_uphold_no_strike(self, request, queryset):
        note = self._note(request)
        _run(self, request, queryset,
             lambda c: services.uphold_claim(c, request.user, note, issue_strike=False), "Upheld (no strike)")

    @admin.action(description="L2/L3  Restore content (L3 needed to reverse an upheld claim)")
    def act_restore(self, request, queryset):
        note = self._note(request)
        _run(self, request, queryset, lambda c: services.restore_claim(c, request.user, note), "Restored")


@admin.register(CopyrightTakedown)
class CopyrightTakedownAdmin(_StaffOnlyAdmin):
    min_level = perms.L2
    list_display = ("claim", "target_type", "target_id", "state", "auto_applied", "applied_at", "restored_at")
    list_filter = ("state", "target_type", "auto_applied")
    search_fields = ("target_id", "claim__claimant_name")


@admin.register(CopyrightCounterNotice)
class CopyrightCounterNoticeAdmin(_StaffOnlyAdmin):
    min_level = perms.L2
    list_display = ("claim", "user", "status", "filed_at", "restore_after")
    list_filter = ("status",)
    search_fields = ("user__username", "claim__claimant_name")


@admin.register(CopyrightStrike)
class CopyrightStrikeAdmin(_StaffOnlyAdmin):
    list_display = ("user", "claim", "issued_at", "expires_at", "active", "revoked_at")
    list_filter = ("revoked_at",)
    search_fields = ("user__username",)
    actions = ("act_revoke",)

    @admin.display(boolean=True, description="active")
    def active(self, obj):
        return obj.is_active

    @admin.action(description="L3  Revoke strike (note = reason)")
    def act_revoke(self, request, queryset):
        note = self._note(request)
        _run(self, request, queryset, lambda s: services.revoke_strike(s, request.user, note), "Revoked")


@admin.register(CopyrightStanding)
class CopyrightStandingAdmin(_StaffOnlyAdmin):
    list_display = ("user", "level", "active_strikes", "terminated_at", "updated_at")
    list_filter = ("level",)
    search_fields = ("user__username",)
    actions = ("act_terminate", "act_reinstate")

    @admin.action(description="L3  Terminate account (repeat infringer)")
    def act_terminate(self, request, queryset):
        note = self._note(request)
        _run(self, request, queryset, lambda s: services.terminate_account(s.user, request.user, note), "Terminated")

    @admin.action(description="L3  Reinstate account")
    def act_reinstate(self, request, queryset):
        note = self._note(request)
        _run(self, request, queryset, lambda s: services.reinstate_account(s.user, request.user, note), "Reinstated")


@admin.register(CopyrightAuditLog)
class CopyrightAuditLogAdmin(_StaffOnlyAdmin):
    min_level = perms.L2
    action_form = helpers.ActionForm
    list_display = ("created_at", "actor_label", "action", "claim", "user", "note")
    list_filter = ("action", "actor_label")
    search_fields = ("note", "actor_label", "user__username")
    date_hierarchy = "created_at"
