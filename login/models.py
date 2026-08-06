from django.contrib.auth.models import AbstractUser
from django.contrib.auth.hashers import make_password, check_password
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

        if self.pk:
            try:
                old = User.objects.get(pk=self.pk)

                if old.profile_photo and old.profile_photo != self.profile_photo:
                    if old.profile_photo.name and os.path.isfile(old.profile_photo.path):
                        os.remove(old.profile_photo.path)

            except User.DoesNotExist:
                pass

        super().save(*args, **kwargs)

    # Delete image when user deleted
    def delete(self, *args, **kwargs):

        if self.profile_photo and self.profile_photo.name:
            if os.path.isfile(self.profile_photo.path):
                os.remove(self.profile_photo.path)

        super().delete(*args, **kwargs)


from django.utils import timezone
from datetime import timedelta

class OTPVerification(models.Model):
    # ✅ SECURITY: OTP ab plaintext me store nahi hota, sirf hash store hota hai
    # (Django's PBKDF2 hasher) — DB leak/backup access se bhi asli code nahi milega.
    target = models.CharField(max_length=100, unique=True)
    otp_hash = models.CharField(max_length=128)

    # ✅ SECURITY: brute-force guessing (6-digit OTP sirf 10 lakh combinations
    # hain) rokne ke liye failed-attempt counter — max attempts ke baad OTP
    # lock ho jayega, naya OTP mangwana padega.
    attempts = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    MAX_ATTEMPTS = 5
    EXPIRY_MINUTES = 5  # 2 min bohot tight tha real-world email/SMS delay ke liye

    def set_otp(self, raw_otp: str) -> None:
        """Hash karke store karo, raw OTP kabhi DB me nahi jaata."""
        self.otp_hash = make_password(raw_otp)
        self.attempts = 0

    def check_otp(self, raw_otp: str) -> bool:
        return check_password(raw_otp, self.otp_hash)

    def is_expired(self) -> bool:
        return timezone.now() > self.created_at + timedelta(minutes=self.EXPIRY_MINUTES)

    def is_locked(self) -> bool:
        return self.attempts >= self.MAX_ATTEMPTS

    def register_failed_attempt(self) -> None:
        self.attempts += 1
        self.save(update_fields=["attempts"])

    def __str__(self):
        return f"{self.target} - OTP (hashed, attempts={self.attempts})"