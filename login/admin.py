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

from .models import OTPVerification, User


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
        "date_joined",
    )
    list_filter = DjangoUserAdmin.list_filter + ("is_private", "is_verified")
    search_fields = DjangoUserAdmin.search_fields + ("phone",)


admin.site.register(OTPVerification)