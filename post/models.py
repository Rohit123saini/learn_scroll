# post/models.py
import uuid
from datetime import timedelta
from django.db import models, transaction
from django.contrib.auth import get_user_model
from django.contrib.postgres.indexes import GinIndex
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.conf import settings
from django.utils import timezone
User = get_user_model()


class Post(models.Model):
    CATEGORY_CHOICES = [
        ('general', 'General'),
        ('tech', 'Technology'),
        ('jobs', 'Jobs'),
        ('news', 'News'),
        ('education', 'Education'),
        ('business', 'Business'),
        ('entertainment', 'Entertainment'),
        ('sports', 'Sports'),
        ('lifestyle', 'Lifestyle'),
        ('other', 'Other'),
    ]

    POST_TYPE_CHOICES = [
        ('text', 'Text'),
        ('image', 'Image'),
        ('video', 'Video'),
        ('document', 'Document'),
        ('poll', 'Poll'),
        ('article', 'Article'),
        ('carousel', 'Carousel'),
        ('link', 'Link'),
        ('repost', 'Repost'),
        # TASK G6 (growth_and_feature_tasks.md) — "Ask a doubt" post: a
        # student posts a question (in `content`, same as a text post) and
        # others/teachers answer inline via the `PostAnswer` model below,
        # with one answer pinnable as the best answer. Deliberately its
        # own post_type (not "text" + a flag) so feed ranking/filtering
        # and the composer's icon-toggle row can treat it as a distinct
        # card type, same as 'poll' already is.
        ('doubt', 'Doubt'),
    ]

    # P1 — audience. `connections` is the "Followers" option in the composer;
    # `close_friends` reuses the owner's CloseFriend list (same rule as stories);
    # `class_campus` = people who share a classroom or campus with the author
    # (see post/audience.py). Anything other than `public` never appears in
    # hashtag / explore / reels discovery.
    VISIBILITY_CHOICES = [
        ('public', 'Public'),
        ('connections', 'Followers'),
        ('close_friends', 'Close friends'),
        ('class_campus', 'My class / campus'),
        ('private', 'Private'),
    ]

    # P1 — what the composer's type chip created. Separate from `post_type`
    # (which says what MEDIA a post carries: text/image/video/document/...) so the
    # feed can tell a lecture-notes resource from a plain PDF post.
    KIND_POST = 'post'
    KIND_REEL = 'reel'
    KIND_DOUBT = 'doubt'
    KIND_POLL = 'poll'
    KIND_RESOURCE = 'resource'
    KIND_CHOICES = [
        (KIND_POST, 'Post'),
        (KIND_REEL, 'Reel'),
        (KIND_DOUBT, 'Doubt'),
        (KIND_POLL, 'Poll'),
        (KIND_RESOURCE, 'Notes / Resource'),
    ]

    MODERATION_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('flagged', 'Flagged'),
        # copyrights app (copyrights/services.py): hidden by a copyright claim.
        # Feeds only show 'approved', so both are invisible to everyone else.
        ('copyright_hold', 'Copyright - on hold'),
        ('copyright_removed', 'Copyright - removed'),
        ('legal_removed', 'Removed by legal order'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='posts')

    # Content
    content = models.TextField(blank=True, null=True)
    title = models.CharField(max_length=300, blank=True, null=True)
    category = models.CharField(max_length=100, choices=CATEGORY_CHOICES, default='general',
                                db_index=True)  # 🔥 Category field
    # TASK 5 — subcategory the composer (new_post.dart) picks from the
    # dynamic `category_subcategory_map` served by the taxonomy endpoint
    # (see api_service.dart's getCategoryTaxonomy()). Deliberately a plain
    # CharField, NOT `choices=` — the valid (category -> [subcategory,...])
    # set lives in that taxonomy source and can grow without a migration;
    # hardcoding a choices list here would fight that and go stale. Cross-
    # field validation ("is this subcategory actually valid for this
    # category") belongs in PostCreateSerializer.validate(), not the model.
    subcategory = models.CharField(max_length=100, blank=True, null=True, db_index=True)

    # Post Type
    post_type = models.CharField(max_length=20, choices=POST_TYPE_CHOICES, default='text', db_index=True)
    visibility = models.CharField(max_length=20, choices=VISIBILITY_CHOICES, default='public')

    # P1 — composer type + knowledge-sharing ("Notes / Resource") fields.
    kind = models.CharField(max_length=12, choices=KIND_CHOICES, default=KIND_POST, db_index=True)
    # Resource posts: what it is about. `topics` is the separate "Topics" field
    # of the composer (Physics, Class 10, ...) - NOT hashtags; searchable via
    # ?topic= on GET /post/resources/. Both are plain text, never choices.
    subject = models.CharField(max_length=100, blank=True, default='', db_index=True)
    level = models.CharField(max_length=60, blank=True, default='')  # "Class 10", "B.Sc 1st year", ...
    topics = models.JSONField(default=list, blank=True)
    # Resource posts: may viewers (other than the author) download the files?
    # Enforced by GET /post/attachments/<id>/download/ (the in-app viewer still
    # shows the file). Always True for non-resource posts (no effect there).
    download_allowed = models.BooleanField(default=True)
    # Resource posts: stamp downloads (PDF / image) and the in-app viewer with a watermark.
    # none | author (author's @username) | viewer (the downloader's @username + date). See post/resource_watermark.py.
    WATERMARK_NONE = 'none'
    WATERMARK_AUTHOR = 'author'
    WATERMARK_VIEWER = 'viewer'
    WATERMARK_CHOICES = [(WATERMARK_NONE, 'None'), (WATERMARK_AUTHOR, 'Author name'), (WATERMARK_VIEWER, 'Viewer name')]
    watermark_mode = models.CharField(max_length=8, choices=WATERMARK_CHOICES, default=WATERMARK_NONE)

    # ---------------------------------------------------------------------
    # REPOST — Instagram/Twitter-style repost/retweet.
    #
    # A repost is a REAL Post row (`post_type='repost'`) with almost every
    # other field left at its default (no content/media of its own is
    # required) — `original_post` is what makes it a repost, pointing at
    # the post being reposted. Self-FK so `Post.objects.filter(...)` and
    # every existing feed query keep working unchanged; a repost shows up
    # in feeds/profile lists exactly like any other post (newest-first),
    # it just renders as a small "Reposted by X" wrapper around an
    # embedded preview of `original_post` (see PostListSerializer's
    # `get_original_post` in serializers.py).
    #
    # SET_NULL (not CASCADE): if the original post is later hard-deleted
    # (soft-delete via `is_deleted` doesn't touch this at all — the repost
    # keeps pointing at it and the serializer/frontend decide how to show
    # a since-removed original), the repost row itself should survive as
    # a plain post rather than vanishing along with it.
    #
    # Chain-flattening (repost-of-a-repost points at the ROOT original,
    # not at the intermediate repost) is enforced in the view
    # (`PostRepostAPIView`), not here — same "business logic doesn't
    # belong in the model" call the rest of this file already makes
    # (see `subcategory`'s comment above) — but the `null=True` here is
    # what makes an ordinary (non-repost) post the common, unconstrained
    # case.
    original_post = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='reposts',
    )
    # Twitter's "Quote Tweet" equivalent — optional text the reposting
    # user adds on top of the embedded original. Blank/null = a plain
    # "quick repost" (one-tap, no added text), exactly like a retweet.
    repost_caption = models.TextField(blank=True, null=True)

    # SHARE A CLASS / TEST SERIES TO THE FEED (see shared_items.py). Opaque
    # reference only - resolved lazily at read time, never a hard FK across
    # apps. Blank on every ordinary post.
    shared_type = models.CharField(max_length=12, blank=True, default='')
    shared_id = models.CharField(max_length=64, blank=True, default='')

    # Engagement Counters - Denormalized for performance
    likes_count = models.PositiveIntegerField(default=0)
    comments_count = models.PositiveIntegerField(default=0)
    shares_count = models.PositiveIntegerField(default=0)
    views_count = models.BigIntegerField(default=0)
    saves_count = models.PositiveIntegerField(default=0)
    # 🔥 NEW — Instagram/Twitter-style Repost feature.
    reposts_count = models.PositiveIntegerField(default=0)
    # TASK G4 (growth_and_feature_tasks.md) — denormalized average watch
    # completion (0.0-1.0) across every PostView row that has watch-progress
    # data (see PostView.watch_seconds below + PostVideoProgressAPIView in
    # views.py). Only ever set for post_type='video' — stays at the 0.0
    # default for every other post type, which is exactly the "no boost"
    # value the feed ranking needs. Kept in sync by
    # update_video_completion_rate (signal, further down this file), same
    # denormalized-counter pattern as shares_count/saves_count above.
    video_completion_rate = models.FloatField(default=0.0)
    # WATCH-TIME RANKING (feed task, Part 1) - two more denormalised numbers
    # over the same PostView rows as video_completion_rate, kept in sync by
    # the same signal: how many viewers have watch data, and their average
    # ABSOLUTE watch time in seconds. Together with the rate they let
    # feed_mix.video_watch_boost() weigh a completion rate by how much
    # evidence backs it (1 viewer != 200 viewers) and reward long watches
    # (finishing a 5 s loop != finishing a 10 min lesson).
    video_watch_count = models.PositiveIntegerField(default=0)
    video_avg_watch_seconds = models.FloatField(default=0.0)
    # TASK G6 — denormalized count of PostAnswer rows, same pattern as
    # shares_count/saves_count above. Only meaningful for post_type='doubt'
    # but harmless (stays 0) on every other post type. Kept in sync by
    # sync_post_answers_count (signal, further down this file).
    answers_count = models.PositiveIntegerField(default=0)

    like_count = models.PositiveIntegerField(default=0)
    confuse_count = models.PositiveIntegerField(default=0)
    wrong_count = models.PositiveIntegerField(default=0)
    imp_count = models.PositiveIntegerField(default=0)
    explain_count = models.PositiveIntegerField(default=0)
    # Flags
    is_edited = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False, db_index=True)
    is_pinned = models.BooleanField(default=False)
    # ARCHIVE (feed card menu): the owner hides a post from everyone (feed, profile grid, explore,
    # hashtags, reels, saved, tagged tab, direct link) WITHOUT deleting it - unlike `is_deleted` it can
    # be restored with DELETE /post/<id>/archive/. Likes/comments/views are kept as they were.
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(blank=True, null=True)
    is_comments_disabled = models.BooleanField(default=False)
    # Round 5 (post options) - WHO may comment when comments are on: 'everyone' | 'followers' (author's accepted
    # followers + co-authors). `is_comments_disabled` above is the "comments off" switch and wins over this.
    COMMENTS_EVERYONE = 'everyone'
    COMMENTS_FOLLOWERS = 'followers'
    COMMENTS_LIMIT_CHOICES = [(COMMENTS_EVERYONE, 'Everyone'), (COMMENTS_FOLLOWERS, 'Followers only')]
    comments_limit = models.CharField(max_length=12, choices=COMMENTS_LIMIT_CHOICES, default=COMMENTS_EVERYONE)
    # S4b - may other people repost / send this post in chat (`allow_reshare`) and save it (`allow_save`)? The author is
    # never limited. Defaults come from the author's account-level privacy defaults when the composer sends nothing
    # (user_profile/privacy.py::content_defaults_for). Enforced in post/post_options.py (can_reshare_post / can_save_post).
    allow_reshare = models.BooleanField(default=True)
    allow_save = models.BooleanField(default=True)
    # Round 5 - set whenever the caption / title / hashtags are changed after publishing (the "Edited" mark's time).
    edited_at = models.DateTimeField(blank=True, null=True)
    # Round 5 - the time zone label the author scheduled in (display only, e.g. "IST (UTC+05:30)"); `published_at` is UTC.
    scheduled_timezone = models.CharField(max_length=64, blank=True, default='')
    is_sensitive = models.BooleanField(default=False)

    # Moderation
    moderation_status = models.CharField(max_length=20, choices=MODERATION_CHOICES, default='approved')
    reported_count = models.PositiveIntegerField(default=0)

    # SEO & Search
    slug = models.SlugField(max_length=500, unique=True, blank=True, null=True)
    hashtags = models.JSONField(default=list, blank=True)  # ['flutter', 'tech']
    mentioned_user_ids = models.JSONField(default=list, blank=True)  # [uuid1, uuid2]

    # Metadata - Poll options, article data, etc
    metadata = models.JSONField(default=dict, blank=True)
    location = models.JSONField(default=dict, blank=True)  # {"city": "Meerut", "country": "India"}

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(blank=True, null=True)
    # TASK 5 — this field already existed ("For scheduled posts") but
    # nothing ever set it; new_post.dart's `scheduledAt` now maps straight
    # onto it (PostCreateSerializer's `scheduled_at` field has
    # `source='published_at'`), so no new datetime column was needed.
    published_at = models.DateTimeField(blank=True, null=True, db_index=True)
    # 🔥 NEW — explicit flag rather than inferring "scheduled" from
    # `published_at > now()` at query time. A boolean + index lets the
    # publish sweep (Celery beat task, see PostCreateSerializer's
    # docstring note) do `filter(is_scheduled=True, published_at__lte=now)`
    # directly, and lets feed queries do `exclude(is_scheduled=True)`
    # without recomputing "is this in the future" per row.
    is_scheduled = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = 'posts'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at', 'is_deleted']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['category', '-created_at']),
            models.Index(fields=['category', 'subcategory', '-created_at']),
            models.Index(fields=['-likes_count', '-created_at']),  # For trending
            models.Index(fields=['is_scheduled', 'published_at']),  # For the publish sweep
            models.Index(fields=['original_post', '-created_at']),  # For reposts_count / "who reposted this"
            # P-F7 - unified search: typo-tolerant (pg_trgm) title match; ignored on non-Postgres backends.
            GinIndex(fields=['title'], name='post_title_trgm', opclasses=['gin_trgm_ops']),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.category} - {self.created_at}"

    @property
    def is_due_for_publish(self):
        """True once a scheduled post's time has arrived. Used by the
        publish sweep task and can double as a defensive check anywhere a
        scheduled Post might otherwise leak into a feed query that forgot
        to filter on `is_scheduled`."""
        return self.is_scheduled and bool(self.published_at) and self.published_at <= timezone.now()

    # ⚠️ FOLLOW-UP REQUIRED OUTSIDE THIS FILE (not in scope of the files
    # provided for this task, flagging so it isn't silently forgotten):
    #   1. Every feed/listing queryset in views.py (home feed, category
    #      feed, profile feed, hashtag feed, trending, ...) needs
    #      `.exclude(is_scheduled=True)` — or, equivalently,
    #      `.filter(Q(is_scheduled=False) | Q(published_at__lte=now()))` —
    #      or a scheduled post is publicly visible the instant it's
    #      created, which defeats the whole feature. The post's own
    #      author should still be able to see/edit it before publish
    #      (e.g. a "scheduled" tab), so this is a feed-level exclusion,
    #      not a moderation_status-style global one.
    #   2. A periodic task (Celery beat, same pattern as
    #      `post.tasks.generate_video_thumbnail`) should run
    #      `Post.objects.filter(is_scheduled=True, published_at__lte=now())
    #      .update(is_scheduled=False)` on a short interval (e.g. every
    #      minute) so posts actually go live at their scheduled time
    #      instead of just being *eligible* to per (1) forever.


