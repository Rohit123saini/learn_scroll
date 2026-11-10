"""
post/sound_views.py - X7: the sound page, reports and the staff takedown API.

    GET  /post/sounds/policy/                 what the app allows (CC0 + own audio), takedown contact          (anyone)
    GET  /post/sounds/<sound_id>/             header of the sound page: name, artist, licence, credit link, uses, and for
                                              "Use this sound" preview_url / duration / tags (Freesound re-checked: still CC0?)
    GET  /post/sounds/<sound_id>/reels/       "Reels with this sound"   ?page=1  &page_size=18 (max 40)
    POST /post/sounds/<sound_id>/report/      {"reason": copyright|wrong_license|offensive|other, "note": ""}   (signed in)

    GET  /post/staff/sounds/reports/          sounds with open reports, most reported first                    (staff)
    POST /post/staff/sounds/action/           {"sound_id", "action": "block"|"restore"|"dismiss",
                                               "reason", "note", "hold_reels": bool}                           (staff)

`<sound_id>` is a Freesound id (digits). Only CC0 sounds have a page: your own audio and "original audio" are not
reusable, so they have no id to open. A blocked sound still answers 200 with `removed: true` (the page shows "removed"
instead of a 404, so a shared link does not look broken) and an empty reel list.
All rules live in post/sounds.py.
"""
import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView
from common.open_throttles import PublicReadThrottle  # P-B3

from . import reels, sounds
from .serializers import ReelSerializer, reel_video_media

logger = logging.getLogger(__name__)

PAGE_SIZE_DEFAULT = 18
PAGE_SIZE_MAX = 40


class SoundReportThrottle(UserRateThrottle):
    scope = "post_sound_report"
    rate = "20/hour"


class SoundStaffThrottle(UserRateThrottle):
    scope = "post_sound_staff"
    rate = "60/min"


def _int(raw, default, lo, hi):
    try:
        return max(lo, min(int(raw), hi))
    except (TypeError, ValueError):
        return default


class SoundPolicyAPIView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PublicReadThrottle]  # P-B3

    @extend_schema(summary="Which sounds the app allows (CC0 + own audio) and who to write to", tags=["Post"])
    def get(self, request):
        return Response(sounds.policy())


class SoundDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Sound page header (name, artist, licence, credit link, how many reels use it)", tags=["Post"])
    def get(self, request, sound_id):
        info = sounds.sound_info(request.user, sound_id)
        if info is None:
            return Response({"detail": "Sound not found."}, status=status.HTTP_404_NOT_FOUND)
        # preview_url / duration / tags only when Freesound still says CC0 (needed by "Use this sound" in the app).
        return Response(sounds.with_reuse_info(info))


class SoundReelsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Reels with this sound (newest first, only reels the caller may watch)", tags=["Post"])
    def get(self, request, sound_id):
        user = request.user
        sid = sounds.clean_sound_id(sound_id)
        if not sid:
            return Response({"detail": "Sound not found."}, status=status.HTTP_404_NOT_FOUND)
        page = _int(request.query_params.get("page"), 1, 1, 10_000)
        size = _int(request.query_params.get("page_size"), PAGE_SIZE_DEFAULT, 1, PAGE_SIZE_MAX)

        if sounds.is_blocked(sid):
            return Response({"count": 0, "next_page": None, "results": []})

        qs = sounds.reels_using_qs(user, sid)
        total = qs.count()
        start = (page - 1) * size
        # one extra row tells us whether a next page exists
        window = list(qs[start:start + size + 1])
        has_more = len(window) > size
        posts = [p for p in window[:size] if reel_video_media(p) is not None]

        reactions, saved = reels.viewer_state(user, [p.id for p in posts])
        following_ids = set()
        author_ids = {p.user_id for p in posts if p.user_id != user.id}
        if author_ids:
            from user_profile.models import Follow

            following_ids = set(
                Follow.objects.filter(
                    follower_id=user.id, following_id__in=author_ids, status=Follow.Status.ACCEPTED,
                ).values_list("following_id", flat=True)
            )
        ctx = {"request": request, "following_ids": following_ids, "reel_reactions": reactions, "reel_saved_ids": saved}
        data = ReelSerializer(posts, many=True, context=ctx).data
        return Response({"count": total, "next_page": (page + 1) if has_more else None, "results": data})


class SoundReportAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [SoundReportThrottle]

    @extend_schema(summary="Report a sound that is not copyright-free", tags=["Post"])
    def post(self, request, sound_id):
        reason = request.data.get("reason", "copyright")
        note = request.data.get("note", "")
        if not isinstance(reason, str) or not isinstance(note, str):
            return Response({"detail": "Reason and note must be text."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            row, created = sounds.report_sound(request.user, sound_id, reason, note)
        except sounds.SoundError as exc:
            return Response({"detail": str(exc), "code": exc.code}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"ok": True, "status": row.status}, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class StaffSoundReportsAPIView(APIView):
    permission_classes = [IsAdminUser]
    throttle_classes = [SoundStaffThrottle]

    @extend_schema(summary="Staff: sounds with open reports", tags=["Post"])
    def get(self, request):
        return Response({"results": sounds.open_reports_summary(_int(request.query_params.get("limit"), 50, 1, 200))})


class StaffSoundActionAPIView(APIView):
    permission_classes = [IsAdminUser]
    throttle_classes = [SoundStaffThrottle]

    @extend_schema(summary="Staff: block / restore a sound, or dismiss its reports", tags=["Post"])
    def post(self, request):
        action = request.data.get("action")
        sound_id = request.data.get("sound_id")
        note = request.data.get("note", "")
        if not isinstance(note, str):
            return Response({"detail": "Note must be text."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            if action == "block":
                row = sounds.block_sound(
                    sound_id, by=request.user, reason=request.data.get("reason") or "copyright", note=note,
                    title=str(request.data.get("title") or ""), artist=str(request.data.get("artist") or ""),
                    hold_reels=request.data.get("hold_reels") is True,
                )
                return Response({"ok": True, "status": row.status, "owners_told": row.owners_told, "reels_held": row.reels_held})
            if action == "restore":
                return Response({"ok": True, "restored": sounds.restore_sound(sound_id, by=request.user, note=note)})
            if action == "dismiss":
                return Response({"ok": True, "dismissed": sounds.dismiss_reports(sound_id)})
        except sounds.SoundError as exc:
            return Response({"detail": str(exc), "code": exc.code}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"detail": "action must be block, restore or dismiss."}, status=status.HTTP_400_BAD_REQUEST)
