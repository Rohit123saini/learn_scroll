"""
post/highlight_views.py

STORIES UPGRADE - PART 3b: Highlights API. Rules + visibility: post/highlights.py.

    GET    /post/highlights/?user_id=          a person's highlights (default: mine). Not paginated
                                               (max 50 per user) but shaped like a page:
                                               {"count", "results": [row, ..]}
    POST   /post/highlights/                   create {"title"?, "story_ids": [..], "cover_story_id"?} -> 201 detail
    GET    /post/highlights/<id>/              detail: row + `stories` (full story objects, in order)
    PATCH  /post/highlights/<id>/              owner: {"title"?, "story_ids"? (ordered, replaces the set),
                                               "cover_story_id"? (null = automatic)} -> detail
    DELETE /post/highlights/<id>/              owner: 204 (the stories themselves are untouched)
    POST   /post/highlights/<id>/stories/      owner: add one {"story_id"} -> 201 added / 200 already there
    DELETE /post/highlights/<id>/stories/<sid>/ owner: remove one (idempotent). Removing the LAST story deletes
                                               the highlight -> {"highlight_deleted": true}
    GET    /post/stories/archive/              owner: my stories that can go into a highlight (paginated)

A highlight the caller may not see is a 404 (never 403), same as stories.
"""
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Exists, Max, OuterRef
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.pagination import StandardPagination

from .highlights import (
    HighlightError,
    archive_queryset,
    build_highlight_rows,
    can_view_highlights_of,
    clean_title,
    max_highlights,
    max_items,
    visible_story_ids,
)
from .models import Highlight, HighlightItem, Story, StoryView
from .serializers import StorySerializer, story_sticker_prefetch

User = get_user_model()


# --------------------------------------------------------------------------
# Input
# --------------------------------------------------------------------------
class HighlightWriteSerializer(serializers.Serializer):
    # No max_length here on purpose: clean_title() gives the friendlier message.
    title = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    story_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=False, max_length=500,
    )
    cover_story_id = serializers.UUIDField(required=False, allow_null=True)


def _error_response(exc):
    body = {"detail": exc.message, "code": exc.code}
    if exc.field:
        body[exc.field] = [exc.message]
    return Response(body, status=status.HTTP_400_BAD_REQUEST)


def _dedupe(ids):
    seen, out = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


# --------------------------------------------------------------------------
# Write helpers (all run inside transaction.atomic in the views)
# --------------------------------------------------------------------------
def _resolve_owned_stories(user, story_ids):
    """Stories from `user`'s archive, in the order given. Anything else
    (someone else's story, unknown id, a story removed before it expired)
    is one error - it does not say which case it was."""
    found = {s.pk: s for s in archive_queryset(user).filter(pk__in=story_ids)}
    if any(i not in found for i in story_ids):
        raise HighlightError(
            "Some of these stories were not found in your archive.", code="story_not_found", field="story_ids",
        )
    return [found[i] for i in story_ids]


def _set_cover(highlight, story_id):
    """`story_id` None -> automatic cover. Otherwise it must be one of the
    highlight's own photo stories (a video has no still to show)."""
    if story_id is None:
        highlight.cover_item = None
        return
    item = HighlightItem.objects.filter(highlight=highlight, story_id=story_id).select_related("story").first()
    if item is None:
        raise HighlightError("The cover must be a story in this highlight.", code="cover_invalid", field="cover_story_id")
    if item.story.media_type != "image":
        raise HighlightError("The cover must be a photo story.", code="cover_not_photo", field="cover_story_id")
    highlight.cover_item = item


def _create_highlight(user, data):
    title = clean_title(data.get("title"), required=False)
    story_ids = _dedupe(data.get("story_ids") or [])
    if not story_ids:
        raise HighlightError("Pick at least one story.", code="no_stories", field="story_ids")
    if len(story_ids) > max_items():
        raise HighlightError(
            f"A highlight can hold at most {max_items()} stories.", code="too_many_stories", field="story_ids",
        )
    if Highlight.objects.filter(user=user).count() >= max_highlights():
        raise HighlightError(
            f"You can have at most {max_highlights()} highlights.", code="highlight_limit",
        )
    stories = _resolve_owned_stories(user, story_ids)
    highlight = Highlight.objects.create(user=user, title=title)
    HighlightItem.objects.bulk_create(
        [HighlightItem(highlight=highlight, story=s, position=i) for i, s in enumerate(stories)]
    )
    if data.get("cover_story_id") is not None:
        _set_cover(highlight, data["cover_story_id"])
        highlight.save(update_fields=["cover_item", "updated_at"])
    return highlight