class PostMedia(models.Model):
    MEDIA_TYPE_CHOICES = [
        ('image', 'Image'),
        ('video', 'Video'),
        ('document', 'Document'),
        ('audio', 'Audio'),
        ('gif', 'GIF'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='media')

    # File Info - 🔥 Har type ki file
    media_type = models.CharField(max_length=20, choices=MEDIA_TYPE_CHOICES)
    file = models.FileField(
        upload_to='posts/%Y/%m/%d/',
        validators=[FileExtensionValidator(
            allowed_extensions=['jpg', 'jpeg', 'png', 'gif', 'mp4', 'mov', 'avi',
                                'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
                                'mp3', 'wav', 'zip', 'txt']
        )]
    )
    thumbnail = models.ImageField(upload_to='posts/thumbnails/%Y/%m/%d/', blank=True, null=True)
    # C4-BE — responsive image sizes, generated off-request by
    # `post.tasks.generate_image_variants` (Celery) for image/gif uploads.
    #   thumb_320  -> max 320px wide (grids, notification previews, chat cards)
    #   medium_720 -> max 720px wide (feed cards)
    # The ORIGINAL stays in `file` untouched (full-screen viewer / download).
    # Both are NULL until the task has run (and stay NULL for non-images), so
    # every reader must treat them as optional and fall back — see
    # PostMediaSerializer. `medium_720` also stays NULL on purpose for animated
    # images (GIF/animated WebP): a still JPEG would replace the animation, so
    # those keep serving the original `file` and only get a still `thumb_320`.
    # Deliberately separate from `thumbnail` above: that one is the VIDEO poster
    # frame (480px, made by generate_video_thumbnail) and other code already
    # reads it (notification previews, share_post_to_conversation) — reusing it
    # for a second meaning would make both features fight over one column.
    thumb_320 = models.ImageField(upload_to='posts/thumb_320/%Y/%m/%d/', max_length=255, blank=True, null=True)
    medium_720 = models.ImageField(upload_to='posts/medium_720/%Y/%m/%d/', max_length=255, blank=True, null=True)
    # TASK 5 — per-attachment caption. new_post.dart collects one caption
    # per attachment (`attachment.caption`) and api_service.dart sends the
    # whole list as `media_captions` (JSON-encoded, same order/index as
    # `media_files`) — this is where PostCreateSerializer.create() now
    # writes caption[i] onto media row i.
    caption = models.CharField(max_length=500, blank=True, default='')
    # P1 — accessibility description of the image/video (screen readers).
    alt_text = models.CharField(max_length=500, blank=True, default='')
    file_name = models.CharField(max_length=500)
    file_size_bytes = models.BigIntegerField()
    mime_type = models.CharField(max_length=100)

    # Media Specific
    width = models.PositiveIntegerField(blank=True, null=True)
    height = models.PositiveIntegerField(blank=True, null=True)
    duration_seconds = models.PositiveIntegerField(blank=True, null=True)  # For video/audio
    page_count = models.PositiveIntegerField(blank=True, null=True)  # For PDFs

    # CDN & Storage
    cdn_url = models.URLField(blank=True, null=True)
    # BlurHash string (~28 chars) for the image placeholder. Column already
    # existed (ReelSerializer reads it for videos); C4-BE now fills it for
    # images in `generate_image_variants`. Exposed to the API as `blurhash`.
    blur_hash = models.CharField(max_length=100, blank=True, null=True)

    # Order in carousel
    display_order = models.PositiveIntegerField(default=0)

    # Metadata
    metadata = models.JSONField(default=dict, blank=True)

    # V1 — video playback pipeline (post.tasks.process_video_for_streaming).
    #   ready       playable as-is (default: every pre-existing row, images, docs)
    #   pending     new video uploaded, background job not started yet
    #   processing  background job is converting it to H.264/AAC + faststart
    #   failed      job could not convert it; the ORIGINAL file is still served,
    #               the client just tries to play it as-is
    # Clients use it only to word the error text ("video process ho rahi hai"
    # vs "video chal nahi paayi") — they never block playback on it.
    PROCESSING_READY = 'ready'
    PROCESSING_PENDING = 'pending'
    PROCESSING_RUNNING = 'processing'
    PROCESSING_FAILED = 'failed'
    PROCESSING_STATUS_CHOICES = [
        (PROCESSING_READY, 'Ready'),
        (PROCESSING_PENDING, 'Pending'),
        (PROCESSING_RUNNING, 'Processing'),
        (PROCESSING_FAILED, 'Failed'),
    ]
    processing_status = models.CharField(
        max_length=12, choices=PROCESSING_STATUS_CHOICES, default=PROCESSING_READY,
    )

    # HLS adaptive rendition (360p / 540p / 720p), built AFTER the MP4 is ready by
    # post.tasks.build_hls_rendition. Storage name of the master playlist
    # (`posts/hls/<media_id>/<run>/master.m3u8`); '' = none (old rows, failed / skipped / disabled).
    # The MP4 in `file` is always kept as the fallback; the API exposes the playlist as `hls_url`.
    hls_playlist = models.CharField(max_length=500, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_media'
        ordering = ['display_order', 'created_at']
        indexes = [
            models.Index(fields=['post', 'display_order']),
        ]

    def __str__(self):
        return f"{self.media_type} - {self.file_name}"


# C4-BE — kick off image-variant generation for every new image/gif PostMedia.
#
# A receiver (not a call inside PostCreateSerializer.create()) on purpose: a
# PostMedia row is created from more than one place — the normal multipart
# create AND the chunked-upload `complete` path in views.py — and a signal is
# the one hook that sees all of them, including any future one. Same thin
# "enqueue and return" shape as post.signals.queue_video_thumbnail_on_create
# (that file wasn't part of this task, so this lives here; it can be moved
# there unchanged — only the @receiver line and the imports matter).
#
# - `transaction.on_commit`: the worker must not run before the row (and its
#   file) is committed, or `PostMedia.objects.get()` races the upload's own
#   transaction and logs a bogus "no longer exists".
# - The `.delay()` is wrapped: a Celery broker outage must NEVER turn a
#   successful upload into a 500. The row simply stays without variants (the
#   serializer falls back to the original file) until
#   `manage.py backfill_image_variants` picks it up.
@receiver(post_save, sender=PostMedia)
def queue_image_variants_on_create(sender, instance, created, raw=False, **kwargs):
    if raw or not created or instance.media_type not in ('image', 'gif'):
        return
    media_id = str(instance.id)

    def _enqueue():
        try:
            from .tasks import generate_image_variants
            generate_image_variants.delay(media_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "could not enqueue generate_image_variants for PostMedia %s "
                "(run backfill_image_variants to catch up)", media_id,
            )

    transaction.on_commit(_enqueue)


# ---------------------------------------------------------------------------
# TASK 5 — real poll support.
#
# new_post.dart already has full poll-composer UI (2-4 options, dedupe/
# empty checks) and sends it as `poll_options`: a JSON list of
# `{"text": ..., "votes": 0}` dicts. `Post.metadata` (a bare JSONField)
# could technically hold this, but that would mean no per-option vote
# counting, no "did this user already vote" enforcement, and no way to
# query/aggregate polls — all real requirements once voting is wired up,
# not just storage for what the composer submits once at create time. A
# proper one-poll-per-post + many-options + many-votes shape, same
# id/FK/db_table conventions as the rest of this file, gets us all three
# without a follow-up migration the first time voting is built.
# ---------------------------------------------------------------------------
class PostPoll(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.OneToOneField(Post, on_delete=models.CASCADE, related_name='poll')
    # Not currently sent by the client (new_post.dart has no expiry UI
    # yet) — nullable/optional so it's ready without blocking on that.
    expires_at = models.DateTimeField(blank=True, null=True)
    # Denormalized number of PEOPLE who voted (not vote rows: on a
    # multiple-choice poll one person owns several rows), same pattern as
    # Post.shares_count/saves_count above — kept in sync by
    # sync_poll_vote_counts() below rather than aggregated on every read.
    # For a single-choice poll this is identical to the old "sum of options".
    total_votes_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    # ---- POLL + QUIZ upgrade (migration 0028) ---------------------------
    RESULTS_ALWAYS = 'always'          # everyone sees live numbers
    RESULTS_AFTER_VOTE = 'after_vote'  # a viewer sees them once they voted (old app behaviour)
    RESULTS_AFTER_END = 'after_end'    # nobody but the creator until the poll ends
    RESULTS_CHOICES = [
        (RESULTS_ALWAYS, 'Always'),
        (RESULTS_AFTER_VOTE, 'After voting'),
        (RESULTS_AFTER_END, 'After the poll ends'),
    ]
    MIN_OPTIONS = 2
    MAX_OPTIONS = 6
    MIN_DURATION_HOURS = 1
    MAX_DURATION_HOURS = 24 * 7

    # Pick more than one option? `max_choices` caps how many (1 when single).
    allow_multiple = models.BooleanField(default=False)
    max_choices = models.PositiveSmallIntegerField(default=1)
    # Anonymous: nobody (not even the creator) is ever shown who picked what.
    # The vote rows still exist (they ARE the double-vote guard) but no API
    # and no CSV export ever returns the voter of an anonymous poll.
    is_anonymous = models.BooleanField(default=False)
    results_visibility = models.CharField(
        max_length=12, choices=RESULTS_CHOICES, default=RESULTS_AFTER_VOTE)
    # Quiz mode: options carry `is_correct`, a vote is final and graded
    # instantly, `explanation` is shown with the verdict.
    is_quiz = models.BooleanField(default=False)
    explanation = models.CharField(max_length=500, blank=True, default='')

    class Meta:
        db_table = 'post_polls'

    def __str__(self):
        return f"Poll on {self.post_id}"

    @property
    def is_expired(self):
        return bool(self.expires_at) and timezone.now() >= self.expires_at


class PostPollOption(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    poll = models.ForeignKey(PostPoll, on_delete=models.CASCADE, related_name='options')
    # Blank is allowed when the option is an image (an option needs text OR image).
    text = models.CharField(max_length=200, blank=True, default='')
    image = models.ImageField(upload_to='post/poll_options/', blank=True, null=True)
    # Quiz mode only; never sent to a viewer before they have answered.
    is_correct = models.BooleanField(default=False)
    votes_count = models.PositiveIntegerField(default=0)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'post_poll_options'
        ordering = ['display_order']
        indexes = [
            models.Index(fields=['poll', 'display_order']),
        ]

    def __str__(self):
        return self.text


class PostPollVote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    poll = models.ForeignKey(PostPoll, on_delete=models.CASCADE, related_name='votes')
    option = models.ForeignKey(PostPollOption, on_delete=models.CASCADE, related_name='votes')
    # 🔥 FIX (fields.E304/E305) — was related_name='poll_votes', which
    # collided with `message.PollVote.user`'s own related_name='poll_votes'
    # (that model votes on chat/group polls in the `message` app; this one
    # votes on post polls here in `post`). Both are plain FKs straight to
    # `User`, and Django requires reverse-accessor names to be unique per
    # target model across the WHOLE project — not just within one app — so
    # two unrelated apps both calling their thing "poll_votes" broke
    # `runserver`/`makemigrations`/`migrate` outright (fields.E304/E305).
    # `message.PollVote` is the older, already-wired-up feature (real
    # voting endpoint exists via MessageViewSet.poll_vote), so it keeps
    # `poll_votes`; this one is renamed instead. If any code already
    # (or in future) calls `user.poll_votes` expecting POST poll votes
    # specifically, use `user.post_poll_votes`.
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='post_poll_votes')
    # Quiz polls: graded when the vote is cast (the same value on every row
    # of one person's answer). NULL for a normal poll.
    is_correct = models.BooleanField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_poll_votes'
        # DOUBLE-VOTE GUARD (DB level): a person can hold at most ONE row per
        # option. A single-choice poll additionally allows only one row per
        # person in total — that part (and `max_choices`) is enforced in
        # post/polls.py::cast_vote() under a row lock on the poll, because a
        # plain unique constraint cannot express "at most N rows".
        # (This used to be unique_together = ['poll', 'user'], which made
        # multiple-choice impossible.)
        constraints = [
            models.UniqueConstraint(fields=['poll', 'user', 'option'], name='uniq_poll_user_option'),
        ]
        indexes = [
            models.Index(fields=['option']),
            models.Index(fields=['poll', 'user'], name='pollvote_poll_user_idx'),
            models.Index(fields=['poll', 'is_correct', 'created_at'], name='pollvote_poll_correct_idx'),
        ]


def recount_poll_votes(poll_id):
    """Recompute the denormalized counters from the real rows (see
    sync_poll_vote_counts). One grouped query for the options + one for the
    voters, instead of a COUNT per option.

    option.votes_count = people who picked that option;
    poll.total_votes_count = distinct people who voted (the % denominator)."""
    from django.db.models import Count

    per_option = dict(
        PostPollVote.objects.filter(poll_id=poll_id)
        .values_list('option_id').annotate(n=Count('id')).values_list('option_id', 'n')
    )
    for option_id in PostPollOption.objects.filter(poll_id=poll_id).values_list('id', flat=True):
        PostPollOption.objects.filter(id=option_id).update(votes_count=per_option.get(option_id, 0))
    voters = PostPollVote.objects.filter(poll_id=poll_id).values('user_id').distinct().count()
    PostPoll.objects.filter(id=poll_id).update(total_votes_count=voters)


@receiver(post_save, sender=PostPollVote)
@receiver(post_delete, sender=PostPollVote)
def sync_poll_vote_counts(sender, instance, **kwargs):
    """Same shape as update_shares_count/update_saves_count further down:
    recompute on every vote/unvote/re-vote rather than +1/-1 in the view
    (which would double-count on a changed vote unless done very carefully)."""
    recount_poll_votes(instance.poll_id)


class PostPollQuestionLink(models.Model):
    """A poll the creator turned into a Test Series question. `question_id`
    is a plain UUID (not an FK) so `post` never imports `testseries` models;
    the unique (poll, series) pair is what stops converting one poll into the
    same series twice."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    poll = models.ForeignKey(PostPoll, on_delete=models.CASCADE, related_name='question_links')
    series_id = models.UUIDField(db_index=True)
    question_id = models.UUIDField()
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='poll_question_links')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_poll_question_links'
        constraints = [
            models.UniqueConstraint(fields=['poll', 'series_id'], name='uniq_poll_series_link'),
        ]


# ---------------------------------------------------------------------------
# TASK G6 — "Ask a doubt" post type. A `Post` with post_type='doubt' holds
# the question in its own `content` field (nothing new needed there); this
# model holds the answers other students/teachers post underneath it, with
# `is_best_answer` letting the asker pin the one that actually solved it
# (rendered first in the answer list, badge in the UI).
# ---------------------------------------------------------------------------
class PostAnswer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='answers')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='post_answers')
    content = models.TextField()
    # Exactly one True per post at a time — enforced in the view
    # (PostAnswerMarkBestAPIView clears any previous pin before setting a
    # new one), not at the DB level, same "business logic doesn't belong
    # in the model" call as Post.subcategory's validation above.
    is_best_answer = models.BooleanField(default=False, db_index=True)
    likes_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'post_answers'
        # Best answer floats to the top, then most-liked, then
        # chronological — matches how the answer list is meant to read.
        ordering = ['-is_best_answer', '-likes_count', 'created_at']
        indexes = [
            models.Index(fields=['post', '-is_best_answer', 'created_at']),
        ]

    def __str__(self):
        return f"Answer by {self.user.username} on doubt {self.post_id}"


@receiver(post_save, sender=PostAnswer)
@receiver(post_delete, sender=PostAnswer)
def sync_post_answers_count(sender, instance, **kwargs):
    """Same denormalized-counter pattern as sync_poll_vote_counts above —
    recompute from the real rows on every create/delete."""
    Post.objects.filter(id=instance.post_id).update(
        answers_count=PostAnswer.objects.filter(post_id=instance.post_id).count()
    )


# ---------------------------------------------------------------------------
# DOUBT POST UPGRADE - the structured part of an "Ask a doubt" post and the richer answers.
#   PostDoubt            subject / class / exam / chapter, urgent tag, on-device OCR text of the question photo,
#                        optional coin bounty (held from the asker's wallet until a best answer is marked) and
#                        `solved_at` (set when the asker marks a best answer -> "Solved" badge).
#   PostAnswerStep       one step of a step-wise solution (text and/or a picture).
#   PostAnswerAttachment an image or a short video that explains the answer.
# The question text itself is still `Post.content` and the question photo is still a PostMedia.
# ---------------------------------------------------------------------------
class PostDoubt(models.Model):
    BOUNTY_NONE = 'none'
    BOUNTY_HELD = 'held'
    BOUNTY_PAID = 'paid'
    BOUNTY_REFUNDED = 'refunded'
    BOUNTY_STATUS_CHOICES = [
        (BOUNTY_NONE, 'No bounty'),
        (BOUNTY_HELD, 'Held until a best answer is chosen'),
        (BOUNTY_PAID, 'Paid to the best answer'),
        (BOUNTY_REFUNDED, 'Refunded to the asker'),
    ]
    MIN_BOUNTY = 5
    MAX_BOUNTY = 500

    post = models.OneToOneField(Post, on_delete=models.CASCADE, related_name='doubt_info')
    subject = models.CharField(max_length=80, blank=True, default='', db_index=True)
    class_level = models.CharField(max_length=40, blank=True, default='')
    exam = models.CharField(max_length=60, blank=True, default='')
    chapter = models.CharField(max_length=120, blank=True, default='')
    is_urgent = models.BooleanField(default=False, db_index=True)
    # Text read from the question photo on the phone (ML Kit). Searchable, used by "similar doubts".
    ocr_text = models.TextField(blank=True, default='')
    bounty_coins = models.PositiveIntegerField(default=0)
    bounty_status = models.CharField(max_length=10, choices=BOUNTY_STATUS_CHOICES, default=BOUNTY_NONE, db_index=True)
    bounty_paid_to = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='doubt_bounties_won')
    solved_at = models.DateTimeField(null=True, blank=True, db_index=True)
    teachers_notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_doubt_info'
        indexes = [
            models.Index(fields=['subject', '-created_at']),
            models.Index(fields=['is_urgent', '-created_at']),
        ]

    @property
    def is_solved(self):
        return self.solved_at is not None

    def __str__(self):
        return f"Doubt info of post {self.post_id}"


def _answer_step_upload_to(instance, filename):
    return f"post/answer_steps/{timezone.now():%Y/%m}/{uuid.uuid4().hex}_{filename[-60:]}"


def _answer_media_upload_to(instance, filename):
    return f"post/answer_media/{timezone.now():%Y/%m}/{uuid.uuid4().hex}_{filename[-60:]}"


class PostAnswerStep(models.Model):
    MAX_STEPS = 20
    MAX_TEXT = 2000

    answer = models.ForeignKey(PostAnswer, on_delete=models.CASCADE, related_name='steps')
    position = models.PositiveSmallIntegerField(default=0)
    text = models.TextField(blank=True, default='')
    image = models.ImageField(upload_to=_answer_step_upload_to, max_length=255, blank=True, null=True)

    class Meta:
        db_table = 'post_answer_steps'
        ordering = ['position']
        constraints = [
            models.UniqueConstraint(fields=['answer', 'position'], name='uniq_answer_step_position'),
        ]


class PostAnswerAttachment(models.Model):
    KIND_IMAGE = 'image'
    KIND_VIDEO = 'video'
    KIND_CHOICES = [(KIND_IMAGE, 'Image'), (KIND_VIDEO, 'Video')]
    MAX_IMAGES = 4
    MAX_VIDEOS = 1

    answer = models.ForeignKey(PostAnswer, on_delete=models.CASCADE, related_name='attachments')
    kind = models.CharField(max_length=5, choices=KIND_CHOICES)
    file = models.FileField(upload_to=_answer_media_upload_to, max_length=255)
    position = models.PositiveSmallIntegerField(default=0)
    file_size_bytes = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_answer_attachments'
        ordering = ['position']


class PostLike(models.Model):
    REACTION_CHOICES = [
        ('like', 'like'),  # 👍
        ('confuse', 'confuse'),  # 🤔
        ('wrong', 'wrong'),  # ❗
        ('imp', 'imp'),  # ⭐
        ('explain', 'explain'),  # 💡
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='likes')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='post_likes')
    reaction_type = models.CharField(max_length=20, choices=REACTION_CHOICES, default='like')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_likes'
        unique_together = ['post', 'user']  # Ek user ek hi baar
        indexes = [
            models.Index(fields=['post', '-created_at']),
            models.Index(fields=['user', '-created_at']),
        ]


class PostComment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='comments')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='post_comments')
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='replies')

    content = models.TextField(blank=True)
    likes_count = models.PositiveIntegerField(default=0)
    replies_count = models.PositiveIntegerField(default=0)

    is_edited = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False, db_index=True)
    is_pinned = models.BooleanField(default=False)
    # Reply with a reel: a REPLY (parent set) may carry one of the replier's OWN video posts. SET_NULL so deleting the
    # reel never deletes the reply; the serializer shows an "unavailable" card while the reel is hidden / archived.
    reply_reel = models.ForeignKey(
        'Post', on_delete=models.SET_NULL, null=True, blank=True, related_name='reel_replies',
    )
    # 🔥 NEW FIELD FOR HIDE
    is_hidden = models.BooleanField(default=False, db_index=True)
    hidden_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='hidden_comments')
    hidden_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'post_comments'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['post', '-created_at', 'is_hidden', 'is_deleted']),
            models.Index(fields=['parent', '-created_at']),
            # TASK G5 — supports CommentListAPIView's new `?sort=top`
            # (comment_view.py), which orders by -is_pinned, -likes_count,
            # -created_at. The existing index above only covers the
            # `?sort=newest` (chronological) path.
            models.Index(fields=['post', '-is_pinned', '-likes_count', '-created_at']),
        ]

class CommentMedia(models.Model):
    MEDIA_TYPES = (
        ('image', 'Image'),
        ('video', 'Video'),
        ('audio', 'Audio'),  # 🔥 voice, mp3
        ('document', 'Document'),
        ('other', 'Other'), # 🔥 koi bhi file
    )
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    comment = models.ForeignKey(PostComment, on_delete=models.CASCADE, related_name='media')
    media_type = models.CharField(max_length=20, choices=MEDIA_TYPES)
    file = models.FileField(upload_to='comment_media/%Y/%m/%d/') # 🔥 No validator = sab kuch acceptable
    file_name = models.CharField(max_length=500, blank=True)
    file_size = models.BigIntegerField(default=0)
    mime_type = models.CharField(max_length=150, blank=True) # 🔥 add kar diya

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'comment_media'

    def save(self, *args, **kwargs):
        if self.file and not self.file_name:
            self.file_name = self.file.name
        super().save(*args, **kwargs)

class PostShare(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='shares')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    share_text = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_shares'
        unique_together = ['post', 'user']


class PostView(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='views')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.TextField(blank=True, null=True)
    viewed_at = models.DateTimeField(auto_now_add=True)
    # TASK G4 — optional watch-progress pair, filled in by
    # PostVideoProgressAPIView (views.py) after this row already exists
    # (created via PostDetailAPIView's plain get_or_create, same as
    # before). Both null = an ordinary non-video "viewed it" row, exactly
    # like today. `watch_seconds` is the FURTHEST position the viewer
    # reached (never decreases — see the view for why), and
    # `video_duration_seconds` is a snapshot of the media's own duration
    # at watch time, so a later edit to the source media can't silently
    # skew an already-recorded ratio.
    watch_seconds = models.FloatField(blank=True, null=True)
    video_duration_seconds = models.FloatField(blank=True, null=True)
    # FEED "SEEN" SIGNAL — False only for rows created by the batch
    # `POST /post/feed/seen/` endpoint (PostSeenBatchAPIView), which marks a
    # post as *seen in the feed* without touching `Post.views_count`.
    # PostDetailAPIView flips it to True (and bumps views_count exactly
    # once) the first time the user actually opens the post. Default True
    # so every pre-existing row (all created via detail-open) and every
    # other code path keep their old "already counted" meaning.
    is_counted = models.BooleanField(default=True)

    class Meta:
        db_table = 'post_views'
        constraints = [
            # One row per (post, user). NULL users (SET_NULL after a
            # user is deleted) never collide with each other in SQL.
            models.UniqueConstraint(fields=['post', 'user'], name='uniq_postview_post_user'),
        ]
        indexes = [
            models.Index(fields=['post', '-viewed_at']),
            # "What has this user seen recently?" — feed de-dup lookups.
            models.Index(fields=['user', '-viewed_at'], name='post_views_user_viewed_idx'),
            # H10 retention: the daily prune / PII scrub filter on `viewed_at < cutoff` alone
            # (post.feed_retention) - without this it is a full scan of a table that grows per (post, user).
            models.Index(fields=['viewed_at'], name='post_views_viewed_at_idx'),
        ]


class PostSave(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='saved_by')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='saved_posts')
    collection_name = models.CharField(max_length=100, default='default')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_saves'
        unique_together = ['post', 'user']
        indexes = [
            models.Index(fields=['user', '-created_at']),
        ]


class SavedCollection(models.Model):
    """P2 - a named folder inside MY saved posts ("JEE formulas", "Revise later").

    `PostSave.collection_name` stays the link (a plain string, 'default' = no folder), so every existing save keeps
    working; this row only adds what a bare string cannot hold: the folder exists even when empty, and its PRIVACY.
    `is_private=True` (the default) = only I can see it. A public one can be listed by other people on my profile
    (post/profile_tabs.py: GET collections/of/<username>/), always through the normal post-visibility rules.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='saved_collections')
    name = models.CharField(max_length=40)
    is_private = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'post_saved_collections'
        ordering = ['created_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'name'], name='uniq_saved_collection_user_name'),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.name}"


# NOTE (fix, see post_app.md §14 issues #3 & #4):
#
# `update_likes_count` REMOVED — it duplicated `update_reaction_counts`
# (defined further below), which already recomputes `likes_count` as the
# sum of all per-reaction-type counts. Having both fire on every
# PostLike save/delete meant two separate COUNT queries + two separate
# UPDATE statements per like/unlike, always converging on the same
# number — pure redundancy, no correctness bug, just wasted DB round
# trips. `update_reaction_counts` was the superset (it also set
# like_count/confuse_count/wrong_count/imp_count/explain_count and the
# auto-flag-on-5-wrong logic) — see B-5 further down: that receiver has
# since been removed from this file too, in favor of the equivalent
# (and now sole) `post.signals.sync_post_reaction_counts`.
#
# `update_comments_count` REMOVED ENTIRELY — this one WAS a real
# correctness bug, not just redundant work. It recomputed
# `Post.comments_count` to the true absolute count on every
# PostComment save (create AND soft-delete). But `views.py`'s
# CommentCreateAPIView and `comment_view.py`'s CommentDeleteAPIView
# *also* separately do `F('comments_count') + 1` / `- 1` right after
# calling `.save()`/`.create()` — which fires this signal first. Net
# effect: every top-level comment create double-incremented
# comments_count by 1 extra, and every top-level delete
# double-decremented it by 1 extra. The count would silently drift
# further from reality with every create/delete.
#
# Removing the signal fixes both directions at once, because the
# manual F()-based updates already scattered through the views
# (create/+1, delete/-1) are correct on their own — same pattern
# already used for `replies_count`, which never had a signal and never
# had this bug. CommentHideAPIView is unaffected: it never relied on
# this signal (the signal doesn't know about `is_hidden`), it already
# does its own manual +1/-1 — see post_app.md §14 issue #3's note,
# which is still accurate for the hide/unhide path specifically.
@receiver(post_save, sender=PostShare)
@receiver(post_delete, sender=PostShare)
def update_shares_count(sender, instance, **kwargs):
    Post.objects.filter(id=instance.post_id).update(
        shares_count=PostShare.objects.filter(post_id=instance.post_id).count()
    )

@receiver(post_save, sender=PostSave)
@receiver(post_delete, sender=PostSave)
def update_saves_count(sender, instance, **kwargs):
    Post.objects.filter(id=instance.post_id).update(
        saves_count=PostSave.objects.filter(post_id=instance.post_id).count()
    )


# TASK G4 (growth_and_feature_tasks.md) — keeps Post.video_completion_rate
# in sync, same denormalized-counter shape as update_shares_count/
# update_saves_count just above. Deliberately a no-op for the vast
# majority of PostView writes: PostDetailAPIView's plain
# `PostView.objects.get_or_create(post=instance, user=request.user)` never
# touches watch_seconds/video_duration_seconds, so every non-video "viewed
# it" row still costs nothing extra here. Only PostVideoProgressAPIView
# (views.py) ever sets both fields, which is what actually triggers a
# recompute.
#
# Average is computed in Python rather than as a SQL division
# (watch_seconds / video_duration_seconds per row) on purpose — division
# expressions inside `.aggregate()`/`.annotate()` behave differently across
# the Postgres/SQLite split this project runs on (LearnScroll/settings.py),
# and this signal only ever touches the handful of PostView rows for one
# post, so the extra Python loop is cheap.
@receiver(post_save, sender=PostView)
def update_video_completion_rate(sender, instance, **kwargs):
    if instance.watch_seconds is None or not instance.video_duration_seconds:
        return
    progress_rows = PostView.objects.filter(
        post_id=instance.post_id,
        watch_seconds__isnull=False,
        video_duration_seconds__gt=0,
    ).values_list('watch_seconds', 'video_duration_seconds')
    # One pass gives all three numbers. Watch time is clamped to the row's own
    # duration snapshot (same guard as the ratio) so a bad client value can't
    # inflate the average.
    ratios, watched_list = [], []
    for watched, duration in progress_rows:
        if duration:
            ratios.append(min(watched / duration, 1.0))
            watched_list.append(min(watched, duration))
    if not ratios:
        return
    Post.objects.filter(id=instance.post_id, post_type='video').update(
        video_completion_rate=sum(ratios) / len(ratios),
        video_watch_count=len(ratios),
        video_avg_watch_seconds=sum(watched_list) / len(watched_list),
    )


# ---------------------------------------------------------------------------
# REPOST — keeps `Post.reposts_count` in sync, same shape as
# update_saves_count/update_shares_count just above (recompute the real
# count on every relevant save/delete rather than +1/-1 in the view).
#
# Fires on every `Post` save (not a separate model like PostShare/
# PostSave — a repost IS a Post row), but is a no-op unless
# `original_post_id` is set, so plain posts/edits pay for one extra
# cheap attribute check, not a query.
#
# `Post.objects.filter(...).update(...)` is a queryset UPDATE, which
# Django does NOT route back through `post_save` — so this can't
# recursively re-trigger itself when it writes `reposts_count` on the
# original post.
#
# `is_deleted=False` in the recompute is what makes a soft-deleted
# repost (PostDeleteAPIView flips `is_deleted` via `.save()`, which
# fires this same signal) stop counting toward the original's total,
# without needing a separate "on soft-delete" code path.
@receiver(post_save, sender=Post)
@receiver(post_delete, sender=Post)
def update_reposts_count(sender, instance, **kwargs):
    if not instance.original_post_id:
        return
    Post.objects.filter(id=instance.original_post_id).update(
        reposts_count=Post.objects.filter(original_post_id=instance.original_post_id, is_deleted=False).count()
    )


# ---------------------------------------------------------------------------
# TASK 27 — video thumbnail generation moved OUT of models.py.
#
# What used to live here (`auto_generate_video_thumbnail`, a `post_save`
# receiver on `PostMedia`) had two real problems, on top of not belonging
# in models.py in the first place (business logic mixed into the model
# module, an `ffmpeg-python` import pulled in just for this one signal):
#
#   1. It ran ffmpeg SYNCHRONOUSLY inside the `post_save` signal — i.e.
#      inline in whatever request created the `PostMedia` row
#      (`PostCreateAPIView.post()`). Every video upload's response time
#      included however long ffmpeg took to extract a frame.
#   2. On S3/GCS (`USE_S3_STORAGE=true`, task 26) it detected
#      `instance.file.path` raising `NotImplementedError`, logged a
#      warning, and just... gave up. Cloud-storage uploads never got a
#      thumbnail at all — not a crash, but not a fix either.
#
# Replaced by (see post/signals.py, post/tasks.py, post/services.py):
#   - `post.signals.queue_video_thumbnail_on_create` — the ONLY thing
#     still triggered by `PostMedia`'s `post_save`; it does nothing but
#     `generate_video_thumbnail.delay(instance.id)` and return.
#   - `post.tasks.generate_video_thumbnail` — the actual Celery task.
#     Runs off the request path, so ffmpeg's runtime no longer affects
#     upload latency.
#   - `post.services.download_storage_file_to_temp` /
#     `.generate_video_thumbnail_file` — read the source file via the
#     storage API (`.open()` + chunked read) instead of `.path`, which
#     works identically for local disk AND S3/GCS. This is what actually
#     fixes case 2 above instead of just logging around it: cloud-stored
#     videos now get real thumbnails too, not a permanent skip.
#   - Uses the `ffmpeg` CLI via `subprocess` (already a hard runtime
#     dependency either way — a server without the `ffmpeg` binary
#     installed couldn't run the old `ffmpeg-python` wrapper either)
#     instead of the `ffmpeg-python` package, so no extra pip dependency
#     was added for this fix.
# ---------------------------------------------------------------------------

# NOTE (fix, see B-5): `update_reaction_counts` REMOVED FROM HERE.
#
# It was a second `post_save`/`post_delete` receiver on `PostLike`,
# running alongside `post.signals.sync_post_reaction_counts` — both
# converged on the same numbers (not a correctness bug), but every
# like/unlike paid for two full aggregate-recompute + UPDATE round
# trips on the app's hottest write path. `sync_post_reaction_counts`
# is the superset (same per-type + total counts) and is the one kept;
# its 5+-wrong auto-flag logic now lives there too, folded into the
# same aggregate query and the same UPDATE instead of a second one —
# see post/signals.py.


import uuid
from django.conf import settings

class ChunkedUpload(models.Model):
    upload_id = models.CharField(max_length=100, unique=True)
    file_name = models.CharField(max_length=500)
    total_chunks = models.IntegerField()
    total_size = models.BigIntegerField()
    post_id = models.CharField(max_length=100)
    parent_id = models.CharField(max_length=100, null=True, blank=True)
    content = models.TextField(blank=True)
    # FIX: Yaha kabhi bhi 'authapp.User' mat likho, ye use karo
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    is_completed = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.upload_id} - {self.file_name}"


# TASK 3 — dedicated model backing the new /post/chunked/* routes
# (views.py's post_chunked_upload_init/_chunk/_complete/_status).
#
# `ChunkedUpload` above can't be reused as-is for this: it's shaped
# specifically for a comment attachment (`post_id`/`parent_id` pick the
# PostComment's target, `content` is the comment body) and has nowhere to
# park the post-creation fields (title, category, post_type, visibility,
# hashtags, location, ...) a chunked *post* upload needs to hold onto
# between `init` (when the client sends them once) and `complete` (when
# the Post row actually gets created — see PostCreateSerializer.create()
# in serializers.py for the equivalent non-chunked field set this
# mirrors).
#
# TASK 5 UPDATE: `subcategory`, `poll_options`, `is_scheduled`,
# `scheduled_at`, `media_caption` were previously left out here on
# purpose, because `Post` and `PostCreateSerializer` didn't persist them
# either — adding them here without a matching `Post` column would have
# silently dropped them a step later. Now that Task 5 added those as real
# columns (`Post.subcategory`, `Post.is_scheduled`, `Post.published_at`,
# the `PostPoll`/`PostPollOption` models, `PostMedia.caption`), this model
# is updated to match so the chunked path holds the same payload shape as
# the non-chunked one all the way through `complete()`. `media_caption` is
# singular here (one file per chunked upload) where the non-chunked path's
# `media_captions` is a list — matches the existing `media_type` (singular)
# vs. the non-chunked `media_types` (list) split already in this model.
class PostChunkedUpload(models.Model):
    upload_id = models.CharField(max_length=100, unique=True)
    file_name = models.CharField(max_length=500)
    total_chunks = models.IntegerField()
    total_size = models.BigIntegerField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    # Post-creation payload, captured once at init() and applied unchanged
    # at complete() — see PostCreateSerializer's Meta.fields for the
    # non-chunked equivalent of this set.
    title = models.CharField(max_length=300, blank=True, default='')
    content = models.TextField(blank=True, default='')
    category = models.CharField(max_length=100, default='general')
    subcategory = models.CharField(max_length=100, blank=True, null=True)
    post_type = models.CharField(max_length=20, default='video')
    visibility = models.CharField(max_length=20, default='public')
    hashtags = models.JSONField(default=list, blank=True)
    location = models.JSONField(default=dict, blank=True)
    media_caption = models.CharField(max_length=500, blank=True, default='')
    media_type = models.CharField(max_length=20, blank=True, default='')
    # Same shape client sends non-chunked: [{"text": ..., "votes": 0}, ...].
    # Applied at complete() the same way PostCreateSerializer.create()
    # applies it — see that method for the validation rules (2-4 options,
    # no duplicates) which run once, at init(), via
    # PostChunkedUploadInitSerializer reusing PostCreateSerializer's poll
    # validators rather than duplicating them.
    poll_options = models.JSONField(default=list, blank=True)
    is_scheduled = models.BooleanField(default=False)
    scheduled_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    is_completed = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.upload_id} - {self.file_name}"



class CommentLike(models.Model):
    REACTION_CHOICES = [
        ('like', 'like'),
        ('confuse', 'confuse'),
        ('wrong', 'wrong'),
        ('imp', 'imp'),
        ('explain', 'explain'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    comment = models.ForeignKey(PostComment, on_delete=models.CASCADE, related_name='likes')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='comment_likes')
    reaction_type = models.CharField(max_length=20, choices=REACTION_CHOICES, default='like')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'comment_likes'
        unique_together = ['comment', 'user']
        indexes = [
            models.Index(fields=['comment', '-created_at']),
        ]

@receiver(post_save, sender=CommentLike)
@receiver(post_delete, sender=CommentLike)
def update_comment_reaction_counts(sender, instance, **kwargs):
    from django.db.models import Count
    comment_id = instance.comment_id
    total = CommentLike.objects.filter(comment_id=comment_id).count()
    PostComment.objects.filter(id=comment_id).update(likes_count=total)

# ---------------------------------------------------------------------------
# Story / StoryView (checklist items 54/55/57/60).
#
# Added here for real — the previously-uploaded Tasks.py assumed this
# model already existed and it didn't. Follows the exact same pattern as
# the rest of this app: UUID pk, soft-delete (is_deleted/deleted_at),
# `-created_at` ordering, denormalized counter kept in sync by a signal
# (same shape as update_saves_count/update_shares_count above).
# ---------------------------------------------------------------------------
def default_story_expiry():
    return timezone.now() + timedelta(hours=24)


class Story(models.Model):
    MEDIA_TYPE_CHOICES = [
        ('image', 'Image'),
        ('video', 'Video'),
    ]

    # STORIES UPGRADE - PART 1 (Close Friends). Who may see this story.
    # `everyone` = the old behaviour (all followers). `close_friends` = only
    # people on the owner's CloseFriend list (+ the owner). Enforced in ONE
    # place - post/story_visibility.py - never re-implement it in a view.
    AUDIENCE_EVERYONE = 'everyone'
    AUDIENCE_CLOSE_FRIENDS = 'close_friends'
    # F3-AUD: two more audiences. `followers` = accepted followers only (a stranger who opens the id gets 404);
    # `class` = members of ONE tuition classroom (`audience_classroom_id`; teacher / active pass holders).
    AUDIENCE_FOLLOWERS = 'followers'
    AUDIENCE_CLASS = 'class'
    AUDIENCE_CHOICES = [
        (AUDIENCE_EVERYONE, 'Everyone'),
        (AUDIENCE_FOLLOWERS, 'Followers'),
        (AUDIENCE_CLOSE_FRIENDS, 'Close friends'),
        (AUDIENCE_CLASS, 'Class'),
    ]
    # Who may reply to / react on the story (enforced in post/story_visibility.can_reply_to_story).
    REPLY_EVERYONE = 'everyone'
    REPLY_FOLLOWERS = 'followers'
    REPLY_OFF = 'off'
    REPLY_CHOICES = [
        (REPLY_EVERYONE, 'Everyone'),
        (REPLY_FOLLOWERS, 'Followers'),
        (REPLY_OFF, 'Off'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='stories')

    media = models.FileField(
        upload_to='stories/%Y/%m/%d/',
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'gif', 'mp4', 'mov'])],
    )
    media_type = models.CharField(max_length=10, choices=MEDIA_TYPE_CHOICES, default='image')
    caption = models.CharField(max_length=300, blank=True)
    audience = models.CharField(
        max_length=20, choices=AUDIENCE_CHOICES, default=AUDIENCE_EVERYONE,
    )
    # F3-AUD. Only meaningful when audience == 'class' (a plain id, not an FK, so `post` does not depend on the
    # tuitionclass migrations). Custom "hide from" people live in StoryHiddenFrom below and work with every audience.
    audience_classroom_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    reply_setting = models.CharField(max_length=10, choices=REPLY_CHOICES, default=REPLY_EVERYONE)
    allow_reshare = models.BooleanField(default=True)
    allow_save = models.BooleanField(default=True)

    # STORIES UPGRADE - PART 3a (Music). One CC0 track picked from the
    # Freesound search proxy (`/post/music/search/`), stored as a small JSON
    # object so the viewer needs no second lookup:
    #   {"id", "title", "artist", "url", "duration", "start", "license"}
    # `url` is the track's public preview mp3 (host allow-listed), `start` is
    # the second the clip begins at. Rules: post/story_music.py. NULL = no music.
    music = models.JSONField(null=True, blank=True, default=None)

    # Denormalized — kept in sync by update_story_views_count below, same
    # pattern as Post.saves_count / Post.shares_count.
    views_count = models.PositiveIntegerField(default=0)

    is_deleted = models.BooleanField(default=False, db_index=True)
    # Set by copyrights/targets.py while a copyright claim / legal order keeps this story down. Needed
    # because a story that already ENDED (and lives on in a highlight) is is_deleted=True anyway, so
    # is_deleted alone cannot hide it; highlights.highlightable_stories_q() checks this flag.
    takedown_hidden = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    # NOT auto_now_add — this is a fixed future timestamp set once at
    # creation, not "now" at save time. Overridable per-story (e.g. a
    # shorter-lived story) by passing expires_at explicitly on create.
    expires_at = models.DateTimeField(default=default_story_expiry, db_index=True)

    class Meta:
        db_table = 'stories'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['expires_at', 'is_deleted']),
        ]

    def __str__(self):
        return f"{self.user.username} story - {self.created_at}"

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    def soft_delete(self):
        """Mirrors CommentDeleteAPIView's pattern in comment_view.py — a
        real DB write, not just an in-memory flag flip, so it's visible to
        any other query immediately."""
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'deleted_at'])


