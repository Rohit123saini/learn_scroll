"""
post/resource_versions.py  (version replace + paper metadata edit + in-file search)

  POST  /post/attachments/<id>/replace/                 author uploads v2 (v3 ...) of ONE file       multipart: file, note?, ocr_text?
  GET   /post/attachments/<id>/versions/                history: the live version + older ones (older files: author only)
  GET   /post/attachments/<id>/versions/<n>/download/   author downloads an older version
  GET   /post/attachments/<id>/search/?q=               which pages of this file contain the text (viewer search / OCR)
  PATCH /post/<id>/resource-meta/                       author edits: paper metadata, download allow/deny, watermark, subject, level
  GET   /post/<id>/citation/?style=apa|mla              the citation text (the app also builds it locally, offline)

Version replace and COMMENTS
  The replaced file goes into PostAttachmentVersion; the PostAttachment row - and therefore the Post, its comments,
  likes, saves, reading progress and AI-study cache key (it hashes the file size + version) - is KEPT. Only the file, its
  thumbnail, name, size, page count and searchable text change; `version` goes up by one. Nothing is deleted from storage.
  The new file goes through the same checks as an upload (type by magic bytes, size limit, metadata strip, virus scan,
  copyright pre-check) and must be the same KIND as the old one (PDF -> PDF, document -> document, image -> image,
  video -> video).

Every error is `{success: False, code, message}` like the rest of the resource API.
"""
import logging

from django.core.files.base import ContentFile
from django.db import transaction
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import parsers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import citations, resource_text, resource_watermark
from .audience import can_view_post
from .models import Post, PostAttachment, PostAttachmentVersion, ReadingProgress
from .resource_files import (ResourceFileError, check_upload, make_office_thumbnail, make_thumbnail, strip_metadata,
                             virus_scan)
from .resource_views import _err, _json_object, _truthy, paper_payload, save_paper
from .serializers import PostAttachmentSerializer, PostDetailSerializer

logger = logging.getLogger(__name__)

MAX_NOTE = 200


def _viewable_attachment(user, attachment_id):
    """The attachment if `user` may see its post (same gate as the download endpoint), else Http404."""
    att = get_object_or_404(PostAttachment.objects.select_related('post__user'), id=attachment_id)
    post = att.post
    if post.is_deleted or (post.moderation_status != 'approved' and post.user_id != user.id):
        raise Http404
    if not can_view_post(post, user):
        raise Http404
    return att


def _version_dict(*, version, name, size, pages, note, when, download_url=None, mime=''):
    return {'version': version, 'name': name, 'size': size, 'pages': pages, 'note': note, 'mime': mime,
            'date': when.isoformat() if when else None, 'download_url': download_url}


