# user_profile/discovery.py
"""
P8-BE — shared "who should I look at / who do we both know" queries.

Lives in user_profile (not core) because every rule here is a user_profile rule —
Follow / BlockUser / RestrictUser — and core.views.OnboardingSuggestionsView and
user_profile.views.SimilarUsersView must apply the SAME rules. Before this file the
onboarding query lived inline in core/views.py and ignored blocks entirely (a user
who blocked you could be suggested to you); now both endpoints go through here.

Everything stays a QuerySet built from subqueries — nothing pulls a viewer's whole
following list into a Python set (same reasoning as MessageContactSearchView, issue #17).
"""
from django.contrib.auth import get_user_model
from django.db.models import Case, Exists, IntegerField, OuterRef, Value, When

from .models import BlockUser, Follow, RestrictUser

User = get_user_model()


def _blocked_either_way_ids(user):
    """(blocked-by-user ids, blocked-user-by ids) as subqueries — block is symmetric."""
    return (
        BlockUser.objects.filter(blocker=user).values("blocked_id"),
        BlockUser.objects.filter(blocked=user).values("blocker_id"),
    )


def suggested_users_queryset(viewer, *, exclude_user_ids=()):
    """Users worth suggesting to `viewer`, best first.

    Excluded: the viewer, anyone the viewer follows OR has a pending request to,
    anyone in a block relationship with the viewer (either direction), anyone the
    viewer has restricted, inactive accounts, and `exclude_user_ids` (e.g. the profile
    currently being viewed).

    Ranking (unchanged from the original onboarding logic): if the viewer picked
    interests, authors of public posts in those categories come first; then by
    `followers_count`, then newest id.
    """
    blocked_by_me, blocked_me = _blocked_either_way_ids(viewer)

    candidates = (
        User.objects.filter(is_active=True)
        .exclude(id=viewer.id)
        .exclude(id__in=Follow.objects.filter(follower=viewer).values("following_id"))
        .exclude(id__in=blocked_by_me)
        .exclude(id__in=blocked_me)
        .exclude(id__in=RestrictUser.objects.filter(user=viewer).values("restricted_id"))
    )
    # Minor-safety: don't recommend under-18 accounts to adult viewers.
    # (Unknown-DOB users are left alone — nothing is assumed about them.)
    if viewer.is_verified_adult:
        from login.age import minor_cutoff_date

        candidates = candidates.exclude(date_of_birth__gt=minor_cutoff_date())

    extra = [i for i in exclude_user_ids if i is not None]
    if extra:
        candidates = candidates.exclude(id__in=extra)

    try:
        from post.models import UserInterest

        interest_categories = list(
            UserInterest.objects.filter(user=viewer).values_list("category", flat=True)
        )
    except Exception:  # pragma: no cover — post app not installed/migrated yet
        interest_categories = []

    if interest_categories:
        try:
            from post.models import Post

            # Exists() subquery instead of materialising every matching author id.
            # Only public, approved, non-deleted posts count — a private/removed post
            # shouldn't make someone look "relevant".
            has_matching_post = Exists(
                Post.objects.filter(
                    user_id=OuterRef("pk"),
                    category__in=interest_categories,
                    is_deleted=False,
                    moderation_status="approved",
                    visibility="public",
                )
            )
            candidates = candidates.annotate(
                _interest_match=Case(
                    When(has_matching_post, then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                )
            )
            return candidates.order_by("-_interest_match", "-followers_count", "-id")
        except Exception:  # pragma: no cover
            pass

    return candidates.order_by("-followers_count", "-id")


def mutual_followers_queryset(viewer, target):
    """People the VIEWER follows who also follow TARGET ("Followed by X, Y + 5 others").

    Accepted follows only (a pending request is not a relationship). Excludes the viewer
    and the target themselves, inactive accounts, and anyone in a block relationship
    with the viewer or the target. Ordered most-followed first so the 2-3 name preview is
    stable between requests.
    """
    accepted = Follow.objects.filter(status=Follow.Status.ACCEPTED)
    viewer_following = accepted.filter(follower=viewer).values("following_id")
    target_followers = accepted.filter(following=target).values("follower_id")

    qs = (
        User.objects.filter(is_active=True, id__in=viewer_following)
        .filter(id__in=target_followers)
        .exclude(id__in=[viewer.id, target.id])
    )
    for party in (viewer, target):
        by_party, party_by = _blocked_either_way_ids(party)
        qs = qs.exclude(id__in=by_party).exclude(id__in=party_by)
    return qs.order_by("-followers_count", "-id")
