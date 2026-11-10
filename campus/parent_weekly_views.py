# campus/parent_weekly_views.py
"""
P-F14 — GET /campus/parent-weekly/            (all my linked children, all campuses)
        GET /campus/parent-weekly/?campus=ID  (only that campus)
        GET /campus/parent-weekly/?lang=hi    (wording of share_text; default = the user's saved app language)

Read-only; the caller's own `CampusParentLink` rows only (see parent_weekly.build_weekly).
Response = build_weekly(...) plus `email` ({subject, text, html}) so the app can offer "Send to my email /
copy" without a second call.
"""
import uuid

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import parent_weekly


def user_lang(user) -> str:
    try:
        from user_profile.models import UserPreference

        return UserPreference.objects.filter(user=user).values_list("language", flat=True).first() or "en"
    except Exception:  # language is a nicety — never fail the report for it
        return "en"


class ParentWeeklyView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        campus_id = request.query_params.get("campus") or None
        if campus_id is not None:
            try:
                campus_id = uuid.UUID(str(campus_id))
            except ValueError:
                return Response({"detail": "invalid_campus"}, status=400)
        lang = parent_weekly.normalize_lang(request.query_params.get("lang") or user_lang(request.user))
        data = parent_weekly.build_weekly(request.user, campus_id, lang)
        if data["children"]:
            campuses = {c["campus_name"] for c in data["children"]}
            data["email"] = parent_weekly.render_email(
                data,
                parent_name=parent_weekly._name(request.user),
                campus_name=next(iter(campuses)) if len(campuses) == 1 else "",
                lang=lang,
            )
        else:
            data["email"] = None
        return Response(data)
