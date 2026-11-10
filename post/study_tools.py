"""
post/study_tools.py - H13: the "Study tools" of a feed card (compact study action row + bottom sheet).

  GET  /post/<id>/study-tools/              state for the sheet: in_revision_deck + the post's topics (+ following?)
  POST /post/<id>/study-tools/revision/     {"desired": "add" | "remove"}  save to / take out of the Revision deck
  POST /post/topics/follow/                 {"topic": "kinematics", "desired": "follow" | "unfollow"}
  GET  /post/topics/following/              my followed topics (newest first)

WHY A SEPARATE, LAZY ENDPOINT: the feed serializer is query-budgeted per page (tests_feed_query_budget). Revision /
topic state is only needed when a student opens the sheet, so it is NOT added to every card; the sheet asks once.

REVISION DECK = the viewer's `PostSave` rows with collection_name="revision" (the existing Saved list already
filters on `?collection_name=revision`). One PostSave per (post, user) exists, so "add" on an already-saved post MOVES
it into the deck (it stays saved), and "remove" moves it back to the default collection - it is never un-saved by
accident. "add" / "remove" are idempotent (safe to replay from an offline queue or an Undo tap).

TOPICS of a post = its free-text `topics`, its `hashtags` and its `subject`, normalised to hashtag spelling
(hashtags.normalize_tag; spaces dropped: "Data Structures" -> datastructures, same as feed_context.subject_tags),
max MAX_TOPICS_PER_POST. A followed topic is stored as that key.
"""
from __future__ import annotations

import re
from typing import Dict, List

from django.db import IntegrityError
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from .audience import can_view_post
from .hashtags import normalize_tag
from .models import Post, PostSave, TopicFollow

REVISION_COLLECTION = "revision"
DEFAULT_COLLECTION = "default"
MAX_TOPICS_PER_POST = 6
MAX_FOLLOWED_TOPICS = 100


# --------------------------------------------------------------------------- pure helpers
def topic_key(raw) -> str:
    """'Data Structures' / '#Physics' -> 'datastructures' / 'physics'. '' when it is not a valid topic."""
    return normalize_tag(re.sub(r"\s+", "", str(raw or "")))


def topics_of_post(post) -> List[Dict[str, str]]:
    """[{key, label}] - topics, then subject, then hashtags; de-duplicated by key, at most MAX_TOPICS_PER_POST."""
    out: List[Dict[str, str]] = []
    seen = set()
    candidates = list(post.topics or []) + ([post.subject] if getattr(post, "subject", "") else []) + list(post.hashtags or [])
    for raw in candidates:
        label = str(raw or "").strip().lstrip("#").strip()
        key = topic_key(label)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"key": key, "label": label})
        if len(out) >= MAX_TOPICS_PER_POST:
            break
    return out


def state_for(user, post) -> dict:
    topics = topics_of_post(post)
    keys = [t["key"] for t in topics]
    following = set(TopicFollow.objects.filter(user=user, topic__in=keys).values_list("topic", flat=True)) if keys else set()
    in_deck = PostSave.objects.filter(user=user, post=post, collection_name=REVISION_COLLECTION).exists()
    return {
        "success": True,
        "in_revision_deck": in_deck,
        "is_saved": in_deck or PostSave.objects.filter(user=user, post=post).exists(),
        "topics": [{**t, "following": t["key"] in following} for t in topics],
    }


def set_revision(user, post, add: bool) -> bool:
    """Idempotent. Returns the new `in_revision_deck`."""
    row = PostSave.objects.filter(user=user, post=post).first()
    if add:
        if row is None:
            try:
                PostSave.objects.create(user=user, post=post, collection_name=REVISION_COLLECTION)
            except IntegrityError:  # two quick taps: the other request created it first
                PostSave.objects.filter(user=user, post=post).update(collection_name=REVISION_COLLECTION)
        elif row.collection_name != REVISION_COLLECTION:
            PostSave.objects.filter(pk=row.pk).update(collection_name=REVISION_COLLECTION)
        return True
    if row is not None and row.collection_name == REVISION_COLLECTION:
        PostSave.objects.filter(pk=row.pk).update(collection_name=DEFAULT_COLLECTION)
    return False


def set_topic_follow(user, key: str, follow: bool) -> bool:
    """Idempotent. Returns the new `following`. Raises ValueError('too_many_topics') past MAX_FOLLOWED_TOPICS."""
    if follow:
        if TopicFollow.objects.filter(user=user, topic=key).exists():
            return True
        if TopicFollow.objects.filter(user=user).count() >= MAX_FOLLOWED_TOPICS:
            raise ValueError("too_many_topics")
        TopicFollow.objects.get_or_create(user=user, topic=key)
        return True
    TopicFollow.objects.filter(user=user, topic=key).delete()
    return False


# --------------------------------------------------------------------------- views
class StudyToolsThrottle(UserRateThrottle):
    rate = "60/min"
    scope = "post_study_tools"


def _err(code, message, http=status.HTTP_400_BAD_REQUEST):
    return Response({"success": False, "code": code, "message": message}, status=http)


def _visible_post(request, post_id):
    post = get_object_or_404(Post, id=post_id, is_deleted=False)
    if post.moderation_status != "approved" and post.user_id != request.user.id:
        raise Http404
    if not can_view_post(post, request.user):
        raise Http404
    return post


class PostStudyToolsAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [StudyToolsThrottle]

    def get(self, request, id):
        return Response(state_for(request.user, _visible_post(request, id)))


class PostRevisionDeckAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [StudyToolsThrottle]

    def post(self, request, id):
        post = _visible_post(request, id)
        desired = str(request.data.get("desired", "add")).strip().lower()
        if desired not in ("add", "remove"):
            return _err("invalid_desired", "desired must be add or remove.")
        if desired == "add":
            # same rule as the Save button: a hidden post / a blocked person's post cannot be added
            from .moderation_guard import post_hidden_for

            hidden = post_hidden_for(request.user, post)
            if not hidden and post.user_id != request.user.id:
                from user_profile.views import is_blocked_between

                hidden = is_blocked_between(request.user, post.user)
            if hidden:
                raise Http404
        in_deck = set_revision(request.user, post, desired == "add")
        return Response({"success": True, "in_revision_deck": in_deck})


class TopicFollowAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [StudyToolsThrottle]

    def post(self, request):
        key = topic_key(request.data.get("topic"))
        if not key:
            return _err("invalid_topic", "Topic must be 1-50 letters, digits or underscores.")
        desired = str(request.data.get("desired", "follow")).strip().lower()
        if desired not in ("follow", "unfollow"):
            return _err("invalid_desired", "desired must be follow or unfollow.")
        try:
            following = set_topic_follow(request.user, key, desired == "follow")
        except ValueError:
            return _err("too_many_topics", f"You can follow up to {MAX_FOLLOWED_TOPICS} topics.")
        return Response({"success": True, "topic": key, "following": following})


class FollowedTopicsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = TopicFollow.objects.filter(user=request.user).values_list("topic", flat=True)[:MAX_FOLLOWED_TOPICS]
        return Response({"success": True, "topics": list(rows)})
