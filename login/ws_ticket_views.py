# login/ws_ticket_views.py
"""X6b -- POST /login/auth/ws-ticket/  ->  {"ticket": "...", "expires_in": 30}.  See login/ws_ticket.py."""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .errors import UnifiedErrorsMixin
from .session_views import _current_session_jti
from . import ws_ticket


class WsTicketView(UnifiedErrorsMixin, APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ws_ticket"

    def post(self, request):
        ticket, ttl = ws_ticket.issue(request.user.pk, _current_session_jti(request))
        resp = Response({"ticket": ticket, "expires_in": ttl})
        resp["Cache-Control"] = "no-store"
        return resp
