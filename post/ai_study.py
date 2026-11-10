"""
post/ai_study.py  (Feature 1 - AI summary / quiz / flashcards for a Notes-Resource post)

The message app already has `generate_study_buddy()` (text and/or PDF -> explain | quiz |
flashcards). Until now the app called it with a PDF the *client* had downloaded and re-uploaded,
and only looked at `Post.media` - so the PDF of a Notes/Resource post (stored as `PostAttachment`)
was never found. This module makes the server do it:

  * the source is built on the server from the post itself: title, caption, subject, level,
    topics + the first PDF attachment (<= 10 MB) or the first .txt attachment
  * permission: the viewer must be able to see the post. If the author turned downloads off, a
    non-author only gets AI help from the TEXT of the post - the files are never read for them
    (an AI summary of a "no download" file would be a way round the author's setting)
  * the result is stored per (post, mode, language, source_hash) in `PostAiStudy`, so the 2nd..Nth
    student who taps "Summary" on the same notes costs nothing. `source_hash` changes when the
    caption or the attached file changes, so an edited post never shows a stale summary

Pure helpers (`build_source`, `source_hash`) need no AI; the Gemini call is `generate_study_buddy`.
"""
import hashlib
import logging

from django.db import IntegrityError, transaction

from .models import PostAiStudy, PostAttachment

logger = logging.getLogger(__name__)

try:  # P-F6 telemetry (aiquality); never required for the feature to work
    from aiquality.telemetry import current as _aiq_current, note_cache_hit as _note_cache_hit
except Exception:  # pragma: no cover
    def _aiq_current():
        return None

    def _note_cache_hit(*_a, **_k):
        return None

# API mode -> message.ai_service mode
MODES = {'summary': 'explain', 'quiz': 'quiz', 'flashcards': 'flashcards'}
LANGUAGES = ('en', 'hi', 'hinglish')
MIN_TEXT_LEN = 10
MAX_TEXT_LEN = 8000
MAX_TXT_BYTES = 200 * 1024  # a .txt note longer than this is cut, not rejected


class NotEnoughContent(Exception):
    """The post has nothing the AI can read (e.g. only a video / image and no caption)."""


class AiNotAllowed(Exception):
    """Author disabled downloads and the visible text alone is too short."""


class Source:
    def __init__(self, text, pdf_bytes, digest, used_file):
        self.text = text
        self.pdf_bytes = pdf_bytes
        self.digest = digest
        self.used_file = used_file


def post_text(post):
    """Caption-level text of a post, one clean string (no AI)."""
    parts = [post.title or '', post.content or '']
    meta = []
    if post.subject:
        meta.append(f'Subject: {post.subject}')
    if post.level:
        meta.append(f'Level: {post.level}')
    topics = [t for t in (post.topics or []) if isinstance(t, str) and t.strip()]
    if topics:
        meta.append('Topics: ' + ', '.join(topics[:10]))
    parts.append('. '.join(meta))
    return '\n'.join(p.strip() for p in parts if p and p.strip())[:MAX_TEXT_LEN]


def source_hash(text, attachment=None, used_file=False):
    """Stable fingerprint of what the AI is shown. Changes when the text or the file changes."""
    h = hashlib.sha256()
    h.update(text.encode('utf-8'))
    if attachment is not None and used_file:
        h.update(f'|{attachment.id}|{attachment.size}'.encode())
        # a replaced file (v2, v3 ...) must not reuse the AI summary of the old one; v1 keeps its old hash (cache stays valid)
        if (getattr(attachment, 'version', 1) or 1) > 1:
            h.update(f'|v{attachment.version}'.encode())
    else:
        h.update(b'|nofile')
    return h.hexdigest()


def _read(att, limit):
    f = att.file
    f.open('rb')
    try:
        return f.read(limit + 1)
    finally:
        f.close()


def pick_attachment(post):
    """First PDF, else first .txt document. Videos / images are not readable by this feature."""
    atts = list(post.attachments.all())  # ordered by (order, created_at); prefetch-friendly
    for a in atts:
        if a.kind == 'pdf':
            return a
    for a in atts:
        if a.kind == 'doc' and (a.mime == 'text/plain' or a.original_name.lower().endswith('.txt')):
            return a
    return None


def build_source(post, viewer):
    """-> Source. Raises AiNotAllowed / NotEnoughContent. Reads at most one file."""
    from message.ai_service import MAX_STUDY_PDF_BYTES

    text = post_text(post)
    can_read_files = viewer.id == post.user_id or post.download_allowed
    att = pick_attachment(post) if can_read_files else None

    pdf_bytes = None
    used_file = False
    if att is not None:
        try:
            if att.kind == 'pdf':
                if att.size and att.size <= MAX_STUDY_PDF_BYTES:
                    data = _read(att, MAX_STUDY_PDF_BYTES)
                    if len(data) <= MAX_STUDY_PDF_BYTES:
                        pdf_bytes, used_file = data, True
            else:  # .txt
                data = _read(att, MAX_TXT_BYTES)[:MAX_TXT_BYTES]
                body = data.decode('utf-8', errors='ignore').strip()
                if body:
                    text = (text + '\n\n' + body)[:MAX_TEXT_LEN]
                    used_file = True
        except Exception:  # storage hiccup: fall back to the text only, never 500
            logger.warning('AI study: could not read attachment %s of post %s', att.id, post.id, exc_info=True)
            pdf_bytes, used_file = None, False

    # Something readable = a PDF, a non-empty .txt, or a real caption/title. "Subject: Physics" alone
    # is metadata, not content.
    caption_len = len((post.title or '').strip()) + len((post.content or '').strip())
    if pdf_bytes is None and not used_file and caption_len < MIN_TEXT_LEN:
        if not can_read_files and pick_attachment(post) is not None:
            raise AiNotAllowed()
        raise NotEnoughContent()

    return Source(text, pdf_bytes, source_hash(text, att, used_file), used_file)


def lookup(post, mode, language, digest):
    return PostAiStudy.objects.filter(
        post=post, mode=mode, language=language, source_hash=digest).first()


def get_or_generate(post, viewer, mode, language):
    """-> (PostAiStudy, cached: bool). Raises AiNotAllowed / NotEnoughContent / RuntimeError / Exception."""
    from message.ai_service import generate_study_buddy

    src = build_source(post, viewer)
    row = lookup(post, mode, language, src.digest)
    if row is not None:
        _note_cache_hit()
        return row, True

    payload = generate_study_buddy(
        mode=MODES[mode], content=src.text, language=language, pdf_bytes=src.pdf_bytes)
    # P-F6: remember which prompt version / model made this shared result, so a 👎 on a cached copy still points at it.
    ctx = _aiq_current()
    if ctx is not None and isinstance(payload, dict):
        payload = {**payload, '_ai': {'prompt_version': ctx.prompt_version, 'model': ctx.model or ''}}

    try:
        with transaction.atomic():
            row = PostAiStudy.objects.create(
                post=post, mode=mode, language=language, source_hash=src.digest,
                payload=payload, created_by=viewer)
    except IntegrityError:  # two students generated the same thing at once: use the winner's row
        row = lookup(post, mode, language, src.digest)
        if row is None:
            raise
        return row, True
    return row, False


def peek(post, viewer, mode, language):
    """Cached result for the CURRENT version of the post, or None. Never calls the AI."""
    try:
        src = build_source(post, viewer)
    except (NotEnoughContent, AiNotAllowed):
        return None
    return lookup(post, mode, language, src.digest)
