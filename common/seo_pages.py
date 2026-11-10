# common/seo_pages.py
"""
TASK T2.6 — public, INDEXABLE web pages (Loop 4-5: search -> page -> install).

  GET /test-series/<share_slug>/     published test series        (upgrades web_links.test_series_landing)
  GET /assignments/<public_slug>/    published personal assignment
  GET /doubts/<public_slug>/         teacher-published, answered classroom doubt (Q&A)
  GET /sitemap.xml                   every indexable URL above
  GET /robots.txt                    crawl rules + sitemap pointer

Why a separate module from `web_links.py`: that file's pages are deliberately
DB-free `noindex` "Open in app" stubs ("can't leak private profiles, can't be
used to enumerate"). These pages DO read the database, so every one of them is
gated on an explicit "this was made public" flag, shows marketing/preview data
only (never questions, options, answer keys, class rosters, asker identity) and
renders `noindex` for anything that is shareable-but-unlisted.

What is indexed (`<meta name="robots" content="index,follow">`, in sitemap):
  * test series   — status=published AND source=individual
  * assignments   — status=published AND source=personal AND visibility=public
  * doubts        — is_public AND is_answered (teacher published it)
What is served but `noindex` (and NOT in the sitemap):
  * tuition-class test series, "anyone with the link" assignments.
What 404s: everything else (drafts, private, campus series, unpublished doubts,
malformed slugs). A test-series slug that doesn't qualify returns `None` so the
caller keeps the old DB-free landing page behaviour for it.

Every dynamic value is `html.escape`d, JSON-LD is serialised with `</` escaped,
slugs are validated before any query, and responses carry a short public cache
so a crawler burst can't hammer the DB (60s — an unpublish disappears within a
minute, which is the staleness we are willing to accept).
"""
from __future__ import annotations

import json
import re
from html import escape

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count
from django.http import Http404, HttpResponse
from django.utils.text import Truncator

from . import web_links

SITE_NAME = "LearnScroll"
_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{4,40}$")
_SITEMAP_CACHE_KEY = "seo:sitemap:v1"
_SITEMAP_TTL = 60 * 60
_SITEMAP_LIMIT_PER_KIND = 10000  # protocol max is 50k URLs/file; stay far below it
_PAGE_CACHE = "public, max-age=60"


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _origin() -> str:
    return (getattr(settings, "PUBLIC_WEB_BASE_URL", "") or "https://learnscroll.app").rstrip("/")


def _indexing_enabled() -> bool:
    """Master switch for search indexing of these pages (default ON). Set
    `SEO_PAGES_INDEXABLE = False` in settings to serve them all `noindex` and
    return an empty sitemap (T2.1's landing pages use `LANDING_PAGES_INDEXABLE`,
    default off, for the same product decision - this one is separate because
    assignments/doubts here are only reachable after their owner made them public)."""
    return bool(getattr(settings, "SEO_PAGES_INDEXABLE", True))


def _clean(text) -> str:
    return " ".join(str(text or "").split())


def _desc(text, fallback: str, limit: int = 160) -> str:
    text = _clean(text) or fallback
    return Truncator(text).chars(limit)


def _display(user) -> str:
    if user is None:
        return SITE_NAME + " educator"
    full = ""
    getter = getattr(user, "get_full_name", None)
    if callable(getter):
        full = _clean(getter())
    return full or getattr(user, "username", "") or SITE_NAME + " educator"


