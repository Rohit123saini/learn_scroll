"""
post/hashtags.py  (P1)

Hashtag normalisation + the `Hashtag` count table.

`Post.hashtags` (JSON list) stays the lookup source the existing hashtag page
and feed code read; the `Hashtag` table adds fast counts, trending and
prefix search on top of it.
"""
import re
import unicodedata

MAX_HASHTAGS_PER_POST = 10
MAX_TAG_LEN = 50
# \w covers unicode letters/digits/underscore (Hindi matras are category M*, so allow them explicitly).
_TAG_RE = re.compile(r'#([\w\u0900-\u097F]+)', re.UNICODE)
_VALID_RE = re.compile(r'^[\w\u0900-\u097F]+$', re.UNICODE)


def normalize_tag(raw):
    """'#Physics ' -> 'physics'; NFKC + casefold so unicode variants collapse.
    Returns '' for anything that is not a valid tag."""
    if not raw:
        return ''
    tag = unicodedata.normalize('NFKC', str(raw)).strip().lstrip('#').strip().casefold()
    if not tag or len(tag) > MAX_TAG_LEN or not _VALID_RE.match(tag):
        return ''
    return tag


def extract_from_text(text):
    return [m.group(1) for m in _TAG_RE.finditer(text or '')]


def normalize_list(tags, limit=MAX_HASHTAGS_PER_POST):
    """Normalise, drop invalid + duplicates (order kept), cap at `limit`."""
    out = []
    for t in tags or []:
        n = normalize_tag(t)
        if n and n not in out:
            out.append(n)
        if len(out) >= limit:
            break
    return out


def sync_post_hashtags(post, old_tags=None):
    """Keep Hashtag.posts_count in step with `post.hashtags` (public, live posts only
    count towards trending, but the row count tracks every use)."""
    from django.db.models import F
    from django.utils import timezone
    from .models import Hashtag

    new = set(normalize_list(post.hashtags))
    old = set(normalize_list(old_tags)) if old_tags is not None else set()
    for name in new - old:
        obj, created = Hashtag.objects.get_or_create(name=name, defaults={'posts_count': 1, 'last_used_at': timezone.now()})
        if not created:
            Hashtag.objects.filter(pk=obj.pk).update(posts_count=F('posts_count') + 1, last_used_at=timezone.now())
    for name in old - new:
        Hashtag.objects.filter(name=name, posts_count__gt=0).update(posts_count=F('posts_count') - 1)


def release_post_hashtags(post):
    """Call when a post is deleted."""
    sync_post_hashtags(type('P', (), {'hashtags': []})(), old_tags=post.hashtags)


def clean_topics(values, limit=10):
    """The composer's separate "Topics" field (Physics, Class 10 ...): free text,
    trimmed, case-insensitive de-duplicated, max `limit` of 40 chars each."""
    if isinstance(values, str):
        values = [v for v in values.split(',')]
    out, seen = [], set()
    for v in values or []:
        t = ' '.join(str(v).split())[:40]
        if t and t.casefold() not in seen:
            seen.add(t.casefold())
            out.append(t)
        if len(out) >= limit:
            break
    return out
