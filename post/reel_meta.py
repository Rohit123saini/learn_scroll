"""
post/reel_meta.py

REEL STUDIO - server side of `metadata.reel`.

The Flutter reel studio (lib/post/reel/reel_models.dart `ReelProject.toMeta`) sends a small summary of how the reel
was made together with the video:

    metadata = {"reel": {"v": 1, "clips": 4, "duration": 27.5, "speed_used": true, "original_audio": true,
                         "voiceover": false, "allow_remix": true,
                         "music": {"id": "123", "name": "Calm piano", "artist": "x", "license": "CC0"},
                         "captions": {"language": "en", "cues": [{"s": 0.0, "e": 2.0, "t": "Hello"}]},
                         "template": "tips3",
                         "remix": {"mode": "duet", "source_post_id": "<uuid>"}}}

`POST /post/create/` is public API, so everything is re-checked here and ONLY the known keys are kept (unknown keys
are dropped, never stored). The reel is the user's own video: nothing in here changes the media file, it is
information for the feed card (music credit, captions track, "remix of @user", copyright flag).

Rules
  * `v` must be 1 (anything else -> the whole `reel` block is dropped).
  * clips 1..30, duration 0..120 s (90 s reel + up to 5 s remix intro, with a little slack), numbers only.
  * music: license `CC0` (Freesound; a sound that staff blocked is refused - X7, post/sounds.py) or `user_audio` (the user's own audio). `user_audio` needs
    `rights_confirmed: true`, otherwise the post is REFUSED (ReelMetaError -> 400). A user-audio reel is stored with
    `copyright_flag: true` so moderation / the copyright flow can find it.
  * captions: language like `en` / `hi` / `en-IN`, at most 40 cues, each 0 <= s < e <= 120 and 1..120 characters;
    control characters are removed, cues are sorted and overlaps are cut.
  * template: one of KNOWN_TEMPLATES.
  * chapters: at most 12, each {t: 0 <= t < duration, title 1..40 chars}, sorted, at least 1 s apart.
  * subject: one of KNOWN_SUBJECTS; exams: at most 3 of KNOWN_EXAMS (no repeats). The Flutter studio also adds them to the
    caption as #hashtags (that is what the feed's exam boost reads); here they are only kept as structured data.
  * series: {title 1..60, part 1..99, total 0..99 (0 = unknown, otherwise >= part)}.
  * notes {post_id, title}: a visible Notes / Resource post (kind `resource`); test {series_id, title}: a PUBLISHED test
    series. A link that does not exist, is not visible or is not the right kind is dropped silently (never blocks a post).
  * timer: {label 0..28 chars, seconds 1..120} - the countdown is burned into the video, this is only for display.
  * study payload (X2): `study_payload(post)` hands the stored series / chapters / subject / exams / notes / test blocks to
    the reel feed (ReelSerializer.study). They were cleaned when the reel was created, so the feed does no query for them.
  * remix: mode remix / duet / stitch / reply; the source must be a visible, not deleted / archived VIDEO post
    that has not turned remixes off (`metadata.reel.allow_remix == false` on the source). A user cannot remix a reel
    he or she cannot see. Self remix is fine.
  * SERVER-ONLY keys (X12): `metadata.class_recording` ({classroom_id, session_id}) is written ONLY by the "save a class
    recording" job (tuitionclass/recording_save.py). It decides who may open the post (post/audience.py), so a client
    can never send it: it is stripped from every create / edit payload (PROTECTED_KEYS, strip_protected_keys).
All of the rules live here so the serializer and the tests cannot drift apart.
"""
import json
import math
import re
import uuid

MAX_CLIPS = 30
MAX_DURATION = 120.0
MAX_CUES = 40
MAX_CUE_CHARS = 120
MIN_CUE_SECONDS = 0.2
MAX_MUSIC_TEXT = 100
MAX_ARTIST_TEXT = 60
KNOWN_TEMPLATES = ("tips3", "concept", "beforeafter", "qna", "steps4")
REMIX_MODES = ("remix", "duet", "stitch", "reply")
MUSIC_LICENSES = ("CC0", "user_audio")
MAX_CHAPTERS = 12
MAX_CHAPTER_TITLE = 40
MIN_CHAPTER_GAP = 1.0
KNOWN_SUBJECTS = ("physics", "chemistry", "maths", "biology", "english", "hindi", "history", "geography",
                  "civics", "economics", "computer", "reasoning", "gk")
