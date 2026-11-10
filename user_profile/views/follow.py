# user_profile/views/follow.py — split out of the old user_profile/views.py (P-C4). Code is moved verbatim.

from rest_framework.views import APIView
from ..serializers import (
    BulkFollowRequestSerializer,
    FollowActionResponseSerializer,
    FollowListRowSerializer,
    bulk_accepted_connection_ids,
    bulk_viewer_follow_status,
)
from ..models import Follow
from rest_framework.generics import (
    GenericAPIView,
    ListAPIView,
)
from django.http import Http404
from django.db import (
    IntegrityError,
    transaction,
)
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
)
from drf_spectacular.types import OpenApiTypes
from rest_framework.response import Response
from ..services import (
    blocked_user_ids,
    pending_follow_requests_for,
    remove_follower,
)
from common.pagination import get_max_page_size
from django.shortcuts import get_object_or_404
from .. import privacy as privacy_rules
from .. import relationships
from rest_framework import status
from ._common import (
    User,
    is_blocked_between,
)


def _follow_lists_hidden_response(view, request, message):
    """S4 -- None when `request.user` may open the followers / following list of `<username>`, else the 200 reply with
    `hidden: true` and an empty page (same shape as the real answer, so no client has to special-case an error)."""
    target = get_object_or_404(User, username=view.kwargs["username"])
    me = request.user
    allowed = True
    if target.pk != me.pk:
        # P11b - the same rules the profile page itself follows (UserProfileDetailView / MutualFollowersView):
        #   * a block in EITHER direction looks exactly like "no such user" (404, nothing leaks);
        #   * a switched-off (suspended) account shows nothing;
        #   * a PRIVATE account's lists are for its accepted followers only - who follows a private account is not
        #     something a stranger or a pending requester may read (before this the owner's audience control alone
        #     decided, and its default is "everyone").
        if is_blocked_between(me, target):
            raise Http404
        if not target.is_active:
            allowed = False
        elif target.is_private and not Follow.objects.filter(
            follower=me, following=target, status=Follow.Status.ACCEPTED
        ).exists():
            allowed = False
    if allowed and privacy_rules.can_see_follow_lists(target, me):
        return None
    paginator = view.paginator
    empty = {"count": 0, "next": None, "previous": None, "results": []} if paginator is not None else []
    return Response({"status": True, "message": message, "hidden": True, "data": empty})


