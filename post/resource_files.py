"""
post/resource_files.py  (P1)

Validation + hygiene for files attached to a "Notes / Resource" post.

  * type is decided from the file's first bytes (magic), NOT from its name or
    the client's Content-Type, and the extension must agree with it
  * size limits per kind: PDF/DOC/PPT <= 25 MB, video <= 200 MB, image <= 15 MB
    (settings: POST_RESOURCE_MAX_MB_*)
  * images are re-encoded (EXIF / GPS stripped); PDFs get their document
    metadata blanked; a virus-scan hook runs before anything is saved
  * page count (PDF) and a thumbnail (image, PDF first page) are produced

Pure helpers: no DB access. Errors are `ResourceFileError(code, message)`
so the API can answer with a clear, translatable message.
"""
import io
import os
from dataclasses import dataclass

from django.conf import settings

MB = 1024 * 1024


class ResourceFileError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# kind -> (allowed extensions, allowed mimes, settings key, default MB)
KINDS = {
    'pdf': ({'pdf'}, {'application/pdf'}, 'POST_RESOURCE_MAX_MB_PDF', 25),
    'doc': ({'doc', 'docx', 'ppt', 'pptx', 'xls', 'xlsx', 'txt'},
            {'application/msword',
             'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
             'application/vnd.ms-powerpoint',
             'application/vnd.openxmlformats-officedocument.presentationml.presentation',
             'application/vnd.ms-excel',
             'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
             'text/plain'}, 'POST_RESOURCE_MAX_MB_DOC', 25),
    'image': ({'jpg', 'jpeg', 'png', 'webp', 'gif'},
              {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}, 'POST_RESOURCE_MAX_MB_IMAGE', 15),
    'video': ({'mp4', 'mov', 'webm', 'm4v'},
              {'video/mp4', 'video/quicktime', 'video/webm'}, 'POST_RESOURCE_MAX_MB_VIDEO', 200),
}
MAX_FILES_PER_POST = 10

_OOXML_MIME = {
    'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'ppt': 'application/vnd.ms-powerpoint',
    'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'txt': 'text/plain',
}


@dataclass
class Checked:
    kind: str          # pdf | doc | image | video
    mime: str
    ext: str
    size: int


def max_bytes(kind):
    key, default = KINDS[kind][2], KINDS[kind][3]
    return int(getattr(settings, key, default)) * MB


def _sniff(head, ext):
    """(kind, mime) from the first bytes, or None if not an allowed type."""
    if head.startswith(b'%PDF-'):
        return 'pdf', 'application/pdf'
    if head.startswith(b'\xff\xd8\xff'):
        return 'image', 'image/jpeg'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image', 'image/png'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image', 'image/webp'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'image', 'image/gif'
    if head[4:8] == b'ftyp':  # ISO base media: mp4 / mov / m4v
        return 'video', ('video/quicktime' if ext == 'mov' else 'video/mp4')
    if head.startswith(b'\x1a\x45\xdf\xa3'):
        return 'video', 'video/webm'
    if head.startswith(b'PK\x03\x04') and ext in ('docx', 'pptx', 'xlsx'):
        return 'doc', _OOXML_MIME[ext]
    if head.startswith(b'\xd0\xcf\x11\xe0') and ext in ('doc', 'ppt', 'xls'):
        return 'doc', _OOXML_MIME[ext]
    if ext == 'txt' and b'\x00' not in head:
        return 'doc', 'text/plain'
    return None


def check_upload(upload):
    """Validate one uploaded file. Returns `Checked` or raises ResourceFileError."""
    name = getattr(upload, 'name', '') or ''
    ext = os.path.splitext(name)[1].lower().lstrip('.')
    size = int(getattr(upload, 'size', 0) or 0)
    if size <= 0:
        raise ResourceFileError('empty_file', f'"{name}" is empty.')

    pos = upload.tell() if hasattr(upload, 'tell') else 0
    upload.seek(0)
    head = upload.read(32)
    upload.seek(pos if pos else 0)

    sniffed = _sniff(head, ext)
    if sniffed is None:
        raise ResourceFileError(
            'unsupported_type',
            f'"{name}": this file type is not allowed. Use PDF, DOC/DOCX, PPT/PPTX, image or video.')
    kind, mime = sniffed
    if ext not in KINDS[kind][0]:
        raise ResourceFileError('extension_mismatch', f'"{name}": the file content does not match its extension.')
    limit = max_bytes(kind)
    if size > limit:
        raise ResourceFileError(
            'file_too_large',
            f'"{name}" is too large - {kind.upper()} files can be at most {limit // MB} MB.')
    return Checked(kind=kind, mime=mime, ext=ext, size=size)