class StoryView(models.Model):
    """One row per (story, viewer) pair — mirrors PostView's job for posts,
    and is what auto_expiry/analytics can query without recomputing from
    scratch."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    story = models.ForeignKey(Story, on_delete=models.CASCADE, related_name='views')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='story_views')
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'story_views'
        unique_together = ['story', 'user']
        indexes = [
            models.Index(fields=['story', '-viewed_at']),
        ]


@receiver(post_save, sender=StoryView)
@receiver(post_delete, sender=StoryView)
def update_story_views_count(sender, instance, **kwargs):
    Story.objects.filter(id=instance.story_id).update(
        views_count=StoryView.objects.filter(story_id=instance.story_id).count()
    )


# ---------------------------------------------------------------------------
# StoryReaction — Instagram-style quick emoji reaction on a story. One row
# per (story, user), same `unique_together` shape as message.MessageReaction
# — tapping a new emoji replaces the old one (see StoryReactAPIView.post in
# views.py), tapping the SAME emoji again removes it, exactly one reaction
# per viewer per story at any time.
# ---------------------------------------------------------------------------
class StoryReaction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    story = models.ForeignKey(Story, on_delete=models.CASCADE, related_name='reactions')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='story_reactions')
    emoji = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'story_reactions'
        unique_together = ['story', 'user']
        indexes = [
            models.Index(fields=['story', '-created_at']),
        ]

    def __str__(self):
        return f"{self.user.username} reacted {self.emoji} on {self.story_id}"


# ---------------------------------------------------------------------------
# CloseFriend - STORIES UPGRADE, PART 1. `owner`'s private "Close Friends"
# list (Instagram's green-ring list). One row per (owner, friend). The list
# is ONE-WAY and private: the friend is never told they were added, and
# being on someone's list does not put them on yours.
#
# Used by post/story_visibility.py to decide who can see a
# `Story(audience='close_friends')`. Rows are removed automatically when
# either side blocks the other (post/signals.py).
# ---------------------------------------------------------------------------
class CloseFriend(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='close_friend_entries')
    friend = models.ForeignKey(User, on_delete=models.CASCADE, related_name='close_friend_of_entries')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'close_friends'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['owner', 'friend'], name='unique_close_friend'),
            models.CheckConstraint(
                condition=~models.Q(owner=models.F('friend')),
                name='close_friend_no_self',
            ),
        ]
        indexes = [
            models.Index(fields=['owner', '-created_at']),
        ]

    def __str__(self):
        return f"{self.owner_id} -> {self.friend_id} (close friend)"


class StoryHiddenFrom(models.Model):
    """F3-AUD "hide story from": one row = this person may NOT see this story, whatever its audience (the owner's
    own list always wins). Rows are written once at creation (post/story_visibility.py is the only reader)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    story = models.ForeignKey(Story, on_delete=models.CASCADE, related_name='hidden_from')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='stories_hidden_from_me')

    class Meta:
        db_table = 'story_hidden_from'
        constraints = [
            models.UniqueConstraint(fields=['story', 'user'], name='unique_story_hidden_from'),
        ]
        indexes = [
            models.Index(fields=['user', 'story'], name='story_hidden_user_idx'),
        ]

    def __str__(self):
        return f"{self.story_id} hidden from {self.user_id}"


