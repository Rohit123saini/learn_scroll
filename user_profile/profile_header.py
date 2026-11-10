"""
P1 - Profile header: the rules in one place (pure logic; views / serializers only call these).    NOT RUN.

  * USERNAME      format + reserved names + case-insensitive uniqueness + "1 change per 14 days" + availability check.
  * BIO ENTITIES  @mentions (only REAL accounts), #hashtags and http(s) links found in the bio - the app makes exactly
                  these tappable, so a made-up @name or a javascript: link can never become a tap target.
  * VERIFIED      the tick always carries a REASON (teacher / campus / creator / official / identity) + a label.
  * COMPLETENESS  "80% - add your exam target": percent + what is missing, computed HERE (one source of truth).
  * MASKING       under-18 accounts never show location / links to anyone but themselves.

Settings (all optional):  PROFILE_USERNAME_CHANGE_DAYS (14).
"""
import re
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

# ---------------------------------------------------------------------------------------------------------- username
USERNAME_MIN = 3
USERNAME_MAX = 30
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]+\Z")  # \Z not $: "ravi\n" must not pass (P11)

# Names nobody may take (impersonation / routes). Compared lower-case.
RESERVED_USERNAMES = frozenset({
    "admin", "administrator", "root", "support", "help", "staff", "moderator", "mod", "official", "security",
    "learnscroll", "learn_scroll", "learnscroll.app", "team", "system", "api", "null", "undefined", "me", "you",
    "settings", "login", "logout", "signup", "register", "wallet", "coins", "billing", "payments", "abuse",
    "feedback", "grievance", "privacy", "terms", "about", "contact", "anonymous", "deleted", "user", "users",
})

USERNAME_MESSAGES = {
    "empty": "Username is required.",
    "too_short": f"Username must be at least {USERNAME_MIN} characters.",
    "too_long": f"Username can be at most {USERNAME_MAX} characters.",
    "bad_chars": "Use only letters, numbers, dots and underscores.",
    "bad_edges": "Username cannot start or end with a dot, or have two dots in a row.",
    "all_digits": "Username cannot be only numbers.",
    "reserved": "This username is not available.",
    "taken": "Ye username already taken hai.",
    "limit": "You can change your username once every {days} days.",
    "same": "This is your current username.",
    "ok": "Username is available.",
}


def username_change_days() -> int:
    try:
        return max(0, int(getattr(settings, "PROFILE_USERNAME_CHANGE_DAYS", 14)))
    except (TypeError, ValueError):
        return 14


def username_format_error(value: str):
    """-> error code or None. `value` is already stripped."""
    if not value:
        return "empty"
    if len(value) < USERNAME_MIN:
        return "too_short"
    if len(value) > USERNAME_MAX:
        return "too_long"
    if not _USERNAME_RE.match(value):
        return "bad_chars"
    if value.startswith(".") or value.endswith(".") or ".." in value:
        return "bad_edges"
    if value.isdigit():
        return "all_digits"
    if value.lower() in RESERVED_USERNAMES:
        return "reserved"
    return None


def username_message(code: str) -> str:
    return USERNAME_MESSAGES.get(code, "").format(days=username_change_days())


