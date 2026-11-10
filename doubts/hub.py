"""
doubts/hub.py — the pure (no Django, no DB) core of the Doubts hub.

Everything here is plain Python so it can be unit-tested without a database
(see tests_hub.py): the unified vocabulary, composite ids, cursor codec,
k-way merge and summary maths. Adapters (adapters/*.py) do the DB work and
produce dicts in the shape documented under `make_item`.
"""
from __future__ import annotations

import base64
import heapq
import json
from datetime import datetime
from typing import Iterable, Iterator, Optional

# --- vocabulary ---------------------------------------------------------------
SOURCE_CLASSROOM_CHAT = "classroom_chat"
SOURCE_CAMPUS = "campus"
SOURCE_TUITION = "tuition_class"
SOURCE_FEED_POST = "feed_post"
SOURCE_TESTSERIES = "testseries"

SOURCES = (
    SOURCE_CLASSROOM_CHAT,
    SOURCE_CAMPUS,
    SOURCE_TUITION,
    SOURCE_FEED_POST,
    SOURCE_TESTSERIES,
)

STATUS_OPEN = "open"
STATUS_ANSWERED = "answered"
STATUS_RESOLVED = "resolved"
STATUSES = (STATUS_OPEN, STATUS_ANSWERED, STATUS_RESOLVED)

# Unified verbs a client may send to POST /doubt-hub/<id>/<action>/.
ACTION_ANSWER = "answer"          # teacher answer / reply / community answer
ACTION_UPVOTE = "upvote"
ACTION_UNUPVOTE = "unupvote"
ACTION_RESOLVE = "resolve"
ACTION_REOPEN = "reopen"
ACTION_REVEAL = "reveal"          # teacher unmasks an anonymous asker
ACTION_MARK_BEST = "mark_best"    # asker (or teacher) pins the answer that solved it
ACTION_VERIFY = "verify"          # teacher stamps an answer "Verified by teacher"
ACTIONS = (
    ACTION_ANSWER, ACTION_UPVOTE, ACTION_UNUPVOTE, ACTION_RESOLVE,
    ACTION_REOPEN, ACTION_REVEAL, ACTION_MARK_BEST, ACTION_VERIFY,
)

ROLE_ALL = "all"
ROLE_ASKED = "asked"            # doubts I asked
ROLE_TO_ANSWER = "to_answer"    # still-open doubts I am allowed to answer
ROLES = (ROLE_ALL, ROLE_ASKED, ROLE_TO_ANSWER)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


class HubError(Exception):
    """Client-facing problem (mapped to HTTP `status` by views.py)."""

    def __init__(self, detail: str, status: int = 400, code: str = "invalid"):
        super().__init__(detail)
        self.detail, self.status, self.code = detail, status, code


# --- composite ids ------------------------------------------------------------
# Source ids are UUIDs for most apps but an int for tuitionclass, and two
# systems could in theory collide — so a hub id is always "<source>:<id>".
def encode_id(source: str, source_id) -> str:
    return f"{source}:{source_id}"


def decode_id(hub_id: str) -> tuple[str, str]:
    source, sep, source_id = str(hub_id or "").partition(":")
    if not sep or source not in SOURCES or not source_id.strip():
        raise HubError("Malformed doubt id.", 400, "bad_id")
    return source, source_id.strip()


# --- filters ------------------------------------------------------------------
class HubFilter:
    """Parsed + validated query parameters (see `from_params`)."""

    __slots__ = ("sources", "status", "role", "q", "cursor", "limit", "parent_id")

    def __init__(self, sources=None, status=None, role=ROLE_ALL, q="", cursor=None,
                 limit=DEFAULT_PAGE_SIZE, parent_id=None):
        self.sources = tuple(sources) if sources else SOURCES
        self.status = status
        self.role = role
        self.q = q
        self.cursor = cursor
        self.limit = limit
        self.parent_id = parent_id

    @classmethod
    def from_params(cls, params) -> "HubFilter":
        def get(key):
            value = params.get(key)
            return value.strip() if isinstance(value, str) else value

        raw_sources = get("source")
        sources = None
        if raw_sources:
            sources = [s.strip() for s in raw_sources.split(",") if s.strip()]
            bad = [s for s in sources if s not in SOURCES]
            if bad:
                raise HubError(f"Unknown source(s): {', '.join(bad)}.", 400, "bad_source")

        status = get("status") or None
        if status and status not in STATUSES:
            raise HubError(f"status must be one of {', '.join(STATUSES)}.", 400, "bad_status")

        role = get("role") or ROLE_ALL
        if role not in ROLES:
            raise HubError(f"role must be one of {', '.join(ROLES)}.", 400, "bad_role")

        try:
            limit = int(get("limit") or DEFAULT_PAGE_SIZE)
        except (TypeError, ValueError):
            raise HubError("limit must be an integer.", 400, "bad_limit")
        limit = max(1, min(limit, MAX_PAGE_SIZE))

        q = (get("q") or "")[:100]
        cursor = decode_cursor(get("cursor")) if get("cursor") else None
        return cls(sources, status, role, q, cursor, limit, get("parent_id") or None)