KNOWN_EXAMS = ("jee", "neet", "boards", "cbse", "upsc", "ssc", "cuet", "gate", "cat", "olympiad")
MAX_EXAM_TAGS = 3
MAX_SERIES_TITLE = 60
MAX_SERIES_PART = 99
MAX_TIMER_LABEL = 28
MAX_LINK_TITLE = 100
# X12: keys of `Post.metadata` that only the server may write. A user-sent copy is dropped on create and on edit.
PROTECTED_KEYS = ("class_recording",)

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_LANG = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
_MUSIC_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class ReelMetaError(ValueError):
    """A reel block that breaks a rule. `str(exc)` is the client-facing text."""

    def as_text(self):
        return str(self)


def _text(value, max_len):
    if not isinstance(value, str):
        return ""
    return _CONTROL.sub(" ", value).strip()[:max_len]


def _num(value, lo, hi):
    """float in [lo, hi] or None (bool / NaN / inf / out of range -> None)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    if math.isnan(f) or math.isinf(f) or f < lo or f > hi:
        return None
    return f


def _clean_music(raw):
    if not isinstance(raw, dict):
        return None
    mid = raw.get("id")
    mid = str(mid) if isinstance(mid, (int, str)) and not isinstance(mid, bool) else ""
    name = _text(raw.get("name"), MAX_MUSIC_TEXT)
    if not _MUSIC_ID.match(mid) or not name:
        return None
    lic = raw.get("license")
    if lic not in MUSIC_LICENSES:
        raise ReelMetaError("Music licence must be CC0 or your own audio.")
    if lic == "CC0":
        # X7: a CC0 sound staff took down (post/sounds.py) can't be used in a NEW reel. Old reels keep their block.
        from . import sounds

        if sounds.is_blocked(mid):
            raise ReelMetaError(sounds.BLOCKED_TEXT)
    out = {"id": mid, "name": name, "license": lic}
    artist = _text(raw.get("artist"), MAX_ARTIST_TEXT)
    if artist:
        out["artist"] = artist
    if lic == "user_audio":
        if raw.get("rights_confirmed") is not True:
            raise ReelMetaError("Confirm that you may use this audio, or pick a copyright-free sound.")
        out["rights_confirmed"] = True
        out["copyright_flag"] = True
    return out


def _clean_captions(raw):
    if not isinstance(raw, dict):
        return None
    lang = raw.get("language")
    lang = lang if isinstance(lang, str) and _LANG.match(lang) else "en"
    cues = []
    for c in (raw.get("cues") if isinstance(raw.get("cues"), list) else [])[: MAX_CUES * 2]:
        if not isinstance(c, dict):
            continue
        s, e = _num(c.get("s"), 0, MAX_DURATION), _num(c.get("e"), 0, MAX_DURATION)
        t = _text(c.get("t"), MAX_CUE_CHARS)
        if s is None or e is None or not t or e - s < MIN_CUE_SECONDS:
            continue
        cues.append({"s": round(s, 2), "e": round(e, 2), "t": t})
    cues.sort(key=lambda q: q["s"])
    fixed = []
    for q in cues:
        if fixed and fixed[-1]["e"] > q["s"]:
            fixed[-1]["e"] = q["s"]
            if fixed[-1]["e"] - fixed[-1]["s"] < MIN_CUE_SECONDS:
                fixed.pop()
        fixed.append(q)
    fixed = fixed[:MAX_CUES]
    return {"language": lang, "cues": fixed} if fixed else None


def _clean_chapters(raw, duration):
    if not isinstance(raw, list):
        return None
    items = []
    for c in raw[: MAX_CHAPTERS * 2]:
        if not isinstance(c, dict):
            continue
        t = _num(c.get("t"), 0, MAX_DURATION)
        title = " ".join(_text(c.get("title"), 200).split())[:MAX_CHAPTER_TITLE].strip()
        if t is None or not title or t >= duration:
            continue
        items.append({"t": round(t, 2), "title": title})
    items.sort(key=lambda q: q["t"])
    out = []
    for q in items:
        if out and q["t"] - out[-1]["t"] < MIN_CHAPTER_GAP:
            continue
        out.append(q)
        if len(out) >= MAX_CHAPTERS:
            break
    return out or None


def _clean_exams(raw):
    if not isinstance(raw, list):
        return None
    out = []
    for e in raw:
        if isinstance(e, str) and e in KNOWN_EXAMS and e not in out:
            out.append(e)
        if len(out) >= MAX_EXAM_TAGS:
            break
    return out or None


def _int(value, lo, hi):
    f = _num(value, lo, hi)
    return None if f is None else int(f)


def _clean_series(raw):
    if not isinstance(raw, dict):
        return None
    title = " ".join(_text(raw.get("title"), 200).split())[:MAX_SERIES_TITLE].strip()
    part = _int(raw.get("part"), 1, MAX_SERIES_PART)
    total = _int(raw.get("total", 0), 0, MAX_SERIES_PART)
    if not title or part is None:
        return None
    if total is None:
        total = 0
    if total and total < part:
        total = part
    return {"title": title, "part": part, "total": total}


def _clean_timer(raw, duration):
    if not isinstance(raw, dict):
        return None
    seconds = _int(raw.get("seconds"), 1, MAX_DURATION)
    if seconds is None:
        return None
    return {"label": _text(raw.get("label"), MAX_TIMER_LABEL), "seconds": seconds}


def _uuid_or_none(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _clean_notes_link(raw, viewer):
    """{post_id, title} of a visible Notes / Resource post, or None."""
    if not isinstance(raw, dict):
        return None
    pid = _uuid_or_none(raw.get("post_id"))
    if pid is None:
        return None
    from .audience import can_view_post
    from .models import Post

    post = Post.objects.filter(pk=pid, is_deleted=False, is_archived=False, kind=Post.KIND_RESOURCE).first()
    if post is None or not can_view_post(post, viewer):
        return None
    title = _text(post.title, MAX_LINK_TITLE) or _text(raw.get("title"), MAX_LINK_TITLE)
    return {"post_id": str(post.pk), "title": title}


def _clean_test_link(raw, viewer):
    """{series_id, title} of a published test series, or None (a series that is not published is not linkable)."""
    if not isinstance(raw, dict):
        return None
    sid = _uuid_or_none(raw.get("series_id"))
    if sid is None:
        return None
    try:
        from testseries.models import TestSeries
    except ImportError:  # the test series app is not installed
        return None
    series = TestSeries.objects.filter(pk=sid, status=TestSeries.Status.PUBLISHED).first()
    if series is None:
        return None
    title = _text(getattr(series, "title", ""), MAX_LINK_TITLE) or _text(raw.get("title"), MAX_LINK_TITLE)
    return {"series_id": str(series.pk), "title": title}


def check_remix_source(source_id, viewer):
    """The Post a remix points at, or raises ReelMetaError. See the module docstring for the rules."""
    from .audience import can_view_post
    from .models import Post

    try:
        pid = uuid.UUID(str(source_id))
    except (ValueError, AttributeError, TypeError):
        raise ReelMetaError("The reel you want to remix was not found.")
    post = Post.objects.filter(pk=pid, is_deleted=False, is_archived=False, post_type="video").first()
    if post is None or not can_view_post(post, viewer):
        raise ReelMetaError("The reel you want to remix was not found.")
    src_meta = post.metadata if isinstance(post.metadata, dict) else {}
    src_reel = src_meta.get("reel") if isinstance(src_meta.get("reel"), dict) else {}
    if src_reel.get("allow_remix") is False and post.user_id != getattr(viewer, "id", None):
        raise ReelMetaError("This creator has turned remixes off.")
    return post


def _clean_remix(raw, viewer):
    if not isinstance(raw, dict):
        return None
    mode = raw.get("mode")
    if mode not in REMIX_MODES:
        raise ReelMetaError("Unknown remix mode.")
    post = check_remix_source(raw.get("source_post_id"), viewer)
    return {"mode": mode, "source_post_id": str(post.pk), "source_user_id": post.user_id}


def clean_reel_meta(raw, viewer):
    """Cleaned `metadata.reel` dict, or None when there is nothing usable. Raises ReelMetaError for a rule break."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict) or raw.get("v") != 1:
        return None
    clips = _num(raw.get("clips"), 1, MAX_CLIPS)
    duration = _num(raw.get("duration"), 0, MAX_DURATION)
    if clips is None or duration is None:
        return None
    out = {
        "v": 1,
        "clips": int(clips),
        "duration": round(duration, 2),
        "speed_used": raw.get("speed_used") is True,
        "original_audio": raw.get("original_audio") is not False,
        "voiceover": raw.get("voiceover") is True,
        "allow_remix": raw.get("allow_remix") is not False,
    }
    music = _clean_music(raw.get("music"))
    if music:
        out["music"] = music
        if music.get("copyright_flag"):
            out["copyright_flag"] = True
    caps = _clean_captions(raw.get("captions"))
    if caps:
        out["captions"] = caps
    if raw.get("template") in KNOWN_TEMPLATES:
        out["template"] = raw["template"]
    chapters = _clean_chapters(raw.get("chapters"), duration)
    if chapters:
        out["chapters"] = chapters
    if raw.get("subject") in KNOWN_SUBJECTS:
        out["subject"] = raw["subject"]
    exams = _clean_exams(raw.get("exams"))
    if exams:
        out["exams"] = exams
    series = _clean_series(raw.get("series"))
    if series:
        out["series"] = series
    timer = _clean_timer(raw.get("timer"), duration)
    if timer:
        out["timer"] = timer
    if raw.get("notes") is not None:
        notes = _clean_notes_link(raw.get("notes"), viewer)
        if notes:
            out["notes"] = notes
    if raw.get("test") is not None:
        test = _clean_test_link(raw.get("test"), viewer)
        if test:
            out["test"] = test
    if raw.get("remix") is not None:
        remix = _clean_remix(raw.get("remix"), viewer)
        if remix:
            out["remix"] = remix
    return out


