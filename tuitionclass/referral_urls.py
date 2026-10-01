# ============================================================
# APP-WIDE REFERRAL ROUTING  (growth_and_feature_tasks.md — Task G12)
#
# PROBLEM (as filed): "referrals_screen.dart exists under tuitionclass/ only —
# referrals should be an app-wide growth lever, not scoped to one module."
#
# WHAT WAS ACTUALLY MISSING: not much, functionally. ReferralViewSet /
# Referral / referral_code_for_user (see views.py "13B. REFERRAL PROGRAM"
# and models.py) were already a generic User->User feature — any
# authenticated user can generate a code, redeem one, and REFERRAL_BONUS
# coins land in the same User.coin wallet every other feature in this app
# reads. Nothing about the model or the reward is classroom-specific.
#
# The one real gap was the URL: this viewset only ever lived at
# /tuitionclass/referrals/..., which is why a Profile-level "Invite & Earn"
# entry had nowhere neutral to point at without importing straight into the
# tuitionclass module. This file re-exposes the exact same viewset at the
# project root instead (see LearnScroll/urls.py -> path("referrals/", ...)):
#
#   GET  /referrals/                        same as /tuitionclass/referrals/
#   GET  /referrals/my-code/                same as /tuitionclass/referrals/my-code/
#   POST /referrals/redeem/                 same as /tuitionclass/referrals/redeem/
#   GET  /referrals/class-referral-summary/ same as /tuitionclass/referrals/class-referral-summary/
#
# This is a second door onto the same room, not a move: tuitionclass/urls.py
# still registers ReferralViewSet under /tuitionclass/ too, so the
# tuitionclass-embedded ReferralsScreen (which still legitimately wants the
# Tuition-Class-specific class_referral_summary action alongside its own
# module's nav chrome) keeps working unchanged. The new root path is what
# the rest of the app (Profile, Settings, a future share-sheet on any
# screen) should use going forward.
# ============================================================
from rest_framework.routers import DefaultRouter

from .views import ReferralViewSet

router = DefaultRouter()
router.register(r"", ReferralViewSet, basename="referral-root")

urlpatterns = router.urls