def virus_scan(data_stream):
    """Hook: raise ResourceFileError('infected') if the scanner flags the file.
    Enabled with settings.POST_RESOURCE_CLAMAV_HOST (clamd over TCP) or
    POST_RESOURCE_CLAMAV_SOCKET (unix socket). If neither is set the scan is
    skipped; if it is set but clamd is unreachable the upload is REFUSED
    (fail closed) unless POST_RESOURCE_SCAN_FAIL_OPEN is True."""
    host = getattr(settings, 'POST_RESOURCE_CLAMAV_HOST', '')
    sock = getattr(settings, 'POST_RESOURCE_CLAMAV_SOCKET', '')
    if not host and not sock:
        return
    try:
        import clamd  # optional dependency (pyclamd/clamd)
        cd = clamd.ClamdNetworkSocket(host=host, port=int(getattr(settings, 'POST_RESOURCE_CLAMAV_PORT', 3310))) \
            if host else clamd.ClamdUnixSocket(path=sock)
        data_stream.seek(0)
        verdict = cd.instream(data_stream).get('stream', ('OK', None))
        data_stream.seek(0)
    except Exception:
        if getattr(settings, 'POST_RESOURCE_SCAN_FAIL_OPEN', False):
            return
        raise ResourceFileError('scan_unavailable', 'Could not scan the file right now. Please try again later.')
    if verdict[0] != 'OK':
        raise ResourceFileError('infected', 'This file was blocked by the virus scanner.')


def strip_metadata(upload, checked):
    """Returns (bytes_or_None, pages). bytes is the cleaned file content (images, PDFs),
    None when the file is kept as is (video/docs). `pages` is the PDF page count or None."""
    upload.seek(0)
    if checked.kind == 'image':
        try:
            from PIL import Image, ImageOps
            im = Image.open(upload)
            if getattr(im, 'is_animated', False):  # keep GIF/WebP animation untouched
                upload.seek(0)
                return None, None
            im = ImageOps.exif_transpose(im)
            fmt = {'image/jpeg': 'JPEG', 'image/png': 'PNG', 'image/webp': 'WEBP'}.get(checked.mime, 'PNG')
            if fmt == 'JPEG' and im.mode not in ('RGB', 'L'):
                im = im.convert('RGB')
            out = io.BytesIO()
            im.save(out, format=fmt)   # re-encode: EXIF / GPS / ICC comments dropped
            return out.getvalue(), None
        except Exception:
            raise ResourceFileError('corrupt_file', 'This image could not be read.')
    if checked.kind == 'pdf':
        try:
            from pypdf import PdfReader, PdfWriter
            reader = PdfReader(upload)
            if reader.is_encrypted:
                raise ResourceFileError('encrypted_pdf', 'Password-protected PDFs are not supported.')
            writer = PdfWriter()
            for page in reader.pages:
                writer.add_page(page)
            writer.add_metadata({'/Author': '', '/Creator': '', '/Producer': '', '/Title': '', '/Subject': '', '/Keywords': ''})
            out = io.BytesIO()
            writer.write(out)
            return out.getvalue(), len(reader.pages)
        except ResourceFileError:
            raise
        except Exception:
            raise ResourceFileError('corrupt_file', 'This PDF could not be read.')
    return None, None


def make_thumbnail(data, checked, width=480):
    """JPEG bytes of a preview (image itself / PDF first page) or None."""
    try:
        from PIL import Image
        if checked.kind == 'image':
            im = Image.open(io.BytesIO(data)).convert('RGB')
        elif checked.kind == 'pdf':
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(data)
            im = pdf[0].render(scale=1.2).to_pil().convert('RGB')
        else:
            return None
        im.thumbnail((width, width * 2))
        out = io.BytesIO()
        im.save(out, format='JPEG', quality=80)
        return out.getvalue()
    except Exception:
        return None  # a missing preview must never fail the upload


# ----------------------------------------------------------------------------- Office preview (optional)
def office_preview_pdf(data, ext, timeout=45):
    """PDF bytes of a Word / PowerPoint / Excel file, made with LibreOffice (`soffice --headless`), or None.

    OFF unless settings.POST_RESOURCE_OFFICE_PREVIEW is True AND `soffice` is installed on the server; used only to draw the
    first-page thumbnail of a DOC / PPT (the file itself is stored untouched). Any problem -> None: a missing preview must
    never fail the upload."""
    if not getattr(settings, 'POST_RESOURCE_OFFICE_PREVIEW', False) or ext not in ('doc', 'docx', 'ppt', 'pptx', 'xls', 'xlsx'):
        return None
    import shutil
    import subprocess
    import tempfile
    exe = shutil.which(getattr(settings, 'POST_RESOURCE_SOFFICE', 'soffice'))
    if not exe:
        return None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, f'in.{ext}')
            with open(src, 'wb') as fh:
                fh.write(data)
            subprocess.run([exe, '--headless', '--convert-to', 'pdf', '--outdir', tmp, src],
                           check=True, timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env={**os.environ, 'HOME': tmp})
            out = os.path.join(tmp, 'in.pdf')
            if not os.path.exists(out):
                return None
            with open(out, 'rb') as fh:
                return fh.read()
    except Exception:
        return None


def make_office_thumbnail(data, ext, width=480):
    """First-page JPEG of a Word / PowerPoint file (via office_preview_pdf) or None."""
    pdf = office_preview_pdf(data, ext)
    if pdf is None:
        return None
    return make_thumbnail(pdf, Checked(kind='pdf', mime='application/pdf', ext='pdf', size=len(pdf)), width=width)