def _update_highlight(highlight, data):
    if "title" in data:
        highlight.title = clean_title(data["title"], required=True)

    if "story_ids" in data:
        wanted = _dedupe(data["story_ids"])
        if len(wanted) > max_items():
            raise HighlightError(
                f"A highlight can hold at most {max_items()} stories.", code="too_many_stories", field="story_ids",
            )
        existing = {item.story_id: item for item in highlight.items.all()}
        new_ids = [i for i in wanted if i not in existing]
        new_stories = {s.pk: s for s in _resolve_owned_stories(highlight.user, new_ids)}

        highlight.items.exclude(story_id__in=wanted).delete()  # a removed cover falls back to automatic (SET_NULL)
        to_create = []
        for position, story_id in enumerate(wanted):
            if story_id in existing:
                if existing[story_id].position != position:
                    HighlightItem.objects.filter(pk=existing[story_id].pk).update(position=position)
            else:
                to_create.append(HighlightItem(highlight=highlight, story=new_stories[story_id], position=position))
        HighlightItem.objects.bulk_create(to_create)
        highlight.refresh_from_db(fields=["cover_item"])

    if "cover_story_id" in data:
        _set_cover(highlight, data["cover_story_id"])

    highlight.save()  # bumps updated_at


def _touch(highlight_id):
    Highlight.objects.filter(pk=highlight_id).update(updated_at=timezone.now())


# --------------------------------------------------------------------------
# Read helpers
# --------------------------------------------------------------------------
def _visible_stories_in_order(highlight, viewer):
    """The highlight's stories `viewer` may see, in position order."""
    story_ids = list(
        HighlightItem.objects.filter(highlight=highlight, story_id__in=visible_story_ids(viewer))
        .order_by("position", "created_at")
        .values_list("story_id", flat=True)
    )
    if not story_ids:
        return []
    qs = (
        _story_queryset()
        .filter(pk__in=story_ids)
        .annotate(is_viewed_annotated=Exists(StoryView.objects.filter(story=OuterRef("pk"), user=viewer)))
    )
    by_id = {s.pk: s for s in qs}
    return [by_id[i] for i in story_ids if i in by_id]


def _story_queryset():
    return Story.objects.select_related("user").prefetch_related(story_sticker_prefetch())


def _detail_payload(highlight, viewer, request):
    """Row + `stories`. Caller has already checked the viewer may see it."""
    is_owner = highlight.user_id == viewer.id
    row = build_highlight_rows([highlight], viewer, request, hide_empty=False)[0]
    stories = _visible_stories_in_order(highlight, viewer)
    row["is_owner"] = is_owner
    row["stories"] = StorySerializer(stories, many=True, context={"request": request}).data
    return row


def _get_own_highlight_or_404(request, highlight_id, *, lock=False):
    qs = Highlight.objects.select_related("user")
    if lock:
        qs = qs.select_for_update(of=("self",))
    return get_object_or_404(qs, id=highlight_id, user=request.user)


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------
class HighlightListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="A person's story highlights (default: mine)",
        parameters=[OpenApiParameter(name="user_id", type=int, required=False)],
        tags=["Stories"],
    )
    def get(self, request):
        raw = request.query_params.get("user_id")
        if raw in (None, ""):
            owner = request.user
        else:
            try:
                owner = User.objects.filter(id=int(raw)).first()
            except (TypeError, ValueError):
                return Response({"detail": "user_id must be a number."}, status=status.HTTP_400_BAD_REQUEST)
            if owner is None:
                raise Http404("User not found.")
        if not can_view_highlights_of(owner, request.user):
            return Response({"count": 0, "results": []})  # nothing to show, nothing leaked
        highlights = Highlight.objects.filter(user=owner).select_related("user").order_by("-updated_at")
        rows = build_highlight_rows(highlights, request.user, request, hide_empty=owner.id != request.user.id)
        return Response({"count": len(rows), "results": rows})

    @extend_schema(summary="Create a highlight from my archived stories",
                   request=HighlightWriteSerializer, tags=["Stories"])
    def post(self, request):
        ser = HighlightWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                highlight = _create_highlight(request.user, ser.validated_data)
        except HighlightError as exc:
            return _error_response(exc)
        highlight = Highlight.objects.select_related("user").get(pk=highlight.pk)
        return Response(_detail_payload(highlight, request.user, request), status=status.HTTP_201_CREATED)


class HighlightDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="One highlight with its stories", tags=["Stories"])
    def get(self, request, highlight_id):
        highlight = get_object_or_404(Highlight.objects.select_related("user"), id=highlight_id)
        if not can_view_highlights_of(highlight.user, request.user):
            raise Http404("Highlight not found.")
        payload = _detail_payload(highlight, request.user, request)
        if not payload["is_owner"] and not payload["stories"]:
            raise Http404("Highlight not found.")  # every item is hidden from this viewer
        return Response(payload)

    @extend_schema(summary="Edit my highlight (title / stories / cover)",
                   request=HighlightWriteSerializer, tags=["Stories"])
    def patch(self, request, highlight_id):
        ser = HighlightWriteSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                highlight = _get_own_highlight_or_404(request, highlight_id, lock=True)
                _update_highlight(highlight, ser.validated_data)
        except HighlightError as exc:
            return _error_response(exc)
        highlight = Highlight.objects.select_related("user").get(pk=highlight.pk)
        return Response(_detail_payload(highlight, request.user, request))

    @extend_schema(summary="Delete my highlight (its stories are not deleted)", tags=["Stories"])
    def delete(self, request, highlight_id):
        highlight = _get_own_highlight_or_404(request, highlight_id)
        highlight.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class HighlightAddStoryAPIView(APIView):
    """POST /post/highlights/<id>/stories/  {"story_id": "<uuid>"} - what the story
    viewer's "Highlight" button calls for an existing highlight."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Add one of my stories to a highlight", tags=["Stories"])
    def post(self, request, highlight_id):
        story_id = request.data.get("story_id")
        field = serializers.UUIDField()
        try:
            story_id = field.run_validation(story_id)
        except serializers.ValidationError:
            return Response({"story_id": ["A valid story id is required."]}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                highlight = _get_own_highlight_or_404(request, highlight_id, lock=True)
                story = archive_queryset(request.user).filter(pk=story_id).first()
                if story is None:
                    raise Http404("Story not found.")
                if HighlightItem.objects.filter(highlight=highlight, story=story).exists():
                    added = False
                else:
                    count = highlight.items.count()
                    if count >= max_items():
                        raise HighlightError(
                            f"A highlight can hold at most {max_items()} stories.", code="too_many_stories",
                        )
                    last = highlight.items.aggregate(m=Max("position"))["m"]
                    HighlightItem.objects.create(
                        highlight=highlight, story=story, position=0 if last is None else last + 1,
                    )
                    _touch(highlight.pk)
                    added = True
        except HighlightError as exc:
            return _error_response(exc)
        return Response(
            {"highlight_id": str(highlight.pk), "story_id": str(story.pk),
             "items_count": highlight.items.count(), "added": added},
            status=status.HTTP_201_CREATED if added else status.HTTP_200_OK,
        )


class HighlightRemoveStoryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Remove one story from my highlight (last one deletes the highlight)", tags=["Stories"])
    def delete(self, request, highlight_id, story_id):
        with transaction.atomic():
            highlight = _get_own_highlight_or_404(request, highlight_id, lock=True)
            highlight.items.filter(story_id=story_id).delete()
            remaining = highlight.items.count()
            if remaining == 0:
                highlight.delete()
                return Response({"highlight_deleted": True, "items_count": 0})
            highlight.refresh_from_db(fields=["cover_item"])
            highlight.save()  # bumps updated_at
        return Response({"highlight_deleted": False, "items_count": remaining})


class StoryArchiveAPIView(APIView):
    """My stories that can go into a highlight: live ones and ones that expired
    within the last 30 days, newest first."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="My story archive (highlight picker)", tags=["Stories"])
    def get(self, request):
        qs = (
            archive_queryset(request.user)
            .select_related("user")
            .prefetch_related(story_sticker_prefetch())
            .annotate(is_viewed_annotated=Exists(StoryView.objects.filter(story=OuterRef("pk"), user=request.user)))
        )
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        data = StorySerializer(page, many=True, context={"request": request}).data
        return paginator.get_paginated_response(data)