def _paragraphs(text) -> str:
    """User text -> safe HTML paragraphs (blank line = new <p>, newline = <br>)."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", str(text or "").replace("\r\n", "\n")) if b.strip()]
    return "".join(f"<p>{escape(b).replace(chr(10), '<br>')}</p>" for b in blocks)


def _jsonld(data: dict) -> str:
    # `</` -> `<\/` so user text can never close the <script> element.
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _fmt_date(dt) -> str:
    return dt.strftime("%d %b %Y") if dt else ""


def _facts(rows) -> str:
    items = "".join(
        f"<div><dt>{escape(label)}</dt><dd>{escape(str(value))}</dd></div>"
        for label, value in rows
        if value not in (None, "", [])
    )
    return f'<dl class="facts">{items}</dl>' if items else ""


_CSS = """
:root{--bg:#fff;--fg:#14161f;--mut:#5b6072;--card:#f4f5fb;--line:#e1e3ee;--acc:#2a3fc7}
@media(prefers-color-scheme:dark){:root{--bg:#0f0f11;--fg:#f2f3f8;--mut:#a6aabb;--card:#1a1b22;--line:#2b2d38;--acc:#8c9bff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.6 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header,main,footer{max-width:760px;margin:0 auto;padding:16px 20px}
header a{color:var(--acc);font-weight:800;text-decoration:none;font-size:18px}
h1{font-size:26px;line-height:1.25;margin:8px 0 6px}.sub{color:var(--mut);margin:0 0 16px;font-size:14px}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 16px;padding:0;list-style:none}
.tags li{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:12px}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0}
.facts div{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px}
.facts dt{font-size:12px;color:var(--mut)}.facts dd{margin:2px 0 0;font-weight:700}
.q,.a{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:4px 16px;margin:14px 0}
.a{border-color:var(--acc)}.lbl{font-size:12px;font-weight:800;color:var(--acc);letter-spacing:.04em;margin:12px 0 0}
.cta{border:1px solid var(--line);border-radius:16px;padding:16px;margin:28px 0;text-align:center}
.cta p{margin:0 0 10px}.b{display:block;margin:8px 0;padding:12px;border-radius:12px;background:var(--acc);
color:#fff;text-decoration:none;font-weight:700}
@media(prefers-color-scheme:dark){.b{color:#0f0f11}}
footer{color:var(--mut);font-size:12px}
"""


def _page(
    *, title, description, path, h1, sub="", tags=(), body_html, jsonld=None, index=True,
    og_type="article", cta_text="", app_url="", referrer="", clip_token="",
) -> HttpResponse:
    canonical = f"{_origin()}{path}"  # never carries ?ref= / tracking params
    page_title = f"{title} | {SITE_NAME}"
    links, script = web_links.store_buttons_html(referrer, clip_token)
    buttons = ""
    if app_url:
        buttons += f'<a class="b" href="{escape(app_url)}">Open in {SITE_NAME}</a>'
    buttons += links
    cta = (
        f'<section class="cta"><p><strong>{escape(cta_text or "Learn on " + SITE_NAME)}</strong></p>{buttons}</section>'
        if buttons else ""
    )
    robots = "index,follow,max-image-preview:large" if index else "noindex,nofollow"
    tag_html = (
        '<ul class="tags">' + "".join(f"<li>{escape(str(t))}</li>" for t in tags if t) + "</ul>"
        if any(tags) else ""
    )
    ld = f'<script type="application/ld+json">{_jsonld(jsonld)}</script>' if jsonld else ""
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(page_title)}</title>
<meta name="description" content="{escape(description)}">
<meta name="robots" content="{robots}">
<link rel="canonical" href="{escape(canonical)}">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:type" content="{escape(og_type)}">
<meta property="og:title" content="{escape(page_title)}">
<meta property="og:description" content="{escape(description)}">
<meta property="og:url" content="{escape(canonical)}">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{escape(page_title)}">
<meta name="twitter:description" content="{escape(description)}">
{ld}
<style>{_CSS}</style></head><body>
<header><a href="{escape(_origin())}/">{SITE_NAME}</a></header>
<main><article>
<h1>{escape(h1)}</h1>
{f'<p class="sub">{escape(sub)}</p>' if sub else ''}
{tag_html}
{body_html}
</article>
{cta}
</main>
<footer>&copy; {SITE_NAME}</footer>
{script}
</body></html>"""
    resp = HttpResponse(html, content_type="text/html; charset=utf-8")
    resp["Referrer-Policy"] = "no-referrer-when-downgrade"
    if index:
        resp["Cache-Control"] = _PAGE_CACHE
    else:
        resp["Cache-Control"] = "private, no-cache"
        resp["X-Robots-Tag"] = "noindex, nofollow"
    return resp


# ---------------------------------------------------------------------------
# TEST SERIES  —  /test-series/<share_slug>/
# ---------------------------------------------------------------------------
def test_series_page(request, slug):
    """Full SEO page for a published, shareable series, or `None` when the slug
    doesn't qualify (caller then serves the old DB-free landing)."""
    from testseries.models import TestSeries

    series = (
        TestSeries.objects.filter(status=TestSeries.Status.PUBLISHED, share_slug=slug)
        .exclude(source=TestSeries.Source.CAMPUS)
        .select_related("creator")
        .annotate(q_count=Count("questions", distinct=True))
        .first()
    )
    if series is None:
        return None

    # Only marketplace series are indexed; a tuition-class series belongs to
    # one class, so it stays reachable by link but out of search.
    index = series.source == TestSeries.Source.INDIVIDUAL and _indexing_enabled()
    path = f"/test-series/{slug}/"
    creator = _display(series.creator)
    q_count = getattr(series, "q_count", 0) or 0
    price = f"{series.price_coins} coins" if series.is_paid and series.price_coins else "Free"
    subject = _clean(series.subject)
    difficulty = series.get_difficulty_display() if series.difficulty else ""

    fallback = f"{series.title} — a {subject + ' ' if subject else ''}test series by {creator} on {SITE_NAME}."
    description = _desc(series.description, fallback)

    cert = ""
    if series.certificate_enabled:
        cert = _clean(series.certificate_title) or "Certificate on passing"
    rows = [
        ("Questions", q_count or None),
        ("Total marks", series.total_marks or None),
        ("Duration", f"{series.duration_minutes} min" if series.duration_minutes else None),
        ("Difficulty", difficulty),
        ("Subject", subject),
        ("Attempts allowed", series.attempts_allowed if series.attempts_allowed and series.attempts_allowed > 1 else None),
        ("Pass mark", f"{series.pass_percentage}%" if series.pass_percentage else None),
        ("Certificate", cert),
        ("Price", price),
    ]
    if series.delivery_mode != "self_paced" and series.starts_at:
        rows.append(("Starts", _fmt_date(series.starts_at)))

    body = _facts(rows) + (_paragraphs(series.description) or "")

    ld = {
        "@context": "https://schema.org",
        "@type": "Course",
        "name": series.title,
        "description": description,
        "url": f"{_origin()}{path}",
        "provider": {"@type": "Organization", "name": SITE_NAME, "sameAs": _origin()},
        "creator": {"@type": "Person", "name": creator},
        "isAccessibleForFree": not series.is_paid,
        "inLanguage": "en",
    }
    if subject:
        ld["teaches"] = subject
    if difficulty:
        ld["educationalLevel"] = difficulty
    if series.duration_minutes:
        ld["timeRequired"] = f"PT{int(series.duration_minutes)}M"
    review_count = getattr(series, "review_count", None)
    avg_rating = getattr(series, "avg_rating", None)
    if review_count and avg_rating:
        ld["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": round(float(avg_rating), 2),
            "reviewCount": int(review_count),
            "bestRating": 5,
            "worstRating": 1,
        }

    ref, referrer, clip = web_links.share_ref_params(request, "testseries", slug)
    app_url = f"learnscroll://test-series/{slug}" + (f"?ref={ref}" if ref else "")
    return _page(
        title=series.title,
        description=description,
        path=path,
        h1=series.title,
        sub=f"Test series by {creator}",
        tags=[subject, difficulty, "Certificate" if cert else "", "Free" if price == "Free" else ""],
        body_html=body,
        jsonld=ld,
        index=index,
        cta_text="Attempt this test series in the app",
        app_url=app_url,
        referrer=referrer,
        clip_token=clip,
    )


# ---------------------------------------------------------------------------
# ASSIGNMENTS  —  /assignments/<public_slug>/
# ---------------------------------------------------------------------------
def assignment_page(request, slug):
    if not _SLUG_RE.match(slug):
        raise Http404()
    from assignments.models import (
        assigments, assigmentsSource, assigmentsStatus, assigmentsVisibility,
    )

    obj = (
        assigments.objects.filter(
            public_slug=slug,
            source=assigmentsSource.PERSONAL,
            status=assigmentsStatus.PUBLISHED,
            visibility__in=[assigmentsVisibility.LINK, assigmentsVisibility.PUBLIC],
        )
        .select_related("posted_by")
        .annotate(
            n_questions=Count("questions", distinct=True),
            n_participants=Count("submissions", distinct=True),
        )
        .first()
    )
    if obj is None:
        raise Http404()

    index = obj.visibility == assigmentsVisibility.PUBLIC and _indexing_enabled()
    path = f"/assignments/{slug}/"
    poster = _display(obj.posted_by)
    tags = [str(t) for t in (obj.tags or []) if t][:8]
    difficulty = obj.get_difficulty_display() if obj.difficulty else ""
    kind = obj.get_kind_display() if hasattr(obj, "get_kind_display") else "Assignment"

    description = _desc(obj.description, f"{obj.title} — a {kind.lower()} by {poster} on {SITE_NAME}.")
    rows = [
        ("Type", kind),
        ("Difficulty", difficulty),
        ("Questions", obj.n_questions or None),
        ("Total marks", obj.total_marks or None),
        ("Due", _fmt_date(obj.due_date) if obj.due_date else None),
        ("Learners joined", obj.n_participants or None),
    ]
    body = _facts(rows) + (_paragraphs(obj.description) or "")

    ld = {
        "@context": "https://schema.org",
        "@type": "LearningResource",
        "name": obj.title,
        "description": description,
        "url": f"{_origin()}{path}",
        "learningResourceType": kind,
        "isAccessibleForFree": True,
        "inLanguage": "en",
        "author": {"@type": "Person", "name": poster},
        "publisher": {"@type": "Organization", "name": SITE_NAME},
    }
    if tags:
        ld["keywords"] = ", ".join(tags)
    if difficulty:
        ld["educationalLevel"] = difficulty
    if obj.published_at:
        ld["datePublished"] = _iso(obj.published_at)
    if obj.updated_at:
        ld["dateModified"] = _iso(obj.updated_at)

    return _page(
        title=obj.title,
        description=description,
        path=path,
        h1=obj.title,
        sub=f"{kind} by {poster}",
        tags=[difficulty, *tags],
        body_html=body,
        jsonld=ld,
        index=index,
        cta_text="Join and submit this in the app",
    )


# ---------------------------------------------------------------------------
# DOUBTS  —  /doubts/<public_slug>/
# ---------------------------------------------------------------------------
def doubt_page(request, slug):
    if not _SLUG_RE.match(slug):
        raise Http404()
    from message.models import DoubtQuestion

    d = DoubtQuestion.objects.filter(
        public_slug=slug, is_public=True, is_answered=True, is_anonymous=False,
    ).exclude(answer_text="").first()
    if d is None:
        raise Http404()

    path = f"/doubts/{slug}/"
    question = _clean(d.text)
    # Page title/H1 = the question's first line, trimmed.
    first_line = _clean((d.text or "").strip().splitlines()[0] if (d.text or "").strip() else question)
    title = Truncator(first_line).chars(90)
    description = _desc(d.answer_text, f"Answer to: {title}")

    # Deliberately generic attribution: the asker's identity is never exposed
    # and the answering teacher didn't opt in to a public byline.
    body = (
        '<section class="q"><p class="lbl">QUESTION</p>' + _paragraphs(d.text) + "</section>"
        '<section class="a"><p class="lbl">ANSWER</p>' + _paragraphs(d.answer_text) + "</section>"
    )
    ld = {
        "@context": "https://schema.org",
        "@type": "QAPage",
        "url": f"{_origin()}{path}",
        "mainEntity": {
            "@type": "Question",
            "name": title,
            "text": question,
            "answerCount": 1,
            "upvoteCount": int(d.upvotes_count or 0),
            "dateCreated": _iso(d.created_at),
            "author": {"@type": "Person", "name": f"{SITE_NAME} student"},
            "acceptedAnswer": {
                "@type": "Answer",
                "text": _clean(d.answer_text),
                "dateCreated": _iso(d.answered_at or d.created_at),
                "upvoteCount": 0,
                "url": f"{_origin()}{path}",
                "author": {"@type": "Person", "name": f"{SITE_NAME} teacher"},
            },
        },
    }
    return _page(
        title=title,
        description=description,
        path=path,
        h1=title,
        sub=f"Asked by a {SITE_NAME} student · answered by a teacher"
            + (f" · {_fmt_date(d.answered_at)}" if d.answered_at else ""),
        body_html=body,
        jsonld=ld,
        index=_indexing_enabled(),
        cta_text="Get your own doubts answered by teachers",
    )


# ---------------------------------------------------------------------------
# CERTIFICATE VERIFY  -  /certificate/<code>/        (P3)
# ---------------------------------------------------------------------------
# "Certificate public verify link": an employer / institute opens the link printed under the QR code and sees
# valid / revoked without an account or the app. Same data as the public API (`CertificateVerifySerializer`):
# the name printed on the certificate, the title, the score and the issue date - nothing else about the student.
# ALWAYS noindex + never cached (a revocation must show at once) + NOT in the sitemap. The code has ~60 bits of
# entropy, so it cannot be enumerated; on top of that a light per-IP counter stops a guessing loop.
_CERT_CODE_RE = re.compile(r"^LS-[A-HJ-KM-NP-Z2-9]{4}-[A-HJ-KM-NP-Z2-9]{4}-[A-HJ-KM-NP-Z2-9]{4}$")
_CERT_RATE_PER_MIN = 30


def _cert_rate_limited(request) -> bool:
    ip = (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR", "") or "-")
    key = f"seo:certpage:{ip}"
    try:
        cache.add(key, 0, 60)
        return cache.incr(key) > _CERT_RATE_PER_MIN
    except Exception:  # a cache problem must never break verification
        return False


def certificate_page(request, code):
    code = str(code or "").strip().upper()
    if not _CERT_CODE_RE.match(code):
        raise Http404()
    if _cert_rate_limited(request):
        resp = HttpResponse("Too many requests - try again in a minute.", status=429, content_type="text/plain")
        resp["Retry-After"] = "60"
        return resp

    from testseries.models import TestCertificate

    cert = TestCertificate.objects.select_related("student", "series").filter(code=code).first()
    path = f"/certificate/{code}/"
    if cert is None:
        resp = _page(
            title="Certificate not found", description="No certificate with this code.", path=path,
            h1="Certificate not found",
            sub="We could not find a LearnScroll certificate with this code. Check the code and try again.",
            body_html="", index=False, cta_text="Learn on " + SITE_NAME,
        )
        resp.status_code = 404
        return resp

    from testseries.serializers import display_name

    holder = display_name(cert.student) or "LearnScroll learner"
    valid = cert.is_valid
    rows = [
        ("Status", "Valid" if valid else "Revoked"),
        ("Awarded to", holder),
        ("Certificate", cert.title),
        ("Test series", cert.series.title),
        ("Score", f"{cert.score} / {cert.total_marks} ({cert.percentage:.0f}%)"),
        ("Issued", _fmt_date(cert.issued_at)),
        ("Code", cert.code),
    ]
    body = _facts(rows)
    if not valid:
        body += f"<p><strong>This certificate has been revoked.</strong> {escape(cert.revoked_reason or '')}</p>"
    return _page(
        title=("Verified certificate" if valid else "Revoked certificate") + f" - {cert.title}",
        description=f"{holder}'s LearnScroll certificate {cert.code} is {'valid' if valid else 'revoked'}.",
        path=path,
        h1="Certificate is valid" if valid else "Certificate revoked",
        sub=f"{cert.title} - {holder}",
        body_html=body,
        index=False,
        og_type="website",
        cta_text="Open LearnScroll",
        app_url=f"learnscroll://certificate/{code}",
    )


# ---------------------------------------------------------------------------
# sitemap.xml / robots.txt
# ---------------------------------------------------------------------------
def _sitemap_entries():
    if not _indexing_enabled():
        return []
    from assignments.models import assigments, assigmentsSource, assigmentsStatus, assigmentsVisibility
    from message.models import DoubtQuestion
    from testseries.models import TestSeries

    origin = _origin()
    out = []
    series = (
        TestSeries.objects.filter(status=TestSeries.Status.PUBLISHED, source=TestSeries.Source.INDIVIDUAL)
        .exclude(share_slug__isnull=True).exclude(share_slug="")
        .order_by("-updated_at").values_list("share_slug", "updated_at")[:_SITEMAP_LIMIT_PER_KIND]
    )
    out += [(f"{origin}/test-series/{s}/", u) for s, u in series]

    asg = (
        assigments.objects.filter(
            source=assigmentsSource.PERSONAL, status=assigmentsStatus.PUBLISHED,
            visibility=assigmentsVisibility.PUBLIC,
        )
        .exclude(public_slug__isnull=True).exclude(public_slug="")
        .order_by("-updated_at").values_list("public_slug", "updated_at")[:_SITEMAP_LIMIT_PER_KIND]
    )
    out += [(f"{origin}/assignments/{s}/", u) for s, u in asg]

    doubts = (
        DoubtQuestion.objects.filter(is_public=True, is_answered=True, is_anonymous=False)
        .exclude(public_slug__isnull=True).exclude(public_slug="").exclude(answer_text="")
        .order_by("-updated_at").values_list("public_slug", "updated_at")[:_SITEMAP_LIMIT_PER_KIND]
    )
    out += [(f"{origin}/doubts/{s}/", u) for s, u in doubts]
    return out


def sitemap_xml(request):
    xml = cache.get(_SITEMAP_CACHE_KEY)
    if xml is None:
        urls = "".join(
            f"<url><loc>{escape(loc)}</loc>"
            + (f"<lastmod>{updated.date().isoformat()}</lastmod>" if updated else "")
            + "</url>"
            for loc, updated in _sitemap_entries()
        )
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + urls + "</urlset>"
        )
        cache.set(_SITEMAP_CACHE_KEY, xml, _SITEMAP_TTL)
    resp = HttpResponse(xml, content_type="application/xml; charset=utf-8")
    resp["Cache-Control"] = "public, max-age=3600"
    return resp


# API / private surfaces a crawler has no business in.
_DISALLOW = (
    "/admin/", "/api/", "/swagger/", "/redoc/", "/login/", "/profile/", "/post/", "/message/",
    "/pmedia/",  # T0.3 — signed private media
    "/tuitionclass/", "/campus/", "/testseries/", "/assigments/", "/core/", "/support/",
    "/leaderboard/", "/referrals/", "/media/", "/parent-link", "/certificate/",
)


def robots_txt(request):
    lines = ["User-agent: *", "Allow: /test-series/", "Allow: /assignments/", "Allow: /doubts/"]
    lines += [f"Disallow: {p}" for p in _DISALLOW]
    lines += ["", f"Sitemap: {_origin()}/sitemap.xml", ""]
    resp = HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")
    resp["Cache-Control"] = "public, max-age=86400"
    return resp
