# Private media (T0.3 / B3) — deploy notes

Private uploads (`class_materials/ assigmentss/ assigments_submissions/ assigments/ certificates/
campus/ testseries/ legal_orders/ copyright_evidence/ support/`) are no longer public.
Their `.url` is `/pmedia/<path>?exp=..&sig=..` (HMAC, ~4-5 h life, URL stable per hour).
Public prefixes (`posts/ stories/ profile/ comment_media/ classroom_covers/`) are unchanged.

## 1. nginx on local disk  (MUST do — otherwise nginx bypasses the check)
```nginx
# deny the public path for private prefixes (Django also returns 403 if a request leaks through)
location ~ ^/media/(class_materials|assigmentss|assigments_submissions|assigments|certificates|campus|testseries|legal_orders|copyright_evidence|support)/ {
    return 403;
}
# public media: long cache is fine here ONLY
location /media/ {
    alias /srv/learnscroll/media/;
    add_header Cache-Control "public, max-age=31536000, immutable";
}
# private bytes: Django validates the signature, nginx streams (Range, sendfile)
location /protected-media/ {
    internal;
    alias /srv/learnscroll/media/;
}
location /pmedia/ { proxy_pass http://django_upstream; }   # same proxy settings as the API
```
Set `PRIVATE_MEDIA_ACCEL_PREFIX=/protected-media` in the env. Without it Django streams the
file itself (works, but ties up a worker per video).

## 2. S3 / CloudFront
* Bucket: block public access for the private prefixes (bucket policy: public `GetObject` ONLY on
  `posts/* stories/* profile/* comment_media/* classroom_covers/*`).
* `PrivateAwareS3Storage` returns S3 presigned GET URLs (`PRIVATE_MEDIA_S3_URL_TTL`, default 4 h) for
  private prefixes. Do NOT route private prefixes through the public CloudFront behaviour.
* Existing objects need no migration — only the URL changes. If they were ever public, rotate/regenerate
  any URL that was shared (old public links stop working once the bucket policy is tightened).

## 3. Verify
```
curl -i https://api.example.com/media/class_materials/x.pdf        # 403
curl -i https://api.example.com/pmedia/class_materials/x.pdf       # 401
curl -i "<URL from an enrolled user's API response>"               # 200 + Cache-Control: private
```
## 4. Client note
URLs expire; never persist them long-term in the app. Re-fetch the list/detail when a media request
returns 403 (cached_network_image / video player should retry once after refreshing the API data).
