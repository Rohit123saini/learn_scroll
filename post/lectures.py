"""
post/lectures.py  (round 4 - "Video lectures")

What the lecture layer adds on top of the video pipeline that already exists (trim / cover / speed in the editor and the reel
composer, H.264 + faststart, HLS 240p / 360p / 540p / 720p in post/tasks.py; 240p = the data-saver rung, P-E2):

  chapters      timestamps of a video            PUT  /post/media/<id>/chapters/     {chapters:[{start,title}]} | {text:"0:00 Intro\\n5:30 ..."}
  captions      Hindi / English subtitles (VTT)  POST /post/media/<id>/captions/     file=.vtt|.srt + language   |   {language, auto:true}
  resume        where this user stopped          PUT  /post/media/<id>/progress/     {position, duration?}
  quality       HLS rungs as separate playlists  (in the bundle: `renditions`)
  notes         a PDF attached to ONE video      POST /post/media/<id>/notes/        file=<pdf> | {attachment_id}
  series        playlist / course, "next lecture" /post/series/...  and  GET /post/<post_id>/next-lecture/?series=<id>
  bundle        everything the player needs      GET  /post/media/<id>/lecture/

This file = pure helpers + small queries (no request / response objects) so they can be unit tested without a browser;
the HTTP layer is post/lecture_views.py, auto-captions are in post/tasks.py (generate_video_captions / poll_video_caption_jobs).
"""
import re
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

MAX_CHAPTERS = 60
MAX_CHAPTER_TITLE = 120
MAX_NOTES_PER_VIDEO = 10
MAX_SERIES_ITEMS = 500
MAX_CAPTION_BYTES = 2 * 1024 * 1024
FINISH_TAIL_SECONDS = 10      # the last 10 s count as "finished"
RESUME_MAX_AGE_DAYS = 60
LANGUAGES = ('en', 'hi')


class LectureError(Exception):
    """Carries the {code, message, http} of the API error."""

    def __init__(self, code, message, http=400):
        super().__init__(message)
        self.code, self.message, self.http = code, message, http


# ----------------------------------------------------------------------------- chapters
_CHAPTER_LINE = re.compile(
    r'^\s*(?:[-*\u2022]\s*)?[(\[]?(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[)\]]?\s*[-\u2013\u2014:.)]*\s*(\S.*?)\s*$')
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')


def _title(raw):
    return ' '.join(_CONTROL.sub(' ', str(raw or '')).split())[:MAX_CHAPTER_TITLE]


def parse_chapter_text(text, duration=None):
    """'0:00 Intro / 12:30 Newton's laws' (the YouTube way, also from a post description) -> [{'start': 0, 'title': 'Intro'}, ...].
    Lines that are not a timestamp + title are ignored; a repeated timestamp keeps the last title; sorted by time."""
    found = {}
    for line in str(text or '').splitlines():
        m = _CHAPTER_LINE.match(line)
        if not m:
            continue
        h, mi, sec = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
        if sec > 59 or (m.group(1) and mi > 59):
            continue
        start = h * 3600 + mi * 60 + sec
        title = _title(m.group(4))
        if not title or (duration and start >= duration):
            continue
        found[start] = title
    return [{'start': s, 'title': t} for s, t in sorted(found.items())][:MAX_CHAPTERS]


def clean_chapters(items, duration=None):
    """Validate the editor's list -> sorted [(start_seconds, title)]. Raises LectureError."""
    if not isinstance(items, (list, tuple)):
        raise LectureError('invalid_chapters', 'chapters must be a list.')
    out = {}
    for it in items:
        if not isinstance(it, dict):
            raise LectureError('invalid_chapters', 'Each chapter needs a start time and a title.')
        try:
            start = int(float(it.get('start')))
        except (TypeError, ValueError):
            raise LectureError('invalid_chapter_start', 'Chapter start must be a number of seconds.')
        if start < 0:
            raise LectureError('invalid_chapter_start', 'Chapter start cannot be negative.')
        if duration and start >= duration:
            raise LectureError('chapter_after_end', 'A chapter starts after the end of the video.')
        title = _title(it.get('title'))
        if not title:
            raise LectureError('chapter_title_required', 'Every chapter needs a title.')
        out[start] = title
    if len(out) > MAX_CHAPTERS:
        raise LectureError('too_many_chapters', f'A video can have at most {MAX_CHAPTERS} chapters.')
    return sorted(out.items())