def strip_protected_keys(metadata):
    """`metadata` without the server-only keys (PROTECTED_KEYS). A str is decoded first; whatever is not a dict comes
    back unchanged. Never mutates the argument."""
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            return metadata
    if not isinstance(metadata, dict):
        return metadata
    return {k: v for k, v in metadata.items() if k not in PROTECTED_KEYS}


def clean_post_metadata(metadata, post_type, viewer):
    """Whole `Post.metadata` as it may be stored: server-only keys (PROTECTED_KEYS, e.g. `class_recording`) are removed,
    the `reel` block is cleaned for a video post and removed for any other post type; every other key is left exactly
    as the client sent it (other features own those)."""
    metadata = strip_protected_keys(metadata)
    if not isinstance(metadata, dict) or "reel" not in metadata:
        return metadata
    out = dict(metadata)
    raw = out.pop("reel")
    if post_type == "video":
        cleaned = clean_reel_meta(raw, viewer)
        if cleaned:
            out["reel"] = cleaned
    return out


# ----------------------------------------------------------------------------------------------------------------- X2
# Study info for the reels feed / viewer (Part 2/5 pill, chapters, notes chip, "Take test" chip, subject / exam chips).
STUDY_KEYS = ("series", "chapters", "subject", "exams", "notes", "test")