def username_limit_state(user, now=None) -> dict:
    """{can_change, next_change_at (ISO or None), days_left}. Never changed (NULL) = can change now."""
    now = now or timezone.now()
    changed = getattr(user, "username_changed_at", None)
    days = username_change_days()
    if changed is None or days == 0:
        return {"can_change": True, "next_change_at": None, "days_left": 0, "window_days": days}
    nxt = changed + timedelta(days=days)
    if now >= nxt:
        return {"can_change": True, "next_change_at": None, "days_left": 0, "window_days": days}
    left = max(1, -(-int((nxt - now).total_seconds()) // 86400))  # ceil to whole days
    return {"can_change": False, "next_change_at": nxt.isoformat(), "days_left": left, "window_days": days}


def is_case_only_change(user, new_value: str) -> bool:
    return new_value != user.username and new_value.lower() == user.username.lower()


def username_taken(value: str, exclude_pk=None) -> bool:
    User = get_user_model()
    qs = User.objects.filter(username__iexact=value)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


def check_username(user, raw) -> dict:
    """Everything the "Is this name free?" field needs. Order of checks = order the person should fix things:
    same name -> format -> 14-day limit -> already taken."""
    value = (raw or "").strip()
    limit = username_limit_state(user)
    out = {"username": value, "valid": False, "available": False, "code": "", "message": "", **limit}

    def done(code, valid, available):
        out.update(code=code, message=username_message(code), valid=valid, available=available)
        return out

    if value == user.username:
        return done("same", True, True)
    err = username_format_error(value)
    if err:
        return done(err, False, False)
    if is_case_only_change(user, value):
        return done("ok", True, True)  # fixing capital letters never uses up the 14-day change
    if not limit["can_change"]:
        return done("limit", True, False)
    if username_taken(value, exclude_pk=user.pk):
        return done("taken", True, False)
    return done("ok", True, True)


# ---------------------------------------------------------------------------------------------------------- bio
_MENTION_RE = re.compile(r"(?<![\w@.])@([A-Za-z0-9._]{3,30})")
_HASHTAG_RE = re.compile(r"(?<![\w#&])#(\w{1,50})", re.UNICODE)
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_TRAILING = ".,;:!?)]}'\""
MAX_ENTITIES = 20


def _clean_url(raw: str):
    url = raw.rstrip(_TRAILING)
    lowered = url.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")) or len(url) > 200 or len(url) < 11:
        return None
    from login.models import is_public_http_url  # P11: same rule as the profile links

    if not is_public_http_url(url):
        return None
    return url


def safe_links(links) -> list:
    """P11 - READ-time guard for `User.links`: whatever is stored (old rows saved before the stricter rules, an admin edit,
    a shell write), only well-formed {title, url} entries with a public http(s) address ever reach another person's app."""
    from login.models import MAX_PROFILE_LINKS, PROFILE_LINK_TITLE_MAX, is_public_http_url

    out = []
    for item in links if isinstance(links, list) else []:
        if not isinstance(item, dict):
            continue
        url, title = item.get("url"), item.get("title")
        if not isinstance(url, str) or not is_public_http_url(url):
            continue
        out.append({"title": str(title or "").strip()[:PROFILE_LINK_TITLE_MAX], "url": url.strip()})
        if len(out) >= MAX_PROFILE_LINKS:
            break
    return out


def bio_entities(text: str, resolve_mentions: bool = True) -> dict:
    """{"mentions": [username, ...], "hashtags": [tag, ...], "urls": [url, ...]} found in `text`.

    mentions: only usernames of REAL, active accounts (canonical spelling) when resolve_mentions is True;
    hashtags: without '#', not purely digits; urls: http(s) only. Each list is de-duplicated, in order of
    appearance, at most MAX_ENTITIES long.
    """
    text = text or ""
    seen = set()
    mentions_raw = []
    for m in _MENTION_RE.finditer(text):
        name = m.group(1).rstrip(".")
        if len(name) >= USERNAME_MIN and name.lower() not in seen:
            seen.add(name.lower())
            mentions_raw.append(name)
        if len(mentions_raw) >= MAX_ENTITIES:
            break
    mentions = mentions_raw
    if resolve_mentions and mentions_raw:
        User = get_user_model()
        found = {
            u.lower(): u
            for u in User.objects.filter(is_active=True, username__in=_case_variants(mentions_raw)).values_list("username", flat=True)
        }
        if len(found) < len(mentions_raw):  # fall back to a case-insensitive lookup (SQLite / Postgres alike)
            from django.db.models import Q

            q = Q()
            for n in mentions_raw:
                q |= Q(username__iexact=n)
            found = {u.lower(): u for u in User.objects.filter(is_active=True).filter(q).values_list("username", flat=True)}
        mentions = [found[n.lower()] for n in mentions_raw if n.lower() in found]

    tags, tag_seen = [], set()
    for m in _HASHTAG_RE.finditer(text):
        tag = m.group(1)
        if tag.isdigit() or tag.lower() in tag_seen:
            continue
        tag_seen.add(tag.lower())
        tags.append(tag)
        if len(tags) >= MAX_ENTITIES:
            break

    urls, url_seen = [], set()
    for m in _URL_RE.finditer(text):
        url = _clean_url(m.group(0))
        if url and url not in url_seen:
            url_seen.add(url)
            urls.append(url)
        if len(urls) >= MAX_ENTITIES:
            break
    return {"mentions": mentions, "hashtags": tags, "urls": urls}


def _case_variants(names):
    # cheap first pass: exact spellings (indexed lookup); the iexact fallback above covers the rest
    return list(names)


# ---------------------------------------------------------------------------------------------------------- verified
VERIFIED_LABELS = {
    "identity": "Verified account",
    "teacher": "Verified teacher",
    "campus": "Verified by campus",
    "creator": "Verified creator",
    "official": "Official account",
}
VERIFIED_EXPLAIN = {
    "identity": "LearnScroll confirmed this is the real person behind the account.",
    "teacher": "LearnScroll checked this person's teaching credentials.",
    "campus": "A school or college on LearnScroll confirmed this person belongs to it.",
    "creator": "LearnScroll confirmed this creator's identity and body of work.",
    "official": "This is an official LearnScroll or partner account.",
}


def verified_info(user) -> dict:
    """{"is_verified", "reason", "label", "explain"}. Not verified -> reason / label are empty (no half-states)."""
    if not getattr(user, "is_verified", False):
        return {"is_verified": False, "reason": "", "label": "", "explain": ""}
    reason = getattr(user, "verified_reason", "") or "identity"
    if reason not in VERIFIED_LABELS:
        reason = "identity"
    return {"is_verified": True, "reason": reason, "label": VERIFIED_LABELS[reason], "explain": VERIFIED_EXPLAIN[reason]}


# Words that look like a badge. A label under the name may not claim them unless the account really has the tick.
_BADGE_WORDS = ("verified", "official", "learnscroll team", "staff")


def category_label_claims_badge(value: str) -> bool:
    low = (value or "").lower()
    return any(w in low for w in _BADGE_WORDS)


# ---------------------------------------------------------------------------------------------------------- completeness
# (key, weight, test). Weights add up to 100. Order = tie-break for "what to add next".
def _has_name(u):
    return bool((u.first_name or "").strip() or (u.last_name or "").strip())


COMPLETENESS_ITEMS = (
    ("photo", 15, lambda u: bool(u.profile_photo)),
    ("exam", 15, lambda u: bool((u.target_exam or "").strip())),
    ("bio", 15, lambda u: bool((u.bio or "").strip())),
    ("name", 10, _has_name),
    ("category", 10, lambda u: bool((u.category_label or "").strip())),
    ("class", 10, lambda u: bool((u.study_class or "").strip())),
    ("links", 10, lambda u: bool(u.links)),
    ("location", 5, lambda u: bool((u.location or "").strip())),
    ("cover", 5, lambda u: bool(u.cover_photo)),
    ("dob", 5, lambda u: u.date_of_birth is not None),
)

COMPLETENESS_HINTS = {
    "photo": "Add a profile photo",
    "exam": "Add your exam target",
    "bio": "Write a short bio",
    "name": "Add your name",
    "category": "Add a label (Student, Teacher ...)",
    "class": "Add your class",
    "links": "Add a link",
    "location": "Add your city",
    "cover": "Add a cover photo",
    "dob": "Add your date of birth",
}


def profile_completeness(user) -> dict:
    """{"percent": 0-100, "done": [keys], "missing": [{"key","weight","hint"}], "next": key | None, "next_hint": str}"""
    done, missing, total = [], [], 0
    for key, weight, test in COMPLETENESS_ITEMS:
        try:
            ok = bool(test(user))
        except Exception:  # a broken field must never break the profile
            ok = False
        if ok:
            done.append(key)
            total += weight
        else:
            missing.append({"key": key, "weight": weight, "hint": COMPLETENESS_HINTS[key]})
    best = max(missing, key=lambda m: m["weight"]) if missing else None  # max() keeps the FIRST of equal weights
    return {
        "percent": min(100, total),
        "done": done,
        "missing": missing,
        "next": best["key"] if best else None,
        "next_hint": best["hint"] if best else "",
    }


# ---------------------------------------------------------------------------------------------------------- masking
def hide_private_header_fields(target, viewer) -> bool:
    """True when `viewer` must NOT see location / links of `target` (an under-18 account seen by someone else)."""
    if viewer is None or getattr(viewer, "pk", None) == target.pk:
        return False
    return bool(getattr(target, "is_minor", False))


# ---------------------------------------------------------------------------------------------------------- P7 edit profile
BIO_LIMIT_DEFAULT = 150
BIO_LIMIT_EXTENDED = 300  # verified accounts (teacher / campus / creator / official ...)


def bio_limit(user) -> int:
    """Characters allowed in the bio: 150, or 300 for a verified account. (The serializer's hard cap of 500 stays.)"""
    if bool(getattr(user, "is_verified", False)):
        return int(getattr(settings, "PROFILE_BIO_LIMIT_VERIFIED", BIO_LIMIT_EXTENDED))
    return int(getattr(settings, "PROFILE_BIO_LIMIT", BIO_LIMIT_DEFAULT))


def birthday_label(user):
    """\"MM-DD\" when the owner chose to show it AND is not a minor, else None. The YEAR is never exposed."""
    dob = getattr(user, "date_of_birth", None)
    if dob is None or not getattr(user, "show_birthday", False) or getattr(user, "is_minor", False):
        return None
    return f"{dob.month:02d}-{dob.day:02d}"


def edit_info(user) -> dict:
    """Owner-only: what the edit screen needs to draw the DOB row and the bio counter.

    date_of_birth is only editable while it is empty (a minor must not be able to turn into an adult by retyping it);
    after that the change goes through support. `can_show_birthday` is False for an under-18 account or without a DOB.
    """
    dob = getattr(user, "date_of_birth", None)
    return {
        "date_of_birth": dob.isoformat() if dob else None,
        "dob_locked": dob is not None,
        "show_birthday": bool(getattr(user, "show_birthday", False)) and not getattr(user, "is_minor", False),
        "can_show_birthday": dob is not None and not getattr(user, "is_minor", False),
        "is_minor": bool(getattr(user, "is_minor", False)),
        "bio_limit": bio_limit(user),
    }
