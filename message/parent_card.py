"""
message/parent_card.py

TASK T2.5 — renders the parent WEEKLY PROGRESS card to a shareable 1080x1350
PNG (WhatsApp-friendly portrait ratio): student name, the week, 3-5 headline
numbers, LearnScroll branding.

Same contract as `user_profile/recap_card.py` / `testseries/
certificate_share_card.py`: `Pillow` is an OPTIONAL dependency imported lazily
inside `render_png()`; the views turn `ImageUnavailable` into a clean HTTP 501
instead of a crash (the app then falls back to sharing the plain-text
summary, which carries the same numbers).

Scope guard (see views_parent.py STRICT SCOPE): `rows` only ever holds
report-card-style aggregates — never chat content, never per-question data.
"""
from __future__ import annotations

import io

CARD_WIDTH = 1080
CARD_HEIGHT = 1350

_BG = "#1F7A4D"          # green: "progress report", distinct from the blue recap card
_BG_ACCENT = "#2E9E68"
_INK = "#FFFFFF"
_MUTED = "#D5F2E3"
MAX_ROWS = 5


class ImageUnavailable(RuntimeError):
    """Pillow is not installed."""


def _fit(draw, text: str, font, max_width: int) -> str:
    """Trim `text` with an ellipsis until it fits `max_width` pixels."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "\u2026", font=font) > max_width:
        text = text[:-1]
    return (text + "\u2026") if text else ""


def render_png(*, student_name: str, label: str, rows, footer: str = "") -> bytes:
    """`rows` — iterable of `(caption, value)` pairs, e.g. `("Classes attended", "3")`.
    At most `MAX_ROWS` are drawn."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover — depends on the environment
        raise ImageUnavailable("Install `Pillow` to enable parent progress cards.") from exc

    img = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), _BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, 0), (CARD_WIDTH, 300)], fill=_BG_ACCENT)

    def _font(size: int):
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
        except Exception:
            return ImageFont.load_default()

    title_font = _font(60)
    name_font = _font(44)
    stat_font = _font(84)
    label_font = _font(32)
    small_font = _font(26)

    inner = CARD_WIDTH - 120
    draw.text((60, 60), "Weekly Progress", font=title_font, fill=_INK)
    draw.text((60, 145), _fit(draw, student_name or "", name_font, inner), font=name_font, fill=_INK)
    draw.text((60, 215), label, font=small_font, fill=_MUTED)

    rows = list(rows)[:MAX_ROWS]
    if rows:
        top = 340
        bottom = CARD_HEIGHT - 150
        row_height = (bottom - top) // len(rows)
        y = top
        for caption, value in rows:
            draw.text((60, y), _fit(draw, str(value), stat_font, inner), font=stat_font, fill=_INK)
            draw.text((60, y + 100), _fit(draw, str(caption), label_font, inner), font=label_font, fill=_MUTED)
            y += row_height

    draw.text(
        (60, CARD_HEIGHT - 90),
        _fit(draw, footer or "LearnScroll \u00b7 parent weekly report", small_font, inner),
        font=small_font,
        fill=_MUTED,
    )

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()
