from django.contrib.auth.models import AbstractUser
from django.db import models
import os

class User(AbstractUser):

    phone = models.CharField(
        max_length=15,
        unique=True,
        null=True,
        blank=True
    )

    profile_photo = models.ImageField(
        upload_to="profile/",
        null=True,
        blank=True
    )

    bio = models.TextField(blank=True)

    is_private = models.BooleanField(default=False)

    is_verified = models.BooleanField(default=False)

    followers_count = models.PositiveIntegerField(default=0)

    following_count = models.PositiveIntegerField(default=0)

    posts_count = models.PositiveIntegerField(default=0)

    coin = models.PositiveIntegerField(default=0)

    def save(self, *args, **kwargs):

        try:
            old = User.objects.get(pk=self.pk)

            if old.profile_photo and old.profile_photo != self.profile_photo:
                if os.path.isfile(old.profile_photo.path):
                    os.remove(old.profile_photo.path)

        except User.DoesNotExist:
            pass

        super().save(*args, **kwargs)

    # Delete image when user deleted
    def delete(self, *args, **kwargs):

        if self.profile_photo:
            if os.path.isfile(self.profile_photo.path):
                os.remove(self.profile_photo.path)

        super().delete(*args, **kwargs)


from django.utils import timezone
from datetime import timedelta

class OTPVerification(models.Model):
    target = models.CharField(max_length=100, unique=True)
    otp = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)

    def is_expired(self):
        return timezone.now() > self.created_at + timedelta(minutes=2)

    def __str__(self):
        return f"{self.target} - {self.otp}"