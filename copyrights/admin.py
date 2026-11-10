"""
copyrights/admin.py - the staff console, split by LEVEL (see permissions.py).

The day-to-day queue (deadlines, search, one-click decisions) is the MODERATOR DASHBOARD:
/admin/copyrights/copyrightclaim/dashboard/ (copyrights/dashboard.py). This admin stays the full record view.

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
from django.utils.html import format_html, format_html_join

from . import permissions as perms
from . import appeals, dashboard, retention, services
from . import targets
from .models import (
    BlockedMediaHash, CopyrightAppeal, CopyrightAuditLog, CopyrightClaim, CopyrightCounterNotice, CopyrightEvidence,
    CopyrightStanding, CopyrightStrike, CopyrightTakedown, FingerprintBand, MediaFingerprint, TargetHold,
    TrustedClaimant,
)


_IMG = (".jpg", ".jpeg", ".png", ".gif", ".webp")
_VID = (".mp4", ".mov", ".avi", ".webm")


def _render_items(items):
    """[(kind, url_or_text)] -> safe HTML the reviewer can look at without leaving the page."""
    rows = []
    for kind, value in items:
        if not value:
            continue
        if kind == "text":
            rows.append(format_html('<blockquote style="margin:.3em 0">{}</blockquote>', value))
        elif kind == "image":
            rows.append(format_html('<a href="{0}" target="_blank"><img src="{0}" style="max-width:360px;max-height:260px"></a>', value))
        elif kind == "video":
            rows.append(format_html('<video src="{}" controls preload="none" style="max-width:360px"></video>', value))
        else:
            rows.append(format_html('<a href="{0}" target="_blank">open file</a>', value))
    return format_html_join("", "<div>{}</div>", ((r,) for r in rows)) if rows else "-"


def _evidence_html(rows):
    out = []
    for ev in rows:
        try:
            url = ev.file.url
        except Exception:
            url = ""
        low = (ev.original_name or "").lower()
        kind = "image" if low.endswith(_IMG) else "video" if low.endswith(_VID) else "file"
        out.append(format_html("<div><strong>{}</strong> ({} KB) {}</div>", ev.original_name or ev.kind,
                               ev.size_bytes // 1024, _render_items([(kind, url)])))
    return format_html_join("", "{}", ((o,) for o in out)) if out else "-"


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
    list_filter = ("status", "priority", "required_level", "target_type", "source", "bad_faith", "retention_hold")
    search_fields = ("claimant_name", "claimant_email", "organisation", "target_id", "content_owner__username",
                     "work_description")
    date_hierarchy = "created_at"
    ordering = ("-priority", "created_at")
    actions = ("act_take", "act_info", "act_reject", "act_reject_bad_faith",
               "act_uphold_strike", "act_uphold_no_strike", "act_restore",
               "act_hold_retention", "act_release_retention", "act_erase_pii")
    _EXTRA = ("preview_current", "preview_proof", "preview_snapshot")

    def get_fields(self, request, obj=None):
        return list(self._EXTRA) + [f.name for f in self.model._meta.fields]

    def get_readonly_fields(self, request, obj=None):
        return list(self._EXTRA) + [f.name for f in self.model._meta.fields]

    @admin.display(description="1. The reported content (live)")
    def preview_current(self, obj):
        spec = targets.spec_for(obj.target_type)
        if spec is None:
            return "-"
        live = spec.load_any(obj.target_id)
        if live is None:
            return "The content no longer exists - see the saved copy below."
        hold = TargetHold.objects.filter(target_type=obj.target_type, target_id=obj.target_id).first()
        note = ""
        if hold is not None and hold.media_quarantined:
            note = format_html("<p><em>Files were moved to quarantine - use the saved copy below.</em></p>")
        owner = spec.owner(live)
        head = format_html("<p>Owner: <strong>{}</strong> &middot; hidden now: <strong>{}</strong></p>",
                           getattr(owner, "username", "?"), hold.level if hold else "no")
        return format_html("{}{}{}", head, note, _render_items(spec.preview(live, hold)))

    @admin.display(description="2. Claimant's proof")
    def preview_proof(self, obj):
        return _evidence_html(obj.evidence.exclude(kind="content_snapshot"))

    @admin.display(description="3. Saved copy of the content (made when the notice arrived)")
    def preview_snapshot(self, obj):
        rows = obj.evidence.filter(kind="content_snapshot")
        meta = obj.content_meta or {}
        top = format_html("<p>Text: <em>{}</em> &middot; owner at filing: {}</p>", obj.content_snapshot or "-",
                          meta.get("owner_username", "?"))
        return format_html("{}{}", top, _evidence_html(rows))

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

    def get_urls(self):
        # the moderator dashboard (copyrights/dashboard.py) lives under this admin, so it shares the login + 2FA
        return dashboard.urls(self) + super().get_urls()

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

    @admin.action(description="L3  LEGAL HOLD - do not erase the claimant's details (court case / dispute)")
    def act_hold_retention(self, request, queryset):
        note = self._note(request)
        _run(self, request, queryset, lambda c: retention.set_retention_hold(c, request.user, True, note),
             "Legal hold on")

    @admin.action(description="L3  ERASE the claimant's personal data now (erasure request - cannot be undone)")
    def act_erase_pii(self, request, queryset):
        note = self._note(request)
        _run(self, request, queryset, lambda c: retention.request_erasure(c, request.user, note), "Claimant data erased")

    @admin.action(description="L3  Release legal hold (details are erased when the retention period is over)")
    def act_release_retention(self, request, queryset):
        note = self._note(request)
        _run(self, request, queryset, lambda c: retention.set_retention_hold(c, request.user, False, note),
             "Legal hold released")


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


@admin.register(CopyrightAppeal)
class CopyrightAppealAdmin(_StaffOnlyAdmin):
    """Appeals against a strike or a termination. L3 only; the person who issued the strike /
    terminated the account can't decide the appeal (four-eyes). Reject needs a note."""

    min_level = perms.L3
    list_display = ("id", "kind", "user", "status", "created_at", "decided_by")
    list_filter = ("kind", "status")
    search_fields = ("user__username", "user__email", "contact_email")
    actions = ("act_accept", "act_reject")

    @admin.action(description="L3  Accept appeal (strike removed / account reinstated)")
    def act_accept(self, request, queryset):
        note = self._note(request)
        _run(self, request, queryset, lambda a: appeals.decide_appeal(a, request.user, True, note), "Accepted")

    @admin.action(description="L3  Reject appeal (note = reason, required)")
    def act_reject(self, request, queryset):
        note = self._note(request)
        _run(self, request, queryset, lambda a: appeals.decide_appeal(a, request.user, False, note), "Rejected")


