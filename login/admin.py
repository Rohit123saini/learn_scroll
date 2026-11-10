# login/admin.py
"""
TASK 39 — proper UserAdmin for the custom `User` model, instead of the
bare `admin.site.register(User)` (which gives every field a flat,
unstyled change form with no grouping, no password-hash-safe widget,
and no add-user flow — the default ModelAdmin's add page shows a raw
`password` CharField, i.e. a new admin-created user's password would
be saved as plaintext unless someone remembers to hash it by hand).

Subclassed off `django.contrib.auth.admin.UserAdmin` (not written from
scratch) so we keep, for free: the two-step "add user" form
(username + password1/password2, hashed via `UserCreationForm`), the
"change password" link instead of an editable raw hash field
(`UserChangeForm`), and permission/group management UI.

Only change from stock `UserAdmin`: its `fieldsets`/`add_fieldsets`
are written for a bare `AbstractUser` and know nothing about this
project's added fields (`phone`, `email` being unique now, `bio`,
`profile_photo`, `is_private`, `is_verified`, and the four denormalized
counters). Extended, not replaced, by appending a fieldset — so any
upstream Django change to the base fieldsets still applies.

`followers_count` / `following_count` / `posts_count` / `coin` are
listed in `readonly_fields`, NOT left editable. See models.py's own
docstring (§4): these are denormalized counters that must only ever
move via an atomic `F('...') + 1` update from the Follow/Post/
CoinLedger write paths. The admin's `save_model()` does a plain
`obj.save()` with whatever value sits in the form field — editing one
of these here would be exactly the read-modify-write the model
docstring warns against (two admins/tabs editing the same user
concurrently would silently drop one increment). Read-only in the
admin makes that invariant impossible to violate by accident instead
of just documenting it; the counters are still visible for
debugging/support, just not hand-editable.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.db.models import Count, Q

from . import audit
from .models import AuthAuditLog, AuthToken, OTPVerification, User


class BlockedByManyFilter(admin.SimpleListFilter):
    """Abuse signal: accounts that many different people blocked.

    One block is noise; a pile of blocks (or blocks plus reports) from
    unrelated people is how a harasser / spammer shows up. Combine with the
    "reports" column and the Content reports admin to decide on action.
    """

    title = "blocked by"
    parameter_name = "blocked_by"

    def lookups(self, request, model_admin):
        return (("1", "1 or more people"), ("5", "5 or more"), ("20", "20 or more"), ("50", "50 or more"))

    def queryset(self, request, queryset):
        if self.value():
            try:
                return queryset.filter(blocked_by_n__gte=int(self.value()))
            except ValueError:
                return queryset
        return queryset


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    # Stock UserAdmin fieldsets, plus one appended group for this
    # project's own fields. `fieldsets`/`add_fieldsets` are tuples, so
    # concatenate rather than mutate — keeps whatever the installed
    # Django version ships in DjangoUserAdmin.fieldsets untouched.
    fieldsets = DjangoUserAdmin.fieldsets + (
        (
            "Profile",
            {
                "fields": (
                    "phone",
                    "bio",
                    "profile_photo",
                    "is_private",
                    "is_verified",
                    "verified_reason",  # P1: WHY the tick was given (shown on the profile)
                    "cover_photo",
                    "location",
                    "username_changed_at",
                )
            },
        ),
        (
            "Denormalized counters (read-only — see models.py)",
            {
                "fields": (
                    "followers_count",
                    "following_count",
                    "posts_count",
                    "coin",
                )
            },
        ),
    )

    # email is unique now (models.py §3) — surfacing it in the add form
    # lets a duplicate get caught by the form's own validation instead
    # of only failing later as a raw DB IntegrityError.
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("Profile", {"fields": ("phone", "email")}),
    )

    readonly_fields = DjangoUserAdmin.readonly_fields + (
        "followers_count",
        "following_count",
        "posts_count",
        "coin",
    )

    list_display = (
        "username",
        "email",
        "phone",
        "is_staff",
        "is_verified",
        "is_private",
        "blocked_by_count",
        "reports_count",
        "date_joined",
    )
    list_filter = DjangoUserAdmin.list_filter + ("is_private", "is_verified", BlockedByManyFilter)

    def get_queryset(self, request):
        # Two annotated counts for the abuse columns. distinct=True because
        # two joins multiply rows against each other.
        return super().get_queryset(request).annotate(
            blocked_by_n=Count("blocked_by_relation", distinct=True),
            reports_n=Count("reports_received", distinct=True),
        )

    @admin.display(description="Blocked by", ordering="blocked_by_n")
    def blocked_by_count(self, obj):
        return obj.blocked_by_n

    @admin.display(description="Reports", ordering="reports_n")
    def reports_count(self, obj):
        return obj.reports_n
    search_fields = DjangoUserAdmin.search_fields + ("phone",)


admin.site.register(OTPVerification)

@admin.register(AuthToken)
class AuthTokenAdmin(admin.ModelAdmin):
    """L1 gap 11 -- support view of live sessions. Deleting a row signs that device out
    (the next request answers TOKEN_EXPIRED); nothing here is editable on purpose."""

    list_display = ("user", "device_name", "platform", "ip_address", "created_at", "last_used_at")
    list_filter = ("platform",)
    search_fields = ("user__username", "user__email", "device_name", "ip_address", "device_id")
    readonly_fields = (
        "user", "jti", "device_id", "device_name", "platform", "ip_address",
        "user_agent", "created_at", "last_used_at",
    )
    ordering = ("-last_used_at",)
    list_select_related = ("user",)

    def has_add_permission(self, request):
        return False


@admin.register(AuthAuditLog)
class AuthAuditLogAdmin(admin.ModelAdmin):
    """L1 gap 19 -- read-only view of the login / OTP / reset / session audit trail.

    Nothing here is editable or deletable on purpose (old rows are purged by
    login.tasks.purge_auth_audit_logs). Useful filters: outcome = failure / blocked to see
    attacks, event = reset_success to see who reset a password and from which device.
    """

    list_display = ("created_at", "event", "outcome", "user", "identifier_masked", "reason", "ip_address", "platform")
    list_filter = ("outcome", "event", "platform", "created_at")
    date_hierarchy = "created_at"
    search_fields = ("user__username", "identifier_masked", "ip_address", "device_id", "reason")
    search_help_text = (
        "Username, masked identifier, IP, device id or reason - or paste a FULL email / phone "
        "to find every row for it (matched by hash)."
    )
    ordering = ("-created_at", "-id")
    list_select_related = ("user",)
    list_per_page = 50
    show_full_result_count = False  # COUNT(*) over a big, growing table on every page load
    readonly_fields = tuple(f.name for f in AuthAuditLog._meta.fields)

    def get_search_results(self, request, queryset, search_term):
        results, may_have_duplicates = super().get_search_results(request, queryset, search_term)
        term = (search_term or "").strip()
        if term:
            digest = audit.identifier_hash(term)
            if digest:
                # one query (no QuerySet OR-combine): text matches OR the keyed-hash match
                results = queryset.filter(Q(pk__in=results.values("pk")) | Q(identifier_hash=digest))
        return results, may_have_duplicates

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