class FollowersListView(ListAPIView):
    """
    GET /profile/profile/<username>/followers/

    Target user (URL me diye gaye username) ke saare ACCEPTED followers —
    same `MessageContactSearchSerializer` reuse kiya hai isliye response
    me id/username/first_name/last_name ke saath tumhare (request.user)
    sath unka mutual_friends count bhi milta hai.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = FollowListRowSerializer

    def get_queryset(self):
        # P8: ?q= search, ?sort=latest|oldest|name, ?filter=all|mutual|not_following_back
        # (see user_profile/relationships.py for the exact meaning of each).
        target_user = get_object_or_404(User, username=self.kwargs["username"])
        q, sort, flt = relationships.parse_list_params(self.request.query_params)
        return relationships.follow_list_queryset(
            target_user, self.request.user, followers=True, q=q, sort=sort, flt=flt,
            exclude_ids=blocked_user_ids(self.request.user),
        )

    def get_serializer_context(self):
        return {
            "request": self.request,
            "connections_map": self._connections_map,
            "connections_map_is_mutual_only": True,  # built with restrict_to_user — see serializer
            # TASK 7 — per-row Follow/Following button state.
            "follow_status_map": self._follow_status_map,
        }

    @extend_schema(description="List of a user's followers, with mutual_friends relative to you")
    def list(self, request, *args, **kwargs):
        # S4 -- the owner chose who may open this list. Answer 200 + `hidden: true` + an empty page (an old app build
        # then just shows an empty list instead of an error dialog).
        hidden = _follow_lists_hidden_response(self, request, "Followers fetched successfully.")
        if hidden is not None:
            return hidden
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is None:
            # No paginator configured: never materialise the whole table
            # (issue #5) — fall back to one bounded page.
            page_rows = list(queryset[:get_max_page_size()])
            rows = page_rows
        else:
            rows = page
        row_ids = [u.id for u in rows]
        self._connections_map = bulk_accepted_connection_ids(
            row_ids, restrict_to_user=request.user
        )
        self._follow_status_map = bulk_viewer_follow_status(row_ids, request.user)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            data = self.get_paginated_response(serializer.data).data
        else:
            serializer = self.get_serializer(rows, many=True)
            data = serializer.data

        return Response({
            "status": True,
            "message": "Followers fetched successfully.",
            "data": data,
        })


class FollowingListView(ListAPIView):
    """
    GET /profile/profile/<username>/following/

    Target user (URL me diye gaye username) jinhe follow karta hai unki
    list — same shape/serializer jaisa `FollowersListView`.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = FollowListRowSerializer

    def get_queryset(self):
        # P8: same query params as the followers list.
        target_user = get_object_or_404(User, username=self.kwargs["username"])
        q, sort, flt = relationships.parse_list_params(self.request.query_params)
        return relationships.follow_list_queryset(
            target_user, self.request.user, followers=False, q=q, sort=sort, flt=flt,
            exclude_ids=blocked_user_ids(self.request.user),
        )

    def get_serializer_context(self):
        return {
            "request": self.request,
            "connections_map": self._connections_map,
            "connections_map_is_mutual_only": True,  # built with restrict_to_user — see serializer
            # TASK 7 — per-row Follow/Following button state.
            "follow_status_map": self._follow_status_map,
        }

    @extend_schema(description="List of who a user is following, with mutual_friends relative to you")
    def list(self, request, *args, **kwargs):
        hidden = _follow_lists_hidden_response(self, request, "Following fetched successfully.")  # S4
        if hidden is not None:
            return hidden
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is None:
            # No paginator configured: never materialise the whole table
            # (issue #5) — fall back to one bounded page.
            page_rows = list(queryset[:get_max_page_size()])
            rows = page_rows
        else:
            rows = page
        row_ids = [u.id for u in rows]
        self._connections_map = bulk_accepted_connection_ids(
            row_ids, restrict_to_user=request.user
        )
        self._follow_status_map = bulk_viewer_follow_status(row_ids, request.user)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            data = self.get_paginated_response(serializer.data).data
        else:
            serializer = self.get_serializer(rows, many=True)
            data = serializer.data

        return Response({
            "status": True,
            "message": "Following fetched successfully.",
            "data": data,
        })


def _new_follower_title(count, actors):
    from core.notification_batching import batch_title

    return batch_title(actors, count, "started following you", max_names=2)


def _new_follower_batch_kwargs(following_user):
    """N2-BE — shared by the follow (create) and unfollow (remove) paths so
    both agree on key/title/window. Key = (recipient, FOLLOW_REQUEST_ACCEPTED,
    recipient's own id) — one open batch per recipient. Only AUTO-ACCEPTED
    follows are batched; follow REQUESTS (each needs its own Confirm/Delete
    by follow_id) are deliberately never batched."""
    from core.models import Notification
    from core.notification_batching import get_batch_window

    return dict(
        recipient=following_user,
        notif_type=Notification.NotifType.FOLLOW_REQUEST_ACCEPTED,
        target_id=following_user.id,
        title_fn=_new_follower_title,
        window_seconds=get_batch_window("new_follower"),
    )