# ----------------------------------------------------------------------------- replace
class AttachmentReplaceAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    @transaction.atomic
    def post(self, request, attachment_id):
        from copyrights.services import can_upload, precheck_upload
        from .moderation_guard import post_locked_by_copyright

        att = get_object_or_404(PostAttachment.objects.select_for_update(), id=attachment_id)
        post = get_object_or_404(Post.objects.select_related('user'), id=att.post_id, is_deleted=False)
        if post.user_id != request.user.id:
            return _err('not_allowed', 'Only the author can replace a file.', status.HTTP_403_FORBIDDEN)
        if post.kind != Post.KIND_RESOURCE:
            return _err('not_a_resource', 'Only files of a Notes / Resource post can be replaced.')
        if post_locked_by_copyright(post):
            return _err('copyright_locked', "This post is under a copyright review and can't be changed.",
                        status.HTTP_403_FORBIDDEN)
        allowed, why = can_upload(request.user)
        if not allowed:
            return _err('copyright_upload_blocked', why, status.HTTP_403_FORBIDDEN)

        f = request.FILES.get('file')
        if f is None:
            return _err('file_required', 'Choose the new file.')
        try:
            checked = check_upload(f)
        except ResourceFileError as e:
            return _err(e.code, e.message, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE if e.code == 'file_too_large'
                        else status.HTTP_400_BAD_REQUEST, file=getattr(f, 'name', ''))
        if checked.kind != att.kind:
            return _err('kind_mismatch', 'The new file must be the same kind as the old one (for example PDF with PDF).')
        ok, code, why = precheck_upload(request.user, files=[f])
        if not ok:
            return _err(code, why, status.HTTP_403_FORBIDDEN)

        note = (request.data.get('note') or '').strip()[:MAX_NOTE]
        try:
            cleaned, pages = strip_metadata(f, checked)
            data = cleaned
            if data is None:
                f.seek(0)
            from django.core.files.base import ContentFile as _CF
            virus_scan(_CF(data) if data is not None else f)
            thumb = make_thumbnail(data if data is not None else f.read(), checked) if checked.kind in ('image', 'pdf') else None
            f.seek(0)
            raw = data
            if raw is None and checked.kind == 'doc':
                raw = f.read()
                f.seek(0)
                if pages is None:
                    pages = resource_text.office_page_count(raw, checked.ext)
                if thumb is None:
                    thumb = make_office_thumbnail(raw, checked.ext)
        except ResourceFileError as e:
            return _err(e.code, e.message, file=getattr(f, 'name', ''))

        client = resource_text.parse_ocr_texts(request.data.get('ocr_texts') or request.data.get('ocr_text'))
        text_pages, text_source = resource_text.build_text(checked, raw, client[0] if client else None)

        # 1) archive the file that is live now (same stored file - nothing is copied or deleted)
        old_version = att.version
        archived = PostAttachmentVersion(
            attachment=att, version=old_version, original_name=att.original_name, mime=att.mime, size=att.size,
            pages=att.pages, note=att.version_note, replaced_by=request.user,
            created_at=att.replaced_at or att.created_at, archived_at=timezone.now())
        archived.file = att.file.name
        if att.thumbnail:
            archived.thumbnail = att.thumbnail.name
        archived.save()

        # 2) put the new file in the SAME row
        att.file.save(f.name, ContentFile(data) if data is not None else f, save=False)
        if thumb:
            att.thumbnail.save('t.jpg', ContentFile(thumb), save=False)
        else:
            att.thumbnail = None
        att.original_name = (f.name or att.original_name)[-255:]
        att.mime = checked.mime
        att.size = len(data) if data is not None else checked.size
        att.pages = pages
        att.version = old_version + 1
        att.version_note = note
        att.replaced_at = timezone.now()
        att.text_pages = text_pages
        att.search_text = resource_text.flatten(text_pages)
        att.text_source = text_source
        att.save()
        try:  # a fuzzy copy of a removed file (re-encoded / re-exported) is held after the commit
            from copyrights.signals import queue_check
            queue_check('post', post.id, att.file.name)
        except Exception:
            logger.exception('copyright check after attachment replace failed')

        # 3) people who were on page 40 of the old file must not point past the end of the new one
        if pages:
            ReadingProgress.objects.filter(attachment=att).update(page_count=pages)
            ReadingProgress.objects.filter(attachment=att, last_page__gt=pages).update(last_page=pages)
        Post.objects.filter(pk=post.pk).update(updated_at=timezone.now())   # comments / likes / saves are untouched
        logger.info('Attachment %s of post %s replaced by %s: v%d -> v%d', att.id, post.id, request.user.id,
                    old_version, att.version)
        return Response({
            'success': True, 'message': f'Replaced - now version {att.version}',
            'data': PostAttachmentSerializer(att, context={'request': request}).data,
            'comments_count': post.comments_count,   # for the app: still the same discussion
        })


# ----------------------------------------------------------------------------- history
class AttachmentVersionsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, attachment_id):
        att = _viewable_attachment(request.user, attachment_id)
        owner = att.post.user_id == request.user.id
        from django.urls import reverse

        rows = []
        for v in att.versions.all():
            url = None
            if owner:
                path = reverse('post-attachment-version-download', kwargs={'attachment_id': att.id, 'version': v.version})
                url = request.build_absolute_uri(path)
            rows.append(_version_dict(version=v.version, name=v.original_name, size=v.size, pages=v.pages, note=v.note,
                                      when=v.created_at, download_url=url, mime=v.mime))
        current = _version_dict(version=att.version, name=att.original_name, size=att.size, pages=att.pages,
                                note=att.version_note, when=att.replaced_at or att.created_at, mime=att.mime)
        return Response({'success': True, 'current': current, 'versions': rows, 'is_owner': owner})


