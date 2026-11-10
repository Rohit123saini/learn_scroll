"""
post/resource_views.py  (P1 - knowledge sharing)

  POST /post/resources/                       create a Notes/Resource post (multipart)
  GET  /post/resources/                       list/search (?q= ?subject= ?level= ?topic= ?tag= ?file_kind= ?mine=1)
  GET  /post/attachments/<id>/download/       download (honours `download_allowed`, audience, moderation; stamps the watermark)
  POST /post/attachments/<id>/report/         report a file (reuses the post report flow's reasons)
  GET  /post/hashtags/search/?q=              prefix search over the Hashtag table (+ counts)
  GET  /post/hashtags/suggest/?q=             typing suggestions: my recent tags first, then trending
  POST /post/<id>/convert-to-doubt/           author turns an existing post into a Doubt

Every error is `{success: False, code, message}` with a stable `code`, so the
app can show a clear message (file_too_large, unsupported_type, ...).

Resources upgrade (research papers, watermark, searchable text) - extra request fields on create:
  paper            JSON object {authors[], abstract, doi, link, year, venue}   (see post/citations.py)
  watermark_mode   none | author | viewer                                      (post/resource_watermark.py)
  ocr_texts        JSON list aligned with `files`: null | "text" | ["page 1", "page 2"]   (on-device OCR, post/resource_text.py)
Extra list filters: ?paper=1 ?year= ?year_from= ?year_to= (a backwards range is swapped) ?author= ?doi= ; `?q=` also matches paper
metadata (a pasted doi.org link / `doi:` form matches that DOI) and the TEXT inside the files (disable the file-text part with ?in_files=0). Version replace / history / in-file search live in
post/resource_versions.py.
"""
import json
import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.core.files.base import ContentFile
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import parsers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination

from .audience import can_view_post, filter_posts_visible_to
import uuid

from .idempotency import idempotent  # H20
from .hashtags import (MAX_HASHTAGS_PER_POST, clean_topics, extract_from_text, normalize_list,
                       normalize_tag, sync_post_hashtags)
from . import citations, resource_text, resource_watermark
from .models import Hashtag, Post, PostAttachment, ResourcePaper
from .resource_files import (MAX_FILES_PER_POST, ResourceFileError, check_upload, make_office_thumbnail, make_thumbnail,
                             strip_metadata, virus_scan)
from .serializers import PostAttachmentSerializer, PostDetailSerializer, PostListSerializer

logger = logging.getLogger(__name__)

_POST_TYPE_BY_KIND = {'pdf': 'document', 'doc': 'document', 'image': 'image', 'video': 'video'}


def _err(code, message, http=status.HTTP_400_BAD_REQUEST, **extra):
    return Response({'success': False, 'code': code, 'message': message, **extra}, status=http)


def _resource_category(raw):
    """T3.2 - category is optional on a resource: a known key / label is kept, anything else falls back to 'education'."""
    from .serializers import normalize_category
    return normalize_category(raw) or 'education'


def _json_list(raw):
    """Multipart sends lists as a JSON string or a comma separated string."""
    if raw in (None, ''):
        return []
    if isinstance(raw, (list, tuple)):
        return list(raw)
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else [val]
    except (TypeError, ValueError):
        return [p for p in str(raw).split(',')]


def _int_or_none(raw):
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _truthy(raw, default=True):
    if raw is None or raw == '':
        return default
    return str(raw).lower() in ('1', 'true', 'yes', 'on')


def _json_object(raw):
    """Multipart sends an object as a JSON string; a JSON body sends a dict."""
    if isinstance(raw, dict):
        return raw
    if raw in (None, ''):
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except (TypeError, ValueError):
        return {}


def save_paper(post, clean):
    """Create / update / delete the ResourcePaper row of `post` from `citations.clean_paper` output ({} = remove)."""
    if not clean:
        ResourcePaper.objects.filter(post=post).delete()
        return None
    paper, _ = ResourcePaper.objects.update_or_create(post=post, defaults={
        'authors': clean['authors'], 'authors_text': '; '.join(clean['authors']),
        'abstract': clean['abstract'], 'doi': clean['doi'], 'link': clean['link'],
        'year': clean['year'], 'venue': clean['venue'],
    })
    return paper


