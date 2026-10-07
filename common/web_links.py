# common/web_links.py
"""
TASK 11 — web side of the https share links / QR codes.

The app shares `https://<host>/u/<username>` (profile) and the backend already builds
`https://<host>/parent-link?code=..&campus=..` (parent invite, common/parent_invite_links.py).
For those https links to open the INSTALLED app directly, the OS must be able to verify that
this domain belongs to the app:

  GET /.well-known/assetlinks.json              Android App Links
  GET /.well-known/apple-app-site-association   iOS Universal Links
  GET /apple-app-site-association               (older iOS path, same JSON)

For people who DON'T have the app (or open the link on a desktop) there is a tiny fallback
page:

  GET /u/<username>/         -> "Open in LearnScroll" (learnscroll://u/<username>) + store links
  GET /parent-link           -> "Open in LearnScroll" (learnscroll://parent-link?code=..)

The fallback pages never look anything up (no DB, no user data), so they can't leak
private profiles and can't be used to enumerate usernames/codes.

Settings (all optional, env-driven, see settings.py):
  ANDROID_APP_PACKAGE, ANDROID_SHA256_CERT_FINGERPRINTS (comma-separated),
  IOS_APP_ID ("<TEAMID>.<bundle id>"), PLAY_STORE_URL, APP_STORE_URL
"""
import re
from html import escape
from urllib.parse import quote, urlencode

from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@+-]{1,150}$")
_CODE_RE = re.compile(r"^[A-Za-z0-9]{4,12}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _csv(value):
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def assetlinks(request):
    package = getattr(settings, "ANDROID_APP_PACKAGE", "")
    prints = _csv(getattr(settings, "ANDROID_SHA256_CERT_FINGERPRINTS", ""))
    if not package or not prints:
        raise Http404("Android app links are not configured.")
    data = [{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": package,
            "sha256_cert_fingerprints": prints,
        },
    }]
    return JsonResponse(data, safe=False)


def apple_app_site_association(request):
    app_id = getattr(settings, "IOS_APP_ID", "")
    if not app_id:
        raise Http404("iOS universal links are not configured.")
    data = {
        "applinks": {
            "apps": [],
            "details": [{"appID": app_id, "paths": ["/u/*", "/parent-link", "/parent-link/*"]}],
        }
    }
    # Apple requires application/json (no redirect, no .json extension).
    return JsonResponse(data)


def _landing(request, *, title, app_url):
    play = getattr(settings, "PLAY_STORE_URL", "")
    store = getattr(settings, "APP_STORE_URL", "")
    links = ""
    if play:
        links += f'<a class="b" href="{escape(play)}">Get it on Google Play</a>'
    if store:
        links += f'<a class="b" href="{escape(store)}">Download on the App Store</a>'
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>{escape(title)}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:0;display:flex;min-height:100vh;align-items:center;
justify-content:center;background:#0f0f11;color:#fff;text-align:center}}
.c{{max-width:340px;padding:24px}} h1{{font-size:20px}}
.b{{display:block;margin:10px 0;padding:12px;border-radius:12px;background:#fff;color:#111;
text-decoration:none;font-weight:700}}
</style></head><body><div class="c">
<h1>{escape(title)}</h1>
<a class="b" href="{escape(app_url)}">Open in LearnScroll</a>
{links}
</div></body></html>"""
    resp = HttpResponse(html)
    resp["Referrer-Policy"] = "no-referrer"
    return resp


def profile_landing(request, username):
    if not _USERNAME_RE.match(username):
        raise Http404()
    app_url = f"learnscroll://u/{quote(username, safe='')}"
    return _landing(request, title="Open this profile in LearnScroll", app_url=app_url)


def parent_link_landing(request):
    code = request.GET.get("code", "")
    if not _CODE_RE.match(code):
        raise Http404()
    params = {"code": code}
    for key in ("campus", "classroom"):
        v = request.GET.get(key, "")
        if v and _ID_RE.match(v):
            params[key] = v
    app_url = "learnscroll://parent-link?" + urlencode(params)
    return _landing(request, title="Add parent access on LearnScroll", app_url=app_url)