class AttachmentVersionDownloadAPIView(APIView):
    """Older versions are for the author only (readers always get the newest file)."""
    permission_classes = [IsAuthenticated]

    def get(self, request, attachment_id, version):
        att = get_object_or_404(PostAttachment.objects.select_related('post'), id=attachment_id)
        if att.post.user_id != request.user.id or att.post.is_deleted:
            raise Http404
        ver = get_object_or_404(PostAttachmentVersion, attachment=att, version=version)
        return HttpResponseRedirect(ver.file.url)


# ----------------------------------------------------------------------------- search inside one file
class AttachmentTextSearchAPIView(APIView):
    """GET ?q=  ->  {hits: [{page, snippet, count}], has_text, text_source}. Works for PDFs with a text layer, Office files
    and anything OCR'd; the viewer uses it when its own search finds nothing (a scan has no text layer)."""
    permission_classes = [IsAuthenticated]

    def get(self, request, attachment_id):
        att = _viewable_attachment(request.user, attachment_id)
        q = (request.query_params.get('q') or '').strip()
        if len(q) < 2:
            return Response({'success': True, 'query': q, 'has_text': resource_text.has_text(att.text_pages),
                             'text_source': att.text_source, 'hits': []})
        hits = resource_text.page_hits(att.text_pages, q)
        return Response({'success': True, 'query': q, 'has_text': resource_text.has_text(att.text_pages),
                         'text_source': att.text_source, 'hits': hits})


# ----------------------------------------------------------------------------- edit resource metadata
class ResourceMetaAPIView(APIView):
    """PATCH /post/<id>/resource-meta/  {paper?: {...} | {}, download_allowed?, watermark_mode?, subject?, level?}
    `paper: {}` (or all fields empty) removes the paper metadata. Anything not sent is left alone."""
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser]

    @transaction.atomic
    def patch(self, request, id):
        from .moderation_guard import post_locked_by_copyright

        post = get_object_or_404(Post.objects.select_related('user'), id=id, is_deleted=False)
        if post.user_id != request.user.id:
            return _err('not_allowed', 'Only the author can do this.', status.HTTP_403_FORBIDDEN)
        if post.kind != Post.KIND_RESOURCE:
            return _err('not_a_resource', 'This post is not a Notes / Resource post.')
        if post_locked_by_copyright(post):
            return _err('copyright_locked', "This post is under a copyright review and can't be changed.",
                        status.HTTP_403_FORBIDDEN)
        d = request.data
        fields = []
        if 'download_allowed' in d:
            post.download_allowed = _truthy(d.get('download_allowed'), True)
            fields.append('download_allowed')
        if 'watermark_mode' in d:
            mode = (d.get('watermark_mode') or '').strip().lower()
            if mode not in resource_watermark.MODES:
                return _err('invalid_watermark', 'Unknown watermark option.')
            post.watermark_mode = mode
            fields.append('watermark_mode')
        if 'subject' in d:
            subject = (d.get('subject') or '').strip()
            if not subject:
                return _err('subject_required', 'Please choose a subject.')
            post.subject = subject[:100]
            fields.append('subject')
        if 'level' in d:
            post.level = (d.get('level') or '').strip()[:60]
            fields.append('level')
        if 'paper' in d:
            clean, err = citations.clean_paper(_json_object(d.get('paper')))
            if err is not None:
                return _err(err.code, err.message)
            save_paper(post, clean)
        if fields:
            post.save(update_fields=fields + ['updated_at'])   # metadata: never marks the post "edited"
        elif 'paper' not in d:
            return _err('nothing_to_update', 'Nothing to update.')
        post = Post.objects.select_related('user', 'paper').get(pk=post.pk)
        return Response({'success': True, 'message': 'Saved',
                         'data': PostDetailSerializer(post, context={'request': request}).data})


# ----------------------------------------------------------------------------- citation text
class PaperCitationAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, id):
        post = get_object_or_404(Post.objects.select_related('user'), id=id, is_deleted=False, kind=Post.KIND_RESOURCE)
        if post.moderation_status != 'approved' and post.user_id != request.user.id:
            raise Http404
        if not can_view_post(post, request.user):
            raise Http404
        paper = paper_payload(post)
        if paper is None:
            return _err('no_paper', 'This resource has no paper details.', status.HTTP_404_NOT_FOUND)
        style = (request.query_params.get('style') or '').lower()
        body = {'success': True, 'apa': paper['citation']['apa'], 'mla': paper['citation']['mla']}
        if style in ('apa', 'mla'):
            body['style'] = style
            body['text'] = paper['citation'][style]
        return Response(body)
