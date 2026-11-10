"""
post/resource_text.py  (searchable text of resource files)

Makes the CONTENT of an attachment searchable with `GET /post/resources/?q=` and inside the in-app viewer:

  * PDF with a text layer    -> text per page (pypdf)                          text_source = 'pdf_text'
  * DOCX / PPTX / XLSX       -> text (one "page" per slide for PPTX)           text_source = 'office'
  * image / scanned PDF      -> OCR text sent by the app (ML Kit, Hindi + English), one string per page
                                                                               text_source = 'client_ocr'
  * optional server OCR      -> pytesseract (lang 'hin+eng') for an image that arrived without client text;
                                OFF unless settings.POST_RESOURCE_SERVER_OCR is True           'server_ocr'

Pure helpers, no DB. Everything is best-effort: a file whose text cannot be read still uploads fine (just not searchable).
Size caps keep one huge PDF from bloating the row: MAX_PAGES pages, MAX_PAGE_CHARS per page, MAX_TOTAL_CHARS overall.
"""
import io
import json
import logging
import re
import zipfile

from django.conf import settings

logger = logging.getLogger(__name__)

MAX_PAGES = 500
MAX_PAGE_CHARS = 20_000
MAX_TOTAL_CHARS = 300_000
MAX_OCR_PAYLOAD_PAGES = 100

_WS = re.compile(r'[ \t\r\f\v]+')
_NL = re.compile(r'\n{3,}')


# ----------------------------------------------------------------------------- shaping
def _tidy(text):
    text = str(text or '').replace('\x00', ' ')
    text = _WS.sub(' ', text)
    text = _NL.sub('\n\n', text)
    return text.strip()


def cap_pages(pages):
    """list[str] -> list[str] with the page / char caps applied (empty pages kept so page numbers stay right)."""
    out, total = [], 0
    for raw in list(pages or [])[:MAX_PAGES]:
        t = _tidy(raw)[:MAX_PAGE_CHARS]
        if total + len(t) > MAX_TOTAL_CHARS:
            t = t[:max(0, MAX_TOTAL_CHARS - total)]
        total += len(t)
        out.append(t)
    while out and not out[-1]:
        out.pop()
    return out


def flatten(pages):
    """The text ?q= matches: pages joined by a blank line."""
    return '\n\n'.join(p for p in pages if p)[:MAX_TOTAL_CHARS]


def has_text(pages):
    return any((p or '').strip() for p in pages or [])