# ----------------------------------------------------------------------------- captions
_TAG = re.compile(r'<[^>]*>')
_TIMING = re.compile(r'(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}\s*-->\s*(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}')


def srt_to_vtt(text):
    """SubRip (.srt) or WebVTT text -> clean WebVTT: BOM / CRLF removed, `,` -> `.` in timings, cue numbers dropped, markup
    stripped (the app shows plain text). Raises LectureError when no cue is found."""
    raw = str(text or '').replace('\ufeff', '').replace('\r\n', '\n').replace('\r', '\n').strip()
    cues = []
    for block in re.split(r'\n{2,}', raw):
        lines = [ln for ln in block.split('\n')]
        # find the timing line (skips "WEBVTT" header, NOTE blocks, numeric cue ids and cue names)
        idx = next((i for i, ln in enumerate(lines) if _TIMING.search(ln)), None)
        if idx is None:
            continue
        timing = lines[idx].split('-->')
        start = _norm_time(timing[0].strip())
        end = _norm_time(timing[1].strip().split()[0])
        body = '\n'.join(_TAG.sub('', ln).strip() for ln in lines[idx + 1:] if ln.strip())
        if body:
            cues.append(f'{start} --> {end}\n{body}')
    if not cues:
        raise LectureError('invalid_captions', 'This is not a valid .vtt / .srt subtitle file.')
    return 'WEBVTT\n\n' + '\n\n'.join(cues) + '\n'


def _norm_time(t):
    t = t.replace(',', '.')
    head, _, ms = t.partition('.')
    parts = head.split(':')
    if len(parts) == 2:
        parts.insert(0, '00')
    h, m, s = parts
    return f'{int(h):02d}:{int(m):02d}:{int(s):02d}.{(ms + "000")[:3]}'


# ----------------------------------------------------------------------------- resume
def clamp_position(position, duration=None):
    """Seconds the client reports -> a safe int (>= 0, never past the known duration)."""
    try:
        pos = int(float(position))
    except (TypeError, ValueError):
        raise LectureError('invalid_position', 'position must be a number of seconds.')
    if pos < 0:
        raise LectureError('invalid_position', 'position cannot be negative.')
    if duration:
        pos = min(pos, int(duration))
    return pos


def is_completed(position, duration):
    if not duration:
        return False
    return position >= max(0, duration - FINISH_TAIL_SECONDS) or position >= duration * 0.97


def first_video_media(post):
    for m in post.media.all():
        if m.media_type == 'video':
            return m
    return None


def visible_video_media(user, media_id):
    """The video PostMedia when `user` may watch it (same rules as the feed: approved, not deleted / archived / scheduled,
    audience rules; the author always can), else None (one 404 for 'missing' and 'not yours to see')."""
    from .audience import can_view_post
    from .models import PostMedia

    media = PostMedia.objects.select_related('post', 'post__user').filter(pk=media_id, media_type='video').first()
    if media is None:
        return None
    post = media.post
    if post.is_deleted:
        return None
    if post.user_id != user.id:
        if post.is_archived or post.is_scheduled or post.moderation_status != 'approved':
            return None
        if not can_view_post(post, user):
            return None
    return media


