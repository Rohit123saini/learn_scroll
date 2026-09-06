# message/views_focus.py
"""
⚠️ RECONSTRUCTION NOTE: is file ka original upload nahi mila is batch
me. Yahan wahi confirmed contract hai jo `CHAT_APP_DOCUMENTATION.md`
§7.18 aur `PROJECT_ARCHITECTURE.md` document karte hain
(`FocusSessionView` — single path, method-differentiated POST/GET/
DELETE, exact same request/response shapes). Agar tumhari asli file ke
internals (serializer names, validation ka exact tarika) alag hain, to
sirf 2 cheezein isse le lena aur baaki apni asli file rakho:
  1. `FocusSessionThrottle` ko `FocusSessionView.throttle_classes` me
     wire karna (Gap Fix #2 — rapid-fire start/cancel spam).
  2. Neeche wali `FocusSessionHistoryView` (Gap Fix #3 — bilkul nayi
     hai, koi conflict nahi hoga).

STUDENT ACCOUNT-SCOPED ONLY — `IsAuthenticated`, `request.user` se
apna hi session start/read/cancel hota hai, koi group/permission check
nahi chahiye (Focus Mode purely per-user hai).
"""
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone

from .models import FocusSession
from .throttles import FocusSessionThrottle


def _session_payload(session):
    seconds_remaining = max(int((session.ends_at - timezone.now()).total_seconds()), 0)
    return {
        'id': str(session.id),
        'starts_at': session.starts_at,
        'ends_at': session.ends_at,
        'exception_rule': session.exception_rule,
        'cancelled_at': session.cancelled_at,
        'seconds_remaining': seconds_remaining,
        'active': True,
    }


class FocusSessionView(APIView):
    """
    POST   /message/focus-session/  {duration_minutes, exception_rule?}
    GET    /message/focus-session/
    DELETE /message/focus-session/
    """
    permission_classes = [IsAuthenticated]
    # 🔧 GAP FIX — pehle koi throttle nahi tha; ek user rapid-fire
    # start/cancel spam kar sakta tha (har call ek DB write hai — start
    # purane session ko cancel karta hai + naya create karta hai, delete
    # bhi ek write hai). Low blast-radius hai (sirf apna account) isliye
    # crash-risk nahi tha, lekin abuse-prevention bilkul nahi thi.
    throttle_classes = [FocusSessionThrottle]

    MIN_DURATION_MINUTES = 5
    MAX_DURATION_MINUTES = 480

    def post(self, request):
        try:
            duration_minutes = int(request.data.get('duration_minutes'))
        except (TypeError, ValueError):
            return Response({'detail': "'duration_minutes' required hai"}, status=status.HTTP_400_BAD_REQUEST)

        if not (self.MIN_DURATION_MINUTES <= duration_minutes <= self.MAX_DURATION_MINUTES):
            return Response(
                {'detail': f"duration_minutes {self.MIN_DURATION_MINUTES}-{self.MAX_DURATION_MINUTES} "
                           f"ke beech hona chahiye."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        exception_rule = request.data.get('exception_rule', FocusSession.ExceptionRule.TEACHERS_ONLY)
        if exception_rule not in FocusSession.ExceptionRule.values:
            return Response({'detail': 'Invalid exception_rule'}, status=status.HTTP_400_BAD_REQUEST)

        session = FocusSession.start_for_user(
            request.user, duration_minutes=duration_minutes, exception_rule=exception_rule,
        )
        return Response(_session_payload(session), status=status.HTTP_201_CREATED)

    def get(self, request):
        session = FocusSession.get_active_for_user(request.user.id)
        if not session:
            return Response({'active': False})
        return Response(_session_payload(session))

    def delete(self, request):
        session = FocusSession.get_active_for_user(request.user.id)
        if not session:
            return Response({'active': False})
        session.cancelled_at = timezone.now()
        session.save(update_fields=['cancelled_at', 'updated_at'])
        return Response({'active': False})


# ======================================================================
# 🔧 NEW — Focus Session history/log (Gap Fix #3)
# ======================================================================
class FocusSessionHistoryView(APIView):
    """
    GET /message/focus-session/history/?limit=20

    Pehle sirf CURRENT active session dikhta tha — student ko apna
    history (kitni baar use kiya, kitni der padha, kitni baar early
    end kiya) dekhne ka koi tarika nahi tha. `FocusSession` rows kabhi
    delete nahi hoti (`start_for_user` purani ko sirf `cancelled_at`
    set karke "close" karta hai) — matlab history data already DB me
    tha, sirf ise dikhane wala endpoint missing tha.

    Returns: {"sessions": [
        {
            "id", "starts_at", "ends_at", "exception_rule",
            "ended_early": bool,      # cancelled before its natural ends_at
            "duration_minutes": int,  # ACTUAL time spent, not the planned one
        }, ...
    ]}
    Newest first, capped by `?limit=` (default 20, max 100).
    """
    permission_classes = [IsAuthenticated]

    DEFAULT_LIMIT = 20
    MAX_LIMIT = 100

    def get(self, request):
        try:
            limit = min(int(request.query_params.get('limit', self.DEFAULT_LIMIT)), self.MAX_LIMIT)
        except (TypeError, ValueError):
            limit = self.DEFAULT_LIMIT
        limit = max(limit, 1)

        sessions = (
            FocusSession.objects.filter(user=request.user)
            .order_by('-starts_at')[:limit]
        )

        return Response({'sessions': [self._entry(s) for s in sessions]})

    @staticmethod
    def _entry(session):
        actual_end = session.cancelled_at or session.ends_at
        duration_minutes = max(int((actual_end - session.starts_at).total_seconds() // 60), 0)
        return {
            'id': str(session.id),
            'starts_at': session.starts_at,
            'ends_at': session.ends_at,
            'exception_rule': session.exception_rule,
            'ended_early': session.cancelled_at is not None,
            'duration_minutes': duration_minutes,
        }