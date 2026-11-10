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
  GET /qr-login?c=..         -> "Open in LearnScroll" (learnscroll://qr-login?c=..)   [S10 QR sign-in]

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
            "details": [{"appID": app_id, "paths": ["/u/*", "/parent-link", "/parent-link/*", "/classroom/*", "/test-series/*", "/p/*", "/qr-login", "/certificate/*"]}],
        }
    }
    # Apple requires application/json (no redirect, no .json extension).
    return JsonResponse(data)


def _abs_url(request, path_or_url):
    """Absolute https URL for media (OG images must be absolute). Prefers the configured
    public base so a proxy that terminates TLS can't make us emit http:// links."""
    if not path_or_url:
        return ""
    if str(path_or_url).startswith(("http://", "https://")):
        return str(path_or_url)
    base = (getattr(settings, "PUBLIC_WEB_BASE_URL", "") or "").rstrip("/")
    if base:
        return f"{base}{path_or_url}"
    try:
        return request.build_absolute_uri(path_or_url)
    except Exception:  # noqa: BLE001
        return ""


def store_buttons_html(referrer="", clip_token=""):
    """(links_html, script_html) for the Play Store / App Store buttons, with the same
    install-attribution (`referrer` on the Play link, `clip_token` copied on tap) as
    `_landing`. Used by the SEO pages (common/seo_pages.py, T2.6)."""
    play = getattr(settings, "PLAY_STORE_URL", "")
    store = getattr(settings, "APP_STORE_URL", "")
    if play and referrer:
        play = f"{play}{'&' if '?' in play else '?'}referrer={quote(referrer, safe='')}"
    links = ""
    if play:
        links += f'<a class="b store" href="{escape(play)}">Get it on Google Play</a>'
    if store:
        links += f'<a class="b store" href="{escape(store)}">Download on the App Store</a>'
    script = ""
    if clip_token:
        script = (
            "<script>document.querySelectorAll('.store').forEach(function(a){"
            "a.addEventListener('click',function(){try{navigator.clipboard.writeText("
            + repr(clip_token) + ")}catch(e){}})})</script>"
        )
    return links, script


