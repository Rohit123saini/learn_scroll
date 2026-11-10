# core/notification_inbox.py
"""
N4 - notification INBOX helpers (search / filters / keyset cursor / bulk actions / read-time grouping).

Sab kuch ek jagah taaki views.py patla rahe aur yeh logic alag se test ho sake. Kuch bhi yahan DB schema
nahi badalta (sirf `Notification.archived_at`, migration 0029).

1. FILTERS (list only)    ?q= (title / message / actor username, icontains, max 100 chars)
                           ?unread=1   ?archived=1 (sirf archived; default me archived chhupe hote hain)
2. KEYSET CURSOR          ?before=<cursor>  (khaali "" = pehla page).  Cursor = opaque `next_before` jo server
                           deta hai (created_at + id), ya seedha ek notification id. OFFSET nahi: 10k rows par bhi
                           har page O(limit), aur beech me delete/bump (batching `created_at` badhata hai) hone par
                           na duplicate na gap. Response me COUNT(*) nahi lagta (`count: null`).
3. BULK                   POST notifications/bulk/ {"ids": [...], "action": read|unread|delete|archive|unarchive}
4. GROUPING (read-time)   ?group=1 -> ek page ke andar same (type, target, din) wali rows ek card:
                           "Asha and 5 others liked your post". Write-time batching (notification_batching.py) sirf
                           burst window me merge karti hai; yeh un rows ko bhi jodta hai jo ghanto door aayi.
                           Group sirf PAGE ke andar banta hai (page boundary par ek group do page me bat sakta hai;
                           client `group_key` se merge karta hai).
"""
import base64
import binascii

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from login.models import User

from .models import Notification
from .notification_batching import batch_title
from .serializers import MAX_ACTORS, _photo_url, extract_actor_ids

SEARCH_MAX_LEN = 100
DEFAULT_PAGE_LIMIT = 30
MAX_PAGE_LIMIT = 100

BULK_ACTIONS = ("read", "unread", "delete", "archive", "unarchive")

_NT = Notification.NotifType

# notif_type -> tail of the grouped title ("<names> <tail>"). Same wording as the write-time batch titles.
GROUP_TAILS = {
    _NT.POST_LIKED.value: "liked your post",
    _NT.POST_COMMENTED.value: "commented on your post",
    _NT.POST_REPOSTED.value: "reposted your post",
    _NT.STORY_REACTION.value: "reacted to your story",
    _NT.FOLLOW_REQUEST_ACCEPTED.value: "started following you",
}
# notif_type -> keys of `data` that identify the thing people reacted to.
_TARGET_KEYS = {
    _NT.POST_LIKED.value: ("post_id",),
    _NT.POST_COMMENTED.value: ("post_id",),
    _NT.POST_REPOSTED.value: ("post_id",),
    _NT.STORY_REACTION.value: ("story_id",),
}


def truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------------------------------
# 1. filters
# ---------------------------------------------------------------------------------------------------
def apply_list_filters(qs, params):
    """?unread= / ?archived= / ?q= on top of the viewset's own is_read / source / category filters."""
    if truthy(params.get("unread")):
        qs = qs.filter(is_read=False)
    if truthy(params.get("archived")):
        qs = qs.filter(archived_at__isnull=False)
    else:
        qs = qs.filter(archived_at__isnull=True)
    q = (params.get("q") or "").strip()[:SEARCH_MAX_LEN]
    if q:
        qs = qs.filter(
            Q(title__icontains=q) | Q(message__icontains=q) | Q(data__actor_username__icontains=q)
        )
    return qs


# ---------------------------------------------------------------------------------------------------
# 2. keyset cursor
# ---------------------------------------------------------------------------------------------------
def cursor_mode(params) -> bool:
    """Keyset paging is opt-in: `?before=` present (even empty) or `?pagination=cursor`."""
    return "before" in params or params.get("pagination") == "cursor"


def page_limit(params, default=DEFAULT_PAGE_LIMIT, maximum=MAX_PAGE_LIMIT) -> int:
    try:
        value = int(params.get("limit", default))
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def encode_cursor(created_at, pk) -> str:
    raw = f"{created_at.isoformat()}|{int(pk)}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: str):
    """-> (created_at, pk). ValueError on anything malformed (the view turns it into a 400: silently
    restarting at page 1 would make the client show duplicates)."""
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        stamp, pk = raw.rsplit("|", 1)
        created_at = parse_datetime(stamp)
        if created_at is None:
            raise ValueError("bad timestamp")
        return created_at, int(pk)
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise ValueError("Invalid cursor.") from exc


def apply_cursor(qs, user, token):
    """Rows strictly OLDER than the cursor, newest first ((-created_at, -pk) total order)."""
    qs = qs.order_by("-created_at", "-pk")
    token = (token or "").strip()
    if not token:
        return qs
    if token.isdigit():  # plain notification id
        row = (
            Notification.objects.filter(recipient=user, pk=int(token))
            .values_list("created_at", "pk")
            .first()
        )
        if row is None:  # row already deleted -> best effort by id
            return qs.filter(pk__lt=int(token))
        created_at, pk = row
    else:
        created_at, pk = decode_cursor(token)
    return qs.filter(Q(created_at__lt=created_at) | Q(created_at=created_at, pk__lt=pk))


