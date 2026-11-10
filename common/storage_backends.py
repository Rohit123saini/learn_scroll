"""
T0.3 — storages whose `.url()` hands out signed URLs for private prefixes.

Used via settings.STORAGES["default"]["BACKEND"]. Public names behave exactly like the
stock backends, so posts/stories/avatars are untouched.
"""
from django.conf import settings
from django.core.files.storage import FileSystemStorage

from .private_media import build_signed_path, is_private_path


class PrivateAwareFileSystemStorage(FileSystemStorage):
    def url(self, name):
        if is_private_path(name):
            return build_signed_path(name)  # relative; DRF's build_absolute_uri() completes it
        return super().url(name)


try:  # django-storages is only installed when USE_S3_STORAGE=true
    from storages.backends.s3 import S3Storage

    class PrivateAwareS3Storage(S3Storage):
        """Private prefixes -> short-lived S3 presigned GET (bucket must block public read).
        Public prefixes -> unchanged (CDN / custom domain / public URL)."""

        def url(self, name, parameters=None, expire=None, http_method=None):
            if is_private_path(name):
                key = self._normalize_name(self._clean_name(name))
                ttl = int(expire or getattr(settings, "PRIVATE_MEDIA_S3_URL_TTL", 4 * 3600))
                return self.bucket.meta.client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket.name, "Key": key},
                    ExpiresIn=ttl,
                )
            return super().url(name, parameters=parameters, expire=expire, http_method=http_method)

except ImportError:  # pragma: no cover
    PrivateAwareS3Storage = None