class FollowAPIView(GenericAPIView):
    """
    Follow/Unfollow a user with count update
    """
    permission_classes = [IsAuthenticated]
    serializer_class = FollowActionResponseSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="user_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="ID of user to follow/unfollow",
            )
        ],
        responses={
            200: FollowActionResponseSerializer,
            201: FollowActionResponseSerializer,
            400: OpenApiTypes.OBJECT,
        },
        description="Follow or unfollow a user",
    )
    @transaction.atomic
    def post(self, request, user_id):
        if request.user.id == user_id:
            return Response(
                {"error": "You cannot follow yourself"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        following_user = get_object_or_404(User, id=user_id)

        # 🔥 FIX: blocking wasn't checked — you could still send a follow
        # request to (or be followed by) someone in a block relationship.
        if is_blocked_between(request.user, following_user):
            return Response(
                {"error": "You can't follow this user."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        follow_obj = Follow.objects.filter(
            follower=request.user, following=following_user
        ).first()

        # T0.6: `only_follow=true` (push-notification "Follow back" button) makes this
        # idempotent — if the relationship already exists, report it instead of
        # toggling it off. Without this, tapping "Follow back" on a stale push would
        # silently UNFOLLOW the user.
        only_follow = str(request.data.get("only_follow", "")).lower() in ("1", "true", "yes")
        # H5 - explicit intent for replaying a queued offline tap: desired="follow" behaves like only_follow
        # (never toggles an existing relation off), desired="unfollow" never creates one.
        desired = str(request.data.get("desired", "")).lower()
        if desired == "follow":
            only_follow = True
        if desired == "unfollow" and not follow_obj:
            return Response({"message": "Not following", "status": None}, status=status.HTTP_200_OK)
        if follow_obj and only_follow:
            return Response({
                "message": "Follow request sent" if follow_obj.status == Follow.Status.PENDING else "Already following",
                "status": follow_obj.status,
                "follow_id": follow_obj.id,
            }, status=status.HTTP_200_OK)

        if follow_obj:
            # Unfollow. followers_count/following_count are NOT touched
            # here any more — the Follow post_delete signal
            # (user_profile/signals.py) recounts both users from real rows.
            was_accepted = follow_obj.status == Follow.Status.ACCEPTED
            follow_obj.delete()
            if was_accepted:
                from ..profile_insights import record_loss  # P9b: a number only, never who

                record_loss(following_user)
                # N2-BE: drop them from the recipient's OPEN "new followers"
                # batch (no-op once the window closed; never raises).
                from core.notification_batching import remove_actor_from_batch

                remove_actor_from_batch(
                    actor=request.user, **_new_follower_batch_kwargs(following_user)
                )
            return Response({
                "message": "Unfollowed successfully",
                "status": None,
            }, status=status.HTTP_200_OK)

        # Naya follow create karo
        is_private = following_user.is_private
        try:
            # 🔥 FIX: two rapid duplicate requests (double-tap, retry after
            # a slow response, etc) could both pass the `.filter().first()`
            # check above before either commits, then both try to
            # `.create()` — the DB's UniqueConstraint would correctly
            # reject the second one, but as an unhandled IntegrityError
            # that surfaces as a raw 500 instead of a clean response.
            #
            # The create runs inside its OWN savepoint (`atomic()`): this
            # whole view is already wrapped in @transaction.atomic, and on
            # PostgreSQL an IntegrityError leaves the OUTER transaction
            # in an aborted state — the very next query (the `existing`
            # lookup in the except-block below) would then raise
            # TransactionManagementError -> a 500, i.e. exactly the
            # failure this except-block exists to prevent. The savepoint
            # rolls back only the failed INSERT and keeps the outer
            # transaction usable.
            with transaction.atomic():
                new_follow = Follow.objects.create(
                    follower=request.user,
                    following=following_user,
                    status=Follow.Status.PENDING if is_private else Follow.Status.ACCEPTED,
                )
        except IntegrityError:
            existing = Follow.objects.filter(
                follower=request.user, following=following_user
            ).first()
            return Response({
                "message": "Follow request sent" if existing and existing.status == Follow.Status.PENDING else "Followed successfully",
                "status": existing.status if existing else None,
                "follow_id": existing.id if existing else None,
            }, status=status.HTTP_200_OK)

        # No manual counter update: Follow's post_save signal
        # (user_profile/signals.py) recounts both users once this
        # transaction commits (a PENDING request changes no count).

        # TASK 2: notify the other side of a follow action. Lazy imports
        # (core.models for the NotifType enum, .services for the
        # _notify wrapper) — user_profile must not hard-depend on core
        # at module-import time, since core.services.create_notification
        # already lazy-imports back into user_profile.views the other
        # way (is_restricted_between, for the restrict check).
        from core.models import Notification
        from ..services import _notify

        if new_follow.status == Follow.Status.PENDING:
            _notify(
                following_user,
                Notification.NotifType.FOLLOW_REQUEST_RECEIVED,
                "New follow request",
                f"{request.user.username} wants to follow you.",
                actor=request.user,
                # TASK 6 (production_readiness_tasks.md) — the notification
                # row needs this to let the recipient Confirm/Delete the
                # request directly from the bell (AcceptFollowRequestView /
                # RejectFollowRequestView both take follow_id, not user_id).
                data={"follow_id": new_follow.id},
                push=True,  # 3.2: follow request also reaches the phone
            )
        else:
            # Public account, auto-accept — no NEW_FOLLOWER type exists
            # in the enum (see core/models.py module docstring, point
            # 5: "product-decision"), so this reuses
            # FOLLOW_REQUEST_ACCEPTED for "someone just started
            # following you" too, same as a request that was actually
            # accepted.
            #
            # N2-BE: BATCHED ("X, Y and 12 others started following you",
            # 6h sliding window, see settings.NOTIFICATION_BATCH_WINDOWS).
            # Calls the batching helper directly (not _notify); it does its
            # own restrict check and never raises.
            from core.notification_batching import (
                create_batched_notification,
                get_batch_max_age,
            )
            from ..services import push_for_row

            # N2 — `is_following_actor` is NOT stored in the row's data any
            # more (it was a snapshot taken at creation time and went stale
            # the moment the user followed back). NotificationSerializer now
            # computes it live from the Follow table on every list/retrieve.
            create_batched_notification(
                actor=request.user,
                extra_data={"kind": "new_follower"},
                max_age_seconds=get_batch_max_age("new_follower"),
                # 3.2: push only for the FIRST follower of a batch (later ones
                # fold into the same row, no push spam); prefs / quiet hours
                # are checked inside send_push_for_notification.
                send_push_row_fn=push_for_row,
                **_new_follower_batch_kwargs(following_user),
            )

        return Response({
            "message": "Follow request sent" if new_follow.status == Follow.Status.PENDING else "Followed successfully",
            "status": new_follow.status,
            "follow_id": new_follow.id,
        }, status=status.HTTP_201_CREATED)


class AcceptFollowRequestView(GenericAPIView):
    """
    Accept a follow request with count update
    """
    permission_classes = [IsAuthenticated]
    serializer_class = FollowActionResponseSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="follow_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="ID of follow request to accept",
            )
        ],
        responses={200: FollowActionResponseSerializer},
        description="Accept a pending follow request",
    )
    @transaction.atomic
    def post(self, request, follow_id):
        # Sirf jis user ko request aayi hai wahi accept kar sakta hai
        follow_request = get_object_or_404(
            Follow,
            id=follow_id,
            following=request.user,
            status=Follow.Status.PENDING,
        )

        # save() fires Follow's post_save signal, which recounts both
        # users' followers_count/following_count (user_profile/signals.py).
        follow_request.status = Follow.Status.ACCEPTED
        follow_request.save(update_fields=["status"])

        # TASK 2: notify the original requester. Same lazy-import
        # pattern as FollowAPIView.post above — see that comment for why.
        from core.models import Notification
        from ..services import _notify

        _notify(
            follow_request.follower_id,
            Notification.NotifType.FOLLOW_REQUEST_ACCEPTED,
            "Follow request accepted",
            f"{request.user.username} accepted your follow request.",
            actor=request.user,
            # N2 — no `is_following_actor` snapshot in data: the serializer
            # derives it live from the Follow table (see NotificationSerializer).
        )

        return Response({
            "message": "Follow request accepted",
            "status": follow_request.status,
        }, status=status.HTTP_200_OK)


class RejectFollowRequestView(GenericAPIView):
    """
    Reject a follow request
    """
    permission_classes = [IsAuthenticated]
    serializer_class = FollowActionResponseSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="follow_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="ID of follow request to reject",
            )
        ],
        responses={200: FollowActionResponseSerializer},
        description="Reject a pending follow request",
    )
    def post(self, request, follow_id):
        follow_request = get_object_or_404(
            Follow,
            id=follow_id,
            following=request.user,
            status=Follow.Status.PENDING,
        )

        follow_request.delete()

        return Response({
            "message": "Follow request rejected",
            "status": None,
        }, status=status.HTTP_200_OK)


