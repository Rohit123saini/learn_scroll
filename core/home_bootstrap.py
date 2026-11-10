"""
core/home_bootstrap.py - H4: ONE request for everything Home needs next to the feed.

    GET core/home-bootstrap/

Before this, opening Home fired ~11 loaders at once (profile, unread bell, unread chats, daily challenge,
feed ad config, me/context, role signals = onboarding + campuses + parent links + classrooms + 5 calls per
campus, classrooms, live-now, stories, live rings, invite summary, next class) plus a socket catch-up. On a slow
connection that is a request storm: every call pays its own TLS/RTT and they fight for the same bandwidth.

Response (every key is present; a section that failed on the server is `null` and its name is listed in
`errors`, so the app falls back to the old individual call for THAT section only):

    {
      "version": 1,
      "server_now": "2026-10-22T09:00:00+00:00",
      "me": { ...core/me/context/ payload (id, username, is_staff, is_minor, ads_enabled, onboarding_role...) },
      "role": {                       # raw signals; the app turns them into admin/teacher/parent/student
          "hint": "teacher" | null,   # onboarding role
          "is_campus_management": bool, "is_campus_teacher": bool, "is_campus_student": bool,
          "has_parent_links": bool, "teaches_classroom": bool,
          "campus_ids": {"management": id|null, "teacher": id|null, "student": id|null, "parent": id|null}
      },
      "unread": {"bell": int, "chats": int},            # bell = source=tuitionclass, chats = source=message
      "daily_challenge": { ...core/daily-challenge/ payload },
      "feed_ad_config": {"ad_every_posts": int, "interstitial_every_posts": int},
      "stories": [ ...post/stories/ rows ],             # the tray (flat, the app groups it)
      "stories_live": [ ...post/stories/live/ rows ],   # LIVE rings
      "classroom_switcher": {
          "classrooms": [ ...tuitionclass/classrooms/?mine=true rows ],
          "live_now": [ ...tuitionclass/sessions/live-now/ rows ],
          "dashboard": { ...tuitionclass/dashboard/ payload }     # next class + server_now
      },
      "invite": { ...tuitionclass/referrals/class-referral-summary/ payload },
      "errors": ["stories", "invite", ...]
    }

The FEED stays its own call on purpose: it has its own cursor, cache and per-session snapshot.

Nothing is re-implemented here. Sections that already had a plain function (me context, daily challenge, ad
config, unread filter) call it directly; the ones that live inside DRF views (stories, live-now, classrooms,
dashboard, referral summary) are run through the SAME view classes in-process (`_call_view`), so their filtering,
serializers, visibility rules and media URLs are exactly what the individual endpoints return - they can never
drift. The role signals mirror what `HomeRoleService.resolve()` used to collect with ~10 client calls.

Everything is read-only and scoped to `request.user`. It is a UI hint like /core/me/context/: every write
endpoint still checks permissions on its own.
"""
import logging

from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.gzip import gzip_page
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from .daily_challenge import build_daily_challenge
from .me_context import build_context

logger = logging.getLogger(__name__)

BOOTSTRAP_VERSION = 1

# Same ceiling the client used (`HomeRoleService._maxCampusesChecked`): approved campuses looked at for the role.
MAX_CAMPUSES_FOR_ROLE = 3

# StaffProfile.Role values (campus/models.py) grouped the way the app's `CampusRole` getters group them.
_MANAGEMENT_ROLES = frozenset({"admin", "principal_hod"})
_TEACHING_ROLES = frozenset({"class_teacher", "subject_teacher", "moderator"})


class HomeBootstrapThrottle(UserRateThrottle):
    """Own scope: an open + a few pull-to-refreshes per minute is normal, a loop is not."""

    scope = "home_bootstrap"
    rate = "60/min"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _call_view(request, view, path, params=None):
    """Run an existing DRF view in-process for `request.user` and return its `response.data`.

    Same class, same queryset, same serializer as the public endpoint - only the HTTP round trip is gone.
    Host / scheme / language are copied from the real request so absolute media URLs and translated strings come
    out exactly as they would from the individual endpoint. Anything but a 200 raises so the caller marks the
    section as failed (the app then falls back to the individual call).
    """
    from rest_framework.test import APIRequestFactory, force_authenticate

    extra = {"HTTP_HOST": request.get_host()}
    lang = request.META.get("HTTP_ACCEPT_LANGUAGE")
    if lang:
        extra["HTTP_ACCEPT_LANGUAGE"] = lang
    sub = APIRequestFactory().get(path, params or {}, secure=request.is_secure(), **extra)
    force_authenticate(sub, user=request.user)
    response = view(sub)
    if response.status_code != 200:
        raise RuntimeError(f"{path} answered {response.status_code}")
    return response.data