@admin.register(CopyrightAuditLog)
class CopyrightAuditLogAdmin(_StaffOnlyAdmin):
    min_level = perms.L2
    action_form = helpers.ActionForm
    list_display = ("created_at", "actor_label", "action", "claim", "legal_order", "user", "note")
    list_filter = ("action", "actor_label")
    search_fields = ("note", "actor_label", "user__username")
    date_hierarchy = "created_at"


# --------------------------------------------------------------------------
# Court orders / government notices  (L2 registers, L3 decides)
# --------------------------------------------------------------------------
from . import legal_orders  # noqa: E402
from .models import LegalOrder, LegalOrderItem  # noqa: E402


class LegalOrderItemInline(admin.TabularInline):
    model = LegalOrderItem
    extra = 1
    fields = ("target_type", "target_id", "state")
    readonly_fields = ("state",)

    def has_add_permission(self, request, obj=None):
        return obj is None  # items are listed when the order is registered

    def has_change_permission(self, request, obj=None):
        return obj is None

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LegalOrder)
class LegalOrderAdmin(admin.ModelAdmin):
    """Register an order with 'Add' (L2+). Decide with the actions below (L3 only).
    The deadline is set automatically: received time + LEGAL_ORDER_SLA_HOURS."""

    action_form = NoteActionForm
    inlines = [LegalOrderItemInline]
    list_display = ("reference", "order_type", "authority", "status", "received_at", "due_at", "hours_left")
    list_filter = ("status", "order_type")
    search_fields = ("reference", "authority", "summary")
    date_hierarchy = "received_at"
    actions = ("act_review", "act_comply", "act_reject", "act_reverse")
    fields = ("reference", "order_type", "authority", "received_via", "received_at", "summary", "document", "notify_owner")

    def _level(self, request):
        return perms.level_for(request.user)

    def has_module_permission(self, request):
        return self._level(request) >= perms.L2

    def has_view_permission(self, request, obj=None):
        return self._level(request) >= perms.L2

    def has_add_permission(self, request):
        return self._level(request) >= perms.L2

    def has_change_permission(self, request, obj=None):
        return False  # evidence: view only after registration

    def has_delete_permission(self, request, obj=None):
        return False

    def get_fields(self, request, obj=None):
        if obj is None:
            return self.fields
        return self.fields + ("status", "due_at", "decided_by", "decided_at", "decision_note")

    def get_readonly_fields(self, request, obj=None):
        return self.get_fields(request, obj) if obj is not None else ()

    @admin.display(description="time left")
    def hours_left(self, obj):
        if not obj.is_open:
            return "-"
        h = int((obj.due_at - timezone.now()).total_seconds() // 3600)
        return f"{h}h" if h >= 0 else f"OVERDUE {-h}h"

    def save_model(self, request, obj, form, change):
        if not change:
            from datetime import timedelta
            from django.conf import settings

            obj.received_by = request.user
            obj.due_at = obj.received_at + timedelta(hours=getattr(settings, "LEGAL_ORDER_SLA_HOURS", 36))
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if change:
            return
        order = form.instance
        legal_orders.add_items(order, [(i.target_type, i.target_id) for i in order.items.all()])  # resolves owners
        legal_orders._olog(order, "order_registered", actor=request.user, note=f"{order.reference}, due {order.due_at:%Y-%m-%d %H:%M}")
        services._notify_staff(perms.L3, "Legal order received", f"{order.authority}: {order.reference}",
                               data={"role": "staff", "legal_order": order.id})

    def _note(self, request):
        return (request.POST.get("note") or "").strip()

    @admin.action(description="L2  Mark as in legal review")
    def act_review(self, request, queryset):
        _run(self, request, queryset, lambda o: legal_orders.start_review(o, request.user), "In review")

    @admin.action(description="L3  COMPLY - remove all listed content (no strike)")
    def act_comply(self, request, queryset):
        note = self._note(request)
        _run(self, request, queryset, lambda o: legal_orders.comply(o, request.user, note), "Complied")

    @admin.action(description="L3  Reject / challenge the order (note = reason)")
    def act_reject(self, request, queryset):
        note = self._note(request)
        _run(self, request, queryset, lambda o: legal_orders.reject_order(o, request.user, note), "Rejected")

    @admin.action(description="L3  Reverse a complied order - restore content (note = reason)")
    def act_reverse(self, request, queryset):
        note = self._note(request)
        _run(self, request, queryset, lambda o: legal_orders.reverse_order(o, request.user, note), "Reversed")



# --------------------------------------------------------------------------
# trusted publishers, holds, blocked hashes
# --------------------------------------------------------------------------
@admin.register(TrustedClaimant)
class TrustedClaimantAdmin(admin.ModelAdmin):
    """L2+ can vet a publisher / institute: their complete notices are held at once even without proof.
    Tier "Verified publisher (fast track)" adds queue priority, a shorter review deadline and higher
    daily limits. Set "valid until" to force a re-check; one bad-faith finding drops fast track automatically."""

    list_display = ("user", "organisation", "tier", "active", "valid_until", "added_by", "created_at")
    list_filter = ("tier", "active")
    search_fields = ("user__username", "organisation")
    raw_id_fields = ("user",)
    exclude = ("added_by",)

    def _ok(self, request):
        return perms.level_for(request.user) >= perms.L2

    has_module_permission = _ok
    has_view_permission = lambda self, request, obj=None: self._ok(request)  # noqa: E731
    has_add_permission = _ok
    has_change_permission = lambda self, request, obj=None: self._ok(request)  # noqa: E731
    has_delete_permission = lambda self, request, obj=None: perms.level_for(request.user) >= perms.L3  # noqa: E731

    def save_model(self, request, obj, form, change):
        if not change:
            obj.added_by = request.user
        super().save_model(request, obj, form, change)
        services._log(None, "trusted_claimant_changed", actor=request.user, user=obj.user,
                      note=f"{obj.organisation} tier={obj.tier} active={obj.active} valid_until={obj.valid_until}")


@admin.register(TargetHold)
class TargetHoldAdmin(_StaffOnlyAdmin):
    min_level = perms.L2
    list_display = ("target_type", "target_id", "level", "media_quarantined", "updated_at")
    list_filter = ("level", "target_type", "media_quarantined")
    search_fields = ("target_id",)


@admin.register(BlockedMediaHash)
class BlockedMediaHashAdmin(_StaffOnlyAdmin):
    min_level = perms.L2
    list_display = ("short", "active", "claim", "legal_order", "created_at")
    list_filter = ("active",)
    search_fields = ("sha256",)
    actions = ("act_deactivate",)

    @admin.display(description="sha256")
    def short(self, obj):
        return obj.sha256[:16] + "..."

    @admin.action(description="L3  Stop blocking this file (e.g. the uploader proved a licence)")
    def act_deactivate(self, request, queryset):
        if perms.level_for(request.user) < perms.L3:
            self.message_user(request, "You need copyright staff level 3.", messages.ERROR)
            return
        n = queryset.update(active=False)
        services._log(None, "hash_unblocked", actor=request.user, note=self._note(request) or f"{n} hash(es)")
        self.message_user(request, f"{n} hash(es) are no longer blocked.", messages.SUCCESS)


@admin.register(MediaFingerprint)
class MediaFingerprintAdmin(_StaffOnlyAdmin):
    """Perceptual fingerprints of REMOVED files (image / video / audio / PDF) - see copyrights/fingerprint.py."""
    min_level = perms.L2
    list_display = ("id", "kind", "algo", "active", "claim", "legal_order", "source_name", "created_at")
    list_filter = ("active", "kind", "algo")
    search_fields = ("source_sha256", "source_name")
    actions = ("act_deactivate",)

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    @admin.action(description="L3  Stop matching against this file (e.g. the uploader proved a licence)")
    def act_deactivate(self, request, queryset):
        if perms.level_for(request.user) < perms.L3:
            self.message_user(request, "You need copyright staff level 3.", messages.ERROR)
            return
        n = queryset.update(active=False)
        services._log(None, "fingerprint_unblocked", actor=request.user, note=self._note(request) or f"{n} fingerprint(s)")
        self.message_user(request, f"{n} fingerprint(s) are no longer matched.", messages.SUCCESS)


@admin.register(FingerprintBand)
class FingerprintBandAdmin(_StaffOnlyAdmin):
    """Lookup index of the fingerprints (internal) - only here so nothing in the app is unregistered."""
    min_level = perms.L3
    list_display = ("key", "fingerprint")
    search_fields = ("key",)
    list_select_related = ("fingerprint",)