class RemoveFollowerView(GenericAPIView):
    """
    P11-BE — DELETE /profile/followers/<user_id>/

    "Remove follower": makes <user_id> stop following the logged-in user.
    Only an ACCEPTED follower can be removed (a still-pending request is
    handled by reject-request/). Counters are corrected by the Follow
    post_delete signal; NO notification is sent to the removed user — see
    services.remove_follower.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = FollowActionResponseSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="user_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="ID of the follower to remove",
            )
        ],
        responses={200: FollowActionResponseSerializer, 400: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
        description="Remove a user from your followers (silent — they are not notified)",
    )
    @transaction.atomic
    def delete(self, request, user_id):
        if request.user.id == user_id:
            return Response(
                {"error": "You cannot remove yourself"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not remove_follower(request.user, user_id):
            return Response(
                {"error": "This user is not following you."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response({
            "message": "Follower removed",
            "status": None,
        }, status=status.HTTP_200_OK)


class FollowRequestsListView(ListAPIView):
    """
    P11-BE — GET /profile/follow-requests/

    Paginated list of PENDING follow requests addressed to the logged-in
    user, newest first. Each row is the same shape as the followers/
    following lists (FollowListRowSerializer — the *requester's* user
    row) plus `follow_id`, which is what accept-request/<follow_id>/ and
    reject-request/<follow_id>/ take.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = FollowListRowSerializer

    def get_queryset(self):
        return pending_follow_requests_for(self.request.user)

    def get_serializer_context(self):
        return {
            "request": self.request,
            "connections_map": self._connections_map,
            "connections_map_is_mutual_only": True,  # built with restrict_to_user — see serializer
            "follow_status_map": self._follow_status_map,
        }

    @extend_schema(description="Pending incoming follow requests (each row carries follow_id)")
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        # No paginator configured: never materialise the whole table (issue #5).
        follows = page if page is not None else list(queryset[:get_max_page_size()])

        users = [f.follower for f in follows]
        row_ids = [u.id for u in users]
        self._connections_map = bulk_accepted_connection_ids(
            row_ids, restrict_to_user=request.user
        )
        self._follow_status_map = bulk_viewer_follow_status(row_ids, request.user)

        follow_id_by_user = {f.follower_id: f.id for f in follows}
        rows = []
        for row in self.get_serializer(users, many=True).data:
            row = dict(row)
            row["follow_id"] = follow_id_by_user.get(row.get("id"))
            rows.append(row)

        data = self.get_paginated_response(rows).data if page is not None else rows
        return Response({
            "status": True,
            "message": "Follow requests fetched successfully.",
            "data": data,
        })


