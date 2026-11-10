# tuitionclass/models/social.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

from login.models import User
from django.db import models
from .classroom import Classroom


# ---------------------------------------------------------------------------
# 11. RATINGS & REVIEWS (trust signal on a classroom)
# ---------------------------------------------------------------------------
class ClassroomReview(models.Model):
    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="reviews")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="classroom_reviews")

    rating = models.PositiveSmallIntegerField(choices=[(i, str(i)) for i in range(1, 6)])
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("classroom", "student")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.classroom.title} - {self.rating}★ by {self.student}"


# ---------------------------------------------------------------------------
# 11B. WISHLIST ("save for later" — browsing interest, no payment/enrollment
# implied. Deliberately separate from PassPurchase: a student should be able
# to bookmark a classroom they're considering without committing coins,
# same UX pattern as Udemy/Byju's-style "save to wishlist".)
# ---------------------------------------------------------------------------
class ClassroomWishlist(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="wishlisted_classrooms")
    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="wishlisted_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "classroom")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return f"{self.user} \u2665 {self.classroom.title}"


# ---------------------------------------------------------------------------
# 11C. CLASSROOM SHARE (word-of-mouth: in-app + outside-the-app sharing)
#
# WHY THIS EXISTS: there was no "share this class" feature at all — a
# student who liked a classroom had no way to send it to a friend, whether
# that friend is already on the platform (in-app) or not (outside the app,
# via WhatsApp/SMS/email/copy-link). One row per share attempt, whatever
# the channel, so:
#   - a teacher can see how much word-of-mouth their classroom is getting
#     (see ClassroomViewSet.share-stats), broken down by channel
#   - shared_with (nullable) lets an in-app share also drive a Notification
#     to a specific platform user — see ClassroomViewSet.share in views.py
#   - it's a natural place to hang basic anti-spam (e.g. a per-user daily
#     share cap) later without redesigning anything, if that ever becomes
#     necessary
# ---------------------------------------------------------------------------
class ClassroomShare(models.Model):
    class Channel(models.TextChoices):
        IN_APP = "in_app", "In-App"
        WHATSAPP = "whatsapp", "WhatsApp"
        SMS = "sms", "SMS"
        EMAIL = "email", "Email"
        COPY_LINK = "copy_link", "Copy Link"
        OTHER = "other", "Other"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="shares")
    shared_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="classroom_shares_made")
    # Only set for an IN_APP share to a specific platform user — every
    # outside-the-app channel (WhatsApp/SMS/email/copy-link/other) hands
    # the link off to something outside this app entirely, so there's no
    # platform user row to point at.
    shared_with = models.ForeignKey(
        User, on_delete=models.CASCADE, null=True, blank=True, related_name="classroom_shares_received"
    )
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.OTHER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["classroom", "-created_at"]),
            models.Index(fields=["shared_by", "-created_at"]),
        ]

    def __str__(self):
        target = self.shared_with or self.get_channel_display()
        return f"{self.shared_by} shared {self.classroom.title} -> {target}"
