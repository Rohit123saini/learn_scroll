# user_profile/views/search.py — split out of the old user_profile/views.py (P-C4). Code is moved verbatim.

from rest_framework.views import APIView
from ..models import (
    BlockUser,
    Follow,
)
from django.db.models import (
    Case,
    IntegerField,
    Q,
    Value,
    When,
)
from django.http import Http404
from rest_framework.permissions import IsAuthenticated
from rest_framework.generics import ListAPIView
from ..serializers import (
    MessageContactSearchSerializer,
    MutualFollowersResponseSerializer,
    SimilarUsersResponseSerializer,
    UserSearchSerializer,
    bulk_accepted_connection_ids,
)
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
)
from drf_spectacular.types import OpenApiTypes
from rest_framework.response import Response
from rest_framework import (
    filters,
    status,
)
from common.pagination import get_max_page_size
from django.shortcuts import get_object_or_404
from ..discovery import (
    mutual_followers_queryset,
    suggested_users_queryset,
)
from .. import privacy as privacy_rules
from ._common import (
    User,
    is_blocked_between,
)


class UserSearchView(ListAPIView):
    """
    Search users by username, first_name, last_name
    """
    permission_classes = [IsAuthenticated]
    serializer_class = UserSearchSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["username", "first_name", "last_name"]

    def get_queryset(self):
        # 🔥 FIX: search used to return literally every user, including
        # yourself and anyone in a block relationship with you.
        blocked_ids = BlockUser.objects.filter(
            Q(blocker=self.request.user) | Q(blocked=self.request.user)
        ).values_list("blocker_id", "blocked_id")
        excluded_ids = {self.request.user.id}
        for blocker_id, blocked_id in blocked_ids:
            excluded_ids.add(blocker_id)
            excluded_ids.add(blocked_id)

        qs = User.objects.filter(is_active=True).exclude(id__in=excluded_ids)
        # S4 -- "hide me from search": gone for everyone except people with an accepted follow link.
        qs = privacy_rules.exclude_hidden_from_search(qs, self.request.user)
        # P-F16 -- an under-18 account is not found by adult / unknown-age strangers (a connection or a minor viewer still finds them).
        from common import minor_policy

        return minor_policy.exclude_minors_for_stranger(qs, self.request.user, feature=minor_policy.SEARCH)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search query",
            )
        ],
        description="Search users",
    )
    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({
            "status": True,
            "message": "Users fetched successfully.",
            "data": response.data,
        })


class MutualFollowersView(APIView):
    """GET profile/<username>/mutuals/  (P8-BE)

    "Followed by X, Y + 5 others": the people YOU follow who also follow <username>.

        {"status": true, "preview": [<=3 users], "total": 7}

    Frontend text: preview names, then `total - len(preview)` others.

    Rules:
      * accepted follows only, blocked users (either direction) never counted;
      * your own profile -> empty (you can't have "mutuals" with yourself);
      * blocked between you and <username> -> 404, same as UserProfileDetailView;
      * private <username> you don't follow yet -> EMPTY result (not 403): who follows a
        private account is not something a non-follower should be able to probe.
    """

    permission_classes = [IsAuthenticated]
    PREVIEW_SIZE = 3

    @extend_schema(
        parameters=[OpenApiParameter(name="username", type=OpenApiTypes.STR, location=OpenApiParameter.PATH)],
        responses={200: MutualFollowersResponseSerializer, 404: OpenApiTypes.OBJECT},
        description="Users you follow who also follow this user (preview + total).",
    )
    def get(self, request, username):
        target = get_object_or_404(User, username=username)
        me = request.user

        if target != me and is_blocked_between(me, target):
            raise Http404
        if target != me and not target.is_active:  # P11b: a switched-off account is "no such user" here too
            raise Http404

        empty = Response({"status": True, "preview": [], "total": 0}, status=status.HTTP_200_OK)
        if target == me:
            return empty
        if target.is_private and not Follow.objects.filter(
            follower=me, following=target, status=Follow.Status.ACCEPTED
        ).exists():
            return empty

        qs = mutual_followers_queryset(me, target)
        total = qs.count()
        preview = list(qs[: self.PREVIEW_SIZE]) if total else []
        return Response(
            {
                "status": True,
                "preview": UserSearchSerializer(preview, many=True, context={"request": request}).data,
                "total": total,
            },
            status=status.HTTP_200_OK,
        )


