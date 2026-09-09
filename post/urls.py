"""
post/urls.py

⚠️ CRITICAL FIX — the uploaded file did
`from .views import CommentDeleteView, FeedView, PostViewSet, StoryViewSet`
— **none** of these four names exist in `views.py`. `views.py` defines
`PostCreateAPIView`, `HomeFeedView`, `PostListAPIView`, `PostDetailAPIView`,
`PostReactionAPIView`, `PostSaveToggleAPIView`, `SavedPostsListAPIView`,
`serve_media_with_range`; `comment_view.py` defines
`CommentCreateAPIView`, `CommentUpdateAPIView`, `CommentDeleteAPIView`,
`CommentListAPIView`, `CommentRepliesAPIView`, `CommentHideAPIView`, plus
the chunked-upload functions and `comment_react`. This file would raise
`ImportError` on load. Rewritten against the real view names (matching
post_app.md §8), with explicit imports instead of `from .views import *`
/ `from .comment_view import *` for the same reason explicit imports were
used in every other file this session.
"""
from django.conf import settings
from django.urls import path, re_path

from . import comment_view, views
from .comment_view import (
    CommentCreateAPIView,
    CommentDeleteAPIView,
    CommentHideAPIView,
    CommentListAPIView,
    CommentRepliesAPIView,
    CommentUpdateAPIView,
    comment_react,
)
from .views import (
    ExploreFeedAPIView,
    HashtagPostsAPIView,
    HomeFeedView,
    PostCreateAPIView,
    PostDeleteAPIView,
    PostDetailAPIView,
    PostListAPIView,
    PostReactionAPIView,
    PostSaveToggleAPIView,
    SavedPostsListAPIView,
    StoryCreateAPIView,
    StoryListAPIView,
    StoryViewAPIView,
    TrendingHashtagsAPIView,
    serve_media_with_range,
)

urlpatterns = [
    path("create/", PostCreateAPIView.as_view(), name="post-create"),
    path("list/", PostListAPIView.as_view(), name="post-list"),
    path("details/<uuid:id>/", PostDetailAPIView.as_view(), name="post-detail"),
    # NEW — checklist item 57 ("create/list/delete Post") had no delete
    # route anywhere before this; soft-delete, author-or-staff.
    path("<uuid:id>/delete/", PostDeleteAPIView.as_view(), name="post-delete"),
    path("feed/", HomeFeedView.as_view(), name="home-feed"),
    path("like/<uuid:post_id>/reaction/", PostReactionAPIView.as_view(), name="post-reaction"),
    path("<uuid:post_id>/save/", PostSaveToggleAPIView.as_view(), name="post-save-toggle"),
    path("saved/", SavedPostsListAPIView.as_view(), name="saved-posts-list"),

    # NEW — hashtag discovery + explore/discover surface. Overview
    # table lists "Hashtag" and "Explore-content" as core responsibilities
    # of this app; neither had a queryable endpoint before this.
    path("explore/", ExploreFeedAPIView.as_view(), name="post-explore"),
    path("hashtag/<str:tag>/", HashtagPostsAPIView.as_view(), name="hashtag-posts"),
    path("hashtags/trending/", TrendingHashtagsAPIView.as_view(), name="trending-hashtags"),

    # stories — NEW (checklist items 54/55/57/60)
    path("stories/", StoryListAPIView.as_view(), name="story-list"),
    path("stories/create/", StoryCreateAPIView.as_view(), name="story-create"),
    path("stories/<uuid:story_id>/view/", StoryViewAPIView.as_view(), name="story-view"),

    # comments
    path("comment/create/", CommentCreateAPIView.as_view(), name="comment-create"),
    path("comment/<uuid:comment_id>/delete/", CommentDeleteAPIView.as_view(), name="comment-delete"),
    path("comment/post/<uuid:post_id>/", CommentListAPIView.as_view(), name="comment-list"),
    path("comment/<uuid:comment_id>/hide/", CommentHideAPIView.as_view(), name="comment-hide"),
    path("comment/<uuid:comment_id>/replies/", CommentRepliesAPIView.as_view(), name="comment-replies"),
    path("comment/chunked/init/", comment_view.chunked_upload_init, name="chunked-init"),
    path("comment/chunked/chunk/", comment_view.chunked_upload_chunk, name="chunked-chunk"),
    path("comment/chunked/complete/", comment_view.chunked_upload_complete, name="chunked-complete"),
    path("comment/<uuid:comment_id>/react/", comment_react, name="comment-react"),
    path("comment/<uuid:comment_id>/update/", CommentUpdateAPIView.as_view(), name="comment-update"),
]

# ⚠️ Dev-only: `serve_media_with_range` has no auth/permission checks of its
# own (see views.py notes) and re-reads the file from disk on every request
# with no CDN caching in front of it — fine for local development, not
# something to rely on in production. Configure nginx/S3/CDN for real
# media serving there instead (post_app.md §14 issue #6).
if settings.DEBUG:
    urlpatterns += [
        re_path(r"^media/(?P<path>.*)$", serve_media_with_range, name="serve-media"),
    ]