def _landing(request, *, title, app_url, referrer="", clip_token="", card=None, trial_url=""):
    """`referrer` rides on the Play Store link (Google Play Install Referrer), and
    `clip_token` is copied to the clipboard when a store button is tapped - both are
    how a ?ref= survives an INSTALL (the app reads them once on first launch).

    T2.1: `card` (optional dict) turns the bare "Open in LearnScroll" page into a real
    landing page: Open Graph / Twitter tags (so WhatsApp / Telegram previews show the
    class), the class / teacher / price / time rows, and - when `trial_url` is given -
    a "Try a free demo class" CTA. Only PUBLIC fields ever go in `card` (see
    _classroom_card); everything is HTML-escaped here."""
    card = card or {}
    play = getattr(settings, "PLAY_STORE_URL", "")
    store = getattr(settings, "APP_STORE_URL", "")
    if play and referrer:
        play = f"{play}{'&' if '?' in play else '?'}referrer={quote(referrer, safe='')}"
    links = ""
    if play:
        links += f'<a class="b store" href="{escape(play)}">Get it on Google Play</a>'
    if store:
        links += f'<a class="b store" href="{escape(store)}">Download on the App Store</a>'
    script = ""
    if clip_token:
        # Only ever our own opaque token (validated pieces), never user text.
        script = (
            "<script>document.querySelectorAll('.store').forEach(function(a){"
            "a.addEventListener('click',function(){try{navigator.clipboard.writeText("
            + repr(clip_token) + ")}catch(e){}})})</script>"
        )

    page_title = card.get("og_title") or title
    og = ""
    robots = '<meta name="robots" content="noindex">'
    if card:
        # Teachers' class names/prices are public via the share link, but letting search
        # engines index them is a product decision -> opt-in (LANDING_PAGES_INDEXABLE=True).
        if getattr(settings, "LANDING_PAGES_INDEXABLE", False):
            robots = '<meta name="robots" content="index,follow">'
        desc = card.get("og_description", "")
        og += f'<meta name="description" content="{escape(desc, quote=True)}">'
        og += '<meta property="og:site_name" content="LearnScroll"><meta property="og:type" content="website">'
        og += f'<meta property="og:title" content="{escape(page_title, quote=True)}">'
        og += f'<meta property="og:description" content="{escape(desc, quote=True)}">'
        if card.get("canonical"):
            og += f'<meta property="og:url" content="{escape(card["canonical"], quote=True)}">'
            og += f'<link rel="canonical" href="{escape(card["canonical"], quote=True)}">'
        if card.get("image"):
            og += f'<meta property="og:image" content="{escape(card["image"], quote=True)}">'
        og += ('<meta name="twitter:card" content="%s">'
               % ("summary_large_image" if card.get("image") else "summary"))
        og += f'<meta name="twitter:title" content="{escape(page_title, quote=True)}">'
        og += f'<meta name="twitter:description" content="{escape(desc, quote=True)}">'
        if card.get("image"):
            og += f'<meta name="twitter:image" content="{escape(card["image"], quote=True)}">'

    img_html = ""
    if card.get("image"):
        img_html = f'<img class="cv" src="{escape(card["image"], quote=True)}" alt="">'
    rows = ""
    for label, value in card.get("rows", []):
        if value:
            rows += f'<div class="r"><span>{escape(label)}</span><b>{escape(value)}</b></div>'
    summary_html = ""
    if card.get("summary"):
        summary_html = '<p class="d">' + escape(card["summary"]) + "</p>"
    trial_html = ""
    if trial_url:
        trial_html = f'<a class="b trial" href="{escape(trial_url, quote=True)}">{escape(card.get("trial_label") or "Try a free demo class")}</a>'

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{robots}
<title>{escape(page_title)}</title>
{og}
<style>
body{{font-family:system-ui,sans-serif;margin:0;display:flex;min-height:100vh;align-items:center;
justify-content:center;background:#0f0f11;color:#fff;text-align:center}}
.c{{max-width:380px;width:100%;padding:24px;box-sizing:border-box}} h1{{font-size:20px;margin:8px 0 12px}}
.cv{{width:100%;max-height:200px;object-fit:cover;border-radius:14px;margin-bottom:8px}}
.r{{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid #2a2a2e;
font-size:14px;text-align:left}} .r span{{color:#9a9aa2}} .r b{{font-weight:600;text-align:right}}
.d{{color:#c9c9d0;font-size:14px;margin:0 0 12px}}
.b{{display:block;margin:10px 0;padding:12px;border-radius:12px;background:#fff;color:#111;
text-decoration:none;font-weight:700}}
.b.trial{{background:#22c55e;color:#04210f}}
</style></head><body><div class="c">
{img_html}
<h1>{escape(title)}</h1>
{summary_html}
{rows}
{trial_html}
<a class="b" href="{escape(app_url, quote=True)}">Open in LearnScroll</a>
{links}
</div>{script}</body></html>"""
    resp = HttpResponse(html)
    resp["Referrer-Policy"] = "no-referrer"
    return resp


def _profile_card(request, username):
    """P4 - public facts for a profile link preview (WhatsApp / Telegram / Slack show name + @username + photo).

    Privacy: the generic page is returned (None) for EVERYTHING that is not a plain public adult account -
    unknown, inactive, private, or minor - so the page cannot be used to tell those cases apart or to leak a
    private / minor profile. Only name, @username, bio (first 120 chars) and the profile photo are exposed: the
    same fields anyone sees on a public profile without logging in. No counts, no DOB, no contact details."""
    try:
        from django.contrib.auth import get_user_model

        u = get_user_model().objects.filter(username__iexact=username, is_active=True).first()
        from common.minor_policy import is_minor  # P-F16: was `u.is_minor()` - is_minor is a property, calling it raised TypeError

        if u is None or u.is_private or is_minor(u):
            return None
        name = f"{u.first_name} {u.last_name}".strip() or u.username
        bio = " ".join((u.bio or "").split())
        if len(bio) > 120:
            bio = bio[:117].rstrip() + "..."
        photo = ""
        try:
            if u.profile_photo and getattr(u.profile_photo, "name", ""):
                photo = _abs_url(request, u.profile_photo.url)
        except Exception:  # noqa: BLE001
            photo = ""
        base = (getattr(settings, "PUBLIC_WEB_BASE_URL", "") or "").rstrip("/")
        desc = f"@{u.username} on LearnScroll" + (f" - {bio}" if bio else "")
        return {
            "title": name,
            "og_title": f"{name} (@{u.username}) on LearnScroll",
            "og_description": desc[:300],
            "summary": bio,
            "image": photo,
            "canonical": f"{base}/u/{quote(u.username, safe='')}" if base else "",
            "rows": [("Username", f"@{u.username}")],
            "trial": False,
        }
    except Exception:  # noqa: BLE001 - a landing page must never 500
        return None


def profile_landing(request, username):
    if not _USERNAME_RE.match(username):
        raise Http404()
    app_url = f"learnscroll://u/{quote(username, safe='')}"
    card = _cached_card(request, "profile", username.lower(), lambda: _profile_card(request, username))
    return _landing(
        request, title=(card or {}).get("title") or "Open this profile in LearnScroll", app_url=app_url, card=card
    )


def post_landing(request, post_id):
    """GET /p/<post-uuid>/ - "Copy link" of a feed post. No DB lookup on purpose: nothing about a
    post (even whether it exists / is private) can leak or be enumerated; the app asks the API."""
    return _landing(request, title="Open this post in LearnScroll", app_url=f"learnscroll://p/{post_id}")


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


def qr_login_landing(request):
    """GET /qr-login?c=<code> - the QR shown on a computer (S10). The phone camera opens this link; with the app
    installed the OS hands it to the app (App Links / Universal Links), otherwise this page offers "Open in LearnScroll".
    No DB lookup: the code is only ever checked by the signed-in app through /login/auth/qr/lookup/."""
    code = request.GET.get("c", "")
    if not _ID_RE.match(code) or len(code) < 8:
        raise Http404()
    return _landing(
        request, title="Approve sign-in on LearnScroll", app_url="learnscroll://qr-login?" + urlencode({"c": code})
    )


# ---------------------------------------------------------------------------
# SHARE LINKS FOR A TUITION CLASS / TEST SERIES  (commission links)
#
#   GET /classroom/<id>?ref=<code>        GET /test-series/<slug>/?ref=<code>
#
# These are the URLs people paste into WhatsApp / other apps. With the app
# installed, the OS opens the app directly (App Links / Universal Links, see
# assetlinks + AASA above) and the app attributes ?ref= itself. This page is the
# fallback for everyone else, and it makes the ref survive an INSTALL:
#   * Android: the Play Store link carries `referrer=ref=..&t=..&id=..`
#     (Google Play Install Referrer) - read once on first launch.
#   * iOS / fallback: a `learnscroll-ref:<code>:<type>:<id>` token is copied to the
#     clipboard when a store button is tapped - read once on first launch.
# No DB lookup here: nothing about a class / series / user can leak or be enumerated.
# ---------------------------------------------------------------------------
_REF_RE = re.compile(r"^[A-Za-z0-9]{4,20}$")
_CLASS_ID_RE = re.compile(r"^\d{1,18}$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{4,40}$")


def _fmt_price(prices):
    """prices: list of Decimal coin prices of ACTIVE passes / the series price."""
    if not prices:
        return ""
    low = min(prices)
    if low <= 0:
        return "Free"
    low = int(low) if low == int(low) else low
    return f"From {low} coins"


_CARD_TTL = 60  # seconds - anonymous public facts only, so sharing one cached copy is safe
_MISS = "__none__"


def _cached_card(request, kind, key, builder):
    """Landing pages are unauthenticated plain Django views (DRF throttles don't cover them)
    and now run a few queries, so a burst of hits on one link would hit the DB every time.
    Cache the (anonymous, public-fields-only) card briefly. Host is part of the key because
    image URLs are built from it. A cache outage must never break the page."""
    from django.core.cache import cache

    try:
        ck = f"landing:{kind}:{request.get_host()}:{key}"
    except Exception:  # noqa: BLE001 - bad Host header etc.
        return builder()
    try:
        hit = cache.get(ck)
        if hit is not None:
            return None if hit == _MISS else hit
    except Exception:  # noqa: BLE001
        return builder()
    card = builder()
    try:
        cache.set(ck, _MISS if card is None else card, _CARD_TTL)
    except Exception:  # noqa: BLE001
        pass
    return card


def _classroom_card(request, class_id, ref):
    """Public facts for a class landing page, or None (unknown / deleted / inactive /
    flagged -> the page falls back to the generic "Open in LearnScroll" screen, so it
    can't be used to probe which ids exist). Never returns trial_password, student
    data, session room ids or anything the Explore listing wouldn't show anyone."""
    try:
        from tuitionclass.models import ClassSession, Classroom

        c = (
            Classroom.objects.select_related("teacher")
            .filter(pk=int(class_id), is_active=True, is_deleted=False, is_flagged=False)
            .first()
        )
        if c is None:
            return None
        teacher = c.organisation_name if (c.classroom_type == "organisation" and c.organisation_name) else c.teacher.username
        prices = [p.price for p in c.passes.filter(is_active=True)]
        timing = c.weekly_timing_summary()
        if timing == "No active schedule set":
            timing = ""
        nxt = None
        try:
            from django.utils import timezone as djtz

            s = (
                ClassSession.objects.filter(classroom=c, scheduled_start__gte=djtz.now())
                .exclude(status="cancelled").order_by("scheduled_start").first()
            )
            if s:
                nxt = djtz.localtime(s.scheduled_start).strftime("%a %d %b, %I:%M %p")
        except Exception:  # noqa: BLE001
            nxt = None
        price = _fmt_price(prices)
        trial = bool(c.is_trial_enabled)
        bits = [b for b in (f"By {teacher}", c.subject, price, timing) if b]
        desc = " | ".join(bits)
        if trial:
            desc += f" | Free {c.trial_duration_minutes}-min demo available"
        summary = (c.description or "").strip().replace("\n", " ")
        if len(summary) > 160:
            summary = summary[:157].rstrip() + "..."
        base = (getattr(settings, "PUBLIC_WEB_BASE_URL", "") or "").rstrip("/")
        cover = ""
        try:
            if c.cover_image and getattr(c.cover_image, "name", ""):
                cover = _abs_url(request, c.cover_image.url)
        except Exception:  # noqa: BLE001
            cover = ""
        return {
            "title": c.title,
            "og_title": f"{c.title} - {teacher} on LearnScroll",
            "og_description": desc[:300],
            "summary": summary,
            "image": cover,
            "canonical": f"{base}/classroom/{c.id}" if base else "",
            "rows": [
                ("Teacher", teacher), ("Subject", c.subject), ("Language", c.language),
                ("Price", price), ("Timing", timing), ("Next class", nxt or ""),
            ],
            "trial": trial,
            "trial_label": f"Try a free {c.trial_duration_minutes}-min demo class",
        }
    except Exception:  # noqa: BLE001 - a landing page must never 500
        return None


def _series_card(request, slug):
    try:
        from testseries.models import TestSeries

        qs = TestSeries.objects.select_related("creator").filter(
            status=TestSeries.Status.PUBLISHED, source=TestSeries.Source.INDIVIDUAL
        )
        s = qs.filter(share_slug=slug).first()
        if s is None:
            return None
        price = f"{int(s.price_coins)} coins" if s.is_paid and s.price_coins else "Free"
        desc = " | ".join(b for b in (f"By {s.creator.username}", s.subject, price) if b)
        base = (getattr(settings, "PUBLIC_WEB_BASE_URL", "") or "").rstrip("/")
        summary = (s.description or "").strip().replace("\n", " ")
        if len(summary) > 160:
            summary = summary[:157].rstrip() + "..."
        return {
            "title": s.title, "og_title": f"{s.title} - test series on LearnScroll",
            "og_description": desc[:300], "summary": summary, "image": "",
            "canonical": f"{base}/test-series/{slug}/" if base else "",
            "rows": [("By", s.creator.username), ("Subject", s.subject), ("Price", price)],
            "trial": False,
        }
    except Exception:  # noqa: BLE001
        return None


def _share_landing(request, *, kind, item_id, app_path, title, card=None):
    ref = request.GET.get("ref", "")
    ref = ref if _REF_RE.match(ref) else ""
    app_url = f"learnscroll://{app_path}" + (f"?ref={ref}" if ref else "")
    referrer = urlencode({"ref": ref, "t": kind, "id": item_id}) if ref else ""
    clip = f"learnscroll-ref:{ref}:{kind}:{item_id}" if ref else ""
    trial_url = ""
    if card and card.get("trial"):
        trial_url = f"learnscroll://{app_path}?" + urlencode({**({"ref": ref} if ref else {}), "trial": "1"})
    return _landing(
        request, title=(card or {}).get("title") or title, app_url=app_url,
        referrer=referrer, clip_token=clip, card=card, trial_url=trial_url,
    )


def classroom_landing(request, class_id):
    if not _CLASS_ID_RE.match(class_id):
        raise Http404()
    ref = request.GET.get("ref", "")
    card = _cached_card(request, "c", class_id, lambda: _classroom_card(request, class_id, ref))
    return _share_landing(
        request, kind="classroom", item_id=class_id,
        app_path=f"classroom/{class_id}", title="Open this class in LearnScroll", card=card,
    )


def share_ref_params(request, kind, item_id):
    """(ref, referrer, clip_token) for a share link's ?ref= - same validation
    `_share_landing` applies, so the SEO pages attribute identically."""
    ref = request.GET.get("ref", "")
    ref = ref if _REF_RE.match(ref) else ""
    referrer = urlencode({"ref": ref, "t": kind, "id": item_id}) if ref else ""
    clip = f"learnscroll-ref:{ref}:{kind}:{item_id}" if ref else ""
    return ref, referrer, clip


def test_series_landing(request, slug):
    if not _SLUG_RE.match(slug):
        raise Http404()
    # TASK T2.6 - a published, shareable series gets the full SEO page (same ?ref=
    # attribution, Open-in-app + store buttons). Anything that doesn't qualify
    # (unknown / draft / campus) falls through to the T2.1 landing below, unchanged.
    from . import seo_pages

    page = seo_pages.test_series_page(request, slug)
    if page is not None:
        return page
    return _share_landing(
        request, kind="testseries", item_id=slug,
        app_path=f"test-series/{slug}", title="Open this test series in LearnScroll",
        card=_cached_card(request, "s", slug, lambda: _series_card(request, slug)),
    )
