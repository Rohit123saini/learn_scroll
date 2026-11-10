from rest_framework.throttling import UserRateThrottle


class SocialReadThrottle(UserRateThrottle):
    scope = "social_read"              # 240/min - the app polls timer state + boards


class SocialWriteThrottle(UserRateThrottle):
    scope = "social_write"             # 60/min  - timer controls, claim, goal, prefs, manual minutes


class SocialNudgeThrottle(UserRateThrottle):
    scope = "social_nudge"             # 20/hour - on top of the 2-per-day-per-pair rule


class SocialPartnerRequestThrottle(UserRateThrottle):
    scope = "social_partner_request"   # 20/day
