from django.db import models
from django.conf import settings
from django.db.models import Q, Index, UniqueConstraint
from django.contrib.auth import get_user_model
User = get_user_model
class Follow(models.Model):

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACCEPTED = "ACCEPTED", "Accepted"

    follower = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="following_relation",
        on_delete=models.CASCADE
    )

    following = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="followers_relation",
        on_delete=models.CASCADE
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACCEPTED
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:

        constraints = [
            models.UniqueConstraint(
                fields=["follower", "following"],
                name="unique_follow"
            )
        ]

        indexes = [
            models.Index(fields=["follower"]),
            models.Index(fields=["following"]),
            models.Index(fields=["status"]),
            models.Index(fields=["follower", "status"]),
            models.Index(fields=["following", "status"]),
        ]

    def __str__(self):
        return f"{self.follower.username} -> {self.following.username}"
    # def clean(self):
    #     if self.follower == self.following:
    #         raise ValidationError("Khud ko follow nahi kar sakte")





class BlockUser(models.Model):

    blocker=models.ForeignKey(

        settings.AUTH_USER_MODEL,

        related_name="blocked_relation",

        on_delete=models.CASCADE

    )

    blocked=models.ForeignKey(

        settings.AUTH_USER_MODEL,

        related_name="blocked_by_relation",

        on_delete=models.CASCADE

    )

    created_at=models.DateTimeField(auto_now_add=True)

    class Meta:

        constraints=[

            models.UniqueConstraint(

                fields=["blocker","blocked"],

                name="unique_block"

            )

        ]

        indexes=[

            models.Index(fields=["blocker"]),

            models.Index(fields=["blocked"]),

        ]



class RestrictUser(models.Model):

    user=models.ForeignKey(

        settings.AUTH_USER_MODEL,

        on_delete=models.CASCADE,

        related_name="restricted_users"

    )

    restricted=models.ForeignKey(

        settings.AUTH_USER_MODEL,

        on_delete=models.CASCADE,

        related_name="restricted_by"

    )

    created_at=models.DateTimeField(auto_now_add=True)



class coins(models.Model):
    coin=models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,

    )
    credit=models.IntegerField(default=0)
    debit=models.IntegerField(default=0)
    created_at=models.DateTimeField(auto_now_add=True)

