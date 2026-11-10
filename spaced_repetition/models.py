"""
spaced_repetition/models.py - P-F8.

RevisionCard   one SM-2 state per (user, post). Created LAZILY on the first review of a Revision-deck item
               (the deck itself is still just `PostSave(collection_name="revision")`, nothing there changes).
RevisionReview append-only log of every grade (used for stats / retention; never edited).
"""
import uuid

from django.conf import settings
from django.db import models

from . import sm2


class RevisionCard(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="revision_cards")
    post = models.ForeignKey("post.Post", on_delete=models.CASCADE, related_name="revision_cards")
    ease = models.FloatField(default=sm2.START_EASE)
    interval_days = models.PositiveIntegerField(default=0)
    repetitions = models.PositiveIntegerField(default=0)
    lapses = models.PositiveIntegerField(default=0)
    # True right after "Again": the card is shown again in minutes, and may be graded again at once (no 409 not_due).
    relearning = models.BooleanField(default=False)
    next_review_at = models.DateTimeField(db_index=True)
    last_reviewed_at = models.DateTimeField(null=True, blank=True)
    last_grade = models.CharField(max_length=5, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "revision_cards"
        constraints = [models.UniqueConstraint(fields=["user", "post"], name="uniq_revision_card_user_post")]
        indexes = [models.Index(fields=["user", "next_review_at"], name="revcard_user_due_idx")]

    def __str__(self):
        return f"card {self.user_id}:{self.post_id} due {self.next_review_at:%Y-%m-%d %H:%M}"

    def sm2_state(self) -> sm2.CardState:
        return sm2.CardState(
            ease=float(self.ease), interval_days=int(self.interval_days),
            repetitions=int(self.repetitions), lapses=int(self.lapses),
        )


class RevisionReview(models.Model):
    GRADE_CHOICES = [(g, g.title()) for g in sm2.GRADES]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    card = models.ForeignKey(RevisionCard, on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="revision_reviews")
    grade = models.CharField(max_length=5, choices=GRADE_CHOICES)
    reviewed_at = models.DateTimeField(db_index=True)
    interval_before = models.PositiveIntegerField(default=0)
    interval_after = models.PositiveIntegerField(default=0)
    ease_after = models.FloatField(default=sm2.START_EASE)
    was_new = models.BooleanField(default=False)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "revision_reviews"
        indexes = [models.Index(fields=["user", "-reviewed_at"], name="revreview_user_time_idx")]