# ---------------------------------------------------------------------------
# StorySticker - STORIES UPGRADE, PART 2a. ONE overlay placed on top of a
# story: an @mention, a link, and (Part 2b) a poll or a question. They all
# share the same placement shape (x/y/rotation/scale/z_index) so the client
# has a single renderer and a single drag-scale-rotate editor for every kind.
#
# Placement is normalised: x and y are the sticker's CENTRE as a fraction of
# the story canvas (0..1), so it lands in the same place on every screen size.
# Kind-specific content lives in `data` (link: url/label/host) or - for
# mentions, where we need to query "stories that mention me" and clean up on
# block - in the `mentioned_user` FK. Validation: post/story_stickers.py.
# ---------------------------------------------------------------------------
class StorySticker(models.Model):
    KIND_MENTION = 'mention'
    KIND_LINK = 'link'
    KIND_POLL = 'poll'
    KIND_QUESTION = 'question'
    # F3: more overlays. location / countdown / gif are display-only; quiz is a poll with ONE right answer;
    # add_yours is a chain prompt other people answer with their own story.
    KIND_LOCATION = 'location'
    KIND_COUNTDOWN = 'countdown'
    KIND_QUIZ = 'quiz'
    KIND_ADD_YOURS = 'add_yours'
    KIND_GIF = 'gif'
    # F3-AUD: a post / reel shared into a story as a tappable card (data = {post_id, post_kind}; the preview is
    # built at read time so a deleted / now-private post shows as unavailable instead of leaking).
    KIND_POST_CARD = 'post_card'
    KIND_CHOICES = [
        (KIND_MENTION, 'Mention'),
        (KIND_LINK, 'Link'),
        (KIND_POLL, 'Poll'),
        (KIND_QUESTION, 'Question'),
        (KIND_LOCATION, 'Location'),
        (KIND_COUNTDOWN, 'Countdown'),
        (KIND_QUIZ, 'Quiz'),
        (KIND_ADD_YOURS, 'Add yours'),
        (KIND_GIF, 'GIF'),
        (KIND_POST_CARD, 'Post card'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    story = models.ForeignKey(Story, on_delete=models.CASCADE, related_name='stickers')
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)

    x = models.FloatField(default=0.5)
    y = models.FloatField(default=0.5)
    rotation = models.FloatField(default=0.0)
    scale = models.FloatField(default=1.0)
    z_index = models.PositiveSmallIntegerField(default=0)

    mentioned_user = models.ForeignKey(
        User, on_delete=models.CASCADE, null=True, blank=True, related_name='story_mention_stickers',
    )
    data = models.JSONField(default=dict, blank=True)
    # F3 add_yours: the sticker that started the chain this one answers (NULL = a chain's first sticker, or any
    # other kind). Always the ROOT - answering an answer still points at the root - so the count is one query.
    chain_root = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='chain_members',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'story_stickers'
        ordering = ['z_index', 'created_at']
        indexes = [
            models.Index(fields=['story', 'kind'], name='story_stick_story_kind_idx'),
            models.Index(fields=['mentioned_user', '-created_at'], name='story_stick_mention_idx'),
        ]
        constraints = [
            # A person can be tagged once per story.
            models.UniqueConstraint(
                fields=['story', 'mentioned_user'],
                condition=models.Q(kind='mention'),
                name='unique_story_mention',
            ),
            # A mention sticker must name someone.
            models.CheckConstraint(
                condition=~models.Q(kind='mention') | models.Q(mentioned_user__isnull=False),
                name='story_mention_has_user',
            ),
        ]

    def __str__(self):
        return f"{self.kind} sticker on {self.story_id}"


# ---------------------------------------------------------------------------
# StoryPollVote / StoryQuestionAnswer - STORIES UPGRADE, PART 2b. What viewers
# send back to an interactive sticker. One row per (sticker, viewer): a poll
# vote is final (like Instagram), and a question gets one answer per viewer.
# Only the story owner can list them (post/story_sticker_responses.py); a
# viewer only ever sees aggregate poll numbers after they have voted.
# ---------------------------------------------------------------------------
class StoryPollVote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sticker = models.ForeignKey(StorySticker, on_delete=models.CASCADE, related_name='poll_votes')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='story_poll_votes')
    option_index = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'story_poll_votes'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['sticker', 'user'], name='unique_story_poll_vote'),
        ]
        indexes = [
            models.Index(fields=['sticker', 'option_index'], name='story_pollvote_opt_idx'),
        ]

    def __str__(self):
        return f"{self.user_id} voted {self.option_index} on {self.sticker_id}"


