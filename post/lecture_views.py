"""
post/lecture_views.py  (round 4 - "Video lectures", HTTP layer; the rules live in post/lectures.py)

  GET    /post/media/<id>/lecture/                 everything the player needs in ONE call (?series=<id> adds next / previous)
  PUT    /post/media/<id>/chapters/                owner: {chapters:[{start,title}]} | {text:"0:00 Intro\\n5:30 ..."} | {from_description:true}
  GET    /post/media/<id>/captions/                owner: every track incl. pending / failed; viewer: ready tracks
  POST   /post/media/<id>/captions/                owner: file=.vtt|.srt + language   |   {language, auto:true}  (speech-to-text)
  DELETE /post/media/<id>/captions/?language=hi    owner
  GET    /post/media/<id>/progress/                where I stopped
  PUT    /post/media/<id>/progress/                {position, duration?}   (the player sends one every ~10 s, throttled)
  POST   /post/media/<id>/notes/                   owner: file=<pdf> | {attachment_id}   (a PDF that belongs to THIS video)
  DELETE /post/media/<id>/notes/<attachment_id>/   owner
  GET    /post/lectures/continue/                  the last lecture I left unfinished (Home "Continue learning")
  GET    /post/series/                             ?mine=1 | ?classroom=<id> | ?post=<post_id>
  POST   /post/series/                             create {title, description?, kind?, classroom_id?, is_public?} (+ cover file)
  GET    /post/series/<id>/                        detail + ordered entries (+ my progress per entry, `continue_post_id`)
  PATCH  /post/series/<id>/                        owner: title / description / is_public / cover (file) / clear_cover
  DELETE /post/series/<id>/                        owner
  POST   /post/series/<id>/items/                  owner: {post_id | post_ids:[...], section?}
  DELETE /post/series/<id>/items/<post_id>/        owner
  PUT    /post/series/<id>/order/                  owner: {order:[post_id,...], sections?:{post_id: "Unit 1"}}
  GET    /post/<post_id>/next-lecture/?series=<id> previous + next lecture of a series (series omitted: the first visible one)

Every error is `{success: False, code, message}` like the resource API. Nothing here runs ffmpeg: transcoding, trim, cover and
speed are the existing video pipeline; auto captions are the Celery task post.tasks.generate_video_captions.
"""
import json
import logging

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count, Max
from django.utils import timezone
from rest_framework import parsers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from . import lectures
from .lectures import LectureError
from .models import (Post, PostAttachment, PostMedia, VideoCaptionTrack, VideoChapter, VideoProgress, VideoSeries,
                     VideoSeriesItem)
from .resource_views import _err
from .serializers import PostAttachmentSerializer, _absolute_file_url, hls_playlist_url

logger = logging.getLogger(__name__)

MAX_SERIES_PER_USER = 50
MAX_DURATION_SECONDS = 24 * 3600


class LectureProgressThrottle(UserRateThrottle):
    scope = 'lecture_progress'
    rate = '120/min'


class LectureWriteThrottle(UserRateThrottle):
    scope = 'lecture_write'
    rate = '60/min'


# ----------------------------------------------------------------------------- small helpers
def _fail(exc):
    return _err(exc.code, exc.message, exc.http)


def _abs(request, url):
    if not url:
        return None
    if url.startswith('http://') or url.startswith('https://') or request is None:
        return url
    return request.build_absolute_uri(url)


def _media_for_viewer(request, media_id):
    """(media, None) when the caller may watch it, else (None, 404 response)."""
    media = lectures.visible_video_media(request.user, media_id)
    if media is None:
        return None, _err('not_found', 'Video not found.', status.HTTP_404_NOT_FOUND)
    return media, None


def _media_for_owner(request, media_id):
    media, resp = _media_for_viewer(request, media_id)
    if resp is not None:
        return None, resp
    if media.post.user_id != request.user.id:
        return None, _err('not_owner', 'Only the author of this lecture can change it.', status.HTTP_403_FORBIDDEN)
    return media, None


def _thumb(request, media):
    return _absolute_file_url(request, media.thumbnail) if media is not None and media.thumbnail else None


def _json_obj(raw):
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except (TypeError, ValueError):
        return {}


def _data(request):
    """JSON body, or a multipart / form body, as a plain dict-like."""
    return request.data


def _truthy(raw):
    return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')