def continue_video_item(user, request=None):
    """The last video this user left unfinished (dict) or None - same idea as reading_progress.continue_item."""
    from .models import VideoProgress

    from .audience import can_view_post

    cutoff = timezone.now() - timedelta(days=RESUME_MAX_AGE_DAYS)
    rows = (VideoProgress.objects.filter(user=user, completed=False, last_watched_at__gte=cutoff, position_seconds__gte=5)
            .select_related('post', 'post__user', 'media').order_by('-last_watched_at')[:5])
    for row in rows:
        post = row.post
        if post.is_deleted or post.is_archived or post.is_scheduled or post.moderation_status != 'approved':
            continue
        if post.user_id != user.id and not can_view_post(post, user):   # audience may have changed since
            continue
        thumb = None
        try:
            if row.media.thumbnail:
                thumb = row.media.thumbnail.url
                if request is not None and thumb.startswith('/'):
                    thumb = request.build_absolute_uri(thumb)
        except Exception:
            thumb = None
        return {
            'post_id': str(post.id), 'media_id': str(row.media_id), 'title': post_title(post),
            'position': row.position_seconds, 'duration': row.duration_seconds,
            'progress': (round(min(1.0, row.position_seconds / float(row.duration_seconds)), 3)
                         if row.duration_seconds else 0.0),
            'thumbnail_url': thumb,
            'last_watched_at': row.last_watched_at.isoformat(),
        }
    return None


def post_title(post):
    t = (post.title or '').strip()
    if t:
        return t[:120]
    c = ' '.join((post.content or '').split())
    return c[:80] or 'Lecture'


# ----------------------------------------------------------------------------- HLS quality renditions
def hls_renditions(media, storage=None):
    """[{'name': '360p', 'url': <variant playlist>}, ...] from the rung names the HLS build saved in `metadata['hls']`.
    Empty when the video has no HLS. Each rung's playlist lives at <prefix>/<rung>/index.m3u8 (post.services.transcode_video_hls)."""
    from . import services as svc

    prefix = svc.hls_prefix_of(getattr(media, 'hls_playlist', '') or '')
    rungs = ((media.metadata or {}).get('hls') or {}).get('rungs') or []
    if not prefix or not rungs:
        return []
    if storage is None:
        storage = type(media)._meta.get_field('file').storage
    out = []
    for name in rungs:
        try:
            out.append({'name': str(name), 'url': storage.url(f'{prefix}/{name}/index.m3u8')})
        except Exception:
            continue
    return out


# ----------------------------------------------------------------------------- series / course
def classroom_is_taught_by(user, classroom_id):
    try:
        from tuitionclass.models import Classroom
        return Classroom.objects.filter(pk=classroom_id, teacher=user).exists()
    except Exception:  # not installed / malformed id
        return False


def visible_series_q(user):
    """Q for the series `user` may open: own, public, or a classroom course of a classroom they teach / hold a pass for."""
    from .audience import classroom_ids_of

    rooms = [str(i) for i in classroom_ids_of(user)]
    q = Q(owner=user) | Q(is_public=True)
    if rooms:
        q |= Q(kind='classroom', classroom_id__in=rooms)
    return q


def series_visible_to(user, series):
    from .models import VideoSeries
    return VideoSeries.objects.filter(pk=series.pk).filter(visible_series_q(user)).exists()


def series_entries(series, user):
    """Ordered items of `series` whose post the user can watch and that still has a video: [(item, post, media)]."""
    from .audience import filter_posts_visible_to
    from .models import Post

    items = list(series.items.select_related('post').order_by('position', 'added_at'))
    if not items:
        return []
    ids = [i.post_id for i in items]
    ok = filter_posts_visible_to(
        Post.objects.filter(id__in=ids, is_deleted=False, is_archived=False, is_scheduled=False, moderation_status='approved'),
        user).prefetch_related('media')
    posts = {p.id: p for p in ok}
    out = []
    for it in items:
        post = posts.get(it.post_id)
        media = first_video_media(post) if post else None
        if media is not None:
            out.append((it, post, media))
    return out


def neighbours(entries, post_id):
    """(index, previous, next) of `post_id` inside `entries`; index -1 when it is not part of the series."""
    for i, (_it, post, _m) in enumerate(entries):
        if post.id == post_id:
            return i, (entries[i - 1] if i > 0 else None), (entries[i + 1] if i + 1 < len(entries) else None)
    return -1, None, None


def renumber(series):
    """Make `position` 0..n-1 again (after a delete) in one pass."""
    for n, item in enumerate(series.items.order_by('position', 'added_at')):
        if item.position != n:
            type(item).objects.filter(pk=item.pk).update(position=n)