class BulkFollowRequestsView(GenericAPIView):
    """
    P8 - POST /profile/follow-requests/bulk/

        {"action": "accept" | "decline", "follow_ids": [12, 15, ...]}      (max 100)
        {"action": "accept" | "decline", "select_all": true}               (newest 100 pending)

    Answer: {"status": true, "action", "processed", "ids", "skipped", "remaining"}.
    Only requests addressed to the logged-in user count; anything else lands in `skipped` (no error), so an
    app that raced a single accept/delete just sees it in `skipped`. `remaining` > 0 -> call again.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = BulkFollowRequestSerializer

    @extend_schema(
        request=BulkFollowRequestSerializer,
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT},
        description="Accept or decline many pending follow requests at once",
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data
        result = relationships.bulk_resolve_requests(
            request.user,
            action=data["action"],
            follow_ids=data.get("follow_ids"),
            select_all=data.get("select_all", False),
        )
        return Response({"status": True, **result}, status=status.HTTP_200_OK)


class RelationshipSummaryView(APIView):
    """
    P8 - GET /profile/relationships/summary/

    Counts for the "Relationships" hub (followers, following, requests, not_following_back, close_friends,
    restricted, blocked, muted) - one call instead of eight list requests.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: OpenApiTypes.OBJECT}, description="Counts for the relationships hub")
    def get(self, request):
        return Response({
            "status": True,
            "data": relationships.relationship_counts(request.user),
        }, status=status.HTTP_200_OK)
