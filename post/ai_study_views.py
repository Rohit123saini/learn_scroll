"""
post/ai_study_views.py  (Feature 1)

  POST /post/<id>/ai-study/   {"mode": "summary"|"quiz"|"flashcards", "language": "en"|"hi"|"hinglish"}
  GET  /post/<id>/ai-study/?mode=&language=     cached result only (never calls the AI)

Success : {success: True, mode, language, cached, text | questions | cards}
Error   : {success: False, code, message}   codes: invalid_mode, invalid_language, ai_unavailable,
          ai_not_allowed, not_enough_content, ai_failed  (+ 404 for a post the viewer cannot see)
"""
import logging

from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from aiquality.telemetry import ai_context  # P-F6
from .ai_study import LANGUAGES, MODES, AiNotAllowed, NotEnoughContent, get_or_generate, peek
from .audience import can_view_post
from .models import Post

logger = logging.getLogger(__name__)


class PostAiStudyThrottle(UserRateThrottle):
    rate = '20/min'
    scope = 'post_ai_study'


def _err(code, message, http=status.HTTP_400_BAD_REQUEST):
    return Response({'success': False, 'code': code, 'message': message}, status=http)


def _ai_meta(row, ctx):
    """P-F6: what the app echoes back with a 👍 / 👎. A cached row keeps the prompt version it was made with."""
    stored = (row.payload or {}).get('_ai') or {}
    meta = ctx.meta() if ctx is not None else {}
    return {
        'surface': 'post_study', 'feature': row.mode,
        'prompt_version': stored.get('prompt_version') or meta.get('prompt_version') or 'v1',
        'model': stored.get('model') or meta.get('model') or '',
        'call_id': meta.get('call_id'),
        'ref_id': str(row.id),
    }


def _body(row, cached, ctx=None):
    payload = {k: v for k, v in (row.payload or {}).items() if k != '_ai'}
    return {'success': True, 'mode': row.mode, 'language': row.language, 'cached': cached,
            'ref_id': str(row.id), 'ai_meta': _ai_meta(row, ctx), **payload}


def _visible_post(request, post_id):
    post = get_object_or_404(Post.objects.prefetch_related('attachments'), id=post_id, is_deleted=False)
    if post.moderation_status != 'approved' and post.user_id != request.user.id:
        raise Http404
    if not can_view_post(post, request.user):
        raise Http404
    return post


def _params(data):
    mode = (data.get('mode') or '').strip()
    language = (data.get('language') or 'en').strip()
    if mode not in MODES:
        return None, None, _err('invalid_mode', 'mode must be summary, quiz or flashcards.')
    if language not in LANGUAGES:
        return None, None, _err('invalid_language', 'language must be en, hi or hinglish.')
    return mode, language, None


class PostAiStudyAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get_throttles(self):
        # the cached GET is a DB read only - no need to rate-limit it like the AI call
        return [PostAiStudyThrottle()] if self.request.method == 'POST' else []

    def get(self, request, id):
        post = _visible_post(request, id)
        mode, language, bad = _params(request.query_params)
        if bad:
            return bad
        row = peek(post, request.user, mode, language)
        if row is None:
            return Response({'success': True, 'available': False})
        return Response({**_body(row, True), 'available': True})

    def post(self, request, id):
        from message.ai_service import AI_ENABLED

        post = _visible_post(request, id)
        mode, language, bad = _params(request.data)
        if bad:
            return bad
        if not AI_ENABLED:
            return _err('ai_unavailable', 'AI is not available right now.', status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            with ai_context('post_study', mode, request.user) as ctx:
                row, cached = get_or_generate(post, request.user, mode, language)
        except AiNotAllowed:
            return _err('ai_not_allowed',
                        'The author turned off downloads for this resource, so AI help is off for its files.',
                        status.HTTP_403_FORBIDDEN)
        except NotEnoughContent:
            return _err('not_enough_content', 'This post has no text or PDF the AI can read.')
        except Exception:
            logger.exception('Post AI study failed user=%s post=%s mode=%s', request.user.id, post.id, mode)
            return _err('ai_failed', 'AI is temporarily unavailable, please try again.',
                        status.HTTP_502_BAD_GATEWAY)
        return Response(_body(row, cached, ctx))
