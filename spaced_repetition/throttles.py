from rest_framework.throttling import UserRateThrottle


class RevisionReadThrottle(UserRateThrottle):
    scope = "revision_read"     # 120/min - due queue + stats


class RevisionReviewThrottle(UserRateThrottle):
    scope = "revision_review"   # 240/min - one POST per card; a fast learner does ~15/min