class StoryQuestionAnswer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sticker = models.ForeignKey(StorySticker, on_delete=models.CASCADE, related_name='answers')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='story_question_answers')
    text = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'story_question_answers'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['sticker', 'user'], name='unique_story_question_answer'),
        ]
        indexes = [
            models.Index(fields=['sticker', '-created_at'], name='story_qanswer_recent_idx'),
        ]

    def __str__(self):
        return f"{self.user_id} answered {self.sticker_id}"


class StoryQuizAnswer(models.Model):
    """F3 quiz sticker: one answer per (sticker, viewer), final. The right answer is `sticker.data['correct_index']`;
    it is only shown to the owner, or to a viewer once they have answered (post/story_sticker_responses.py)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sticker = models.ForeignKey(StorySticker, on_delete=models.CASCADE, related_name='quiz_answers')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='story_quiz_answers')
    option_index = models.PositiveSmallIntegerField()
    is_correct = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'story_quiz_answers'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['sticker', 'user'], name='unique_story_quiz_answer'),
        ]
        indexes = [
            models.Index(fields=['sticker', 'option_index'], name='story_quizans_opt_idx'),
        ]

    def __str__(self):
        return f"{self.user_id} answered {self.option_index} on {self.sticker_id}"


# ---------------------------------------------------------------------------
# Highlight / HighlightItem - STORIES UPGRADE, PART 3b. A named, permanent
# collection of the owner's past stories shown as a row on their profile.
#
# A highlight item POINTS AT the original Story row (no media copy). What keeps
# that row alive after the 24 h expiry is post/tasks.py: `hard_delete_ancient_
# stories` skips any story that is in a highlight. `expire_old_stories` still
# flags it `is_deleted` once it is over 24 h old - that only hides it from the
# live story tray; highlight reads use `highlightable_stories_q()`
# (post/highlights.py) instead of `is_deleted=False`.
# ---------------------------------------------------------------------------
class Highlight(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='highlights')
    title = models.CharField(max_length=30)
    # Which item's picture is the round cover. NULL = first photo item.
    cover_item = models.ForeignKey(
        'HighlightItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    # Cover crop (round cover only; the photo itself is never modified).
    # cover_x / cover_y = focus point in [-1, 1] (0,0 = centre), cover_zoom in
    # [1, 3]. Only meaningful while `cover_item` is set - reset otherwise.
    cover_zoom = models.FloatField(default=1.0)
    cover_x = models.FloatField(default=0.0)
    cover_y = models.FloatField(default=0.0)
    # P6 - the owner's own order of the round row on the profile (0 = first). Rows that were never
    # reordered all sit at 0, so they keep the old "most recently edited first" order (tie -> -updated_at).
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'story_highlights'
        ordering = ['position', '-updated_at']
        indexes = [
            models.Index(fields=['user', '-updated_at'], name='highlight_user_upd_idx'),
        ]

    def __str__(self):
        return f"{self.user_id} highlight '{self.title}'"


class HighlightItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    highlight = models.ForeignKey(Highlight, on_delete=models.CASCADE, related_name='items')
    story = models.ForeignKey(Story, on_delete=models.CASCADE, related_name='highlight_items')
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'story_highlight_items'
        ordering = ['position', 'created_at']
        constraints = [
            models.UniqueConstraint(fields=['highlight', 'story'], name='unique_highlight_story'),
        ]
        indexes = [
            models.Index(fields=['highlight', 'position'], name='highlight_item_pos_idx'),
        ]

    def __str__(self):
        return f"{self.story_id} in {self.highlight_id} @ {self.position}"


# ---------------------------------------------------------------------------
# UserInterest — TASK 3 (production_readiness_tasks.md). Feed personalization.
#
# Deliberately reuses `Post.CATEGORY_CHOICES` (the same fixed, curated list
# the composer's category picker already uses — see `category_taxonomy` in
# views.py) rather than introducing a second, separate interest-tag
# vocabulary. One row per (user, category) they've opted into; presence of a
# row IS the "selected" state; UserInterestsAPIView.put replaces the full
# set in one call so the client can just POST whatever chips are toggled on.
# ---------------------------------------------------------------------------
class UserInterest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='interests')
    category = models.CharField(max_length=100, choices=Post.CATEGORY_CHOICES, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'user_interests'
        unique_together = ['user', 'category']
        indexes = [
            models.Index(fields=['user']),
        ]

    def __str__(self):
        return f"{self.user.username} -> {self.category}"


# ---------------------------------------------------------------------------
# FEED FEEDBACK CONTROLS - PART 1 (hide + mute).
# "Not interested" on a post and "Mute this account". Both are PRIVATE,
# per-user, silent preferences that only shape what the caller's own
# feeds show (Home / Explore / Hashtag); nothing is sent to the other side.
#   - PostHide      one row per (user, post). Also hides reposts of that post.
#   - MutedAccount  one row per (user, muted_user). Unlike BlockUser the
#                   follow relationship, profile access, DMs and search stay
#                   exactly as they were - only the posts vanish from feeds.
# Part 2 (built): "Show fewer like this" ranking signal (FeedFeedback, below
# MutedAccount) and "Why am I seeing this" (post/feed_explain.py).
# ---------------------------------------------------------------------------
class PostHide(models.Model):
    class Reason(models.TextChoices):
        NOT_INTERESTED = 'not_interested', 'Not interested'
        NOT_RELEVANT = 'not_relevant', 'Not relevant to me'
        SEEN_TOO_OFTEN = 'seen_too_often', 'Seeing this too often'
        OTHER = 'other', 'Other'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='post_hides')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='hides')
    reason = models.CharField(max_length=20, choices=Reason.choices, default=Reason.NOT_INTERESTED)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_hides'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'post'], name='uniq_post_hide_user_post'),
        ]
        indexes = [
            models.Index(fields=['user', '-created_at'], name='post_hide_user_created_idx'),
        ]

    def __str__(self):
        return f"{self.user_id} hid {self.post_id} ({self.reason})"


class MutedAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='muted_accounts')
    muted_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='muted_by_accounts')
    # P8: mute is no longer all-or-nothing. `mute_posts` hides their posts/reposts/reels from my feeds,
    # `mute_stories` drops their stories (and live badge) from my story tray. Both True = the old behaviour,
    # so every row that existed before the migration keeps working unchanged. Both False is not allowed
    # (the API deletes the row instead - that is just "unmute").
    mute_posts = models.BooleanField(default=True)
    mute_stories = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_muted_accounts'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'muted_user'], name='uniq_muted_account'),
            models.CheckConstraint(condition=~models.Q(user=models.F('muted_user')), name='muted_account_no_self_mute'),
            models.CheckConstraint(
                condition=models.Q(mute_posts=True) | models.Q(mute_stories=True),
                name='muted_account_scope_not_empty',
            ),
        ]
        indexes = [
            models.Index(fields=['user', '-created_at'], name='post_muted_user_created_idx'),
        ]

    def __str__(self):
        return f"{self.user_id} muted {self.muted_user_id}"


# ---------------------------------------------------------------------------
# FEED FEEDBACK CONTROLS - PART 2: "Show fewer like this".
# A NEGATIVE ranking signal - the opposite of UserInterest's +15. One row per
# (user, kind, key) where kind says WHAT is being dampened:
#   category  key = Post.category            e.g. "tech"
#   hashtag   key = lower-case tag, no '#'   e.g. "python"
#   author    key = str(author user id)
# `weight` is the strength AT `updated_at`; it grows by one step per "show
# fewer" tap (capped) and DECAYS with a slow half-life at read time (default
# 30 days, settings.FEED_FEEDBACK) - nothing is rewritten by a cron job. The
# decayed weight becomes minus points in feed_mix.build_pool_ids, for the
# recommended + trending pools only (never the following pool). See
# feed_mix.load_feedback_penalties / penalty_expression.
# ---------------------------------------------------------------------------
class FeedFeedback(models.Model):
    class Kind(models.TextChoices):
        CATEGORY = 'category', 'Category'
        HASHTAG = 'hashtag', 'Hashtag'
        AUTHOR = 'author', 'Author'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='feed_feedback')
    kind = models.CharField(max_length=10, choices=Kind.choices)
    key = models.CharField(max_length=100)
    weight = models.FloatField(default=1.0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'post_feed_feedback'
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'kind', 'key'], name='uniq_feed_feedback_user_kind_key'),
            models.CheckConstraint(condition=models.Q(weight__gt=0), name='feed_feedback_weight_positive'),
        ]
        indexes = [
            models.Index(fields=['user', '-updated_at'], name='post_feedback_user_upd_idx'),
        ]

    def __str__(self):
        return f"{self.user_id} fewer {self.kind}:{self.key} ({self.weight:.2f})"


# ---------------------------------------------------------------------------
# C1-BE — feed / profile analytics events (impression, dwell, tap, skip).
#
# `PostView` / the feed "seen" signal only remember THAT a post was seen (one
# row per (post, user)); they cannot say how long it stayed on screen, or
# which surface it was on. This table is the append-only event log for that:
# one row per event, written in bulk by `POST /post/events/`
# (`PostEventBulkAPIView`) and pruned daily (`post.tasks.prune_old_post_events`,
# 30-day retention) so it can never grow unbounded.
#
# Deliberately NOT unique on (user, post, event_type): the same post can
# legitimately be impressed / dwelled on many times, and every row is data.
# ---------------------------------------------------------------------------
class PostEvent(models.Model):
    class EventType(models.TextChoices):
        IMPRESSION = 'impression', 'Impression'
        DWELL = 'dwell', 'Dwell'
        TAP = 'tap', 'Tap'
        SKIP = 'skip', 'Skip'

    class Surface(models.TextChoices):
        FEED = 'feed', 'Feed'
        REELS = 'reels', 'Reels'
        PROFILE = 'profile', 'Profile'
        EXPLORE = 'explore', 'Explore'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='post_events')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='events')
    event_type = models.CharField(max_length=12, choices=EventType.choices)
    # Milliseconds the post was on screen. Only meaningful for `dwell` (and
    # optionally `skip`); 0 for impression/tap.
    dwell_ms = models.PositiveIntegerField(default=0)
    surface = models.CharField(max_length=10, choices=Surface.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_events'
        ordering = ['-created_at']
        indexes = [
            # The daily prune (`created_at < cutoff`) — without this it is a
            # full scan of the biggest table in the app.
            models.Index(fields=['created_at'], name='post_events_created_idx'),
            # Per-post analytics: "impressions / dwell for this post".
            models.Index(fields=['post', 'event_type', '-created_at'], name='post_events_post_type_idx'),
            # Per-user history: "what has this user dwelled on recently?".
            models.Index(fields=['user', '-created_at'], name='post_events_user_idx'),
        ]

    def __str__(self):
        return f"{self.user_id} {self.event_type} {self.post_id} on {self.surface}"


# ---------------------------------------------------------------------------
# post/models.py  —  APPEND this class (P5a-BE). post/models.py wasn't part of
# the upload, so this is a drop-in block instead of an edited file.
#
# Needs these imports at the top of post/models.py if not already there:
#     from django.conf import settings
#     from django.core.validators import MaxValueValidator, MinValueValidator
#
# Then:   python manage.py makemigrations post core && python manage.py migrate
#   * post : creates PostTag
#   * core : state-only AlterField for Notification.notif_type (new POST_TAG choice)
# ---------------------------------------------------------------------------


class PostTag(models.Model):
    """A user tagged in a post (Instagram-style "Tagged" tab on their profile).

    x / y are relative (0..1) positions on the first image; both NULL for posts
    tagged without a position (video, document, text).

    is_hidden = the tagged user took the post off THEIR profile's Tagged tab
    ("Hide from profile"). The tag itself stays on the post. "Remove tag" is a
    real DELETE of this row.

    notified = the POST_TAG notification has gone out. Kept on the row so a
    scheduled post's tags can be notified later, once it is actually published,
    without ever notifying twice.
    """

    post = models.ForeignKey("Post", on_delete=models.CASCADE, related_name="post_tags")
    tagged_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="post_tags_received",
    )
    x = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0.0), MaxValueValidator(1.0)])
    y = models.FloatField(null=True, blank=True, validators=[MinValueValidator(0.0), MaxValueValidator(1.0)])
    is_hidden = models.BooleanField(default=False)
    notified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["post", "tagged_user"], name="uniq_posttag_post_user"),
        ]
        indexes = [
            # Tagged-tab query: "my visible tags, newest first".
            models.Index(fields=["tagged_user", "is_hidden", "-created_at"], name="posttag_user_hidden_idx"),
        ]

    def __str__(self):
        return f"{self.tagged_user_id} tagged in {self.post_id}"

# =============================================================================
# P1 — knowledge-sharing "Notes / Resource" posts
# =============================================================================
def _resource_upload_to(instance, filename):
    # `post_resources/` is a PRIVATE media prefix (common/private_media.py): the
    # file's .url is a signed, expiring /pmedia/ link minted only inside a
    # response the permission layer already authorised.
    import os
    from django.utils import timezone as _tz
    ext = os.path.splitext(filename)[1].lower()
    return f"post_resources/{_tz.now():%Y/%m}/{uuid.uuid4().hex}{ext}"


def _resource_thumb_upload_to(instance, filename):
    from django.utils import timezone as _tz
    return f"post_resources/thumbs/{_tz.now():%Y/%m}/{uuid.uuid4().hex}.jpg"


class PostAttachment(models.Model):
    """One file of a resource post (PDF / DOC / PPT / image / lecture video)."""
    KIND_CHOICES = [('pdf', 'PDF'), ('doc', 'Document'), ('image', 'Image'), ('video', 'Video')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='attachments')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    file = models.FileField(upload_to=_resource_upload_to, max_length=255)
    original_name = models.CharField(max_length=255)
    mime = models.CharField(max_length=100)
    size = models.BigIntegerField()
    pages = models.PositiveIntegerField(null=True, blank=True)      # PDFs
    thumbnail = models.ImageField(upload_to=_resource_thumb_upload_to, max_length=255, null=True, blank=True)
    alt_text = models.CharField(max_length=500, blank=True, default='')
    order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    # Version replace ("v2"): the row (and so the post, its comments, likes, saves, reading progress) stays; only the
    # file behind it changes. Old files are kept in PostAttachmentVersion. See post/resource_versions.py.
    version = models.PositiveSmallIntegerField(default=1)
    replaced_at = models.DateTimeField(null=True, blank=True)
    version_note = models.CharField(max_length=200, blank=True, default='')  # what changed in THIS version (typed on replace)
    # Searchable text: PDF text layer / Office text / OCR (Hindi + English). `text_pages[i]` = text of page i+1 (an image or
    # a Word file is one "page"); `search_text` = the same text flattened, what ?q= matches. See post/resource_text.py.
    text_pages = models.JSONField(default=list, blank=True)
    search_text = models.TextField(blank=True, default='')
    TEXT_SOURCE_CHOICES = [('', 'None'), ('pdf_text', 'PDF text layer'), ('office', 'Office text'),
                           ('client_ocr', 'On-device OCR'), ('server_ocr', 'Server OCR')]
    text_source = models.CharField(max_length=12, choices=TEXT_SOURCE_CHOICES, blank=True, default='')
    # Lecture notes: set when this PDF was attached as the NOTES of one video of the post (post/lectures.py). NULL = an ordinary file.
    for_media = models.ForeignKey('PostMedia', on_delete=models.SET_NULL, null=True, blank=True, related_name='notes')

    class Meta:
        db_table = 'post_attachments'
        ordering = ['order', 'created_at']
        indexes = [models.Index(fields=['post', 'order'], name='post_attach_post_id_order_idx')]

    def __str__(self):
        return f"{self.original_name} ({self.kind}) on {self.post_id}"


class PostAttachmentVersion(models.Model):
    """An OLDER file of a PostAttachment, kept when the author replaces it with a new version (v2, v3 ...).
    The live row is the PostAttachment itself (its `version` is the newest number), so nothing that points at the
    attachment or its post - comments, likes, saves, reading progress, offline copies' ids - is ever lost."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attachment = models.ForeignKey(PostAttachment, on_delete=models.CASCADE, related_name='versions')
    version = models.PositiveSmallIntegerField()
    file = models.FileField(upload_to=_resource_upload_to, max_length=255)
    original_name = models.CharField(max_length=255)
    mime = models.CharField(max_length=100)
    size = models.BigIntegerField()
    pages = models.PositiveIntegerField(null=True, blank=True)
    thumbnail = models.ImageField(upload_to=_resource_thumb_upload_to, max_length=255, null=True, blank=True)
    note = models.CharField(max_length=200, blank=True, default='')   # what changed in THIS version (copied from PostAttachment.version_note)
    replaced_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')
    created_at = models.DateTimeField(default=timezone.now)           # when this file was uploaded originally
    archived_at = models.DateTimeField(default=timezone.now)          # when it was replaced

    class Meta:
        db_table = 'post_attachment_versions'
        ordering = ['-version']
        constraints = [models.UniqueConstraint(fields=['attachment', 'version'], name='post_attach_version_uniq')]

    def __str__(self):
        return f"v{self.version} of {self.attachment_id}"