def _safe_delay(task, *args):
    try:
        task.delay(*args)
        return True
    except Exception:
        logger.warning('could not queue %s (broker down?)', getattr(task, 'name', task), exc_info=True)
        return False


# ----------------------------------------------------------------------------- payload builders
def chapter_payload(media):
    return [{'id': str(c.id), 'start': c.start_seconds, 'title': c.title} for c in media.chapters.all()]


def caption_payload(request, track, owner):
    url = None
    if track.status == 'ready' and track.file:
        try:
            url = _abs(request, track.file.url)
        except Exception:
            url = None
    out = {'id': str(track.id), 'language': track.language, 'source': track.source, 'status': track.status, 'url': url}
    if owner:
        out['error'] = track.error
    return out


def captions_for(request, media, owner):
    tracks = media.caption_tracks.all().order_by('language')
    return [caption_payload(request, t, owner) for t in tracks if owner or (t.status == 'ready' and t.file)]


def progress_payload(row):
    if row is None:
        return None
    pos = row.position_seconds
    resume_at = 0 if row.completed or pos < 5 else max(0, pos - 2)
    return {'position': pos, 'duration': row.duration_seconds, 'completed': row.completed, 'resume_at': resume_at,
            'last_watched_at': row.last_watched_at.isoformat()}


def notes_payload(request, media):
    atts = media.notes.select_related('post').order_by('order', 'created_at')
    return PostAttachmentSerializer(atts, many=True, context={'request': request}).data


def renditions_payload(request, media):
    out = []
    master = hls_playlist_url(request, media)
    if master:
        out.append({'name': 'auto', 'url': master})
    for r in lectures.hls_renditions(media):
        out.append({'name': r['name'], 'url': _abs(request, r['url'])})
    return out


def entry_payload(request, entry, index=None, progress=None):
    item, post, media = entry
    out = {
        'post_id': str(post.id), 'media_id': str(media.id), 'title': lectures.post_title(post), 'section': item.section,
        'position': item.position, 'duration': media.duration_seconds, 'thumbnail_url': _thumb(request, media),
    }
    if index is not None:
        out['index'] = index
    if progress is not None:
        out['progress'] = {'position': progress.position_seconds, 'completed': progress.completed}
    return out


def series_summary(request, series, user, count=None):
    cover = None
    if series.cover:
        cover = _absolute_file_url(request, series.cover)
    return {
        'id': str(series.id), 'title': series.title, 'description': series.description, 'kind': series.kind,
        'classroom_id': series.classroom_id, 'is_public': series.is_public, 'cover_url': cover,
        'owner_id': series.owner_id, 'is_owner': series.owner_id == user.id,
        'count': count if count is not None else series.items.count(),
        'updated_at': series.updated_at.isoformat(),
    }


def navigation_payload(request, series, user, post_id):
    entries = lectures.series_entries(series, user)
    idx, prev, nxt = lectures.neighbours(entries, post_id)
    if idx < 0:
        return None
    out = series_summary(request, series, user, count=len(entries))
    out.update({
        'index': idx, 'total': len(entries),
        'previous': entry_payload(request, prev) if prev else None,
        'next': entry_payload(request, nxt) if nxt else None,
    })
    return out


def _series_from_param(user, raw):
    if not raw:
        return None
    try:
        series = VideoSeries.objects.filter(pk=raw).first()
    except Exception:  # malformed uuid
        return None
    if series is None or not lectures.series_visible_to(user, series):
        return None
    return series


# ----------------------------------------------------------------------------- bundle
class LectureBundleAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, media_id):
        media, resp = _media_for_viewer(request, media_id)
        if resp is not None:
            return resp
        post, owner = media.post, media.post.user_id == request.user.id
        row = VideoProgress.objects.filter(user=request.user, media=media).first()
        series = _series_from_param(request.user, request.query_params.get('series'))
        data = {
            'media_id': str(media.id), 'post_id': str(post.id), 'title': lectures.post_title(post),
            'description': post.content or '', 'author_id': post.user_id,
            'duration': media.duration_seconds, 'poster_url': _thumb(request, media),
            'mp4_url': _absolute_file_url(request, media.file),
            'hls_url': hls_playlist_url(request, media),
            'renditions': renditions_payload(request, media),
            'chapters': chapter_payload(media),
            'captions': captions_for(request, media, owner),
            'progress': progress_payload(row),
            'notes': notes_payload(request, media),
            'series': navigation_payload(request, series, request.user, post.id) if series else None,
            'can_manage': owner,
            'processing_status': media.processing_status,
        }
        return Response({'success': True, 'data': data})


