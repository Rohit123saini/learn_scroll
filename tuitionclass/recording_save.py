"""
tuitionclass/recording_save.py  (X12 - "Save as lecture / reel / post")

Turns a stretch of a finished class recording into a normal post of the post app:

    lecture  a video post (any length up to MAX_CLIP_SECONDS), chapters as VideoChapter rows, optionally added to a
             VideoSeries ("course") of the teacher                      -> Post.kind 'post',  post_type 'video'
    reel     a vertical 720x1280 clip, at most 2 minutes (a longer range is REFUSED, never cut silently), chapters in
             `metadata.reel.chapters`                                   -> Post.kind 'reel',  post_type 'video'
    post     a plain video post (same encode as a lecture, no series)   -> Post.kind 'post',  post_type 'video'

The job is one RecordingSave row (pending -> processing -> ready | failed) - the Flutter save screen polls it
(recording_views.RecordingSaveDetailView). Three layers, same split as recording_tools.py / post/lectures.py:

  1. PURE helpers (no DB, no ffmpeg, no network): `trim_command`, `reel_filter`, `reel_metadata`, `class_recording_meta`,
     `default_title`, `clean_save_request`. Unit-testable without a database.
  2. `create_save_job(...)` - the request side: checks who may save what, then writes the pending row.
  3. `run_save(save_id)` - the worker side (Celery task tuitionclass.save_recording_clip): ffmpeg trim (+ re-encode, an
     exact cut needs one), Post + PostMedia + VideoChapter (+ VideoSeriesItem) in ONE transaction, row -> ready.

AUDIENCE (security): the post is stamped with `metadata.class_recording = {classroom_id, session_id}` by THIS module only
(post/reel_meta.py strips that key from every client payload). With visibility `class_campus` (the default, "class_only")
post/audience.py then shows the post to the members of THAT classroom only, not to everybody who shares some class with
the teacher. `public` / `followers` may be chosen by the classroom's own teacher only (not by a co-teacher / moderator).

No migration: RecordingSave / SessionChapter already exist (0012). Never raises out of `run_save` - a failure is written
to the row (`status=failed`, short `error`) and the temp files are always removed.
"""
import logging
import os
import re
import shutil
import tempfile
import urllib.request
import uuid
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings as django_settings
from django.db import transaction
from django.utils import timezone

from . import recording_tools as rt

logger = logging.getLogger(__name__)

MAX_TITLE = 150
MAX_DESCRIPTION = 2000
MAX_ACTIVE_JOBS_PER_USER = 3
MAX_ACTIVE_JOBS_PER_SESSION = 5
MAX_SOURCE_DOWNLOAD_BYTES = 8 * 1024 * 1024 * 1024    # fallback download of the whole file (a 4 h 1080p class)
DOWNLOAD_TIMEOUT_SECONDS = 60
REEL_WIDTH, REEL_HEIGHT = 720, 1280
PENDING_REQUEUE_AFTER = timedelta(minutes=10)
PENDING_GIVE_UP_AFTER = timedelta(hours=2)
PROCESSING_GIVE_UP_AFTER = timedelta(hours=6)
ERROR_MAX = 200

_ALLOWED_SCHEMES = ("http", "https")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class SaveError(Exception):
    """A refused request. `code` is machine readable, `message` is user safe, `http` the status the view answers with."""

    def __init__(self, code, message, http=400):
        super().__init__(message)
        self.code, self.message, self.http = code, message, http


# ============================================================================================ 1. pure helpers
def _setting(name, default):
    return getattr(django_settings, name, default)


def _clean_text(raw, limit, multiline=False):
    text = str(raw or "")
    if multiline:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", text)
        return text.strip()[:limit]
    return " ".join(_CONTROL.sub(" ", text).split())[:limit]


def default_title(session):
    """\"Physics - 12 Oct\" for a save request that sent no title."""
    classroom_title = _clean_text(getattr(session.classroom, "title", ""), 100) or "Class"
    when = session.actual_start or session.scheduled_start
    try:
        stamp = timezone.localtime(when).strftime("%d %b") if when else ""
    except (ValueError, TypeError):
        stamp = when.strftime("%d %b") if when else ""
    return f"{classroom_title} - {stamp}".strip(" -")[:MAX_TITLE]


