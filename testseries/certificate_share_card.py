# testseries/certificate_share_card.py
"""
TASK G10 (growth_and_feature_tasks.md — certificates as a share loop).

Renders a `TestCertificate` to a shareable 1080x1350 (Instagram-portrait-
ratio, also fine on LinkedIn) PNG card: achievement headline, student name,
series title, score, LearnScroll branding, and a "join me" referral link —
so passing a certificate-enabled test doubles as a referral touchpoint
instead of a dead end once the PDF is downloaded.

`Pillow` is an OPTIONAL dependency, imported lazily inside `render_png()`
— same "rest of the app works without it, the VIEW turns the missing-
dependency exception into a clean HTTP response" contract
`user_profile/recap_card.py` documents for its own PNG card (itself modeled
on `testseries/certificate_pdf.py`'s `reportlab` contract). The view
(`views_advanced.py::AttemptAdvancedActionsMixin.certificate_share_card`)
turns `ImageUnavailable` into 501. Install with `pip install Pillow`.
"""
from __future__ import annotations

import io

CARD_WIDTH = 1080
CARD_HEIGHT = 1350

# Same ink/brand color `certificate_pdf.py` uses for the PDF (`#2A3FC7`) so
# the share card and the certificate itself read as the same product.
_BG = "#2A3FC7"
_BG_ACCENT = "#3F52E8"
_INK = "#FFFFFF"
_MUTED = "#D7DBFF"
_GOLD = "#FFD666"


class ImageUnavailable(RuntimeError):
    """Pillow is not installed."""


def render_png(*, student_name: str, title: str, series_title: str, score: int, total_marks: int,
                percentage: float, code: str, referral_url: str) -> bytes:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover — depends on the environment
        raise ImageUnavailable("Install `Pillow` to enable certificate share cards.") from exc

    img = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), _BG)
    draw = ImageDraw.Draw(img)

    # Same two-tone background band as recap_card.py — zero network/file
    # dependencies, renders identically on every deployment.
    draw.rectangle([(0, 0), (CARD_WIDTH, 230)], fill=_BG_ACCENT)

    def _font(size: int, bold: bool = True):
        # DejaVuSans ships with Pillow itself (see recap_card.py's identical
        # note) — falls back to the PIL default bitmap font only if that
        # lookup somehow fails.
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            return ImageFont.load_default()

    badge_font = _font(30)
    title_font = _font(56)
    name_font = _font(64)
    series_font = _font(34, bold=False)
    stat_font = _font(46)
    stat_label_font = _font(24, bold=False)
    cta_font = _font(30)
    small_font = _font(24, bold=False)

    draw.text((60, 70), "\U0001F3C6  CERTIFICATE EARNED", font=badge_font, fill=_GOLD)
    draw.text((60, 130), "LearnScroll", font=title_font, fill=_INK)

    y = 300
    draw.text((60, y), student_name or "\u2014", font=name_font, fill=_INK)
    y += 90
    draw.text((60, y), "has successfully completed", font=series_font, fill=_MUTED)
    y += 50
    # Wrap the series/certificate title across up to two lines rather than
    # letting a long title run off the card edge.
    display_title = title or series_title
    max_chars = 34
    lines = []
    words = display_title.split()
    line = ""
    for w in words:
        candidate = f"{line} {w}".strip()
        if len(candidate) > max_chars and line:
            lines.append(line)
            line = w
        else:
            line = candidate
    if line:
        lines.append(line)
    for line_text in lines[:2]:
        draw.text((60, y), line_text, font=name_font, fill=_GOLD)
        y += 74

    y += 30
    draw.text((60, y), f"{score} / {total_marks}", font=stat_font, fill=_INK)
    draw.text((60, y + 62), f"Score \u00b7 {percentage:.0f}%", font=stat_label_font, fill=_MUTED)

    y += 150
    draw.line([(60, y), (CARD_WIDTH - 60, y)], fill=_BG_ACCENT, width=2)
    y += 40

    draw.text((60, y), "Think you can beat this score?", font=cta_font, fill=_INK)
    y += 44
    draw.text((60, y), "Join me on LearnScroll \u2192", font=cta_font, fill=_GOLD)
    y += 46
    draw.text((60, y), referral_url, font=small_font, fill=_MUTED)

    draw.text(
        (60, CARD_HEIGHT - 60),
        f"Certificate ID: {code} \u00b7 LearnScroll",
        font=small_font,
        fill=_MUTED,
    )

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()