class SimilarUsersView(APIView):
    """GET profile/<username>/similar/?limit=10  (P8-BE)

    "Suggested for you" shown under a profile. Same ranking as the onboarding
    suggestions (interest match, then popularity) via discovery.suggested_users_queryset,
    minus: yourself, <username>, everyone you already follow / requested, anyone in a
    block relationship with you, and anyone you restricted.

        {"status": true, "suggested_users": [{id, username, first_name, last_name, profile_photo}]}

    `limit` defaults to 10, capped at 20. Blocked between you and <username> -> 404.
    The list does not depend on <username>'s private data, so nothing about a private
    account leaks through it.
    """

    permission_classes = [IsAuthenticated]
    DEFAULT_LIMIT = 10
    MAX_LIMIT = 20

    @extend_schema(
        parameters=[
            OpenApiParameter(name="username", type=OpenApiTypes.STR, location=OpenApiParameter.PATH),
            OpenApiParameter(name="limit", type=OpenApiTypes.INT, location=OpenApiParameter.QUERY, required=False),
        ],
        responses={200: SimilarUsersResponseSerializer, 404: OpenApiTypes.OBJECT},
        description="Suggested accounts to follow, shown on a profile.",
    )
    def get(self, request, username):
        target = get_object_or_404(User, username=username)
        me = request.user
        if target != me and is_blocked_between(me, target):
            raise Http404

        try:
            limit = int(request.query_params.get("limit", self.DEFAULT_LIMIT))
        except (TypeError, ValueError):
            limit = self.DEFAULT_LIMIT
        limit = max(1, min(limit, self.MAX_LIMIT))

        qs = suggested_users_queryset(me, exclude_user_ids=[target.id])
        data = UserSearchSerializer(qs[:limit], many=True, context={"request": request}).data
        return Response({"status": True, "suggested_users": data}, status=status.HTTP_200_OK)