def paper_payload(post):
    """The `paper` object the API returns (None for a post without paper metadata): metadata + both citations."""
    from django.core.exceptions import ObjectDoesNotExist
    try:
        paper = post.paper
    except ObjectDoesNotExist:
        return None
    if paper is None:
        return None
    data = {'authors': list(paper.authors or []), 'abstract': paper.abstract, 'doi': paper.doi,
            'doi_url': citations.doi_url(paper.doi), 'link': paper.link, 'year': paper.year, 'venue': paper.venue}
    data['citation'] = citations.citations_for(post.title or '', data)
    return data


class ResourcePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 50


def _visible_resources(user):
    qs = Post.objects.select_related('user').prefetch_related('attachments', 'media').filter(
        kind=Post.KIND_RESOURCE, is_deleted=False, is_archived=False, moderation_status='approved', is_scheduled=False)
    return filter_posts_visible_to(qs, user)


class ResourceListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    # ------------------------------------------------------------------ list
    def get(self, request):
        qs = _visible_resources(request.user)
        p = request.query_params
        if p.get('mine') in ('1', 'true'):
            qs = qs.filter(user=request.user)
        if p.get('subject'):
            # H18 — same alias spellings the study digest / Home chips use ('maths' = math = mathematics), so the
            # "Maths: 5 new notes" digest row opens a list that matches its count.
            from .feed_chips import _ALIAS_TO_CANONICAL, subject_spellings
            wanted = ' '.join(p['subject'].strip().lower().split())
            wanted = _ALIAS_TO_CANONICAL.get(wanted, wanted)
            subject_q = Q()
            for spelling in subject_spellings(wanted):
                subject_q |= Q(subject__iexact=spelling)
            qs = qs.filter(subject_q)
        if p.get('level'):
            qs = qs.filter(level__iexact=p['level'].strip())
        if p.get('topic'):
            qs = qs.filter(topics__icontains=p['topic'].strip())
        if p.get('tag'):
            tag = normalize_tag(p['tag'])
            qs = qs.filter(hashtags__contains=[tag]) if tag else qs.none()
        if p.get('file_kind') in ('pdf', 'doc', 'image', 'video'):
            qs = qs.filter(attachments__kind=p['file_kind']).distinct()
        # research-paper metadata filters
        if p.get('paper') in ('1', 'true'):
            qs = qs.filter(paper__isnull=False)
        year, year_from, year_to = (_int_or_none(p.get(k)) for k in ('year', 'year_from', 'year_to'))
        if year_from is not None and year_to is not None and year_from > year_to:
            year_from, year_to = year_to, year_from   # a range typed backwards means the same range, not "no results"
        if year is not None:
            qs = qs.filter(paper__year=year)
        if year_from is not None:
            qs = qs.filter(paper__year__gte=year_from)
        if year_to is not None:
            qs = qs.filter(paper__year__lte=year_to)
        if (p.get('author') or '').strip():
            qs = qs.filter(paper__authors_text__icontains=p['author'].strip())
        if (p.get('doi') or '').strip():
            doi = citations.normalize_doi(p['doi']) or p['doi'].strip()
            qs = qs.filter(paper__doi__iexact=doi)
        q = (p.get('q') or '').strip()
        if q:
            cond = (Q(title__icontains=q) | Q(content__icontains=q) | Q(subject__icontains=q)
                    | Q(topics__icontains=q) | Q(hashtags__contains=[normalize_tag(q)])
                    | Q(paper__authors_text__icontains=q) | Q(paper__abstract__icontains=q) | Q(paper__venue__icontains=q)
                    | Q(paper__doi__icontains=q))
            pasted_doi = citations.normalize_doi(q)   # 'https://doi.org/10.1000/ABC' / 'doi:10.1000/ABC' -> '10.1000/ABC'
            if pasted_doi:
                cond |= Q(paper__doi__iexact=pasted_doi)
            if p.get('in_files') not in ('0', 'false'):
                cond |= Q(attachments__search_text__icontains=q)   # text inside the PDFs / scans (OCR)
            qs = qs.filter(cond).distinct()
        qs = qs.select_related('paper')
        qs = qs.order_by('-created_at')
        pager = ResourcePagination()
        page = pager.paginate_queryset(qs, request, view=self)
        data = PostListSerializer(page, many=True, context={'request': request}).data
        return pager.get_paginated_response(data)

    # ---------------------------------------------------------------- create
    @idempotent("resource_create")  # H20: above @transaction.atomic so the claim survives a rollback
    @transaction.atomic
    def post(self, request):
        from copyrights.services import can_upload, precheck_upload
        allowed, why = can_upload(request.user)
        if not allowed:
            return _err('copyright_upload_blocked', why, status.HTTP_403_FORBIDDEN)

        title = (request.data.get('title') or '').strip()
        subject = (request.data.get('subject') or '').strip()
        # T3.1 - title is OPTIONAL: left empty, the server names the post (text -> file name -> "Post - <date>"), see
        # post/auto_title.py. A title the author typed is still length-checked.
        if len(title) > 300:
            return _err('title_too_long', 'Title can be at most 300 characters.')
        if not subject:
            return _err('subject_required', 'Please choose a subject.')

        files = request.FILES.getlist('files')
        if not files:
            return _err('file_required', 'Attach at least one file (PDF, document, image or video).')
        if not title:
            from .auto_title import derive_title
            title = derive_title(request.data.get('content'), [getattr(f, 'name', '') for f in files])
        if len(files) > MAX_FILES_PER_POST:
            return _err('too_many_files', f'You can attach at most {MAX_FILES_PER_POST} files.')

        # 1) validate EVERY file before anything is written
        checked = []
        for f in files:
            try:
                checked.append(check_upload(f))
            except ResourceFileError as e:
                return _err(e.code, e.message, status.HTTP_400_BAD_REQUEST if e.code != 'file_too_large'
                            else status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, file=getattr(f, 'name', ''))
        ok, code, why = precheck_upload(request.user, files=files)
        if not ok:
            return _err(code, why, status.HTTP_403_FORBIDDEN)

        # H23 publish gate (rate limit, abusive/NSFW text, duplicates, audio flag)
        from . import publish_gate
        verdict = publish_gate.evaluate(
            request.user, "resource",
            texts=publish_gate.texts_from_request("resource", request.data), files=files,
        )
        if not verdict.ok:
            return verdict.response()

        from .content_defaults import post_extra_fill, post_visibility_default  # S4b
        visibility = post_visibility_default(request.user, request.data.get('visibility'))
        if visibility not in dict(Post.VISIBILITY_CHOICES):
            return _err('invalid_visibility', 'Unknown audience.')

        scheduled_at = None
        raw_sched = request.data.get('scheduled_at')
        if raw_sched:
            scheduled_at = parse_datetime(str(raw_sched))
            if scheduled_at is None:
                return _err('invalid_schedule', 'Could not read the scheduled time.')
            if timezone.is_naive(scheduled_at):
                scheduled_at = timezone.make_aware(scheduled_at)
            if scheduled_at <= timezone.now() + timedelta(minutes=1):
                return _err('schedule_in_past', 'Pick a time in the future.')

        raw_tags = _json_list(request.data.get('hashtags'))
        description = (request.data.get('content') or '').strip()
        all_tags = normalize_list(raw_tags + extract_from_text(description), limit=10_000)  # de-duplicated
        if len(all_tags) > MAX_HASHTAGS_PER_POST:
            return _err('too_many_hashtags', f'You can add at most {MAX_HASHTAGS_PER_POST} hashtags.')
        tags = all_tags

        # research-paper metadata, watermark choice, on-device OCR text (all optional)
        paper_clean, paper_err = citations.clean_paper(_json_object(request.data.get('paper')))
        if paper_err is not None:
            return _err(paper_err.code, paper_err.message)
        watermark_mode = (request.data.get('watermark_mode') or resource_watermark.MODE_NONE).strip().lower()
        if watermark_mode not in resource_watermark.MODES:
            return _err('invalid_watermark', 'Unknown watermark option.')
        client_ocr = resource_text.parse_ocr_texts(request.data.get('ocr_texts'))

        # 2) sanitise + scan (strip metadata, antivirus) BEFORE the Post row exists
        prepared = []
        for f, c in zip(files, checked):
            try:
                cleaned, pages = strip_metadata(f, c)
                data = cleaned if cleaned is not None else None
                if data is None:
                    f.seek(0)
                virus_scan(ContentFile(data) if data is not None else f)
                thumb = make_thumbnail(data if data is not None else f.read(), c) if c.kind in ('image', 'pdf') else None
                f.seek(0)
                raw_bytes = data
                if raw_bytes is None and c.kind == 'doc':  # Office file: kept as is, read once for text / page count / preview
                    raw_bytes = f.read()
                    f.seek(0)
                    if pages is None:
                        pages = resource_text.office_page_count(raw_bytes, c.ext)
                    if thumb is None:
                        thumb = make_office_thumbnail(raw_bytes, c.ext)
                idx = len(prepared)
                text_pages, text_source = resource_text.build_text(
                    c, raw_bytes, client_ocr[idx] if idx < len(client_ocr) else None)
            except ResourceFileError as e:
                return _err(e.code, e.message, file=getattr(f, 'name', ''))
            prepared.append((f, c, data, pages, thumb, text_pages, text_source))

        alt_texts = _json_list(request.data.get('alt_texts'))

        post = Post.objects.create(
            user=request.user,
            kind=Post.KIND_RESOURCE,
            post_type=_POST_TYPE_BY_KIND[checked[0].kind],
            title=title, content=description or None,
            category=_resource_category(request.data.get('category')),  # T3.2 - optional, unknown / empty -> 'education'
            subject=subject[:100], level=(request.data.get('level') or '').strip()[:60],
            topics=clean_topics(_json_list(request.data.get('topics'))),
            hashtags=tags, visibility=visibility,
            download_allowed=_truthy(request.data.get('download_allowed'), True),
            watermark_mode=watermark_mode,
            is_scheduled=bool(scheduled_at), published_at=scheduled_at,
            slug=f"res-{uuid.uuid4().hex[:12]}",
            moderation_status=verdict.moderation_status,
            **post_extra_fill(request.user, request.data),  # S4b: comments limit / reshare / save defaults
        )
        for i, (f, c, data, pages, thumb, text_pages, text_source) in enumerate(prepared):
            att = PostAttachment(
                post=post, kind=c.kind, original_name=(f.name or 'file')[-255:], mime=c.mime,
                size=len(data) if data is not None else c.size, pages=pages, order=i,
                alt_text=(str(alt_texts[i]).strip()[:500] if i < len(alt_texts) and alt_texts[i] else ''),
                text_pages=text_pages, search_text=resource_text.flatten(text_pages), text_source=text_source,
            )
            att.file.save(f.name, ContentFile(data) if data is not None else f, save=False)
            if thumb:
                att.thumbnail.save('t.jpg', ContentFile(thumb), save=False)
            att.save()
        sync_post_hashtags(post)
        save_paper(post, paper_clean)
        publish_gate.after_create(verdict, request.user, "resource", post)
        logger.info('Resource post %s created by %s (%d files)', post.id, request.user.id, len(prepared))
        out = PostDetailSerializer(post, context={'request': request}).data
        return Response({'success': True, 'message': 'Resource posted', 'data': out, **verdict.held_payload()},
                        status=status.HTTP_201_CREATED)


