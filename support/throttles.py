"""Write-only per-user throttles (GET/HEAD/OPTIONS are never counted).
Rates live in settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]."""
from rest_framework.permissions import SAFE_METHODS
from rest_framework.throttling import UserRateThrottle


class _WritesOnly(UserRateThrottle):
    def allow_request(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return super().allow_request(request, view)


class TicketCreateThrottle(_WritesOnly):
    scope = "support_ticket"


class TicketMessageThrottle(_WritesOnly):
    scope = "support_message"


class BugReportThrottle(_WritesOnly):
    scope = "support_bug"


class FeatureRequestThrottle(_WritesOnly):
    scope = "support_feature"


class FeatureVoteThrottle(_WritesOnly):
    scope = "support_vote"
