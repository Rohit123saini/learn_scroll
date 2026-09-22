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