class ResourcePaper(models.Model):
    """Research-paper metadata of a resource post (one row per post, only for posts that have any). `title` and
    `subject` stay on the Post. Citations (APA / MLA) are built from this + the post title (post/citations.py)."""
    post = models.OneToOneField(Post, on_delete=models.CASCADE, related_name='paper')
    authors = models.JSONField(default=list, blank=True)             # ["Given Family", ...] in author order
    authors_text = models.TextField(blank=True, default='')           # authors joined with '; ' - what ?author= / ?q= match
    abstract = models.TextField(blank=True, default='')
    doi = models.CharField(max_length=150, blank=True, default='', db_index=True)   # normalised: "10.1000/xyz" (no URL prefix)
    link = models.URLField(max_length=500, blank=True, default='')
    year = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    venue = models.CharField(max_length=200, blank=True, default='')  # journal / conference / publisher

    class Meta:
        db_table = 'post_resource_paper'

    def __str__(self):
        return f"paper of {self.post_id}"


class Hashtag(models.Model):
    """Normalised hashtag (lowercase, NFKC/casefold) with a usage counter.
    `Post.hashtags` stays the per-post list; this table powers counts,
    trending and prefix search (GET /post/hashtags/search/)."""
    name = models.CharField(max_length=50, unique=True)
    posts_count = models.PositiveIntegerField(default=0, db_index=True)
    last_used_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = 'post_hashtags'
        ordering = ['-posts_count', 'name']

    def __str__(self):
        return f"#{self.name} ({self.posts_count})"