# ----------------------------------------------------------------------------- extraction
def pdf_pages_text(data):
    """Text per page of a PDF given as bytes ([] when it has no text layer or cannot be read)."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            return []
        pages = []
        for page in list(reader.pages)[:MAX_PAGES]:
            try:
                pages.append(page.extract_text() or '')
            except Exception:  # one odd page must not lose the others
                pages.append('')
        return cap_pages(pages)
    except Exception:
        logger.info('PDF text extraction failed', exc_info=True)
        return []


_XML_TEXT = {
    'docx': re.compile(r'<w:t[^>]*>([^<]*)</w:t>'),
    'pptx': re.compile(r'<a:t>([^<]*)</a:t>'),
    'xlsx': re.compile(r'<t[^>]*>([^<]*)</t>'),
}


def _unescape(text):
    return (text.replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
            .replace('&apos;', "'").replace('&amp;', '&'))


def _read_zip_part(zf, name, limit=8_000_000):
    info = zf.getinfo(name)
    if info.file_size > limit:
        return ''
    return zf.read(name).decode('utf-8', 'ignore')


def office_pages_text(data, ext):
    """Text of a .docx / .pptx / .xlsx as pages (docx / xlsx: one page, pptx: one per slide). [] for old binary formats."""
    ext = (ext or '').lower()
    if ext not in _XML_TEXT:
        return []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            if ext == 'docx':
                xml = _read_zip_part(zf, 'word/document.xml')
                return cap_pages([_unescape(' '.join(_XML_TEXT['docx'].findall(xml)))])
            if ext == 'pptx':
                slides = sorted((n for n in zf.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)),
                                key=lambda n: int(re.findall(r'\d+', n)[-1]))
                return cap_pages([_unescape(' '.join(_XML_TEXT['pptx'].findall(_read_zip_part(zf, n)))) for n in slides])
            xml = _read_zip_part(zf, 'xl/sharedStrings.xml') if 'xl/sharedStrings.xml' in zf.namelist() else ''
            return cap_pages([_unescape(' '.join(_XML_TEXT['xlsx'].findall(xml)))])
    except Exception:
        logger.info('Office text extraction failed', exc_info=True)
        return []


def office_page_count(data, ext):
    """Slides of a .pptx / pages of a .docx (from docProps/app.xml). None when unknown."""
    ext = (ext or '').lower()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            if ext == 'pptx':
                n = len([x for x in names if re.match(r'ppt/slides/slide\d+\.xml$', x)])
                return n or None
            if ext == 'docx' and 'docProps/app.xml' in names:
                m = re.search(r'<Pages>(\d+)</Pages>', _read_zip_part(zf, 'docProps/app.xml'))
                return int(m.group(1)) if m and int(m.group(1)) > 0 else None
    except Exception:
        return None
    return None


# ----------------------------------------------------------------------------- OCR (client text / optional server)
def parse_ocr_texts(raw):
    """`ocr_texts` form field: JSON list aligned with the uploaded files. Each item is null, a string (whole file) or a
    list of strings (one per page). Returns list[list[str] | None] (never raises)."""
    if raw in (None, ''):
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if item is None or item == '':
            out.append(None)
        elif isinstance(item, str):
            out.append(cap_pages([item]))
        elif isinstance(item, (list, tuple)):
            out.append(cap_pages([str(x or '') for x in item[:MAX_OCR_PAYLOAD_PAGES]]))
        else:
            out.append(None)
    return out


def server_ocr_image(data):
    """Optional: OCR an image on the server (Hindi + English). [] unless enabled AND pytesseract + tesseract are installed."""
    if not getattr(settings, 'POST_RESOURCE_SERVER_OCR', False):
        return []
    try:
        import pytesseract
        from PIL import Image
        lang = getattr(settings, 'POST_RESOURCE_OCR_LANGS', 'hin+eng')
        text = pytesseract.image_to_string(Image.open(io.BytesIO(data)), lang=lang, timeout=30)
        return cap_pages([text])
    except Exception:
        logger.info('Server OCR failed', exc_info=True)
        return []


def build_text(checked, data, client_pages):
    """(pages, source) for one prepared attachment.

    `checked` = resource_files.Checked, `data` = the final file bytes (None for video), `client_pages` = what the app sent
    for this file (list[str] | None). A PDF's own text layer wins; when it has none (a scan) the app's OCR text is used."""
    kind, ext = checked.kind, checked.ext
    pages, source = [], ''
    if data is not None and kind == 'pdf':
        pages = pdf_pages_text(data)
        source = 'pdf_text' if has_text(pages) else ''
    elif data is not None and kind == 'doc':
        pages = office_pages_text(data, ext)
        source = 'office' if has_text(pages) else ''
    if not has_text(pages) and client_pages and has_text(client_pages) and kind in ('pdf', 'image', 'doc'):
        pages, source = cap_pages(client_pages), 'client_ocr'
    if not has_text(pages) and kind == 'image' and data is not None:
        pages = server_ocr_image(data)
        source = 'server_ocr' if has_text(pages) else ''
    return (pages, source) if has_text(pages) else ([], '')


# ----------------------------------------------------------------------------- searching inside one file
def page_hits(pages, query, limit=50, radius=60):
    """[{page, snippet, count}] for every page of `pages` containing `query` (case-insensitive), in page order."""
    q = (query or '').strip()
    if not q:
        return []
    needle = q.casefold()
    hits = []
    for i, text in enumerate(pages or []):
        hay = (text or '')
        low = hay.casefold()
        pos = low.find(needle)
        if pos < 0:
            continue
        start = max(0, pos - radius)
        end = min(len(hay), pos + len(q) + radius)
        snippet = _WS.sub(' ', hay[start:end].replace('\n', ' ')).strip()
        hits.append({'page': i + 1,
                     'snippet': ('\u2026' if start > 0 else '') + snippet + ('\u2026' if end < len(hay) else ''),
                     'count': low.count(needle)})
        if len(hits) >= limit:
            break
    return hits
