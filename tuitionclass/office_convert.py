"""PowerPoint / Word -> PDF conversion for the live-room "Present" feature.

The Flutter room can only draw PDF pages (it rasterises them itself) and images, so a `ppt` / `doc` material is
converted ONCE on the server with LibreOffice (headless) and the PDF is cached on `ClassMaterial.presentable_pdf`.
Everyone in the room then loads that PDF exactly like a normal PDF material.

Needs LibreOffice on the web host:  `sudo apt install libreoffice-impress libreoffice-writer`  (binary `soffice`).
Settings (all optional):
  OFFICE_CONVERT_BIN      path to soffice            (default: `soffice` / `libreoffice` found on PATH)
  OFFICE_CONVERT_TIMEOUT  seconds before giving up   (default 90)
If the binary is missing the host gets a clear message ("upload as PDF") instead of a blank stage — same as before.
"""
import io
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)

MAX_SOURCE_BYTES = 50 * 1024 * 1024
_LOCK_SECONDS = 180


class OfficeConvertError(Exception):
    """Message is safe to show to the host as-is."""


def _office_bin():
    configured = getattr(settings, "OFFICE_CONVERT_BIN", "") or ""
    return configured or shutil.which("soffice") or shutil.which("libreoffice")


def count_pdf_pages(data: bytes) -> int:
    from pypdf import PdfReader  # already in requirements.txt

    return len(PdfReader(io.BytesIO(data)).pages)


def _cached_pdf_bytes(material):
    """Bytes of the cached conversion if it still matches the current source file, else None."""
    if not material.presentable_pdf or material.presentable_source_name != (material.file.name or ""):
        return None
    try:
        with material.presentable_pdf.open("rb") as fh:
            return fh.read()
    except Exception:  # file deleted from storage -> just convert again
        logger.warning("presentable_pdf missing in storage for material %s", material.pk)
        return None


def ensure_presentable_pdf(material) -> int:
    """Make sure `material.presentable_pdf` exists and is current. Returns its page count (>= 1).

    Blocking (runs LibreOffice, up to OFFICE_CONVERT_TIMEOUT seconds); the result is cached, so it only happens the
    first time a given file is presented. Raises OfficeConvertError with a host-readable message on any failure.
    """
    if not material.file:
        raise OfficeConvertError("This material has no file to present.")

    cached = _cached_pdf_bytes(material)
    if cached:
        try:
            pages = count_pdf_pages(cached)
            if pages >= 1:
                return pages
        except Exception:
            logger.warning("cached presentable_pdf unreadable for material %s", material.pk)

    binary = _office_bin()
    if not binary:
        raise OfficeConvertError(
            "PowerPoint/Word conversion is not set up on this server yet. Upload the file as a PDF to present it."
        )
    if material.file.size and material.file.size > MAX_SOURCE_BYTES:
        raise OfficeConvertError("This file is too large to convert (max 50 MB). Upload it as a PDF instead.")

    lock_key = f"present-convert:{material.pk}"
    if not cache.add(lock_key, 1, _LOCK_SECONDS):
        raise OfficeConvertError("This file is already being prepared. Try again in a few seconds.")
    try:
        pdf = _run_soffice(material, binary)
        try:
            pages = count_pdf_pages(pdf)
        except Exception:
            raise OfficeConvertError("The converted file could not be read. Upload it as a PDF instead.")
        if pages < 1:
            raise OfficeConvertError("The converted file has no pages. Upload it as a PDF instead.")

        if material.presentable_pdf:
            material.presentable_pdf.delete(save=False)
        material.presentable_pdf.save(f"material_{material.pk}.pdf", ContentFile(pdf), save=False)
        material.presentable_source_name = material.file.name or ""
        material.save(update_fields=["presentable_pdf", "presentable_source_name"])
        return pages
    finally:
        cache.delete(lock_key)


def _run_soffice(material, binary) -> bytes:
    timeout = int(getattr(settings, "OFFICE_CONVERT_TIMEOUT", 90) or 90)
    ext = Path(material.file.name or "").suffix.lower() or ".bin"
    with tempfile.TemporaryDirectory(prefix="present_") as tmp:
        src = os.path.join(tmp, "source" + ext)
        out_dir = os.path.join(tmp, "out")
        profile = os.path.join(tmp, "profile")  # private profile: parallel runs never fight over one lock
        os.makedirs(out_dir)
        with material.file.open("rb") as fh, open(src, "wb") as dst:
            shutil.copyfileobj(fh, dst)
        cmd = [
            binary, "--headless", "--norestore", "--nologo", "--nodefault", "--nolockcheck",
            f"-env:UserInstallation={Path(profile).as_uri()}",
            "--convert-to", "pdf", "--outdir", out_dir, src,
        ]
        try:
            proc = subprocess.run(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            logger.error("office convert timed out for material %s", material.pk)
            raise OfficeConvertError("Converting this file took too long. Upload it as a PDF instead.")
        except OSError:
            logger.exception("office convert could not start for material %s", material.pk)
            raise OfficeConvertError("PowerPoint/Word conversion is not available on this server. Upload the file as a PDF.")
        out_pdf = os.path.join(out_dir, "source.pdf")
        if proc.returncode != 0 or not os.path.exists(out_pdf):
            logger.error("office convert failed for material %s: rc=%s %s", material.pk, proc.returncode, proc.stderr[-500:])
            raise OfficeConvertError("This file could not be converted. Upload it as a PDF instead.")
        with open(out_pdf, "rb") as fh:
            data = fh.read()
    if not data.startswith(b"%PDF"):
        raise OfficeConvertError("This file could not be converted. Upload it as a PDF instead.")
    return data