# =============================================================================
# Feature 1 - cached AI summary / quiz / flashcards of a Notes/Resource post
# =============================================================================
class PostAiStudy(models.Model):
    """One AI result for one version of a post. `source_hash` = sha256 of what the AI was shown
    (text + attachment id/size), so editing the post or swapping the file starts a new row.
    Shared by every viewer of the post: the first student pays for Gemini, the rest read this."""
    MODE_CHOICES = [('summary', 'Summary'), ('quiz', 'Quiz'), ('flashcards', 'Flashcards')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='ai_study_results')
    mode = models.CharField(max_length=12, choices=MODE_CHOICES)
    language = models.CharField(max_length=10, default='en')
    source_hash = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_ai_study'
        constraints = [
            models.UniqueConstraint(fields=['post', 'mode', 'language', 'source_hash'],
                                    name='post_ai_study_uniq'),
        ]

    def __str__(self):
        return f"{self.mode}/{self.language} for {self.post_id}"


# =============================================================================
# H13 - "Follow this topic" (Study tools sheet on a feed card)
# =============================================================================
class TopicFollow(models.Model):
    """A student follows a study topic (`physics`, `kinematics`, `datastructures` ...). `topic` is the normalised
    hashtag-style key (hashtags.normalize_tag), the same spelling `Post.hashtags` uses, so a followed topic can
    be matched against posts / used as a chip. One row per (user, topic)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='followed_topics')
    topic = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_topic_follows'
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['user', 'topic'], name='post_topic_follow_uniq')]
        indexes = [models.Index(fields=['user', '-created_at'], name='post_topic_follow_user_idx')]

    def __str__(self):
        return f"{self.user_id} follows #{self.topic}"



# =============================================================================
# H14 - "Continue learning": where the student stopped in a Notes / PDF
# =============================================================================
class ReadingProgress(models.Model):
    """The page a user reached in ONE resource attachment (PDF). One row per (user, attachment), written only by
    `PUT /post/attachments/<id>/progress/` (post/reading_progress.py) - the viewer reports the page when the user
    pauses / leaves it. `last_opened_at` is what the Home "Continue learning" strip sorts by."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reading_progress')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='reading_progress')
    attachment = models.ForeignKey(PostAttachment, on_delete=models.CASCADE, related_name='reading_progress')
    last_page = models.PositiveIntegerField(default=1)
    page_count = models.PositiveIntegerField(null=True, blank=True)
    first_opened_at = models.DateTimeField(auto_now_add=True)
    last_opened_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'post_reading_progress'
        ordering = ['-last_opened_at']
        constraints = [models.UniqueConstraint(fields=['user', 'attachment'], name='post_reading_progress_uniq')]
        indexes = [models.Index(fields=['user', '-last_opened_at'], name='post_reading_user_recent_idx')]

    def __str__(self):
        return f"{self.user_id} on page {self.last_page} of {self.attachment_id}"