class AttachmentDownloadAPIView(APIView):
    """GET /post/attachments/<id>/download/ - 302 to a short-lived signed /pmedia/ URL."""
    permission_classes = [IsAuthenticated]

    def get(self, request, attachment_id):
        att = get_object_or_404(PostAttachment.objects.select_related('post__user'), id=attachment_id)
        post = att.post
        if post.is_deleted or (post.moderation_status != 'approved' and post.user_id != request.user.id):
            raise Http404
        if not can_view_post(post, request.user):
            raise Http404
        if post.user_id != request.user.id and not post.download_allowed:
            return _err('download_not_allowed', 'The author has turned off downloads for this resource.',
                        status.HTTP_403_FORBIDDEN)
        stamp = resource_watermark.watermark_text(post.watermark_mode, post, request.user)
        if stamp and att.kind in ('pdf', 'image'):
            return self._stamped(att, stamp)
        return HttpResponseRedirect(att.file.url)

    @staticmethod
    def _stamped(att, stamp):
        """The file with the watermark on it, sent as a download. Fails CLOSED: no watermark -> no file."""
        from django.http import HttpResponse
        try:
            with att.file.open('rb') as fh:
                data = fh.read()
        except Exception:
            raise Http404
        if att.kind == 'pdf':
            out, mime, name = resource_watermark.watermark_pdf(data, stamp), 'application/pdf', att.original_name
        else:
            res = resource_watermark.watermark_image(data, stamp)
            out, mime, name = (res[0], res[1], att.original_name.rsplit('.', 1)[0] + '.' + res[2]) if res else (None, '', '')
        if out is None:
            return _err('watermark_failed', 'Could not prepare this file for download right now. Please try again later.',
                        status.HTTP_503_SERVICE_UNAVAILABLE)
        resp = HttpResponse(out, content_type=mime)
        safe = name.replace('"', '').replace('\r', '').replace('\n', '')
        resp['Content-Disposition'] = f'attachment; filename="{safe}"'
        resp['Cache-Control'] = 'private, no-store'
        return resp


