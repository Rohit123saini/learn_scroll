# liveclass/permissions.py — ADDITIVE snippet (Task 6)
"""
This is a drop-in class, not a full file — real `liveclass/permissions.py`
wasn't uploaded, so this is written to be pasted into your existing file
(alongside whatever `IsClassroomManager`/other permission classes you
already have there) rather than replacing it. Just copy the import below
+ the class into your real file.
"""

from rest_framework.permissions import BasePermission

from core.classroom_chat_bridge import resolve_parent_from_token


class HasValidParentSessionToken(BasePermission):
    """
    Permission for parent-facing liveclass endpoints (Phase 2 — "parent as
    a one-to-one live-session observer"). Reads the `X-Parent-Token`
    header and resolves it via `core.classroom_chat_bridge.
    resolve_parent_from_token()` (Task 5) — `liveclass` never imports
    `message.models` directly, same cross-app invariant this whole
    project follows everywhere else.

    On success, attaches to `request` exactly like `message` app's own
    `HasValidParentToken` does (see CHAT_APP_DOCUMENTATION.md §7.16) —
    same attribute names, so any code/tests already familiar with that
    contract transfer straight over:

        request.parent_access_code -> the resolved ParentAccessCode
        request.parent_student     -> the linked student (a `login.User`)

    Deliberately does NOT touch `request.user` and does NOT require
    `IsAuthenticated` — a parent has no `User` row in this system at all.
    Any view using this permission must read `request.parent_student`,
    never `request.user`, to know who is asking.

    ⚠️ SCOPE LIMIT — this class only proves "this is a legitimate parent
    of *some* student, with a live, non-expired token". It does NOT check
    that `request.parent_student` actually has access to whatever
    classroom/session the view is about — a parent token is valid
    platform-wide, not per-classroom. Every view using this permission is
    responsible for that second, endpoint-specific check itself (e.g. "is
    `request.parent_student` a participant with an active pass for THIS
    classroom?") — this class can't do it generically since different
    endpoints scope it differently (classroom vs. session vs. a specific
    student). See the planned `ClassSessionViewSet.parent_join` (Task 9)
    for where that second check belongs.
    """

    message = "Invalid ya expired parent token."

    def has_permission(self, request, view) -> bool:
        token = request.headers.get('X-Parent-Token')
        resolution = resolve_parent_from_token(token)
        if resolution is None:
            return False

        request.parent_access_code = resolution.parent_access_code
        request.parent_student = resolution.student
        return True