# ---------------------------------------------------------------------------------------------------
# 3. bulk
# ---------------------------------------------------------------------------------------------------
def parse_ids(raw, maximum):
    """[1, "2", ...] -> set of ints. Junk entries are skipped (stale client lists are harmless); a non-list or
    too many ids is a ValueError (400)."""
    if not isinstance(raw, list):
        raise ValueError("ids must be a list.")
    ids = set()
    for value in raw:
        if isinstance(value, bool):
            continue
        try:
            ids.add(int(value))
        except (TypeError, ValueError):
            continue
    if len(ids) > maximum:
        raise ValueError(f"At most {maximum} ids per call.")
    return ids


def run_bulk(user, action, ids):
    """Apply `action` to the caller's OWN rows among `ids`. Idempotent. -> (affected, badge_changed)."""
    qs = Notification.objects.for_user(user).filter(pk__in=ids)
    now = timezone.now()
    if action == "read":
        n = qs.filter(is_read=False).update(is_read=True, read_at=now)
        return n, bool(n)
    if action == "unread":
        n = qs.filter(is_read=True).update(is_read=False, read_at=None)
        return n, bool(n)
    if action == "delete":
        n, _ = qs.delete()
        return n, bool(n)
    if action == "archive":
        # Archiving also reads the row: unread counts / badges must never include archived rows.
        read_now = qs.filter(is_read=False).update(is_read=True, read_at=now)
        n = qs.filter(archived_at__isnull=True).update(archived_at=now)
        return n, bool(read_now)
    if action == "unarchive":
        n = qs.filter(archived_at__isnull=False).update(archived_at=None)
        return n, False
    raise ValueError(f"Unknown action {action!r}.")


# ---------------------------------------------------------------------------------------------------
# 4. grouping
# ---------------------------------------------------------------------------------------------------
def group_key_for(n):
    """'<type>:<target>:<YYYY-MM-DD>' for rows that can be collapsed, else None. The day bucket keeps a group
    inside one 'Today / Yesterday / Earlier' section."""
    tail = GROUP_TAILS.get(n.notif_type)
    if tail is None:
        return None
    data = n.data if isinstance(n.data, dict) else {}
    if n.notif_type == _NT.FOLLOW_REQUEST_ACCEPTED.value:
        # only the auto-accepted "new follower" batch; a real "X accepted your request" row has its own
        # follow-back button and must stay a single row.
        if data.get("kind") != "new_follower":
            return None
        target = "self"
    else:
        target = next((data[k] for k in _TARGET_KEYS[n.notif_type] if data.get(k) not in (None, "")), None)
        if target is None:
            return None
    day = timezone.localtime(n.created_at).date().isoformat()
    return f"{n.notif_type}:{target}:{day}"


def plan_groups(rows):
    """rows (newest first) -> [(representative_row, [members newest first], group_key|None)], page order kept.
    A key with a single row is NOT a group (key None)."""
    entries, index = [], {}
    for row in rows:
        key = group_key_for(row)
        if key is None:
            entries.append([row, [row], None])
        elif key in index:
            entries[index[key]][1].append(row)
        else:
            index[key] = len(entries)
            entries.append([row, [row], key])
    return [(rep, members, key if len(members) > 1 else None) for rep, members, key in entries]


def _ordered_actor_ids(members):
    """Unique actor ids across the group, newest row first, newest actor of a row first."""
    seen, out = set(), []
    for m in members:
        for uid in reversed(extract_actor_ids(m.data)):
            if uid not in seen:
                seen.add(uid)
                out.append(uid)
    return out


def decorate_groups(plan, data, request):
    """Add group fields to the already-serialized representatives (`data` is aligned with `plan`).
    Every item gets group_key / group_count / group_ids; real groups additionally get merged actors, a rebuilt
    title and is_read = all members read."""
    per_group = {}
    need = set()
    for i, (rep, members, key) in enumerate(plan):
        if key is None:
            continue
        ordered = _ordered_actor_ids(members)
        per_group[i] = ordered
        need.update(ordered[:MAX_ACTORS])
    users = {u.id: u for u in User.objects.filter(id__in=need)} if need else {}

    for i, ((rep, members, key), item) in enumerate(zip(plan, data)):
        item["group_key"] = key
        item["group_count"] = len(members)
        item["group_ids"] = [m.id for m in members]
        if key is None:
            continue
        ordered = per_group[i]
        shown = [users[uid] for uid in ordered[:MAX_ACTORS] if uid in users]
        all_read = all(m.is_read for m in members)
        item["is_read"] = all_read
        if not all_read:
            item["read_at"] = None
        item["actors"] = [{"id": u.id, "username": u.username, "photo": _photo_url(u, request)} for u in shown]
        item["actor_count"] = max(len(ordered), 1)
        # batch_title() names the LAST actor of the list it gets (arrival order) -> pass oldest..newest.
        item["title"] = batch_title(list(reversed(shown)), item["actor_count"], GROUP_TAILS[rep.notif_type])
        # a merged follow row has no single actor to "follow back"
        item["is_following_actor"] = None
        item["follow_requested"] = False