class MessageContactSearchView(ListAPIView):
    """
    GET /profile/chat-search/?search=<query>

    🔥 Message/group ke "add members" step ke liye — `UserSearchView` se
    ALAG hai: yahan poore app ke users nahi, sirf wahi log aate hain
    jinko maine follow kiya hua hai YA jinhone mujhe follow kiya hua hai
    (dono me se ek bhi kaafi hai, pura mutual hona zaroori nahi — warna
    list bahut chhoti reh jaati) AUR unke friends-of-friends (mere kisi
    connection ke connection) — wo list me direct connections ke BAAD aate
    hain, `is_friend_of_friend: true` ke saath. Response me profile_photo/bio waghera
    nahi, sirf id/username/first_name/last_name/mutual_friends.

    🔥 FIX (private-profile discovery leak): `is_private` accounts were
    showing up here for ANY follow relation, including someone who only
    follows *me* (target ne mujhe follow kiya, maine unhe nahi) — jinki
    private profile mujhe kabhi dikhti hi nahi (`UserProfileDetailView`
    ka `is_accepted_follower` check dekho: wo sirf tab True hota hai jab
    MAIN unhe follow karta hoon aur wo ACCEPTED hai). Ab discovery search
    me private target sirf tab aata hai jab wo public ho YA main khud
    unka accepted follower hoon (i.e. maine unhe follow kiya — is case
    me unki private profile already dikhti hai, to search me chhupane
    ka koi matlab nahi). Ye sirf is naye discovery/chat-search endpoint
    ka badlaav hai — `UserProfileDetailView` / followers / following
    list wagera jahan private-profile access already decide hota hai,
    wo sab as-is hain.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = MessageContactSearchSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["username", "first_name", "last_name"]

    def get_queryset(self):
        # Issue #17: this used to pull ALL of the viewer's connection ids into
        # a Python set and then run `id IN (<that whole set>)` — for a user
        # with tens of thousands of connections that is a huge in-memory set
        # AND a huge SQL statement (PostgreSQL also caps bind parameters),
        # before pagination could limit anything. Now the whole thing stays in
        # the database as subqueries, and only the requested page is fetched.
        me = self.request.user
        accepted = Follow.objects.filter(status=Follow.Status.ACCEPTED)
        my_following_ids = accepted.filter(follower=me).values("following_id")
        my_follower_ids = accepted.filter(following=me).values("follower_id")

        # 🔥 FIX: someone you've since blocked (or who blocked you) could
        # still show up here as a "connection" and be pickable as a chat
        # contact — excluded in both directions.
        # Friends-of-friends: accepted connections of MY connections (either
        # direction). Still pure subqueries — no Python set, no giant IN-list.
        fof_via_follower = accepted.filter(
            Q(follower_id__in=my_following_ids) | Q(follower_id__in=my_follower_ids)
        ).values("following_id")
        fof_via_following = accepted.filter(
            Q(following_id__in=my_following_ids) | Q(following_id__in=my_follower_ids)
        ).values("follower_id")

        # Chat-side block table (message.BlockedUser) is separate from
        # user_profile.BlockUser — local import, message imports user_profile.
        from message.models import BlockedUser as ChatBlockedUser

        qs = (
            User.objects.filter(is_active=True)
            .filter(
                Q(id__in=my_following_ids)
                | Q(id__in=my_follower_ids)
                | Q(id__in=fof_via_follower)
                | Q(id__in=fof_via_following)
            )
            # Private-profile gate: public users always pass; a private
            # user only passes if I'm already their accepted follower
            # (same "can I actually see this profile" test as
            # `UserProfileDetailView.is_accepted_follower`) — a person who
            # merely follows *me* (or is only a friend-of-friend) doesn't
            # clear that bar.
            .filter(Q(is_private=False) | Q(id__in=my_following_ids))
            .exclude(id=me.id)
            .exclude(id__in=BlockUser.objects.filter(blocker=me).values("blocked_id"))
            .exclude(id__in=BlockUser.objects.filter(blocked=me).values("blocker_id"))
            .exclude(id__in=ChatBlockedUser.objects.filter(blocker=me).values("blocked_id"))
            .exclude(id__in=ChatBlockedUser.objects.filter(blocked=me).values("blocker_id"))
            # Direct connections first, friends-of-friends after them.
            .annotate(
                _direct=Case(
                    When(Q(id__in=my_following_ids) | Q(id__in=my_follower_ids), then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                )
            )
            .order_by("_direct", "username", "id")
        )
        # S4 -- direct connections stay findable even when hidden; friends-of-friends who hid themselves do not.
        qs = privacy_rules.exclude_hidden_from_search(qs, me)
        # P-F16 -- friends-of-friends: an under-18 account is not surfaced to a non-minor unless they are directly linked.
        from common import minor_policy

        return minor_policy.exclude_minors_for_stranger(qs, me, feature=minor_policy.SEARCH)

    def get_serializer_context(self):
        return {
            "request": self.request,
            "connections_map": self._connections_map,
            "connections_map_is_mutual_only": True,  # built with restrict_to_user — see serializer
        }

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search query",
            )
        ],
        description="Search only within users who follow you or whom you follow (for chat/group member picking)",
    )
    def list(self, request, *args, **kwargs):
        # 🔥 FIX (N+1): precompute mutual_friends connections for the whole
        # page in 2 queries instead of 2 queries per row (see
        # bulk_accepted_connection_ids docstring).
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is None:
            # No paginator configured: never materialise the whole table
            # (issue #5) — fall back to one bounded page.
            page_rows = list(queryset[:get_max_page_size()])
            rows = page_rows
        else:
            rows = page
        self._connections_map = bulk_accepted_connection_ids(
            (u.id for u in rows), restrict_to_user=request.user
        )

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            data = self.get_paginated_response(serializer.data).data
        else:
            serializer = self.get_serializer(rows, many=True)
            data = serializer.data

        return Response({
            "status": True,
            "message": "Contacts fetched successfully.",
            "data": data,
        })