class AttachmentReportAPIView(APIView):
    """POST /post/attachments/<id>/report/ {reason, details} - reports the parent post through the
    app-wide report flow (user_profile.services.file_report -> ContentReport)."""
    permission_classes = [IsAuthenticated]

    def post(self, request, attachment_id):
        from user_profile.services import ReportRateLimited, file_report
        att = get_object_or_404(PostAttachment.objects.select_related('post'), id=attachment_id)
        if not can_view_post(att.post, request.user):
            raise Http404
        details = ((request.data.get('details') or '')[:900] + f' [file {att.id}: {att.original_name}]')[:1000]
        try:
            file_report(request.user, 'post', str(att.post_id), request.data.get('reason') or 'other', details)
        except LookupError:
            raise Http404
        except ValueError:
            return _err('own_content', 'You cannot report your own post.')
        except ReportRateLimited:
            return _err('rate_limited', 'You have sent many reports recently. Please try again later.',
                        status.HTTP_429_TOO_MANY_REQUESTS)
        return Response({'success': True, 'message': 'Thanks - we will review this file.'})


class HashtagSearchAPIView(APIView):
    """GET /post/hashtags/search/?q=phy&limit=20 -> [{hashtag, count}] (prefix match, most used first)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        q = normalize_tag(request.query_params.get('q', ''))
        limit = min(int(request.query_params.get('limit', 20) or 20), 50)
        if not q:
            return Response({'success': True, 'results': []})
        rows = Hashtag.objects.filter(name__startswith=q, posts_count__gt=0).order_by('-posts_count', 'name')[:limit]
        return Response({'success': True, 'results': [{'hashtag': h.name, 'count': h.posts_count} for h in rows]})


class HashtagSuggestAPIView(APIView):
    """GET /post/hashtags/suggest/?q=ph - composer suggestions while typing `#ph`:
    the caller's own earlier tags first, then trending ones. `q` may be empty
    (just after typing `#`)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        q = normalize_tag(request.query_params.get('q', ''))
        mine, seen = [], set()
        recent = Post.objects.filter(user=request.user, is_deleted=False).exclude(hashtags=[]) \
            .order_by('-created_at').values_list('hashtags', flat=True)[:60]
        for tags in recent:
            for t in tags:
                if t not in seen and (not q or t.startswith(q)):
                    seen.add(t)
                    mine.append(t)
        since = timezone.now() - timedelta(days=14)
        trend_qs = Hashtag.objects.filter(posts_count__gt=0, last_used_at__gte=since)
        if q:
            trend_qs = trend_qs.filter(name__startswith=q)
        trending = [h.name for h in trend_qs.order_by('-posts_count')[:15] if h.name not in seen]
        return Response({'success': True,
                         'mine': mine[:10],
                         'trending': trending[:10]})


class ConvertToDoubtAPIView(APIView):
    """POST /post/<id>/convert-to-doubt/ - the author turns a text/image post into a Doubt."""
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, id):
        post = get_object_or_404(Post, id=id, is_deleted=False)
        if post.user_id != request.user.id:
            return _err('not_allowed', 'Only the author can do this.', status.HTTP_403_FORBIDDEN)
        if post.post_type in ('doubt', 'poll', 'repost') or post.kind == Post.KIND_RESOURCE:
            return _err('cannot_convert', 'This kind of post cannot be turned into a doubt.')
        if not (post.content or post.title):
            return _err('empty_post', 'Add some text to your post first - a doubt needs a question.')
        post.post_type = 'doubt'
        post.kind = Post.KIND_DOUBT
        post.save(update_fields=['post_type', 'kind'])
        return Response({'success': True, 'message': 'Converted to a doubt',
                         'data': {'id': str(post.id), 'post_type': post.post_type, 'kind': post.kind}})
