"""
doubts/adapters/base.py — one adapter per doubt system.

An adapter does two things and nothing else:
  * READ  — build the viewer's *visibility-scoped* queryset for its system,
            apply the hub filters, and normalise rows with `hub.make_item`.
  * WRITE — translate a unified verb into a call to that system's own view
            (`dispatch.call_view`).

The base class implements the generic list/get/summary flow; subclasses
supply only the system-specific pieces (see the hook methods).
"""
from __future__ import annotations

from django.db.models import Count, Q

from .. import hub


def user_payload(user, request=None):
    """Minimal public identity for a user, or None."""
    if user is None:
        return None
    avatar = None
    for attr in ("profile_photo", "profile_picture", "avatar", "image"):
        field = getattr(user, attr, None)
        if field:
            try:
                url = field.url
                avatar = request.build_absolute_uri(url) if request is not None else url
            except Exception:  # no file / storage error -> just no avatar
                avatar = None
            break
    full_name = ""
    try:
        full_name = (user.get_full_name() or "").strip()
    except Exception:
        pass
    return {
        "id": str(user.pk),
        "username": getattr(user, "username", ""),
        "name": full_name or getattr(user, "username", ""),
        "avatar": avatar,
    }


# Matches nothing, for statuses a system cannot have. Deliberately NOT Q(pk__in=[]):
# that raises EmptyResultSet inside Count(filter=...) and would blank the WHOLE
# aggregate in counts(). `pk IS NULL` is always false and compiles normally.
NEVER = Q(pk__isnull=True)


class BaseAdapter:
    source: str = ""
    label: str = ""
    # model field holding the creation time / author, for cursor + "mine"
    created_field = "created_at"
    author_field = "author_id"
    text_field = "text"
    # unified verbs this system can actually perform
    supported_actions: frozenset = frozenset()
    # can the hub create a new doubt in this system (feed doubts are made
    # in the post composer, so that one says no)
    can_ask = True

    # ---- hooks subclasses implement -------------------------------------
    def scoped_queryset(self, user, ctx):
        """Every row `user` may see, with select_related/annotations."""
        raise NotImplementedError

    def status_q(self) -> dict:
        """{'open': Q, 'answered': Q, 'resolved': Q} over scoped rows."""
        raise NotImplementedError

    def build_ctx(self, user, request=None) -> dict:
        """Per-request lookups (role sets etc.) computed ONCE, not per row."""
        return {"user": user, "request": request}

    def to_answer_q(self, ctx) -> Q:
        """Rows that are still open AND that the viewer may answer."""
        raise NotImplementedError

    def parent_filter_field(self) -> str:
        """Model lookup that parent_id filters on."""
        raise NotImplementedError

    def to_item(self, obj, ctx) -> dict:
        raise NotImplementedError

    def prepare_objs(self, objs, ctx) -> None:
        """Optional batch hook: load anything `to_item` needs for ALL rows
        of this page in one query (avoids an N+1). Default: nothing."""

    def thread(self, obj, ctx) -> list:
        return []

    def dispatch_ask(self, request, payload) -> object:
        raise hub.HubError("Asking is not available for this doubt type.", 400, "ask_unsupported")

    def dispatch_action(self, request, obj, action, payload, ctx):
        raise hub.HubError("Unsupported action.", 400, "action_unsupported")

    def targets(self, user, ctx) -> list:
        """Places the viewer can ask a doubt (for the unified Ask picker)."""
        return []

    # ---- generic flow ----------------------------------------------------
    def _apply_filters(self, qs, flt: "hub.HubFilter", ctx):
        user = ctx["user"]
        if flt.status:
            qs = qs.filter(self.status_q()[flt.status])
        if flt.role == hub.ROLE_ASKED:
            qs = qs.filter(**{self.author_field: user.pk})
        elif flt.role == hub.ROLE_TO_ANSWER:
            qs = qs.filter(self.to_answer_q(ctx))
        if flt.q:
            qs = qs.filter(**{f"{self.text_field}__icontains": flt.q})
        if flt.parent_id:
            qs = qs.filter(**{self.parent_filter_field(): flt.parent_id})
        return qs

    def fetch(self, user, flt: "hub.HubFilter", request=None) -> list:
        """Newest-first normalised items, at most `limit + 1` (the extra
        row tells the merger whether another page exists)."""
        ctx = self.build_ctx(user, request)
        qs = self._apply_filters(self.scoped_queryset(user, ctx), flt, ctx)
        if flt.cursor:
            qs = qs.filter(**{f"{self.created_field}__lt": flt.cursor[0]})
        qs = qs.order_by(f"-{self.created_field}", "-pk")[: flt.limit + 1]
        objs = list(qs)
        self.prepare_objs(objs, ctx)
        items = [self.to_item(obj, ctx) for obj in objs]
        return [i for i in items if hub.after_cursor(i, flt.cursor)]

    def get(self, user, source_id, request=None):
        """(obj, ctx) for ONE doubt the viewer is allowed to see, else None.
        Uses the same scoped queryset as the list, so there is exactly one
        visibility rule per system and a hidden doubt is a plain 404."""
        from django.core.exceptions import ValidationError

        ctx = self.build_ctx(user, request)
        try:
            obj = self.scoped_queryset(user, ctx).filter(pk=source_id).first()
        except (ValueError, TypeError, ValidationError):  # pk of the wrong type (e.g. non-UUID)
            return None
        return (obj, ctx) if obj is not None else None

    def counts(self, user, request=None):
        """({'open':n,'answered':n,'resolved':n}, to_answer_open_count)."""
        ctx = self.build_ctx(user, request)
        qs = self.scoped_queryset(user, ctx)
        sq = self.status_q()
        agg = qs.aggregate(**{k: Count("pk", filter=q, distinct=True) for k, q in sq.items()})
        to_answer = qs.filter(self.to_answer_q(ctx)).count()
        return {k: int(agg.get(k) or 0) for k in hub.STATUSES}, to_answer

    def check_action(self, action):
        if action not in self.supported_actions:
            raise hub.HubError(
                f"'{action}' is not available for {self.label.lower()} doubts.", 400, "action_unsupported"
            )
