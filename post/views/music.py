# post/views/music.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

from rest_framework.permissions import IsAuthenticated
from common.open_throttles import FreesoundSearchThrottle  # P-B3
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
)
from drf_spectacular.types import OpenApiTypes
from rest_framework.response import Response
from rest_framework.decorators import (
    api_view,
    permission_classes,
    throttle_classes,
)
from django.conf import settings

# `requests` is guarded the same way it always was: if it genuinely isn't installed the
# endpoint degrades to a clean 503 instead of an ImportError at import time.
try:
    import requests
except ImportError:  # pragma: no cover - requests should be a real dependency
    requests = None
from ._common import logger


# ===================== FREESOUND MUSIC SEARCH (TASK 4) =====================
# GET /post/music/search/?q=&page= — ApiService.searchFreesoundMusic()
# (api_service.dart) was already calling this exact path; nothing backing
# it existed, so the Music tab in media_edit_screen.dart/auto_edit_screen.dart
# couldn't search. Thin server-side proxy so settings.FREESOUND_API_KEY
# (already configured via the FREESOUND_API_KEY env var — settings.py)
# never has to ship inside the app.
FREESOUND_SEARCH_URL = "https://freesound.org/apiv2/search/text/"
FREESOUND_TRENDING_QUERY = "music"   # ?trending=1 without q: sorted by downloads, so the generic word is enough


@api_view(['GET'])
@permission_classes([IsAuthenticated])  # P-B3: only the logged-in editor uses this; it spends OUR Freesound key
@throttle_classes([FreesoundSearchThrottle])
@extend_schema(
    summary="Search Freesound for CC0 music",
    parameters=[
        OpenApiParameter(name="q", type=OpenApiTypes.STR, required=False),
        OpenApiParameter(name="trending", type=OpenApiTypes.BOOL, required=False),
        OpenApiParameter(name="page", type=OpenApiTypes.INT, required=False),
    ],
    tags=["Post"],
)
def freesound_music_search(request):
    """Matches searchFreesoundMusic()'s contract exactly:
    {"success", "data": {"results": [...]}}, each result
    {id, name, artist, duration, preview_url, license, tags}.

    Only CC0 ("Creative Commons 0" / public-domain) results are requested
    from Freesound — per that Dart method's own comment ("sirf
    CC0-licensed (copyright-free) results deta hai"). CC0 needs no
    attribution, so it's the only license this app's Music tab can safely
    let someone attach to a post without a credit-line feature to go
    with it — anything else (CC-BY etc.) would need attribution UI this
    app doesn't have yet.
    """
    if requests is None:
        logger.error("Freesound search unavailable: the 'requests' package isn't installed")
        return Response({"success": False, "message": "Music search unavailable"}, status=503)

    api_key = getattr(settings, 'FREESOUND_API_KEY', None)
    if not api_key:
        return Response({"success": False, "message": "Music search is not configured"}, status=503)

    query = (request.query_params.get('q') or '').strip()
    # Reel studio music sheet: `?trending=1` (no q) = the most downloaded CC0 sounds, short ones first-class for reels.
    trending = (request.query_params.get('trending') or '').strip().lower() in ('1', 'true', 'yes')
    if not query and not trending:
        return Response({"success": False, "message": "q is required"}, status=400)
    if not query:
        query = FREESOUND_TRENDING_QUERY

    try:
        page = int(request.query_params.get('page', 1))
    except (TypeError, ValueError):
        page = 1
    page = max(page, 1)

    try:
        resp = requests.get(
            FREESOUND_SEARCH_URL,
            params={
                'query': query,
                'token': api_key,
                'page': page,
                # CC0 only — see docstring above for why.
                'filter': 'license:"Creative Commons 0"',
                **({'sort': 'downloads_desc'} if trending else {}),
                'fields': 'id,name,username,duration,previews,license,tags',
            },
            timeout=8,
        )
    except requests.RequestException as e:
        logger.error(f'Freesound search request failed: {e}')
        return Response({"success": False, "message": "Music search unavailable"}, status=503)

    if resp.status_code != 200:
        logger.error(f'Freesound returned {resp.status_code}: {resp.text[:300]}')
        return Response({"success": False, "message": "Music search failed"}, status=502)

    try:
        payload = resp.json()
    except ValueError:
        logger.error('Freesound returned non-JSON response')
        return Response({"success": False, "message": "Music search failed"}, status=502)

    results = []
    # X7: sounds staff took down are not listed any more (post/sounds.py).
    from .. import sounds as _sounds
    blocked_sounds = _sounds.blocked_ids()
    for item in payload.get('results', []):
        if str(item.get('id')) in blocked_sounds:
            continue
        previews = item.get('previews') or {}
        preview_url = previews.get('preview-hq-mp3') or previews.get('preview-lq-mp3')
        results.append({
            "id": item.get('id'),
            "name": item.get('name'),
            "artist": item.get('username'),
            "duration": item.get('duration'),
            "preview_url": preview_url,
            "license": "CC0",
            "tags": item.get('tags') or [],
        })

    return Response({
        "success": True,
        "data": {
            "results": results,
            "count": payload.get('count', len(results)),
            # Freesound's own `next` is a full API URL (with the token in
            # it) — never pass that through to the client. Just tell it
            # whether another page exists; it already knows how to ask
            # for `page + 1` itself (searchFreesoundMusic's `page` param).
            "next_page": (page + 1) if payload.get('next') else None,
        },
    })