# --- cursor -------------------------------------------------------------------
# Sort order is (created_at DESC, hub id DESC). The cursor is the (created_at,
# id) of the last item of the previous page; adapters ask the DB for rows
# strictly older than it. Opaque to clients (urlsafe base64 JSON).
def encode_cursor(created_at: datetime, hub_id: str) -> str:
    raw = json.dumps({"t": created_at.isoformat(), "k": hub_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(token: str) -> tuple[datetime, str]:
    try:
        padded = token + "=" * (-len(token) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        return datetime.fromisoformat(data["t"]), str(data["k"])
    except Exception:
        raise HubError("Invalid cursor.", 400, "bad_cursor")


# --- normalised item ----------------------------------------------------------
def make_item(*, source, source_id, text, status, created_at, updated_at=None,
              parent=None, author=None, is_anonymous=False, is_mine=False,
              role=ROLE_ALL, answers_count=0, answer_preview="", upvotes_count=0,
              upvoted_by_me=False, capabilities=None, extra=None) -> dict:
    """The ONE shape every client renders, whatever the source.

    author            -> {id, username, name, avatar} or None (hidden/anonymous)
    parent            -> {type, id, name}: where it lives (classroom, section
                         subject, post, test attempt…) — used for grouping and deep links
    role              -> how the *viewer* relates: "asked" | "to_answer" | "all"(viewer)
    capabilities      -> what the viewer may do next (drives which buttons show);
                         the server still re-checks on every action.
    """
    caps = {a: False for a in ACTIONS}
    caps["ask_follow_up"] = False
    caps.update(capabilities or {})
    return {
        "id": encode_id(source, source_id),
        "source": source,
        "source_id": str(source_id),
        "parent": parent,
        "text": text,
        "author": author,
        "is_anonymous": bool(is_anonymous),
        "is_mine": bool(is_mine),
        "role": role,
        "status": status,
        "answers_count": int(answers_count or 0),
        "answer_preview": (answer_preview or "")[:200],
        "upvotes_count": int(upvotes_count or 0),
        "upvoted_by_me": bool(upvoted_by_me),
        "created_at": created_at,
        "updated_at": updated_at or created_at,
        "capabilities": caps,
        "extra": extra or {},
    }


def sort_key(item: dict):
    return (item["created_at"], item["id"])


def merge_pages(per_source: Iterable[list], limit: int) -> tuple[list, Optional[tuple]]:
    """k-way merge of already newest-first lists -> (page, next_cursor_key).

    Each adapter fetched up to `limit + 1` rows, so if more than `limit`
    survive the merge there IS a next page. Returns the page and the
    (created_at, id) of its last item when more remain, else None."""
    merged: Iterator = heapq.merge(*per_source, key=sort_key, reverse=True)
    page = []
    has_more = False
    for item in merged:
        if len(page) == limit:
            has_more = True
            break
        page.append(item)
    if has_more and page:
        return page, sort_key(page[-1])
    return page, None


def after_cursor(item: dict, cursor: Optional[tuple]) -> bool:
    """True if `item` sorts strictly after (i.e. is older than) the cursor."""
    return cursor is None or sort_key(item) < cursor


def empty_counts() -> dict:
    return {s: 0 for s in STATUSES}


def summarize(per_source_counts: dict, to_answer: dict) -> dict:
    """Roll adapter counts up into the hub summary payload."""
    totals = empty_counts()
    for counts in per_source_counts.values():
        for status in STATUSES:
            totals[status] += int(counts.get(status, 0))
    return {
        "total": sum(totals.values()),
        "by_status": totals,
        "by_source": {s: dict(c, total=sum(int(c.get(x, 0)) for x in STATUSES))
                      for s, c in per_source_counts.items()},
        "to_answer": {"total": sum(to_answer.values()), "by_source": dict(to_answer)},
    }