# ----------------------------------------------------------------------------- chapters
class MediaChaptersAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [LectureWriteThrottle]

    def get(self, request, media_id):
        media, resp = _media_for_viewer(request, media_id)
        if resp is not None:
            return resp
        return Response({'success': True, 'data': chapter_payload(media)})

    @transaction.atomic
    def put(self, request, media_id):
        media, resp = _media_for_owner(request, media_id)
        if resp is not None:
            return resp
        body = _data(request)
        duration = media.duration_seconds
        warnings = []
        try:
            if _truthy(body.get('from_description')):
                items = lectures.parse_chapter_text(media.post.content or '', duration)
                if not items:
                    return _err('no_chapters_found',
                                'No timestamps like "0:00 Intro" were found in the description.')
                clean = lectures.clean_chapters(items, duration)
            elif body.get('text') is not None:
                text = str(body.get('text') or '')
                items = lectures.parse_chapter_text(text, duration)
                if text.strip() and not items:
                    return _err('no_chapters_found', 'Write one chapter per line, like "0:00 Introduction".')
                clean = lectures.clean_chapters(items, duration)
            else:
                items = body.get('chapters')
                if isinstance(items, str):
                    try:
                        items = json.loads(items)
                    except ValueError:
                        items = None
                if items is None:
                    return _err('invalid_chapters', 'Send chapters as a list, or text with timestamps.')
                clean = lectures.clean_chapters(items, duration)
        except LectureError as e:
            return _fail(e)
        if clean and clean[0][0] != 0:
            warnings.append('first_chapter_not_zero')
        media.chapters.all().delete()
        VideoChapter.objects.bulk_create([VideoChapter(media=media, start_seconds=s, title=t) for s, t in clean])
        return Response({'success': True, 'data': chapter_payload(media), 'warnings': warnings})


# ----------------------------------------------------------------------------- captions
def _decode_caption_bytes(raw):
    for enc in ('utf-8-sig', 'utf-16', 'cp1252'):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise LectureError('invalid_captions', 'Could not read this subtitle file (use UTF-8).')


def auto_captions_enabled():
    return bool(getattr(settings, 'LECTURE_AUTO_CAPTIONS_ENABLED', True)) and bool(
        getattr(settings, 'CAPTIONS_S3_BUCKET', '') or getattr(settings, 'AWS_STORAGE_BUCKET_NAME', ''))


class MediaCaptionsAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]
    throttle_classes = [LectureWriteThrottle]

    def get(self, request, media_id):
        media, resp = _media_for_viewer(request, media_id)
        if resp is not None:
            return resp
        owner = media.post.user_id == request.user.id
        return Response({'success': True, 'data': captions_for(request, media, owner),
                         'auto_available': auto_captions_enabled() if owner else None})

    def post(self, request, media_id):
        media, resp = _media_for_owner(request, media_id)
        if resp is not None:
            return resp
        body = _data(request)
        language = str(body.get('language') or '').strip().lower()
        if language not in lectures.LANGUAGES:
            return _err('invalid_language', 'Language must be "hi" (Hindi) or "en" (English).')
        upload = request.FILES.get('file')

        # ---- the author's own .vtt / .srt
        if upload is not None:
            if upload.size > lectures.MAX_CAPTION_BYTES:
                return _err('file_too_large', 'Subtitle file can be at most 2 MB.', status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
            name = (upload.name or '').lower()
            if not (name.endswith('.vtt') or name.endswith('.srt')):
                return _err('unsupported_type', 'Upload a .vtt or .srt subtitle file.')
            try:
                vtt = lectures.srt_to_vtt(_decode_caption_bytes(upload.read()))
            except LectureError as e:
                return _fail(e)
            with transaction.atomic():
                track, _ = VideoCaptionTrack.objects.select_for_update().get_or_create(
                    media=media, language=language, defaults={'source': 'upload', 'status': 'ready'})
                old_file = track.file.name if track.file else ''
                track.source, track.status, track.job_name, track.error = 'upload', 'ready', '', ''
                track.file.save('captions.vtt', ContentFile(vtt.encode('utf-8')), save=False)
                track.save()
            if old_file:
                try:
                    track.file.storage.delete(old_file)
                except Exception:
                    logger.warning('could not delete the replaced caption file %s', old_file, exc_info=True)
            return Response({'success': True, 'data': caption_payload(request, track, True)},
                            status=status.HTTP_201_CREATED)

        # ---- speech-to-text
        if not _truthy(body.get('auto')):
            return _err('file_required', 'Attach a .vtt / .srt file, or ask for automatic captions.')
        if not auto_captions_enabled():
            return _err('auto_captions_unavailable', 'Automatic captions are not switched on for this server.',
                        status.HTTP_503_SERVICE_UNAVAILABLE)
        cap = int(getattr(settings, 'LECTURE_AUTO_CAPTION_MAX_SECONDS', 3 * 3600))
        if media.duration_seconds and media.duration_seconds > cap:
            return _err('video_too_long', f'Automatic captions work for videos up to {cap // 60} minutes.')
        with transaction.atomic():
            track, created = VideoCaptionTrack.objects.select_for_update().get_or_create(
                media=media, language=language, defaults={'source': 'auto', 'status': 'pending'})
            if not created and track.status in ('pending', 'processing') and track.source == 'auto':
                return Response({'success': True, 'data': caption_payload(request, track, True)})  # already running
            if not created:
                track.source, track.status, track.job_name, track.error = 'auto', 'pending', '', ''
                track.save(update_fields=['source', 'status', 'job_name', 'error', 'updated_at'])
        from .tasks import generate_video_captions
        if not _safe_delay(generate_video_captions, str(track.id)):
            VideoCaptionTrack.objects.filter(pk=track.pk).update(status='failed', error='queue_unavailable')
            track.refresh_from_db()
            return _err('queue_unavailable', 'Could not start the captions job right now. Please try again.',
                        status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({'success': True, 'data': caption_payload(request, track, True)}, status=status.HTTP_202_ACCEPTED)

    def delete(self, request, media_id):
        media, resp = _media_for_owner(request, media_id)
        if resp is not None:
            return resp
        language = str(request.query_params.get('language') or '').strip().lower()
        track = media.caption_tracks.filter(language=language).first()
        if track is None:
            return _err('not_found', 'No captions in this language.', status.HTTP_404_NOT_FOUND)
        name = track.file.name if track.file else ''
        track.delete()
        if name:
            try:
                track.file.storage.delete(name)
            except Exception:
                logger.warning('could not delete caption file %s', name, exc_info=True)
        return Response({'success': True})


# ----------------------------------------------------------------------------- resume
class MediaProgressAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [LectureProgressThrottle]

    def get(self, request, media_id):
        media, resp = _media_for_viewer(request, media_id)
        if resp is not None:
            return resp
        row = VideoProgress.objects.filter(user=request.user, media=media).first()
        return Response({'success': True, 'data': progress_payload(row)})

    @transaction.atomic
    def put(self, request, media_id):
        media, resp = _media_for_viewer(request, media_id)
        if resp is not None:
            return resp
        body = _data(request)
        duration = media.duration_seconds
        if not duration:
            try:
                sent = int(float(body.get('duration')))
                duration = sent if 0 < sent <= MAX_DURATION_SECONDS else None
            except (TypeError, ValueError):
                duration = None
        try:
            position = lectures.clamp_position(body.get('position'), duration)
        except LectureError as e:
            return _fail(e)
        if position > MAX_DURATION_SECONDS:
            return _err('invalid_position', 'position is too large.')
        completed = lectures.is_completed(position, duration)
        row, created = VideoProgress.objects.get_or_create(
            user=request.user, media=media,
            defaults={'post_id': media.post_id, 'position_seconds': position, 'duration_seconds': duration,
                      'completed': completed})
        if not created:
            row.position_seconds, row.completed = position, completed
            row.duration_seconds = duration or row.duration_seconds
            row.last_watched_at = timezone.now()
            row.save(update_fields=['position_seconds', 'duration_seconds', 'completed', 'last_watched_at'])
        return Response({'success': True, 'data': progress_payload(row)})


class ContinueLectureAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [LectureProgressThrottle]

    def get(self, request):
        return Response({'success': True, 'data': lectures.continue_video_item(request.user, request)})


# ----------------------------------------------------------------------------- notes (a PDF that belongs to ONE video)
class MediaNotesAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]
    throttle_classes = [LectureWriteThrottle]

    def get(self, request, media_id):
        media, resp = _media_for_viewer(request, media_id)
        if resp is not None:
            return resp
        return Response({'success': True, 'data': notes_payload(request, media)})

    @transaction.atomic
    def post(self, request, media_id):
        media, resp = _media_for_owner(request, media_id)
        if resp is not None:
            return resp
        if media.notes.count() >= lectures.MAX_NOTES_PER_VIDEO:
            return _err('too_many_notes', f'A video can have at most {lectures.MAX_NOTES_PER_VIDEO} notes.')
        body = _data(request)
        upload = request.FILES.get('file')

        if upload is None:   # attach a PDF the post already has
            att_id = body.get('attachment_id')
            if not att_id:
                return _err('file_required', 'Attach a PDF file.')
            try:
                att = PostAttachment.objects.filter(pk=att_id, post_id=media.post_id, kind='pdf').first()
            except Exception:
                att = None
            if att is None:
                return _err('not_found', 'That PDF is not part of this post.', status.HTTP_404_NOT_FOUND)
            att.for_media = media
            att.save(update_fields=['for_media'])
            return Response({'success': True, 'data': notes_payload(request, media)}, status=status.HTTP_201_CREATED)

        from . import resource_text
        from .resource_files import (ResourceFileError, check_upload, make_thumbnail, strip_metadata, virus_scan)
        try:
            checked = check_upload(upload)
            if checked.kind != 'pdf':
                return _err('unsupported_type', 'Notes must be a PDF file.')
            cleaned, pages = strip_metadata(upload, checked)
            if cleaned is None:
                upload.seek(0)
            virus_scan(ContentFile(cleaned) if cleaned is not None else upload)
            thumb = make_thumbnail(cleaned if cleaned is not None else upload.read(), checked)
            upload.seek(0)
            text_pages, text_source = resource_text.build_text(checked, cleaned, None)
        except ResourceFileError as e:
            return _err(e.code, e.message, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE if e.code == 'file_too_large'
                        else status.HTTP_400_BAD_REQUEST)
        order = (media.post.attachments.aggregate(m=Max('order'))['m'] or 0) + 1
        att = PostAttachment(
            post=media.post, kind='pdf', original_name=(upload.name or 'notes.pdf')[-255:], mime=checked.mime,
            size=len(cleaned) if cleaned is not None else checked.size, pages=pages, order=order,
            text_pages=text_pages, search_text=resource_text.flatten(text_pages), text_source=text_source,
            for_media=media)
        att.file.save(upload.name or 'notes.pdf', ContentFile(cleaned) if cleaned is not None else upload, save=False)
        if thumb:
            att.thumbnail.save('t.jpg', ContentFile(thumb), save=False)
        att.save()
        return Response({'success': True, 'data': notes_payload(request, media)}, status=status.HTTP_201_CREATED)


class MediaNoteDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [LectureWriteThrottle]

    @transaction.atomic
    def delete(self, request, media_id, attachment_id):
        media, resp = _media_for_owner(request, media_id)
        if resp is not None:
            return resp
        att = media.notes.filter(pk=attachment_id).first()
        if att is None:
            return _err('not_found', 'Notes not found.', status.HTTP_404_NOT_FOUND)
        if media.post.kind == Post.KIND_RESOURCE:   # an ordinary resource file: just stop calling it the notes of this video
            att.for_media = None
            att.save(update_fields=['for_media'])
        else:                                       # a video post: the PDF only ever existed as the notes
            att.delete()
        return Response({'success': True, 'data': notes_payload(request, media)})


# ----------------------------------------------------------------------------- series / course
def _clean_title(raw, required=True):
    title = ' '.join(str(raw or '').split())[:120]
    if required and not title:
        raise LectureError('title_required', 'Give the series a title.')
    return title


def _save_cover(series, upload):
    from .resource_files import ResourceFileError, check_upload, strip_metadata
    try:
        checked = check_upload(upload)
        if checked.kind != 'image':
            raise LectureError('unsupported_type', 'The cover must be an image.')
        cleaned, _pages = strip_metadata(upload, checked)
    except ResourceFileError as e:
        raise LectureError(e.code, e.message)
    if cleaned is None:
        upload.seek(0)
    series.cover.save('cover.jpg', ContentFile(cleaned) if cleaned is not None else upload, save=False)


def _own_video_post(user, post_id):
    try:
        post = Post.objects.filter(pk=post_id, user=user, is_deleted=False).prefetch_related('media').first()
    except Exception:
        return None
    if post is None or lectures.first_video_media(post) is None:
        return None
    return post


class SeriesListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]
    throttle_classes = [LectureWriteThrottle]

    def get(self, request):
        p = request.query_params
        qs = VideoSeries.objects.filter(lectures.visible_series_q(request.user))
        if p.get('mine') in ('1', 'true'):
            qs = qs.filter(owner=request.user)
        if p.get('classroom'):
            qs = qs.filter(kind='classroom', classroom_id=str(p['classroom']))
        if p.get('post'):
            try:   # a sub-query, so the item count below is still the whole series (not just this lecture)
                qs = qs.filter(id__in=VideoSeriesItem.objects.filter(post_id=p['post']).values('series_id'))
            except Exception:
                qs = qs.none()
        qs = qs.annotate(n_items=Count('items', distinct=True)).order_by('-updated_at')[:60]
        data = [series_summary(request, s, request.user, count=s.n_items) for s in qs]
        return Response({'success': True, 'data': data})

    @transaction.atomic
    def post(self, request):
        body = _data(request)
        try:
            title = _clean_title(body.get('title'))
        except LectureError as e:
            return _fail(e)
        kind = str(body.get('kind') or 'creator')
        if kind not in ('creator', 'classroom'):
            return _err('invalid_kind', 'Unknown series type.')
        classroom_id = ''
        if kind == 'classroom':
            classroom_id = str(body.get('classroom_id') or '').strip()
            if not classroom_id or not lectures.classroom_is_taught_by(request.user, classroom_id):
                return _err('not_your_classroom', 'You can only make a course for a class you teach.',
                            status.HTTP_403_FORBIDDEN)
        if VideoSeries.objects.filter(owner=request.user).count() >= MAX_SERIES_PER_USER:
            return _err('too_many_series', f'You can have at most {MAX_SERIES_PER_USER} series.')
        series = VideoSeries(owner=request.user, title=title, description=str(body.get('description') or '').strip()[:2000],
                             kind=kind, classroom_id=classroom_id,
                             is_public=_truthy(body.get('is_public')) if kind == 'creator' else False)
        cover = request.FILES.get('cover')
        if cover is not None:
            try:
                _save_cover(series, cover)
            except LectureError as e:
                return _fail(e)
        series.save()
        return Response({'success': True, 'data': series_summary(request, series, request.user, count=0)},
                        status=status.HTTP_201_CREATED)