def class_recording_meta(session):
    """The server-only audience marker stored on the post (see the module docstring)."""
    return {"classroom_id": int(session.classroom_id), "session_id": int(session.pk)}


def reel_filter():
    """filter_complex for a 720x1280 reel from a landscape class video: the picture is centred over a blurred, zoomed copy
    of itself (no black bars). A portrait source simply fills the frame. Output label [v]."""
    w, h = REEL_WIDTH, REEL_HEIGHT
    return (
        f"[0:v]split=2[a][b];"
        f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=24:6[bg];"
        f"[b]scale=w={w}:h={h}:force_original_aspect_ratio=decrease:force_divisible_by=2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p,setsar=1[v]"
    )


def lecture_scale_filter(max_side=None):
    """Same scale rule as post.services.transcode_video_h264: only ever scale DOWN, longer side <= max_side, even sizes."""
    max_side = int(max_side or max(240, int(_setting("VIDEO_TRANSCODE_MAX_SIDE", 1280))))
    return (
        f"scale=w='if(gt(iw,ih),min({max_side},iw),-2)':h='if(gt(iw,ih),-2,min({max_side},ih))',format=yuv420p"
    )


def trim_command(src, out, start, end, kind, *, remote=False):
    """The ffmpeg argv that cuts [start, end) out of `src` (a local path, or an http(s) URL when `remote`) into `out`.

    `-ss` BEFORE `-i` seeks fast (HTTP range requests for a URL) and `-t` after it keeps the cut frame-exact because the
    clip is re-encoded anyway (a stream copy could only cut on keyframes)."""
    length = int(end) - int(start)
    if length <= 0:
        raise ValueError("end must be after start")
    preset = str(_setting("VIDEO_TRANSCODE_PRESET", "veryfast"))
    crf = str(int(_setting("VIDEO_TRANSCODE_CRF", 23)))
    cmd = ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error"]
    if remote:
        # a recording link can only be plain http(s): no file:, concat:, data: ... inputs through this job
        cmd += ["-protocol_whitelist", "http,https,tcp,tls,crypto"]
    cmd += ["-ss", str(int(start)), "-i", src, "-t", str(length)]
    if kind == "reel":
        cmd += ["-filter_complex", reel_filter(), "-map", "[v]", "-map", "0:a:0?"]
    else:
        cmd += ["-map", "0:v:0", "-map", "0:a:0?", "-vf", lecture_scale_filter()]
    cmd += [
        "-c:v", "libx264", "-preset", preset, "-crf", crf, "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        "-movflags", "+faststart", "-max_muxing_queue_size", "1024",
        out,
    ]
    return cmd


def ffmpeg_timeout(length_seconds):
    base = int(_setting("VIDEO_TRANSCODE_TIMEOUT_SECONDS", 1800))
    return int(min(max(base, int(length_seconds) * 2), 4 * 3600))


def reel_metadata(session, user, clip_seconds, chapters):
    """Cleaned `Post.metadata['reel']` block for a saved reel, or None. `chapters` = shift_chapters() output
    [{'start', 'title'}]. Goes through post.reel_meta.clean_reel_meta so the stored block follows the exact same rules as a
    reel made in the studio (chapters <= 12, titles <= 40 chars, subject / exam whitelists ...)."""
    try:
        from post import reel_meta
    except Exception:
        return None
    raw = {
        "v": 1, "clips": 1, "duration": round(float(clip_seconds), 2),
        "speed_used": False, "original_audio": True, "voiceover": False,
        "allow_remix": False,     # a paid class is not remix material unless the teacher turns it on later
        "chapters": [{"t": c["start"], "title": c["title"]} for c in chapters],
    }
    subject = _clean_text(getattr(session.classroom, "subject", ""), 40).lower()
    if subject in reel_meta.KNOWN_SUBJECTS:
        raw["subject"] = subject
    try:
        return reel_meta.clean_reel_meta(raw, user)
    except Exception:
        logger.exception("reel_metadata: clean_reel_meta failed for session %s", getattr(session, "pk", None))
        return None


