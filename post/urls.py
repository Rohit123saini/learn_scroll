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
from .poll_views import (  # polls + quiz mode
    PostPollConvertToQuestionAPIView,
    PostPollLeaderboardAPIView,
    PostPollResultsAPIView,
    PostPollResultsExportAPIView,
    PostPollVoteAPIView,
    PostQuizMyScoreAPIView,
)
from .reels_views import ReelsFeedView
from .sound_views import (  # X7: sound page, report, staff takedown
    SoundDetailAPIView, SoundPolicyAPIView, SoundReelsAPIView, SoundReportAPIView,
    StaffSoundActionAPIView, StaffSoundReportsAPIView,
)
from .reply_reel_views import MyPublicReelsAPIView  # picker: my public reels for "reply with a reel"
from .media_retry_views import MediaRetryProcessingAPIView  # owner: retry a failed video conversion
from .guest_views import GuestFeedAPIView  # read-only feed for guests (no login)
from .post_options_views import (  # round 5: edit history, co-authors
    MyCollabInvitesAPIView, PostCollaboratorRemoveAPIView, PostCollaboratorRespondAPIView, PostCollaboratorsAPIView,
    PostHistoryAPIView,
)
from .card_previews import CardPreviewsAPIView  # feed card: liked-by + top comments, batched
from .insights_views import CreatorInsightsOverviewAPIView, PostInsightsAPIView  # creator insights
from .creator_analytics_views import CreatorAnalyticsAPIView  # P-F9_F10
from .reel_series_views import ReelSeriesAPIView  # X2: parts of a reel series (reels viewer pill)
from .reel_insights_views import ReelInsightsAPIView  # reel insights (retention, follows gained)
from .reel_manage_views import (  # reel management after publish: schedule list / reschedule / publish now, cover
    PostPublishNowAPIView, PostScheduleAPIView, ReelCoverAPIView, ScheduledPostsListAPIView,
)
from .staff_moderation_views import (  # feed card Review / Remove + H23 flagged queue
    StaffModerationQueueActionAPIView,
    StaffModerationQueueAPIView,
    StaffModerationQueueSummaryAPIView,
    StaffPostActionAPIView,
    StaffPostReviewAPIView,
)
from .publish_check_views import MinorPrivacyAPIView, PublishCheckAPIView  # H23 publish safety
from .rich_text_views import LinkPreviewAPIView  # rich caption: link preview card
from .highlight_views import (
    HighlightAddStoryAPIView,
    HighlightDetailAPIView,
    HighlightListCreateAPIView,
    HighlightRemoveStoryAPIView,
    HighlightReorderAPIView,  # P6
    HighlightStorySaveAPIView,  # ST-A (1)
    HighlightStoryShareAPIView,  # ST-A (1)
    StoryArchiveAPIView,
)
from .close_friends_views import (
    CloseFriendCandidatesAPIView,
    CloseFriendDetailAPIView,
    CloseFriendsAPIView,
)
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
    FeedAdConfigAPIView,
    HashtagPostsAPIView,
    HomeFeedView,
    PostCountsAPIView,
    PostCreateAPIView,
    PostDeleteAPIView,
    PostDetailAPIView,
    PostEditAPIView,
    PostEventBulkAPIView,
    PostListAPIView,
    PostReactionAPIView,
    PostRepostAPIView,
    PostShareItemAPIView,
    PostSeenBatchAPIView,
    # TASK G6 — "Ask a doubt" answers.
    PostAnswerListCreateAPIView,
    PostAnswerMarkBestAPIView,
    DoubtConfigAPIView,
    DoubtSimilarAPIView,
    PostVideoProgressAPIView,
    PostSaveToggleAPIView,
    PostShareAPIView,
    PostShareLinkAPIView,
    PostVisibilityAPIView,
    SavedPostsListAPIView,
    StoryCreateAPIView,
    StoryListAPIView,
    StoryLiveAPIView,
    StoryLimitsAPIView,  # F2
    StoryViewAPIView,
    StoryReactAPIView,
    StoryReplyAPIView,
    StoryShareAPIView,  # X1
    StorySaveAPIView,  # X1
    StoryViewersAPIView,
    StoryDetailAPIView,
    StoryMentionCandidatesAPIView,
    StoryPollVoteAPIView,
    StoryQuestionAnswerAPIView,
    StoryQuizAnswerAPIView,
    GifSearchAPIView,
    StoryStickerResponsesAPIView,
    TrendingHashtagsAPIView,
    # TASK 3 — feed personalization (production_readiness_tasks.md).
    UserInterestsAPIView,
    # Feed feedback controls (Part 1): Not interested + Mute account.
    NotInterestedAPIView,
    NotInterestedListAPIView,
    MutedAccountsAPIView,
    UnmuteAccountAPIView,
    # Feed feedback controls (Part 2): Show fewer + Why am I seeing this.
    ShowFewerAPIView,
    FeedFeedbackListAPIView,
    FeedMetricsAPIView,
    FeedOpsAPIView,
    FeedFeedbackDeleteAPIView,
    WhyAmISeeingThisAPIView,
    serve_media_with_range,
    # TASK 3/4 — dedicated post chunked-upload routes (see views.py's own
    # comment on why these are separate from the comment chunked-upload
    # functions, and why the chunk-upload step itself is NOT among these).
    post_chunked_upload_init,
    post_chunked_upload_complete,
    post_chunked_upload_status,
    # TASK 4 — category taxonomy + Freesound music search proxy.
    category_taxonomy,
    freesound_music_search,
    # P3 pin / P5a tags.
    PostPinAPIView,
    PostArchiveAPIView,
    ArchivedPostsListAPIView,
    PostTaggedAPIView,
    PostTagAPIView,
)
from .ai_study_views import PostAiStudyAPIView  # Feature 1
from .ai_assist_views import PostAiAssistAPIView, PostAiAssistConfigAPIView  # composer AI helpers
from .upload_views import (  # H20 UploadQueue + Drafts
    DraftDetailAPIView,
    DraftListAPIView,
    UploadChunkAPIView,
    UploadCompleteAPIView,
    UploadSessionCreateAPIView,
    UploadSessionDetailAPIView,
)
from .reading_progress import AttachmentProgressAPIView, ContinueReadingAPIView  # H14 PDF page resume
from .lecture_views import (  # round 4 - video lectures (chapters, captions, resume, notes, series / course)
    ContinueLectureAPIView,
    LectureBundleAPIView,
    MediaCaptionsAPIView,
    MediaChaptersAPIView,
    MediaNoteDetailAPIView,
    MediaNotesAPIView,
    MediaProgressAPIView,
    NextLectureAPIView,
    SeriesDetailAPIView,
    SeriesItemDetailAPIView,
    SeriesItemsAPIView,
    SeriesListCreateAPIView,
    SeriesOrderAPIView,
)
from .trust_signals import PostHelpfulAPIView  # H15 "helpful for N students"
from .study_tools import (  # H13 study tools on the feed card
    FollowedTopicsAPIView,
    PostRevisionDeckAPIView,
    PostStudyToolsAPIView,
    TopicFollowAPIView,
)
from .resource_versions import (  # resources upgrade
    AttachmentReplaceAPIView, AttachmentTextSearchAPIView, AttachmentVersionDownloadAPIView,
    AttachmentVersionsAPIView, PaperCitationAPIView, ResourceMetaAPIView,
)
from .resource_views import (  # P1
    AttachmentDownloadAPIView,
    AttachmentReportAPIView,
    ConvertToDoubtAPIView,
    HashtagSearchAPIView,
    HashtagSuggestAPIView,
    ResourceListCreateAPIView,
)