def _series_for_view(request, series_id):
    try:
        series = VideoSeries.objects.select_related('owner').filter(pk=series_id).first()
    except Exception:
        series = None
    if series is None or not lectures.series_visible_to(request.user, series):
        return None, _err('not_found', 'Series not found.', status.HTTP_404_NOT_FOUND)
    return series, None


def _series_for_owner(request, series_id):
    series, resp = _series_for_view(request, series_id)
    if resp is not None:
        return None, resp
    if series.owner_id != request.user.id:
        return None, _err('not_owner', 'Only the owner can change this series.', status.HTTP_403_FORBIDDEN)
    return series, None


class SeriesDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]
    throttle_classes = [LectureWriteThrottle]

    def get(self, request, series_id):
        series, resp = _series_for_view(request, series_id)
        if resp is not None:
            return resp
        entries = lectures.series_entries(series, request.user)
        rows = {r.media_id: r for r in VideoProgress.objects.filter(
            user=request.user, media_id__in=[m.id for _i, _p, m in entries])}
        out_entries = [entry_payload(request, e, index=i, progress=rows.get(e[2].id)) for i, e in enumerate(entries)]
        continue_post = next((e['post_id'] for e in out_entries
                              if not (e.get('progress') or {}).get('completed')), out_entries[0]['post_id'] if out_entries else None)
        data = series_summary(request, series, request.user, count=len(entries))
        done = sum(1 for e in out_entries if (e.get('progress') or {}).get('completed'))
        data.update({'entries': out_entries, 'continue_post_id': continue_post, 'completed_count': done})
        return Response({'success': True, 'data': data})

    @transaction.atomic
    def patch(self, request, series_id):
        series, resp = _series_for_owner(request, series_id)
        if resp is not None:
            return resp
        body = _data(request)
        try:
            if 'title' in body:
                series.title = _clean_title(body.get('title'))
            if 'description' in body:
                series.description = str(body.get('description') or '').strip()[:2000]
            if 'is_public' in body and series.kind == 'creator':
                series.is_public = _truthy(body.get('is_public'))
            if _truthy(body.get('clear_cover')) and series.cover:
                series.cover.delete(save=False)
            cover = request.FILES.get('cover')
            if cover is not None:
                _save_cover(series, cover)
        except LectureError as e:
            return _fail(e)
        series.save()
        return Response({'success': True, 'data': series_summary(request, series, request.user)})

    def delete(self, request, series_id):
        series, resp = _series_for_owner(request, series_id)
        if resp is not None:
            return resp
        series.delete()
        return Response({'success': True})


class SeriesItemsAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [LectureWriteThrottle]

    @transaction.atomic
    def post(self, request, series_id):
        series, resp = _series_for_owner(request, series_id)
        if resp is not None:
            return resp
        body = _data(request)
        ids = body.get('post_ids')
        if ids is None:
            ids = [body.get('post_id')] if body.get('post_id') else []
        if not isinstance(ids, (list, tuple)) or not ids:
            return _err('post_required', 'Choose at least one lecture to add.')
        section = ' '.join(str(body.get('section') or '').split())[:80]
        have = series.items.count()
        if have + len(ids) > lectures.MAX_SERIES_ITEMS:
            return _err('too_many_items', f'A series can have at most {lectures.MAX_SERIES_ITEMS} lectures.')
        nxt = (series.items.aggregate(m=Max('position'))['m'])
        nxt = 0 if nxt is None else nxt + 1
        added = 0
        for raw in ids:
            post = _own_video_post(request.user, raw)
            if post is None:
                return _err('not_a_lecture', 'You can only add your own video posts.', status.HTTP_400_BAD_REQUEST)
            _item, created = VideoSeriesItem.objects.get_or_create(
                series=series, post=post, defaults={'position': nxt, 'section': section})
            if created:
                nxt += 1
                added += 1
        series.save(update_fields=['updated_at'])
        return Response({'success': True, 'data': {'added': added, 'count': series.items.count()}},
                        status=status.HTTP_201_CREATED)


class SeriesItemDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [LectureWriteThrottle]

    @transaction.atomic
    def delete(self, request, series_id, post_id):
        series, resp = _series_for_owner(request, series_id)
        if resp is not None:
            return resp
        deleted, _ = series.items.filter(post_id=post_id).delete()
        if not deleted:
            return _err('not_found', 'That lecture is not in this series.', status.HTTP_404_NOT_FOUND)
        lectures.renumber(series)
        series.save(update_fields=['updated_at'])
        return Response({'success': True})


class SeriesOrderAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [LectureWriteThrottle]

    @transaction.atomic
    def put(self, request, series_id):
        series, resp = _series_for_owner(request, series_id)
        if resp is not None:
            return resp
        body = _data(request)
        order, sections = body.get('order'), body.get('sections')
        if isinstance(order, str):
            try:
                order = json.loads(order)
            except ValueError:
                order = None
        if not isinstance(order, (list, tuple)):
            return _err('invalid_order', 'Send the lectures in their new order.')
        sections = _json_obj(sections) if sections is not None else {}
        items = {str(i.post_id): i for i in series.items.all()}
        seen, n = set(), 0
        for pid in [str(x) for x in order] + [k for k in items if k not in {str(x) for x in order}]:
            item = items.get(pid)
            if item is None or pid in seen:
                continue
            seen.add(pid)
            fields = {'position': n}
            if pid in sections:
                fields['section'] = ' '.join(str(sections[pid] or '').split())[:80]
            VideoSeriesItem.objects.filter(pk=item.pk).update(**fields)
            n += 1
        series.save(update_fields=['updated_at'])
        return Response({'success': True, 'data': {'count': n}})


# ----------------------------------------------------------------------------- next lecture
class NextLectureAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [LectureProgressThrottle]

    def get(self, request, post_id):
        raw = request.query_params.get('series')
        series = _series_from_param(request.user, raw)
        if raw and series is None:
            return _err('not_found', 'Series not found.', status.HTTP_404_NOT_FOUND)
        if series is None:   # no series given: the first visible one that contains this lecture
            for cand in (VideoSeries.objects.filter(lectures.visible_series_q(request.user), items__post_id=post_id)
                         .distinct().order_by('-updated_at')[:5]):
                series = cand
                break
        if series is None:
            return Response({'success': True, 'data': None})
        nav = navigation_payload(request, series, request.user, post_id)
        return Response({'success': True, 'data': nav})