def clean_save_request(data):
    """Raw request body -> {kind, title, description, start, end (None = whole recording), visibility, include_chapters,
    series_id}. Pure: range limits that need the recording's length are applied later (clamp_clip). Raises SaveError."""
    if not isinstance(data, dict):
        raise SaveError("invalid_body", "Send a JSON object.")
    kind = str(data.get("kind") or "").strip().lower()
    if kind not in ("lecture", "reel", "post"):
        raise SaveError("invalid_kind", "kind must be lecture, reel or post.")
    visibility = str(data.get("visibility") or "class_only").strip().lower()
    if visibility not in ("class_only", "followers", "public"):
        raise SaveError("invalid_visibility", "visibility must be class_only, followers or public.")

    def _seconds(*names):
        for name in names:
            if data.get(name) not in (None, ""):
                return data.get(name)
        return None

    start_raw, end_raw = _seconds("start_seconds", "start"), _seconds("end_seconds", "end")
    if kind == "reel" and (start_raw is None or end_raw is None):
        raise SaveError("invalid_range", "Choose the start and end of the reel (at most 2 minutes).")
    include = data.get("include_chapters", True)
    if isinstance(include, str):
        include = include.strip().lower() not in ("0", "false", "no", "")
    series_id = _clean_text(data.get("series_id") or data.get("series"), 40)
    if series_id and kind != "lecture":
        raise SaveError("series_only_for_lecture", "Only a lecture can be added to a series.")
    return {
        "kind": kind,
        "title": _clean_text(data.get("title"), MAX_TITLE),
        "description": _clean_text(data.get("description"), MAX_DESCRIPTION, multiline=True),
        "start_raw": start_raw, "end_raw": end_raw,
        "visibility": visibility,
        "include_chapters": bool(include),
        "series_id": series_id,
    }


def _check_source_url(url):
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.netloc:
        raise SaveError("bad_recording_link", "The recording link cannot be used.")
    return url


def _short_error(text):
    return _clean_text(text, ERROR_MAX)


# ============================================================================================ 2. request side
def _teacher_only_choice(visibility):
    return visibility in ("followers", "public")


def active_jobs(session=None, user=None):
    from .models import RecordingSave

    qs = RecordingSave.objects.filter(status__in=(RecordingSave.Status.PENDING, RecordingSave.Status.PROCESSING))
    if session is not None:
        qs = qs.filter(session=session)
    if user is not None:
        qs = qs.filter(created_by=user)
    return qs


def _series_for(user, series_id, session):
    """The teacher's own VideoSeries for `series_id`, or raises SaveError. A series of ANOTHER classroom is refused."""
    if not series_id:
        return None
    try:
        from post.lectures import MAX_SERIES_ITEMS
        from post.models import VideoSeries
    except Exception:
        raise SaveError("series_unavailable", "Series are not available right now.", 503)
    try:
        series = VideoSeries.objects.filter(pk=series_id, owner=user).first()
    except Exception:           # malformed uuid
        series = None
    if series is None:
        raise SaveError("series_not_found", "That series was not found (it must be your own).", 404)
    if series.kind == "classroom" and series.classroom_id and str(series.classroom_id) != str(session.classroom_id):
        raise SaveError("series_other_class", "That series belongs to another class.")
    if series.items.count() >= MAX_SERIES_ITEMS:
        raise SaveError("series_full", f"A series can have at most {MAX_SERIES_ITEMS} lectures.")
    return series