from . import profile_tabs  # P2

urlpatterns = [
    path("create/", PostCreateAPIView.as_view(), name="post-create"),
    path("list/", PostListAPIView.as_view(), name="post-list"),
    path("details/<uuid:id>/", PostDetailAPIView.as_view(), name="post-detail"),
    # TASK 4 — composer category/subcategory pickers were calling this
    # already; nothing existed to answer it. See category_taxonomy's own
    # docstring (views.py) for the subcategory-data caveat.
    path("categories/", category_taxonomy, name="post-categories"),
    # TASK 3 — feed personalization chip picker. GET = current selection +
    # taxonomy; PUT/POST = replace full selection.
    path("interests/", UserInterestsAPIView.as_view(), name="user-interests"),
    # Feed feedback controls (Part 1) - see NotInterestedAPIView / MutedAccountsAPIView.
    path("<uuid:post_id>/not-interested/", NotInterestedAPIView.as_view(), name="post-not-interested"),
    path("not-interested/", NotInterestedListAPIView.as_view(), name="post-not-interested-list"),
    path("muted-accounts/", MutedAccountsAPIView.as_view(), name="post-muted-accounts"),
    path("muted-accounts/<int:user_id>/", UnmuteAccountAPIView.as_view(), name="post-unmute-account"),
    # Feed feedback controls (Part 2) - see ShowFewerAPIView / WhyAmISeeingThisAPIView.
    path("<uuid:post_id>/show-fewer/", ShowFewerAPIView.as_view(), name="post-show-fewer"),
    path("<uuid:post_id>/why/", WhyAmISeeingThisAPIView.as_view(), name="post-why"),
    path("feed/metrics/", FeedMetricsAPIView.as_view(), name="post-feed-metrics"),
    path("feed/ops/", FeedOpsAPIView.as_view(), name="post-feed-ops"),
    path("feedback/", FeedFeedbackListAPIView.as_view(), name="post-feedback-list"),
    path("feedback/<uuid:feedback_id>/", FeedFeedbackDeleteAPIView.as_view(), name="post-feedback-delete"),
    # TASK 4 — Music tab (media_edit_screen.dart / auto_edit_screen.dart)
    # search, proxied server-side so FREESOUND_API_KEY never ships in the app.
    path("music/search/", freesound_music_search, name="post-music-search"),
    path("gifs/search/", GifSearchAPIView.as_view(), name="post-gif-search"),  # F3: GIF sticker (Giphy proxy)
    # NEW — checklist item 57 ("create/list/delete Post") had no delete
    # route anywhere before this; soft-delete, author-or-staff.
    path("<uuid:id>/delete/", PostDeleteAPIView.as_view(), name="post-delete"),
    # NEW — post update/edit had no PATCH endpoint anywhere before this
    # (see PostEditAPIView's docstring), and a separate one for visibility
    # (Post.visibility was previously create-time-only).
    path("<uuid:id>/edit/", PostEditAPIView.as_view(), name="post-edit"),
    path("<uuid:id>/visibility/", PostVisibilityAPIView.as_view(), name="post-visibility"),
    # P3 pin (max 3) / P5a tagged tab + tag management.
    path("<uuid:post_id>/pin/", PostPinAPIView.as_view(), name="post-pin"),
    # Feed card menu: archive / restore one of my posts, and the list of my archived posts.
    path("<uuid:post_id>/archive/", PostArchiveAPIView.as_view(), name="post-archive"),
    path("archived/", ArchivedPostsListAPIView.as_view(), name="post-archived-list"),
    # Round 5: edit history + co-authors.
    path("<uuid:post_id>/history/", PostHistoryAPIView.as_view(), name="post-history"),
    path("<uuid:post_id>/collaborators/", PostCollaboratorsAPIView.as_view(), name="post-collaborators"),
    path("<uuid:post_id>/collaborators/respond/", PostCollaboratorRespondAPIView.as_view(), name="post-collaborator-respond"),
    path("<uuid:post_id>/collaborators/<int:user_id>/", PostCollaboratorRemoveAPIView.as_view(), name="post-collaborator-remove"),
    path("collab-invites/", MyCollabInvitesAPIView.as_view(), name="post-collab-invites"),
    path("<uuid:post_id>/tag/", PostTagAPIView.as_view(), name="post-tag"),
    path("tagged/<str:username>/", PostTaggedAPIView.as_view(), name="post-tagged"),
    # NEW — internal "forward to chat" (checklist item 61). See
    # PostShareAPIView / services.py's share_post_to_conversation for the
    # send_message ImportError bug this replaces.
    path("<uuid:id>/share/", PostShareAPIView.as_view(), name="post-share"),
    # Reels / feed "Share link": records the share, returns the deep link (never the raw media URL).
    path("<uuid:id>/share-link/", PostShareLinkAPIView.as_view(), name="post-share-link"),
    path("feed/", HomeFeedView.as_view(), name="home-feed"),
    # Guest browsing: PUBLIC, read-only, no token (post/guest_views.py).
    path("guest/feed/", GuestFeedAPIView.as_view(), name="guest-feed"),
    # Creator insights (post/insights.py): all my posts, and one of my posts.
    path("insights/", CreatorInsightsOverviewAPIView.as_view(), name="creator-insights"),
    path("creator-analytics/", CreatorAnalyticsAPIView.as_view(), name="creator-analytics"),
    path("<uuid:post_id>/insights/", PostInsightsAPIView.as_view(), name="post-insights"),
    path("<uuid:post_id>/reel-insights/", ReelInsightsAPIView.as_view(), name="reel-insights"),
    path("scheduled/", ScheduledPostsListAPIView.as_view(), name="post-scheduled-list"),
    path("<uuid:post_id>/schedule/", PostScheduleAPIView.as_view(), name="post-schedule"),
    path("<uuid:post_id>/publish-now/", PostPublishNowAPIView.as_view(), name="post-publish-now"),
    path("<uuid:post_id>/reel-cover/", ReelCoverAPIView.as_view(), name="reel-cover"),
    # Staff-only Review / Remove shortcut on a feed card (post/staff_moderation_views.py).
    path("<uuid:post_id>/staff-review/", StaffPostReviewAPIView.as_view(), name="post-staff-review"),
    path("<uuid:post_id>/staff-action/", StaffPostActionAPIView.as_view(), name="post-staff-action"),
    # H23 - the flagged queue (staff only): list / badge counts / approve-dismiss-remove one item.
    path("staff/moderation-queue/", StaffModerationQueueAPIView.as_view(), name="staff-moderation-queue"),
    path("staff/moderation-queue/summary/", StaffModerationQueueSummaryAPIView.as_view(), name="staff-moderation-queue-summary"),
    path("staff/moderation-queue/action/", StaffModerationQueueActionAPIView.as_view(), name="staff-moderation-queue-action"),
    # X7 - reel sounds: policy, sound page, "Reels with this sound", report; staff: reports list + block / restore.
    path("sounds/policy/", SoundPolicyAPIView.as_view(), name="sound-policy"),
    re_path(r"^sounds/(?P<sound_id>\d{1,40})/$", SoundDetailAPIView.as_view(), name="sound-detail"),
    re_path(r"^sounds/(?P<sound_id>\d{1,40})/reels/$", SoundReelsAPIView.as_view(), name="sound-reels"),
    re_path(r"^sounds/(?P<sound_id>\d{1,40})/report/$", SoundReportAPIView.as_view(), name="sound-report"),
    path("staff/sounds/reports/", StaffSoundReportsAPIView.as_view(), name="staff-sound-reports"),
    path("staff/sounds/action/", StaffSoundActionAPIView.as_view(), name="staff-sound-action"),
    # H23 - publish safety: what to grey out for a minor, and the preflight before a big upload.
    path("minor-privacy/", MinorPrivacyAPIView.as_view(), name="post-minor-privacy"),
    path("publish-check/", PublishCheckAPIView.as_view(), name="post-publish-check"),
    path("link-preview/", LinkPreviewAPIView.as_view(), name="post-link-preview"),  # rich caption: preview card
    # Feed "seen" signal — batch (max 50), never bumps views_count.
    path("previews/", CardPreviewsAPIView.as_view(), name="card-previews"),
    path("feed/seen/", PostSeenBatchAPIView.as_view(), name="feed-seen"),
    # C1-BE — bulk analytics events (impression / dwell / tap / skip), max 100 per
    # request, throttle scope "post_events".
    path("events/", PostEventBulkAPIView.as_view(), name="post-events"),
    # Reels (vertical short videos): ranked pool, cursor + frozen snapshot, ?start=<post_id>.
    path("reels/", ReelsFeedView.as_view(), name="reels-feed"),
    path("reels/mine/", MyPublicReelsAPIView.as_view(), name="reels-mine"),  # reply-with-reel picker
    path("reels/<uuid:post_id>/series/", ReelSeriesAPIView.as_view(), name="reel-series"),  # X2: parts of a reel series
    path("like/<uuid:post_id>/reaction/", PostReactionAPIView.as_view(), name="post-reaction"),
    # NEW — SujhaavFayda1 item 2: bulk counts polling for currently-loaded
    # feed posts (see PostCountsAPIView docstring for the WebSocket note).
    path("counts/", PostCountsAPIView.as_view(), name="post-counts"),
    path("<uuid:post_id>/save/", PostSaveToggleAPIView.as_view(), name="post-save-toggle"),
    # NEW — Instagram-style repost. POST /post/<id>/repost/ (quick repost
    # with an empty body, "repost with caption" with `repost_caption`).
    path("<uuid:post_id>/repost/", PostRepostAPIView.as_view(), name="post-repost"),
    # NEW — share a tuition class / test series into the feed (embedded card).
    path("share-item/", PostShareItemAPIView.as_view(), name="post-share-item"),
    # TASK G6 — poll voting (live results) + "Ask a doubt" answers.
    path("<uuid:post_id>/poll/vote/", PostPollVoteAPIView.as_view(), name="post-poll-vote"),
    path("<uuid:post_id>/poll/results/", PostPollResultsAPIView.as_view(), name="post-poll-results"),
    path("<uuid:post_id>/poll/results/export/", PostPollResultsExportAPIView.as_view(), name="post-poll-results-export"),
    path("<uuid:post_id>/poll/leaderboard/", PostPollLeaderboardAPIView.as_view(), name="post-poll-leaderboard"),
    path("<uuid:post_id>/poll/convert-to-question/", PostPollConvertToQuestionAPIView.as_view(), name="post-poll-convert"),
    path("quiz/my-score/", PostQuizMyScoreAPIView.as_view(), name="post-quiz-my-score"),
    path("<uuid:post_id>/answers/", PostAnswerListCreateAPIView.as_view(), name="post-answers"),
    path("answers/<uuid:answer_id>/mark-best/", PostAnswerMarkBestAPIView.as_view(), name="post-answer-mark-best"),
    # Doubt post upgrade: composer limits + wallet balance, and the "already asked?" duplicate check.
    path("doubts/config/", DoubtConfigAPIView.as_view(), name="post-doubt-config"),
    path("doubts/similar/", DoubtSimilarAPIView.as_view(), name="post-doubt-similar"),
    # TASK G4 — feed video widget reports watch progress here so
    # HomeFeedView/ExploreFeedAPIView can rank by video-completion-rate.
    path("<uuid:post_id>/video-progress/", PostVideoProgressAPIView.as_view(), name="post-video-progress"),
    path("saved/", SavedPostsListAPIView.as_view(), name="saved-posts-list"),
    # P2 - saved collections (folders) + public folders on a profile.
    path("saved/collections/", profile_tabs.SavedCollectionListCreateAPIView.as_view(), name="saved-collections"),
    path("saved/collections/<uuid:pk>/", profile_tabs.SavedCollectionDetailAPIView.as_view(), name="saved-collection-detail"),
    path("saved/<uuid:post_id>/collection/", profile_tabs.SavedPostMoveAPIView.as_view(), name="saved-post-move"),
    path("collections/of/<str:username>/", profile_tabs.UserPublicCollectionsAPIView.as_view(), name="user-public-collections"),
    path("collections/of/<str:username>/<str:name>/posts/", profile_tabs.UserPublicCollectionPostsAPIView.as_view(), name="user-public-collection-posts"),

    # TASK 3 — chunked (large-video) post upload. Fixes api_service.dart's
    # initPostChunkedUpload/completePostChunkedUpload hitting these exact
    # paths and 404ing because they never existed before. Chunk upload
    # itself intentionally stays on the comment/chunked/chunk/ route below
    # (see views.py's post_chunked_upload_init comment) — only init/
    # complete/status are post-specific.
    path("chunked/init/", post_chunked_upload_init, name="post-chunked-init"),
    path("chunked/complete/", post_chunked_upload_complete, name="post-chunked-complete"),
    # TASK 4 — resume-status endpoint api_service.dart's
    # getChunkedUploadStatus() already calls; previously missing entirely.
    path("chunked/status/<str:upload_id>/", post_chunked_upload_status, name="post-chunked-status"),

    # H20 - resumable upload for EVERY media kind (post / reel / story / resource) + synced drafts.
    path("uploads/", UploadSessionCreateAPIView.as_view(), name="upload-create"),
    path("uploads/<uuid:session_id>/", UploadSessionDetailAPIView.as_view(), name="upload-detail"),
    path("uploads/<uuid:session_id>/chunk/", UploadChunkAPIView.as_view(), name="upload-chunk"),
    path("uploads/<uuid:session_id>/complete/", UploadCompleteAPIView.as_view(), name="upload-complete"),
    path("drafts/", DraftListAPIView.as_view(), name="draft-list"),
    path("drafts/<uuid:client_id>/", DraftDetailAPIView.as_view(), name="draft-detail"),

    # NEW — SujhaavFayda1 item 3: feed ad/interstitial cadence, backend-driven
    # instead of hardcoded Dart consts.
    path("feed/ad-config/", FeedAdConfigAPIView.as_view(), name="feed-ad-config"),

    # NEW — hashtag discovery + explore/discover surface. Overview
    # table lists "Hashtag" and "Explore-content" as core responsibilities
    # of this app; neither had a queryable endpoint before this.
    path("explore/", ExploreFeedAPIView.as_view(), name="post-explore"),
    path("hashtag/<str:tag>/", HashtagPostsAPIView.as_view(), name="hashtag-posts"),
    path("hashtags/trending/", TrendingHashtagsAPIView.as_view(), name="trending-hashtags"),
    # P1 — knowledge sharing: Notes/Resource posts, hashtag search + composer suggestions.
    path("hashtags/search/", HashtagSearchAPIView.as_view(), name="hashtag-search"),
    path("hashtags/suggest/", HashtagSuggestAPIView.as_view(), name="hashtag-suggest"),
    path("resources/", ResourceListCreateAPIView.as_view(), name="post-resources"),
    path("attachments/<uuid:attachment_id>/download/", AttachmentDownloadAPIView.as_view(), name="post-attachment-download"),
    path("attachments/<uuid:attachment_id>/report/", AttachmentReportAPIView.as_view(), name="post-attachment-report"),
    path("<uuid:id>/convert-to-doubt/", ConvertToDoubtAPIView.as_view(), name="post-convert-to-doubt"),
    # Resources upgrade: version replace, history, in-file search, paper metadata edit, citation.
    path("attachments/<uuid:attachment_id>/replace/", AttachmentReplaceAPIView.as_view(), name="post-attachment-replace"),
    path("attachments/<uuid:attachment_id>/versions/", AttachmentVersionsAPIView.as_view(), name="post-attachment-versions"),
    path("attachments/<uuid:attachment_id>/versions/<int:version>/download/", AttachmentVersionDownloadAPIView.as_view(), name="post-attachment-version-download"),
    path("attachments/<uuid:attachment_id>/search/", AttachmentTextSearchAPIView.as_view(), name="post-attachment-search"),
    path("<uuid:id>/resource-meta/", ResourceMetaAPIView.as_view(), name="post-resource-meta"),
    path("<uuid:id>/citation/", PaperCitationAPIView.as_view(), name="post-citation"),
    path("<uuid:id>/ai-study/", PostAiStudyAPIView.as_view(), name="post-ai-study"),  # Feature 1
    # Composer AI helpers (caption / tags / summary / alt text / translate / step hints + free quality hints).
    # `config/` is listed first so it is never read as a <feature>.
    path("ai-assist/config/", PostAiAssistConfigAPIView.as_view(), name="post-ai-assist-config"),
    path("ai-assist/<slug:feature>/", PostAiAssistAPIView.as_view(), name="post-ai-assist"),
    # H13 - study tools of a feed card (state is loaded lazily when the sheet opens, never per card)
    path("<uuid:id>/study-tools/", PostStudyToolsAPIView.as_view(), name="post-study-tools"),
    path("<uuid:id>/study-tools/revision/", PostRevisionDeckAPIView.as_view(), name="post-study-revision"),
    path("topics/follow/", TopicFollowAPIView.as_view(), name="topic-follow"),
    path("topics/following/", FollowedTopicsAPIView.as_view(), name="topics-following"),
    # H14 - Continue learning: page the student reached in a PDF + the last unfinished one
    path("attachments/<uuid:attachment_id>/progress/", AttachmentProgressAPIView.as_view(), name="post-attachment-progress"),
    path("reading/continue/", ContinueReadingAPIView.as_view(), name="post-reading-continue"),
    # H15 - "helpful for N students" on a Notes / Resource card
    path("<uuid:id>/helpful/", PostHelpfulAPIView.as_view(), name="post-helpful"),

    # Round 4 - video lectures: the player bundle, chapters, captions (hi / en), resume, notes PDF, series / course.
    path("media/<uuid:media_id>/retry-processing/", MediaRetryProcessingAPIView.as_view(), name="media-retry-processing"),
    path("media/<uuid:media_id>/lecture/", LectureBundleAPIView.as_view(), name="lecture-bundle"),
    path("media/<uuid:media_id>/chapters/", MediaChaptersAPIView.as_view(), name="lecture-chapters"),
    path("media/<uuid:media_id>/captions/", MediaCaptionsAPIView.as_view(), name="lecture-captions"),
    path("media/<uuid:media_id>/progress/", MediaProgressAPIView.as_view(), name="lecture-progress"),
    path("media/<uuid:media_id>/notes/", MediaNotesAPIView.as_view(), name="lecture-notes"),
    path("media/<uuid:media_id>/notes/<uuid:attachment_id>/", MediaNoteDetailAPIView.as_view(), name="lecture-note-detail"),
    path("lectures/continue/", ContinueLectureAPIView.as_view(), name="lecture-continue"),
    path("series/", SeriesListCreateAPIView.as_view(), name="video-series"),
    path("series/<uuid:series_id>/", SeriesDetailAPIView.as_view(), name="video-series-detail"),
    path("series/<uuid:series_id>/items/", SeriesItemsAPIView.as_view(), name="video-series-items"),
    path("series/<uuid:series_id>/items/<uuid:post_id>/", SeriesItemDetailAPIView.as_view(), name="video-series-item"),
    path("series/<uuid:series_id>/order/", SeriesOrderAPIView.as_view(), name="video-series-order"),
    path("<uuid:post_id>/next-lecture/", NextLectureAPIView.as_view(), name="lecture-next"),

    # stories — NEW (checklist items 54/55/57/60)
    path("stories/", StoryListAPIView.as_view(), name="story-list"),
    path("stories/create/", StoryCreateAPIView.as_view(), name="story-create"),
    path("stories/live/", StoryLiveAPIView.as_view(), name="story-live"),  # LV1
    path("stories/limits/", StoryLimitsAPIView.as_view(), name="story-limits"),  # F2
    path("stories/<uuid:story_id>/view/", StoryViewAPIView.as_view(), name="story-view"),
    path("stories/<uuid:story_id>/react/", StoryReactAPIView.as_view(), name="story-react"),
    path("stories/<uuid:story_id>/reply/", StoryReplyAPIView.as_view(), name="story-reply"),
    path("stories/<uuid:story_id>/share/", StoryShareAPIView.as_view(), name="story-share"),  # X1
    path("stories/<uuid:story_id>/save/", StorySaveAPIView.as_view(), name="story-save"),  # X1
    path("stories/<uuid:story_id>/viewers/", StoryViewersAPIView.as_view(), name="story-viewers"),
    # stickers / mentions — STORIES UPGRADE, PART 2a
    path("stories/mention-candidates/", StoryMentionCandidatesAPIView.as_view(), name="story-mention-candidates"),
    # STORIES UPGRADE - PART 3b: highlights + the archive picker.
    path("stories/archive/", StoryArchiveAPIView.as_view(), name="story-archive"),
    path("highlights/", HighlightListCreateAPIView.as_view(), name="highlight-list"),
    path("highlights/reorder/", HighlightReorderAPIView.as_view(), name="highlight-reorder"),  # P6
    path("highlights/<uuid:highlight_id>/", HighlightDetailAPIView.as_view(), name="highlight-detail"),
    path("highlights/<uuid:highlight_id>/stories/", HighlightAddStoryAPIView.as_view(), name="highlight-add-story"),
    path("highlights/<uuid:highlight_id>/stories/<uuid:story_id>/", HighlightRemoveStoryAPIView.as_view(), name="highlight-remove-story"),
    path("highlights/<uuid:highlight_id>/stories/<uuid:story_id>/share/", HighlightStoryShareAPIView.as_view(), name="highlight-story-share"),  # ST-A (1)
    path("highlights/<uuid:highlight_id>/stories/<uuid:story_id>/save/", HighlightStorySaveAPIView.as_view(), name="highlight-story-save"),  # ST-A (1)
    # poll / question stickers - STORIES UPGRADE, PART 2b
    path("stories/<uuid:story_id>/stickers/<uuid:sticker_id>/vote/", StoryPollVoteAPIView.as_view(), name="story-poll-vote"),
    path("stories/<uuid:story_id>/stickers/<uuid:sticker_id>/answer/", StoryQuestionAnswerAPIView.as_view(), name="story-question-answer"),
    path("stories/<uuid:story_id>/stickers/<uuid:sticker_id>/quiz-answer/", StoryQuizAnswerAPIView.as_view(), name="story-quiz-answer"),  # F3
    path("stories/<uuid:story_id>/stickers/<uuid:sticker_id>/responses/", StoryStickerResponsesAPIView.as_view(), name="story-sticker-responses"),
    path("stories/<uuid:story_id>/", StoryDetailAPIView.as_view(), name="story-detail"),

    # close friends — STORIES UPGRADE, PART 1
    path("close-friends/", CloseFriendsAPIView.as_view(), name="close-friends"),
    path("close-friends/candidates/", CloseFriendCandidatesAPIView.as_view(), name="close-friends-candidates"),
    path("close-friends/<int:user_id>/", CloseFriendDetailAPIView.as_view(), name="close-friend-detail"),

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
    path("comment/<uuid:comment_id>/pin/", comment_view.CommentPinAPIView.as_view(), name="comment-pin"),  # round 5
]

# TASK 25 — production media serving.
#
# ⚠️ `serve_media_with_range` has no auth/permission checks of its own
# (see views.py notes), streams every request through a Python worker
# instead of the webserver's sendfile path, and has zero CDN/shared-cache
# in front of it. It is a local-dev convenience, not a production media
# server.
#
# Gated on `settings.SERVE_MEDIA_VIA_DJANGO` rather than bare `DEBUG` so
# ops can tell at a glance (in settings.py) exactly when this route is
# live, and so a production box that hasn't wired up nginx yet 404s
# loudly on `/media/` instead of silently working via this fallback.
# Real production media serving is nginx (`deploy/nginx.conf`, local-disk
# storage) or S3/CloudFront (`deploy/S3_CLOUDFRONT_SETUP.md`, when
# `USE_S3_STORAGE=true` — that path never even reaches this route, since
# `default_storage.url()` already points straight at S3/CloudFront).
if settings.SERVE_MEDIA_VIA_DJANGO:
    urlpatterns += [
        re_path(r"^media/(?P<path>.*)$", serve_media_with_range, name="serve-media"),
    ]