# =============================================================================
# H15 - "Helpful" on a Notes / Resource post ("helpful for N students")
# =============================================================================
class PostHelpful(models.Model):
    """A viewer marked a resource post as helpful. One row per (post, user); the count on the card is a plain
    COUNT over these rows (batched per feed page in the list serializer - no denormalised counter to drift).
    The author's own vote is not allowed (checked in the view)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='helpful_votes')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='helpful_votes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_helpful'
        constraints = [models.UniqueConstraint(fields=['post', 'user'], name='post_helpful_uniq')]
        indexes = [models.Index(fields=['post', '-created_at'], name='post_helpful_post_idx')]

    def __str__(self):
        return f"{self.user_id} found {self.post_id} helpful"



# ===========================================================================
# H20 - UPLOAD QUEUE backend: UploadSession (one chunked upload for ANY media of a
# post / reel / story / resource), Draft (server-side draft sync) and
# IdempotencyRecord (duplicate-submit guard for the plain create endpoints).
# See post/upload_sessions.py, post/drafts.py, post/idempotency.py.
# ===========================================================================
class UploadSession(models.Model):
    KIND_POST = 'post'
    KIND_REEL = 'reel'
    KIND_STORY = 'story'
    KIND_RESOURCE = 'resource'
    KIND_CHOICES = [
        (KIND_POST, 'Post'),
        (KIND_REEL, 'Reel'),
        (KIND_STORY, 'Story'),
        (KIND_RESOURCE, 'Resource'),
    ]
    STATUS_UPLOADING = 'uploading'
    STATUS_COMPLETED = 'completed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_UPLOADING, 'Uploading'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='upload_sessions')
    # Client-made key (one per "Post" tap). Same key = same session = at most ONE post.
    idempotency_key = models.CharField(max_length=64)
    kind = models.CharField(max_length=12, choices=KIND_CHOICES)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_UPLOADING)
    # Every form field of the create request (alt texts, captions, topics, music, stickers, poll ...).
    payload = models.JSONField(default=dict, blank=True)
    # [{index, role: media|cover, name, size, total_chunks, content_type, upload_id}]
    files = models.JSONField(default=list, blank=True)
    chunk_size = models.PositiveIntegerField(default=5 * 1024 * 1024)
    # What the real create endpoint answered, replayed on a duplicate complete.
    result_status = models.PositiveSmallIntegerField(null=True, blank=True)
    result_body = models.JSONField(null=True, blank=True)
    result_object_id = models.CharField(max_length=64, blank=True, default='')
    last_error_code = models.CharField(max_length=64, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = 'post_upload_session'
        constraints = [models.UniqueConstraint(fields=['user', 'idempotency_key'], name='post_upload_session_idem_uniq')]
        indexes = [
            models.Index(fields=['user', '-created_at'], name='post_upload_sess_user_idx'),
            models.Index(fields=['status', 'expires_at'], name='post_upload_sess_exp_idx'),
        ]

    def __str__(self):
        return f"{self.kind} upload {self.id} ({self.status})"


class Draft(models.Model):
    """One unsent post / reel / story / resource, synced across the user's devices.
    Only fields + a media manifest live here (the picked files stay on the phone)."""
    KIND_CHOICES = UploadSession.KIND_CHOICES

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='post_drafts')
    # Made by the app (uuid4); unique per user, so a retried PUT never makes a second row.
    client_id = models.UUIDField()
    kind = models.CharField(max_length=12, choices=KIND_CHOICES)
    data = models.JSONField(default=dict, blank=True)
    media = models.JSONField(default=list, blank=True)
    # +1 on every accepted write; the client sends the version it started from (base_version).
    version = models.PositiveIntegerField(default=1)
    client_updated_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'post_draft'
        ordering = ['-client_updated_at']
        constraints = [models.UniqueConstraint(fields=['user', 'client_id'], name='post_draft_client_uniq')]
        indexes = [models.Index(fields=['user', 'is_deleted', '-client_updated_at'], name='post_draft_user_idx')]

    def __str__(self):
        return f"{self.kind} draft {self.client_id} v{self.version}"


class IdempotencyRecord(models.Model):
    """`Idempotency-Key` header guard for POST /post/create/, /post/stories/create/, /post/resources/."""
    STATE_IN_PROGRESS = 'in_progress'
    STATE_DONE = 'done'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='idempotency_records')
    scope = models.CharField(max_length=40)
    key = models.CharField(max_length=64)
    state = models.CharField(max_length=12, default=STATE_IN_PROGRESS)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'post_idempotency_record'
        constraints = [models.UniqueConstraint(fields=['user', 'scope', 'key'], name='post_idempotency_uniq')]
        indexes = [models.Index(fields=['created_at'], name='post_idem_created_idx')]

    def __str__(self):
        return f"{self.scope}:{self.key} ({self.state})"


class PublishFingerprint(models.Model):
    """What the PUBLISH gate (post/publish_gate.py) remembers about something that was just published,
    so the next upload can be checked for a duplicate: one row per hash.

        hash_kind 'text'   digest = sha1 of the normalised caption/title (case, spaces, punctuation ignored)
        hash_kind 'media'  digest = sha256 of one uploaded file's bytes

    Short-lived on purpose (PUBLISH_FINGERPRINT_KEEP_DAYS, default 30; purge_publish_fingerprints task).
    `surface` is post | story | resource, `target_id` the Post / Story / resource-post id as text.
    """
    HASH_TEXT = 'text'
    HASH_MEDIA = 'media'
    HASH_CHOICES = [(HASH_TEXT, 'Text'), (HASH_MEDIA, 'Media file')]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='publish_fingerprints')
    surface = models.CharField(max_length=12)
    target_id = models.CharField(max_length=64)
    hash_kind = models.CharField(max_length=6, choices=HASH_CHOICES)
    digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'post_publish_fingerprint'
        indexes = [
            models.Index(fields=['hash_kind', 'digest', '-created_at'], name='post_pubfp_digest_idx'),
            models.Index(fields=['user', '-created_at'], name='post_pubfp_user_idx'),
            models.Index(fields=['created_at'], name='post_pubfp_created_idx'),
        ]

    def __str__(self):
        return f"{self.surface}:{self.target_id} {self.hash_kind}"


# =============================================================================
# LECTURES (round 4): chapters, captions (hi / en), resume position, series / course, notes PDF. See post/lectures.py.
# =============================================================================
def _caption_upload_to(instance, filename):
    from django.utils import timezone as _tz
    return f"post_captions/{_tz.now():%Y/%m}/{uuid.uuid4().hex}.vtt"


class VideoChapter(models.Model):
    """One timestamp of a video (\"0:00 Intro\", \"12:30 Newton's laws\"). Replaced as a whole by PUT /post/media/<id>/chapters/."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    media = models.ForeignKey(PostMedia, on_delete=models.CASCADE, related_name='chapters')
    start_seconds = models.PositiveIntegerField()
    title = models.CharField(max_length=120)

    class Meta:
        db_table = 'post_video_chapters'
        ordering = ['start_seconds']
        constraints = [models.UniqueConstraint(fields=['media', 'start_seconds'], name='post_video_chapter_uniq')]

    def __str__(self):
        return f"{self.start_seconds}s {self.title}"


class VideoCaptionTrack(models.Model):
    """Subtitles of one video in one language (WebVTT). `auto` = made by speech-to-text (post.tasks.generate_video_captions),
    `upload` = the author's own .vtt / .srt. One track per (video, language)."""
    LANG_CHOICES = [('en', 'English'), ('hi', 'Hindi')]
    SOURCE_CHOICES = [('auto', 'Automatic'), ('upload', 'Uploaded')]
    STATUS_CHOICES = [('pending', 'Pending'), ('processing', 'Processing'), ('ready', 'Ready'), ('failed', 'Failed')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    media = models.ForeignKey(PostMedia, on_delete=models.CASCADE, related_name='caption_tracks')
    language = models.CharField(max_length=5, choices=LANG_CHOICES)
    source = models.CharField(max_length=8, choices=SOURCE_CHOICES, default='upload')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='ready', db_index=True)
    file = models.FileField(upload_to=_caption_upload_to, max_length=255, blank=True, null=True)
    job_name = models.CharField(max_length=120, blank=True, default='')
    error = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'post_video_captions'
        constraints = [models.UniqueConstraint(fields=['media', 'language'], name='post_video_caption_uniq')]

    def __str__(self):
        return f"{self.language} captions of {self.media_id} ({self.status})"


class VideoProgress(models.Model):
    """Where a user stopped in ONE video (resume playback). One row per (user, video); written by PUT /post/media/<id>/progress/."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='video_progress')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='video_progress')
    media = models.ForeignKey(PostMedia, on_delete=models.CASCADE, related_name='progress_rows')
    position_seconds = models.PositiveIntegerField(default=0)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    completed = models.BooleanField(default=False)
    last_watched_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'post_video_resume'
        ordering = ['-last_watched_at']
        constraints = [models.UniqueConstraint(fields=['user', 'media'], name='post_video_resume_uniq')]
        indexes = [models.Index(fields=['user', '-last_watched_at'], name='post_video_resume_recent_idx')]

    def __str__(self):
        return f"{self.user_id} at {self.position_seconds}s of {self.media_id}"


class VideoSeries(models.Model):
    """A playlist / course of lecture posts, in order. `creator` = the owner's own collection; `classroom` = a tuition
    classroom's course (`classroom_id`, only that class's teacher / pass holders see it, unless `is_public`)."""
    KIND_CHOICES = [('creator', 'Creator collection'), ('classroom', 'Classroom course')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='video_series')
    title = models.CharField(max_length=120)
    description = models.TextField(blank=True, default='')
    cover = models.ImageField(upload_to='posts/series_covers/%Y/%m/', blank=True, null=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default='creator')
    classroom_id = models.CharField(max_length=40, blank=True, default='', db_index=True)  # opaque, like Post.shared_id
    is_public = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'post_video_series'
        ordering = ['-updated_at']

    def __str__(self):
        return self.title


class VideoSeriesItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    series = models.ForeignKey(VideoSeries, on_delete=models.CASCADE, related_name='items')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='series_items')
    position = models.PositiveIntegerField(default=0)
    section = models.CharField(max_length=80, blank=True, default='')  # course module heading ("Unit 1 - Motion")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_video_series_items'
        ordering = ['position', 'added_at']
        constraints = [models.UniqueConstraint(fields=['series', 'post'], name='post_video_series_item_uniq')]
        indexes = [models.Index(fields=['series', 'position'], name='post_vseries_pos_idx')]

    def __str__(self):
        return f"{self.series_id} #{self.position}"


# ===================== ROUND 5 - CO-AUTHORS + EDIT HISTORY =====================
class PostCollaborator(models.Model):
    """A co-author invited to a post (post/collaborators.py). `pending` until the invited person accepts; only
    `accepted` rows show as "with @x" on the post. The author can always remove a row; the invitee can leave."""
    STATUS_PENDING = 'pending'
    STATUS_ACCEPTED = 'accepted'
    STATUS_DECLINED = 'declined'
    STATUS_CHOICES = [(STATUS_PENDING, 'Pending'), (STATUS_ACCEPTED, 'Accepted'), (STATUS_DECLINED, 'Declined')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='collaborators')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='post_collaborations')
    invited_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='post_collab_invites_sent')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'post_collaborators'
        unique_together = [('post', 'user')]
        ordering = ['created_at']

    def __str__(self):
        return f'{self.user_id} on {self.post_id} ({self.status})'


class PostEditVersion(models.Model):
    """One row per edit of a published post: what the post looked like BEFORE the edit (post/post_options.py
    snapshot_before_edit). `version` counts up from 1 per post; the live post is the newest version."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='edit_versions')
    version = models.PositiveIntegerField()
    title = models.CharField(max_length=300, blank=True, default='')
    content = models.TextField(blank=True, default='')
    hashtags = models.JSONField(default=list, blank=True)
    visibility = models.CharField(max_length=20, default='public')
    changed_fields = models.JSONField(default=list, blank=True)  # what this edit changed: title / content / hashtags / visibility
    edited_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    saved_at = models.DateTimeField(default=timezone.now)  # when the OLD text was replaced

    class Meta:
        db_table = 'post_edit_versions'
        unique_together = [('post', 'version')]
        ordering = ['-version']


# ----------------------------------------------------------------------------------------------------------------- X7
class SoundTakedown(models.Model):
    """X7 - a reel / story SOUND that can no longer be used (post/sounds.py has the whole flow).

    Sound policy: only CC0 (Freesound) sounds and the user's own audio are allowed. A CC0 sound can still turn out to be
    wrong (the uploader did not own the rights, a rights holder writes to us). Staff then BLOCKS it: new reels / stories
    that pick it are refused, search stops listing it and the sound page shows "removed". Reels that already use it keep
    playing (the audio is part of the video file) but their credit shows "Sound removed", their owners are told, and
    staff can send them to the review queue (`reels_held`). Restoring flips `status` back; nothing is ever deleted.
    """
    class Status(models.TextChoices):
        BLOCKED = 'blocked', 'Blocked'
        RESTORED = 'restored', 'Restored'

    class Reason(models.TextChoices):
        COPYRIGHT = 'copyright', 'Copyright complaint'
        WRONG_LICENSE = 'wrong_license', 'Not really CC0'
        OFFENSIVE = 'offensive', 'Offensive / unsafe'
        OTHER = 'other', 'Other'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=20, default='freesound')
    sound_id = models.CharField(max_length=40, db_index=True)
    title = models.CharField(max_length=100, blank=True, default='')
    artist = models.CharField(max_length=60, blank=True, default='')
    reason = models.CharField(max_length=20, choices=Reason.choices, default=Reason.COPYRIGHT)
    note = models.CharField(max_length=300, blank=True, default='')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.BLOCKED, db_index=True)
    reels_held = models.PositiveIntegerField(default=0)  # reels sent to the review queue when it was blocked
    owners_told = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    restored_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    restored_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'post_sound_takedowns'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['provider', 'sound_id'], condition=models.Q(status='blocked'), name='uniq_blocked_sound',
            ),
        ]
        indexes = [models.Index(fields=['provider', 'sound_id', 'status'], name='sound_td_lookup_idx')]

    def __str__(self):
        return f'{self.provider}:{self.sound_id} ({self.status})'

    # The blocked-sound set is cached (post/sounds.py); any save / delete (also from Django admin) clears it.
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from . import sounds
        sounds.invalidate()

    def delete(self, *args, **kwargs):
        out = super().delete(*args, **kwargs)
        from . import sounds
        sounds.invalidate()
        return out


class SoundReport(models.Model):
    """X7 - "this sound is not copyright-free" from a user. One row per (user, sound): reporting again updates it.
    Staff read these in the sound review list and decide with SoundTakedown; the reporter is never shown to anyone else."""
    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        ACTIONED = 'actioned', 'Sound blocked'
        DISMISSED = 'dismissed', 'Dismissed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sound_reports')
    provider = models.CharField(max_length=20, default='freesound')
    sound_id = models.CharField(max_length=40, db_index=True)
    reason = models.CharField(max_length=20, default='copyright')
    note = models.CharField(max_length=300, blank=True, default='')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'post_sound_reports'
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'provider', 'sound_id'], name='uniq_sound_report_per_user'),
        ]
        indexes = [models.Index(fields=['status', '-updated_at'], name='sound_report_status_idx')]

    def __str__(self):
        return f'report {self.provider}:{self.sound_id} ({self.status})'