def role_signals(user) -> dict:
    """Raw facts the app needs to pick admin / teacher / parent / student (see `HomeRoleResolver` in
    lib/services/home_role_service.dart). Server side this is a handful of indexed queries instead of
    ~4 + 5 x campuses HTTP calls.

    Mirrors the client rules:
      * only APPROVED campuses, at most `MAX_CAMPUSES_FOR_ROLE` (Campus has no default ordering, so pk order
        makes the pick stable);
      * an active StaffProfile decides the campus role (admin/principal -> management; class/subject teacher or
        moderator -> teacher; non-teaching -> none of the three);
      * no staff row but an ACTIVE enrollment -> student;
      * parent = the user is the `parent` of a CampusParentLink (the old client check looked at every link the
        user could SEE, which made every campus admin look like a parent - not what the rule means);
      * teaches a classroom = owns a non-deleted tuition-class Classroom.
    """
    from campus.models import Campus, CampusParentLink, StaffProfile, StudentEnrollment
    from campus.visibility import compute_visibility
    from tuitionclass.models import Classroom

    member_ids = compute_visibility(user).member_campus_ids
    campuses = []
    if member_ids:
        campuses = list(
            Campus.objects.filter(id__in=member_ids, verification_status=Campus.VerificationStatus.APPROVED).order_by(
                "pk"
            )[:MAX_CAMPUSES_FOR_ROLE]
        )
    campus_ids = [c.pk for c in campuses]

    staff_role = {}
    enrolled = set()
    if campus_ids:
        staff_role = dict(
            StaffProfile.objects.filter(user=user, is_active=True, campus_id__in=campus_ids).values_list(
                "campus_id", "role"
            )
        )
        enrolled = set(
            StudentEnrollment.objects.filter(
                student=user,
                status=StudentEnrollment.Status.ACTIVE,
                section__school_class__campus_id__in=campus_ids,
            ).values_list("section__school_class__campus_id", flat=True)
        )

    management = teacher = student = None
    for c in campuses:
        role = staff_role.get(c.pk)
        if role in _MANAGEMENT_ROLES:
            management = management or str(c.pk)
        elif role in _TEACHING_ROLES:
            teacher = teacher or str(c.pk)
        elif role is None and c.pk in enrolled:
            student = student or str(c.pk)

    parent_campus = (
        CampusParentLink.objects.filter(parent=user).order_by("-created_at").values_list("campus_id", flat=True).first()
    )
    teaches_classroom = Classroom.objects.filter(teacher=user, is_deleted=False).exists()

    return {
        "hint": (getattr(user, "onboarding_role", "") or None),
        "is_campus_management": management is not None,
        "is_campus_teacher": teacher is not None,
        "is_campus_student": student is not None,
        "has_parent_links": parent_campus is not None,
        "teaches_classroom": bool(teaches_classroom),
        "campus_ids": {
            "management": management,
            "teacher": teacher,
            "student": student,
            "parent": str(parent_campus) if parent_campus is not None else None,
        },
    }


def unread_counts(user) -> dict:
    """Bell (everything except chat) and Chats badge - the very same split `GET notifications/unread-count/`
    applies for ?source=tuitionclass / ?source=message (one shared helper, so they cannot disagree)."""
    from .models import Notification
    from .views import NotificationViewSet

    base = Notification.objects.for_user(user).unread()
    return {
        "bell": NotificationViewSet._filter_by_source(base, "tuitionclass").count(),
        "chats": NotificationViewSet._filter_by_source(base, "message").count(),
    }


# ---------------------------------------------------------------------------
# the view
# ---------------------------------------------------------------------------
# gzip only THIS endpoint (not a project-wide GZipMiddleware): it is the biggest JSON the app loads at start and
# slow connections are the whole point of H4. gzip_page skips clients that did not send Accept-Encoding: gzip.
@method_decorator(gzip_page, name="dispatch")
class HomeBootstrapView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [HomeBootstrapThrottle]

    def get(self, request):
        user = request.user
        errors = []

        def run(name, fn):
            """One section = one try/except: a broken section must never take the others (or Home) down."""
            try:
                return fn()
            except Exception:  # noqa: BLE001
                logger.warning("home-bootstrap section %r failed", name, exc_info=True)
                errors.append(name)
                return None

        # Imports of the other apps' views live inside the handler: core must not import tuitionclass / post at
        # module load time (same reasoning as the local imports in core/daily_challenge.py).
        def stories():
            from post.views import StoryListAPIView

            return _call_view(request, StoryListAPIView.as_view(), "/post/stories/")

        def stories_live():
            from post.views import StoryLiveAPIView

            data = _call_view(request, StoryLiveAPIView.as_view(), "/post/stories/live/")
            return data.get("results", []) if isinstance(data, dict) else data

        def classrooms():
            from tuitionclass.views import ClassroomViewSet

            return _call_view(
                request, ClassroomViewSet.as_view({"get": "list"}), "/tuitionclass/classrooms/", {"mine": "true"}
            )

        def live_now():
            from tuitionclass.views import ClassSessionViewSet

            return _call_view(
                request,
                ClassSessionViewSet.as_view({"get": "live_now"}),
                "/tuitionclass/sessions/live-now/",
                {"limit": 10},
            )

        def dashboard():
            from tuitionclass.views import MyDashboardView

            return _call_view(request, MyDashboardView.as_view(), "/tuitionclass/dashboard/")

        def invite():
            from tuitionclass.views import ReferralViewSet

            return _call_view(
                request,
                ReferralViewSet.as_view({"get": "class_referral_summary"}),
                "/tuitionclass/referrals/class-referral-summary/",
            )

        def feed_ad_config():
            from post.views import build_feed_ad_config

            return build_feed_ad_config(user)

        payload = {
            "version": BOOTSTRAP_VERSION,
            "server_now": timezone.now().isoformat(),
            "me": run("me", lambda: build_context(user)),
            "role": run("role", lambda: role_signals(user)),
            "unread": run("unread", lambda: unread_counts(user)),
            "daily_challenge": run("daily_challenge", lambda: build_daily_challenge(user, request)),
            "feed_ad_config": run("feed_ad_config", feed_ad_config),
            "stories": run("stories", stories),
            "stories_live": run("stories_live", stories_live),
            "classroom_switcher": {
                "classrooms": run("classrooms", classrooms),
                "live_now": run("live_now", live_now),
                "dashboard": run("dashboard", dashboard),
            },
            "invite": run("invite", invite),
        }
        payload["errors"] = errors
        return Response(payload)