def create_save_job(session, user, data):
    """Validate a save request and write the pending RecordingSave row -> (save, created).

    Rules: the caller manages the classroom (checked by the view); the recording is finished (`recording_url` set, no egress
    running); the range fits the file (reel <= 2 min, refused not cut); `public` / `followers` only for the classroom's own
    teacher; a series is the caller's own; at most MAX_ACTIVE_JOBS_* jobs run at once; the very same request sent twice
    (double tap, retry) returns the job already running instead of a second copy. Raises SaveError."""
    from .models import RecordingSave

    req = clean_save_request(data)
    kind, visibility = req["kind"], req["visibility"]
    if _teacher_only_choice(visibility) and user.id != session.classroom.teacher_id:
        raise SaveError("teacher_only_audience",
                        "Only the class teacher can publish a recording outside the class. Choose 'This class only'.", 403)
    if _teacher_only_choice(visibility):
        # X12 RULE: a class that had a minor in it (any time, any seat) is saved class-only - never to the public feed
        # or to followers - unless settings.LIVE_PUBLIC_RECORDING_WITH_MINORS is on. Cannot tell -> private.
        from .live_safety import public_clip_blocked_by_minors

        if public_clip_blocked_by_minors(session):
            raise SaveError(
                "public_clip_blocked_minors",
                "Students under 18 were in this class, so the recording can only be saved for this class. "
                "Choose 'This class only'.", 403,
            )
    if not session.recording_url:
        raise SaveError("no_recording", "This class has no finished recording yet.")
    if session.egress_id:
        raise SaveError("still_recording", "The recording is still running. Save it after it stops.")

    duration = rt.estimated_duration(session.recording_duration_seconds, session.recording_started_at, session.actual_end)
    start_raw = req["start_raw"] if req["start_raw"] is not None else 0
    end_raw = req["end_raw"]
    if end_raw is None:
        if not duration:
            raise SaveError("invalid_range", "Choose the start and end of the clip.")
        end_raw = duration
    try:
        start, end = rt.clamp_clip(kind, start_raw, end_raw, duration)
    except rt.ClipError as exc:
        raise SaveError(exc.code, exc.message)

    series = _series_for(user, req["series_id"], session)

    with transaction.atomic():
        # lock the session row so two simultaneous requests cannot both pass the limits below
        type(session).objects.select_for_update().filter(pk=session.pk).first()
        same = active_jobs(session=session, user=user).filter(
            kind=kind, start_seconds=start, end_seconds=end, visibility=visibility,
        ).first()
        if same is not None:
            return same, False
        if active_jobs(user=user).count() >= MAX_ACTIVE_JOBS_PER_USER:
            raise SaveError("too_many_jobs", "You already have clips being processed. Wait for them to finish.", 429)
        if active_jobs(session=session).count() >= MAX_ACTIVE_JOBS_PER_SESSION:
            raise SaveError("too_many_jobs", "Too many clips of this class are being processed right now.", 429)
        save = RecordingSave.objects.create(
            session=session, created_by=user, kind=kind,
            title=req["title"] or default_title(session), description=req["description"],
            start_seconds=start, end_seconds=end, visibility=visibility,
            include_chapters=req["include_chapters"], series_id=str(series.pk) if series else "",
        )
    return save, True


def enqueue(save):
    """Queue the Celery job AFTER the row is committed. A broker outage leaves the row `pending` (sweep_stuck_saves
    re-queues it) - it never fails the request."""
    def _go():
        try:
            from .tasks import save_recording_clip
            save_recording_clip.delay(save.pk)
        except Exception:
            logger.exception("could not queue save_recording_clip for RecordingSave %s (sweeper will retry)", save.pk)

    transaction.on_commit(_go)


# ============================================================================================ 3. worker side
def _fail(save_id, code_or_text):
    from .models import RecordingSave

    RecordingSave.objects.filter(pk=save_id).update(
        status=RecordingSave.Status.FAILED, error=_short_error(code_or_text), finished_at=timezone.now(),
    )
    _push(save_id)


def _push(save_id):
    """Tell the teacher's open app that the job moved (best effort; the screen also polls)."""
    try:
        from .models import RecordingSave
        from .realtime import broadcast_to_user

        save = RecordingSave.objects.filter(pk=save_id).first()
        if save is not None:
            broadcast_to_user(save.created_by_id, "recording.save.updated", status_payload(save))
    except Exception:
        logger.exception("recording.save.updated push failed for %s", save_id)


def status_payload(save):
    """The JSON the save screen polls."""
    return {
        "id": save.pk, "session": save.session_id, "kind": save.kind, "status": save.status,
        "title": save.title, "description": save.description,
        "start_seconds": save.start_seconds, "end_seconds": save.end_seconds,
        "visibility": save.visibility, "include_chapters": save.include_chapters,
        "series_id": save.series_id or None, "post_id": save.post_id or None,
        "error": save.error or None,
        "created_at": save.created_at.isoformat() if save.created_at else None,
        "finished_at": save.finished_at.isoformat() if save.finished_at else None,
    }