def study_payload(post):
    """`{series, chapters, subject, exams, notes, test}` (only the keys the reel has) from the stored, already cleaned
    `metadata.reel`, or None when the reel has no study info. No query: reads the loaded post.

    A notes / test link is NOT re-checked per viewer here (that would be one query per reel on every page); the app opens
    them through the normal screens, which show their own "not available" state when the target was deleted / hidden."""
    meta = post.metadata if isinstance(getattr(post, "metadata", None), dict) else {}
    reel = meta.get("reel") if isinstance(meta.get("reel"), dict) else {}
    out = {}
    for key in STUDY_KEYS:
        value = reel.get(key)
        if value:
            out[key] = value
    return out or None


def feed_study_chip(post):
    """R-B4: what the HOME feed card of a video post needs for its "Part 2/5" / "N chapters" chip:
    `{series: {title, part, total}, chapters: <count>}` (only the keys the reel has), or None. No query: reads the
    loaded post, from the same cleaned `metadata.reel` that `study_payload` reads (so the Reels viewer and the feed
    always agree). Chapter titles / times stay in the Reels viewer (ReelSerializer.study) - the card shows a count."""
    study = study_payload(post)
    if not study:
        return None
    out = {}
    series = study.get("series")
    if isinstance(series, dict) and series.get("title"):
        out["series"] = {"title": series.get("title"), "part": series.get("part"), "total": series.get("total") or 0}
    chapters = study.get("chapters")
    if isinstance(chapters, list) and chapters:
        out["chapters"] = len(chapters)
    return out or None


def series_of(post):
    """The `{title, part, total}` block of a reel, or None."""
    study = study_payload(post)
    series = study.get("series") if study else None
    return series if isinstance(series, dict) and series.get("title") else None
