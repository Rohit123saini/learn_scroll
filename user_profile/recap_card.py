"""
user_profile/recap_card.py

TASK G2 (growth_and_feature_tasks.md) — renders a `WeeklyRecap` to a
shareable 1080x1350 (Instagram-portrait-ratio) PNG card: "Your Week"
title, the four stats, LearnScroll branding.

`Pillow` is an OPTIONAL dependency, imported lazily inside `render_png()`
— same "rest of the app works without it, the VIEW turns the missing-
dependency exception into a clean HTTP response" contract
`testseries/certificate_pdf.py` already documents for `reportlab`
(WeeklyRecapCardView, views.py, turns `ImageUnavailable` into 501).
Install with `pip install Pillow`.
"""
from __future__ import annotations

import io


class ImageUnavailable(RuntimeError):
    """Pillow is not installed."""


CARD_WIDTH = 1080
CARD_HEIGHT = 1350

_BG = "#2A3FC7"
_BG_ACCENT = "#3F52E8"
_INK = "#FFFFFF"
_MUTED = "#D7DBFF"


def render_png(*, username: str, week_start, week_end, tests_attempted: int,
                classes_attended: int, posts_liked_received: int, streak_days: int) -> bytes:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover — depends on the environment
        raise ImageUnavailable("Install `Pillow` to enable recap share cards.") from exc

    img = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), _BG)
    draw = ImageDraw.Draw(img)

    # Simple two-tone background: a slightly lighter accent band behind the
    # header, rather than pulling in any external asset — this card must
    # render with zero network/file dependencies (same "self-contained"
    # constraint the docx/pptx skills document for generated files).
    draw.rectangle([(0, 0), (CARD_WIDTH, 260)], fill=_BG_ACCENT)

    def _font(size: int):
        # DejaVuSans ships with Pillow itself, so this never needs a
        # bundled font file — falls back to the PIL default bitmap font
        # only if even that lookup somehow fails (headless/minimal Pillow
        # builds), so the card still renders, just less crisply.
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
        except Exception:
            return ImageFont.load_default()

    title_font = _font(64)
    label_font = _font(34)
    stat_font = _font(88)
    small_font = _font(28)

    draw.text((60, 90), "Your Week", font=title_font, fill=_INK)
    date_range = f"{week_start.strftime('%d %b')} \u2013 {week_end.strftime('%d %b %Y')}"
    draw.text((60, 175), date_range, font=small_font, fill=_MUTED)

    stats = [
        ("Tests attempted", tests_attempted),
        ("Classes attended", classes_attended),
        ("Likes received", posts_liked_received),
        ("Day streak", streak_days),
    ]
    row_height = (CARD_HEIGHT - 260 - 140) // len(stats)
    y = 260 + 40
    for label, value in stats:
        draw.text((60, y), str(value), font=stat_font, fill=_INK)
        draw.text((60, y + 100), label, font=label_font, fill=_MUTED)
        y += row_height

    draw.text(
        (60, CARD_HEIGHT - 90),
        f"@{username} \u00b7 LearnScroll",
        font=small_font,
        fill=_MUTED,
    )

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()
