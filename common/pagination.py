"""
common/pagination.py

Issue #5 — one project-wide paginator with a HARD upper bound.

`settings.MAX_PAGE_SIZE` is the single knob. DRF has no such setting of its
own (its ceiling is the `max_page_size` attribute of a paginator class),
so this class reads the value from settings and applies it. A client may
ask for a smaller or larger page with `?page_size=N`, but never more than
MAX_PAGE_SIZE; anything above is silently clamped to it, so no request can
force an unbounded query / unbounded per-page memory allocation.

Note: the parameter is `page_size`, not `limit` — `?limit=` belongs to
LimitOffsetPagination, which this project does not use, so it is ignored.
"""
from django.conf import settings
from rest_framework.pagination import PageNumberPagination

DEFAULT_MAX_PAGE_SIZE = 100


def get_max_page_size():
    value = getattr(settings, "MAX_PAGE_SIZE", DEFAULT_MAX_PAGE_SIZE)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_PAGE_SIZE
    return value if value > 0 else DEFAULT_MAX_PAGE_SIZE


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"

    @property
    def max_page_size(self):
        return get_max_page_size()


# ---------------------------------------------------------------------------
# TASK "Cursor pagination" — PART 1
#
# Why: feeds ordered by the *annotated* `engagement_score` used
# PageNumberPagination (OFFSET). Scores change while the user scrolls
# (likes/comments arrive, the freshness boost drops at 3h/12h/48h), so
# "page 2 = rows 20..40 of a re-sorted list" made posts jump up/down:
# duplicates on page 2 and posts silently skipped.
#
# Fix: keyset ("seek") pagination. The cursor stores the sort key of the
# LAST row the client received - (engagement_score, created_at, id) - and
# the next page is "rows strictly after that key". A row moving elsewhere
# in the ranking no longer shifts everything behind it; only rows that
# themselves cross the cursor position can move. `id` is the final
# tiebreaker so the order is total and deterministic.
#
# Contract: query param `cursor` (opaque, take it from `next`) + `page_size`.
# Response: {"next": url|null, "previous": null, "results": [...]}.
# There is deliberately no `count` and no `page` param (COUNT(*) on a
# ranked annotation is what makes deep OFFSET pages slow).
# The queryset MUST be annotated with `engagement_score`.
# ---------------------------------------------------------------------------
import base64
import json
import uuid as _uuid

from django.db.models import Q
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import NotFound
from rest_framework.pagination import BasePagination
from rest_framework.response import Response
from rest_framework.utils.urls import remove_query_param, replace_query_param


class EngagementCursorPagination(BasePagination):
    page_size = 20
    page_size_query_param = "page_size"
    cursor_query_param = "cursor"
    ordering = ("-engagement_score", "-created_at", "-id")

    @property
    def max_page_size(self):
        return get_max_page_size()

    # ---- cursor <-> token -------------------------------------------------
    @staticmethod
    def encode_cursor(score, created_at, pk):
        raw = json.dumps([float(score), created_at.isoformat(), str(pk)])
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")

    @staticmethod
    def decode_cursor(token):
        """token -> (score, created_at, uuid) or raises NotFound (like DRF's
        CursorPagination does for a tampered / garbage cursor)."""
        try:
            raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
            score, created_at, pk = json.loads(raw)
            created_at = parse_datetime(created_at)
            if created_at is None:
                raise ValueError("bad datetime")
            return float(score), created_at, _uuid.UUID(str(pk))
        except Exception:
            raise NotFound("Invalid cursor.")

    # ---- DRF hooks --------------------------------------------------------
    def get_page_size(self, request):
        try:
            size = int(request.query_params.get(self.page_size_query_param, self.page_size))
        except (TypeError, ValueError):
            size = self.page_size
        return max(1, min(size, self.max_page_size))

    def paginate_queryset(self, queryset, request, view=None):
        self.request = request
        size = self.get_page_size(request)

        qs = queryset.order_by(*self.ordering)
        token = request.query_params.get(self.cursor_query_param)
        if token:
            score, created_at, pk = self.decode_cursor(token)
            qs = qs.filter(
                Q(engagement_score__lt=score)
                | Q(engagement_score=score, created_at__lt=created_at)
                | Q(engagement_score=score, created_at=created_at, pk__lt=pk)
            )

        rows = list(qs[: size + 1])  # +1 row => "is there a next page?" w/o COUNT
        self.has_next = len(rows) > size
        self.page = rows[:size]
        self.next_cursor = None
        if self.has_next and self.page:
            last = self.page[-1]
            self.next_cursor = self.encode_cursor(last.engagement_score, last.created_at, last.pk)
        return self.page

    def get_next_link(self):
        if not self.next_cursor:
            return None
        return replace_query_param(
            self.request.build_absolute_uri(), self.cursor_query_param, self.next_cursor
        )

    def get_paginated_response(self, data):
        return Response({
            "next": self.get_next_link(),
            "previous": None,
            "results": data,
        })

    def get_paginated_response_schema(self, schema):
        return {
            "type": "object",
            "properties": {
                "next": {"type": "string", "nullable": True, "format": "uri"},
                "previous": {"type": "string", "nullable": True},
                "results": schema,
            },
        }

    def get_schema_operation_parameters(self, view):
        return [
            {"name": self.cursor_query_param, "required": False, "in": "query",
             "description": "Opaque cursor; copy it from the previous response's `next`.",
             "schema": {"type": "string"}},
            {"name": self.page_size_query_param, "required": False, "in": "query",
             "description": "Page size (clamped to settings.MAX_PAGE_SIZE).",
             "schema": {"type": "integer"}},
        ]