def _download_to_temp(url, max_bytes=MAX_SOURCE_DOWNLOAD_BYTES):
    """Fallback when ffmpeg cannot seek inside the remote file: copy the whole recording to a temp file (size capped)."""
    _check_source_url(url)
    ext = os.path.splitext(urlparse(url).path)[1] or ".mp4"
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    total = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "learnscroll-recording-save/1"})
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_SECONDS) as resp:    # noqa: S310 (scheme checked above)
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise SaveError("source_too_large", "The recording file is too large to cut.")
                tmp.write(chunk)
    except Exception:
        tmp.close()
        _unlink(tmp.name)
        raise
    tmp.close()
    return tmp.name


def _unlink(path):
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass


def _cut_clip(url, start, end, kind):
    """-> (local mp4 path, probed {'width','height','duration_seconds'}). Tries the remote file first (fast seek, no
    download); when that fails the file is downloaded once and cut locally. Raises SaveError."""
    from post import services as svc

    if shutil.which("ffmpeg") is None:
        logger.error("ffmpeg not found on PATH - cannot save recording clips")
        raise SaveError("ffmpeg_missing", "Video processing is not available on the server.", 503)
    length = end - start
    timeout = ffmpeg_timeout(length)
    out_fd, out = tempfile.mkstemp(suffix=".mp4")
    os.close(out_fd)
    local = None
    try:
        ok = svc._run_ffmpeg(trim_command(_check_source_url(url), out, start, end, kind, remote=True), out, timeout, "recording-clip")
        if not ok:
            local = _download_to_temp(url)
            out_fd, out = tempfile.mkstemp(suffix=".mp4")
            os.close(out_fd)
            ok = svc._run_ffmpeg(trim_command(local, out, start, end, kind), out, timeout, "recording-clip-local")
        if not ok:
            raise SaveError("ffmpeg_failed", "Could not cut this part of the recording.")
        probed = svc.verify_streamable_video(out)
        if probed is None:
            raise SaveError("verify_failed", "The clip could not be prepared. Try a different range.")
        return out, probed
    except Exception:
        _unlink(out)
        raise
    finally:
        _unlink(local)


def _session_chapters(session):
    from .models import SessionChapter

    return list(SessionChapter.objects.filter(session=session).order_by("offset_seconds", "id").values_list("offset_seconds", "title"))


def _chapters_for(save, session):
    """Clip chapters [{'start','title'}] (empty when the teacher turned chapters off)."""
    if not save.include_chapters:
        return []
    return rt.shift_chapters(_session_chapters(session), save.start_seconds, save.end_seconds,
                             fallback_title=rt.clean_title(save.title) or "Start")


def _unique_slug():
    return f"rec-{uuid.uuid4().hex[:12]}"


def _create_post(save, session, out_path, probed, chapters):
    """Post + PostMedia + VideoChapter (+ VideoSeriesItem) in ONE transaction -> Post. The file is stored first; when the
    transaction fails the stored file is removed again."""
    from django.core.files import File
    from post.models import Post, PostMedia, VideoChapter, VideoSeries, VideoSeriesItem

    from .recording_tools import post_visibility

    user = save.created_by
    post_visibility_value, _gated = post_visibility(save.visibility)
    clip_seconds = save.end_seconds - save.start_seconds
    metadata = {"class_recording": class_recording_meta(session)}
    if save.kind == "reel":
        reel = reel_metadata(session, user, probed.get("duration_seconds") or clip_seconds, chapters)
        if reel:
            metadata["reel"] = reel
    classroom = session.classroom
    stored_name = None
    try:
        with transaction.atomic():
            post = Post.objects.create(
                user=user,
                kind=Post.KIND_REEL if save.kind == "reel" else Post.KIND_POST,
                post_type="video",
                title=save.title, content=save.description or None,
                category="education", subject=_clean_text(getattr(classroom, "subject", ""), 100),
                visibility=post_visibility_value, slug=_unique_slug(), metadata=metadata,
            )
            media = PostMedia(
                post=post, media_type="video", file_name=f"{save.kind}_{save.pk}.mp4",
                file_size_bytes=os.path.getsize(out_path), mime_type="video/mp4",
                width=probed.get("width"), height=probed.get("height"),
                duration_seconds=probed.get("duration_seconds") or clip_seconds, display_order=0,
            )
            with open(out_path, "rb") as fh:
                media.file.save(f"rec_{save.pk}_{uuid.uuid4().hex[:8]}.mp4", File(fh), save=False)
            stored_name = media.file.name
            media.save()      # post_save signals queue the poster frame, the streaming check, HLS and the seek sprite

            if chapters:
                seen = set()
                rows = []
                for c in chapters[: rt.MAX_VIDEO_CHAPTERS]:
                    if c["start"] in seen:
                        continue
                    seen.add(c["start"])
                    rows.append(VideoChapter(media=media, start_seconds=c["start"], title=c["title"][:120]))
                VideoChapter.objects.bulk_create(rows)

            if save.kind == "lecture" and save.series_id:
                series = VideoSeries.objects.filter(pk=save.series_id, owner=user).first()
                if series is not None:
                    last = series.items.order_by("-position").values_list("position", flat=True).first()
                    VideoSeriesItem.objects.get_or_create(
                        series=series, post=post, defaults={"position": 0 if last is None else last + 1},
                    )
                    series.save(update_fields=["updated_at"])
        return post
    except Exception:
        if stored_name:
            try:
                PostMedia._meta.get_field("file").storage.delete(stored_name)
            except Exception:
                logger.warning("could not remove orphan clip file %s", stored_name, exc_info=True)
        raise


