"""
post/resource_watermark.py  (watermark option of a resource post)

Post.watermark_mode:
  none    nothing
  author  "@author"            - shows whose notes these are, even after a screenshot / re-share
  viewer  "@viewer - 23 Oct 2026"  - traces a leaked copy back to the person who downloaded it

What is stamped
  * downloads of a PDF  -> every page gets a faint diagonal text pattern (pypdf overlay, Helvetica, ASCII only)
  * downloads of an image -> the same pattern drawn with Pillow
  * the in-app viewer   -> the app draws the same text over the file (the API sends it as `watermark_text`)
  * the AUTHOR never gets a watermark on their own files; videos / Office files are not stamped (no safe way to do it
    on the server) - they simply download as they are.

Fail closed: if the watermark cannot be produced the download is refused (see AttachmentDownloadAPIView), never served bare.
"""
import io
import logging
import math
from datetime import date

logger = logging.getLogger(__name__)

MODE_NONE = 'none'
MODE_AUTHOR = 'author'
MODE_VIEWER = 'viewer'
MODES = (MODE_NONE, MODE_AUTHOR, MODE_VIEWER)


def _name(user):
    return (getattr(user, 'username', '') or str(getattr(user, 'pk', '') or 'user')).strip()


def watermark_text(mode, post, viewer, today=None):
    """The text to stamp for `viewer` on a file of `post`, or None (no watermark / the viewer is the author)."""
    if mode not in (MODE_AUTHOR, MODE_VIEWER):
        return None
    if viewer is None or not getattr(viewer, 'is_authenticated', False):
        return None
    if getattr(viewer, 'pk', None) == post.user_id:
        return None
    if mode == MODE_AUTHOR:
        return f'@{_name(post.user)}'
    d = today or date.today()
    return f'@{_name(viewer)} - {d.day} {d.strftime("%b %Y")}'


def _pdf_escape(text):
    safe = text.encode('latin-1', 'replace').decode('latin-1')
    return safe.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def _overlay_stream(width, height, text):
    """PDF content stream: the text tiled over the page at -30 degrees, 18 % opaque grey."""
    size = max(14.0, min(width, height) / 18.0)
    angle = math.radians(30)
    c, s = math.cos(angle), math.sin(angle)
    step_x = max(size * (len(text) * 0.55 + 4), 160.0)
    step_y = size * 5.0
    ops = ['q', '/GS1 gs', '0.45 g', 'BT', f'/F1 {size:.1f} Tf']
    safe = _pdf_escape(text)
    y = -height
    row = 0
    while y < height * 2:
        x = -width + (step_x / 2 if row % 2 else 0)
        while x < width * 2:
            # rotate about the origin, then keep only the tiles that land on the page
            px, py = x * c - y * s, x * s + y * c
            if -step_x < px < width + step_x and -step_y < py < height + step_y:
                ops.append(f'{c:.4f} {s:.4f} {-s:.4f} {c:.4f} {px:.1f} {py:.1f} Tm ({safe}) Tj')
            x += step_x
        y += step_y
        row += 1
    ops += ['ET', 'Q']
    return '\n'.join(ops)


def watermark_pdf(data, text):
    """Bytes of the PDF with `text` stamped on every page, or None if it cannot be done."""
    try:
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, FloatObject, NameObject

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            return None
        writer = PdfWriter()
        for page in reader.pages:
            w, h = float(page.mediabox.width), float(page.mediabox.height)
            tmp = PdfWriter()
            overlay = tmp.add_blank_page(width=w, height=h)
            font = DictionaryObject({
                NameObject('/Type'): NameObject('/Font'),
                NameObject('/Subtype'): NameObject('/Type1'),
                NameObject('/BaseFont'): NameObject('/Helvetica'),
            })
            gstate = DictionaryObject({
                NameObject('/Type'): NameObject('/ExtGState'),
                NameObject('/ca'): FloatObject(0.18),
                NameObject('/CA'): FloatObject(0.18),
            })
            overlay[NameObject('/Resources')] = DictionaryObject({
                NameObject('/Font'): DictionaryObject({NameObject('/F1'): font}),
                NameObject('/ExtGState'): DictionaryObject({NameObject('/GS1'): gstate}),
            })
            stream = DecodedStreamObject()
            stream.set_data(_overlay_stream(w, h, text).encode('latin-1'))
            overlay[NameObject('/Contents')] = tmp._add_object(stream)
            page.merge_page(overlay)
            writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception:
        logger.warning('PDF watermark failed', exc_info=True)
        return None


def watermark_image(data, text):
    """(bytes, mime, ext) of the image with `text` tiled over it, or None. An animated GIF becomes a PNG of frame 1."""
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps

        im = Image.open(io.BytesIO(data))
        fmt = (im.format or 'PNG').upper()
        im = ImageOps.exif_transpose(im).convert('RGBA')
        w, h = im.size
        size = max(14, min(w, h) // 16)
        try:
            font = ImageFont.load_default(size=size)  # Pillow >= 10.1
        except TypeError:
            font = ImageFont.load_default()
        safe = text.encode('latin-1', 'replace').decode('latin-1')
        diag = int(math.hypot(w, h))
        layer = Image.new('RGBA', (diag, diag), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        try:
            tw = int(draw.textlength(safe, font=font))
        except Exception:
            tw = size * len(safe) // 2
        step_x, step_y = max(tw + size * 2, 120), size * 4
        row = 0
        for y in range(0, diag, step_y):
            for x in range(-step_x if row % 2 == 0 else -step_x // 2, diag, step_x):
                draw.text((x, y), safe, font=font, fill=(255, 255, 255, 70), stroke_width=1, stroke_fill=(0, 0, 0, 55))
            row += 1
        layer = layer.rotate(30, resample=Image.BICUBIC)
        left, top = (diag - w) // 2, (diag - h) // 2
        im = Image.alpha_composite(im, layer.crop((left, top, left + w, top + h)))
        out = io.BytesIO()
        if fmt == 'JPEG':
            im.convert('RGB').save(out, format='JPEG', quality=88)
            return out.getvalue(), 'image/jpeg', 'jpg'
        if fmt == 'WEBP':
            im.save(out, format='WEBP', quality=88)
            return out.getvalue(), 'image/webp', 'webp'
        im.save(out, format='PNG')
        return out.getvalue(), 'image/png', 'png'
    except Exception:
        logger.warning('Image watermark failed', exc_info=True)
        return None