def run_save(save_id):
    """Worker entry (Celery task tuitionclass.save_recording_clip). Returns a short status string for logs / tests.
    Idempotent: only a `pending` row is picked up, so a duplicate delivery does nothing."""
    from .models import RecordingSave

    claimed = RecordingSave.objects.filter(pk=save_id, status=RecordingSave.Status.PENDING).update(
        status=RecordingSave.Status.PROCESSING, error="",
    )
    if not claimed:
        return "skipped"
    _push(save_id)
    out_path = None
    try:
        save = RecordingSave.objects.select_related("session__classroom", "created_by").get(pk=save_id)
        session = save.session
        if not session.recording_url:
            raise SaveError("no_recording", "The recording is no longer available.")
        out_path, probed = _cut_clip(session.recording_url, save.start_seconds, save.end_seconds, save.kind)
        chapters = _chapters_for(save, session)
        post = _create_post(save, session, out_path, probed, chapters)
        RecordingSave.objects.filter(pk=save.pk).update(
            status=RecordingSave.Status.READY, post_id=str(post.pk), error="", finished_at=timezone.now(),
        )
        _push(save_id)
        logger.info("recording save %s -> post %s (%s, %ss-%ss)", save.pk, post.pk, save.kind, save.start_seconds, save.end_seconds)
        return "ready"
    except SaveError as exc:
        _fail(save_id, exc.message)
        return f"failed:{exc.code}"
    except Exception as exc:
        logger.exception("recording save %s failed", save_id)
        _fail(save_id, "Could not save this clip. Please try again.")
        return f"failed:{type(exc).__name__}"
    finally:
        _unlink(out_path)


def sweep_stuck_saves(now=None):
    """Beat task body. A job whose Celery message was lost (broker down at request time, worker killed) must not stay
    `pending` / `processing` for ever: pending > 10 min is queued again, pending > 2 h and processing > 6 h are failed.
    Returns {'requeued': n, 'failed': n}."""
    from .models import RecordingSave

    now = now or timezone.now()
    failed = 0
    for row in RecordingSave.objects.filter(status=RecordingSave.Status.PENDING, created_at__lt=now - PENDING_GIVE_UP_AFTER):
        _fail(row.pk, "The clip could not be started. Please try again.")
        failed += 1
    for row in RecordingSave.objects.filter(status=RecordingSave.Status.PROCESSING, created_at__lt=now - PROCESSING_GIVE_UP_AFTER):
        _fail(row.pk, "Processing took too long. Please try again.")
        failed += 1
    requeued = 0
    stale = RecordingSave.objects.filter(
        status=RecordingSave.Status.PENDING, created_at__lt=now - PENDING_REQUEUE_AFTER,
    )
    for row in stale[:50]:
        try:
            from .tasks import save_recording_clip
            save_recording_clip.delay(row.pk)
            requeued += 1
        except Exception:
            logger.exception("sweep_stuck_saves: could not requeue %s", row.pk)
            break
    return {"requeued": requeued, "failed": failed}
