"""
copyrights/fingerprint.py - "looks / sounds / reads the same" detection for removed content.

SHA-256 (media.py / BlockedMediaHash) only catches a byte-identical copy. A re-encoded video, a resized or
mirrored image, a re-exported PDF or a re-compressed song all have different bytes. This module adds a
FINGERPRINT per removed file and compares every new upload against them.

  kind   algo         how it is made                                        how it is compared
  -----  -----------  ----------------------------------------------------  -------------------------------------
  image  dhash64      64-bit difference hash (+ mirrored copy when          Hamming distance <= IMAGE_MAX_DISTANCE
                      registering, so a flipped re-upload is caught)
  video  vframes64    dHash of N frames spread over the duration            share of frames that match >= ratio
                      (+ mirrored frames); needs ffmpeg / ffprobe
  audio  chroma       Chromaprint raw fingerprint (`fpcalc`), also taken    bit-error-rate over small time shifts
                      from the audio track of a video; needs fpcalc
  pdf    minhash64    64-value MinHash over 5-word shingles of the text     estimated Jaccard >= PDF_MIN_JACCARD
  pdf    pdfimg64     scanned PDF (no text): dHash of embedded page images  same as video frames

Candidate lookup (so we never compare against every removed file): image / video / pdf fingerprints are
indexed in FingerprintBand (LSH bands); audio is scanned linearly (the set of removed audio is small).

What this does NOT catch (be honest in the SOP): heavy crops / overlays on images, trimmed or re-ordered
video, a song re-recorded or pitch-shifted, a re-typed / paraphrased PDF. It never removes anything by
itself: a fuzzy match opens an AUTO claim, content is HELD, staff (L2) decide, the owner can counter-notice.

Settings (all optional): COPYRIGHT_FP_ENABLED, COPYRIGHT_FP_IMAGE_MAX_DISTANCE (5), COPYRIGHT_FP_VIDEO_FRAMES (12),
COPYRIGHT_FP_VIDEO_FRAME_DISTANCE (8), COPYRIGHT_FP_VIDEO_MATCH_RATIO (0.6), COPYRIGHT_FP_AUDIO_SECONDS (120),
COPYRIGHT_FP_AUDIO_MAX_BER (0.30), COPYRIGHT_FP_AUDIO_MIN_SECONDS (15), COPYRIGHT_FP_AUDIO_MAX_SCAN (2000),
COPYRIGHT_FP_PDF_MAX_PAGES (40), COPYRIGHT_FP_PDF_MIN_WORDS (80), COPYRIGHT_FP_PDF_MIN_JACCARD (0.6).
Everything here swallows and logs its own errors: a broken file must never break an upload or a takedown.
"""

import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

from django.conf import settings
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)

IMAGE, VIDEO, AUDIO, PDF = "image", "video", "audio", "pdf"
DHASH, VFRAMES, CHROMA, MINHASH, PDFIMG = "dhash64", "vframes64", "chroma", "minhash64", "pdfimg64"
FRAME_ALGOS = (DHASH, VFRAMES, PDFIMG, MINHASH)  # indexed through FingerprintBand

_EXT = {}
for _kind, _exts in (
    (IMAGE, "jpg jpeg png gif webp bmp"),
    (VIDEO, "mp4 mov avi mkv webm m4v 3gp"),
    (AUDIO, "mp3 wav m4a aac ogg oga flac opus"),
    (PDF, "pdf"),
):
    for _e in _exts.split():
        _EXT[_e] = _kind

_MH_PRIME = (1 << 61) - 1
_MH_N = 64
_SHINGLE = 5
_AUDIO_ITEM_SECONDS = 0.1238  # one Chromaprint raw item


def _cfg(name, default):
    return getattr(settings, name, default)


def enabled():
    return bool(_cfg("COPYRIGHT_FP_ENABLED", True))


@dataclass
class Fingerprint:
    kind: str
    algo: str
    data: dict
    bands: list = field(default_factory=list)


@dataclass
class Match:
    fingerprint_id: int
    claim_id: object
    legal_order_id: object
    kind: str
    algo: str
    score: float
    detail: str


# --------------------------------------------------------------------------
# kind detection (magic bytes first - renaming a .pdf to .txt must not hide it)
# --------------------------------------------------------------------------
def kind_for_name(name):
    ext = os.path.splitext(name or "")[1].lower().lstrip(".")
    return _EXT.get(ext)


def sniff_kind(path, name=""):
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        head = b""
    if head.startswith(b"%PDF"):
        return PDF
    if (head[:3] == b"\xff\xd8\xff" or head.startswith(b"\x89PNG") or head[:6] in (b"GIF87a", b"GIF89a")
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")):
        return IMAGE
    if head[4:8] == b"ftyp" or head[:4] == b"\x1aE\xdf\xa3" or (head[:4] == b"RIFF" and head[8:12] == b"AVI "):
        return VIDEO  # also .m4a: no video frames come out, the audio fingerprint still does
    if (head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2") or head[:4] in (b"fLaC", b"OggS")
            or (head[:4] == b"RIFF" and head[8:12] == b"WAVE")):
        return AUDIO
    return kind_for_name(name)


# --------------------------------------------------------------------------
# bit helpers
# --------------------------------------------------------------------------
def _popcount(x):
    return bin(x).count("1")


def _hamming(a, b):
    return _popcount(a ^ b)


def _hash_bands(h):
    """8 bands of 8 bits: two hashes within distance 7 always share one band (pigeonhole)."""
    return [f"i{b}:{(h >> (8 * b)) & 0xFF:02x}" for b in range(8)]


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------
def _dhash(img):
    from PIL import Image

    g = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = list(g.getdata())
    if len(set(px)) <= 3:  # blank / flat picture: every flat picture would "match" every other one
        return None
    bits = 0
    for r in range(8):
        base = r * 9
        for c in range(8):
            bits = (bits << 1) | (1 if px[base + c] > px[base + c + 1] else 0)
    return bits


def _prepare(img):
    from PIL import ImageOps

    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    return img


def _image_hashes(img, with_mirror):
    from PIL import ImageOps

    img = _prepare(img)
    h = _dhash(img)
    if h is None:
        return None, None
    flip = _dhash(ImageOps.mirror(img)) if with_mirror else None
    return h, flip


def image_fingerprint(path, registry=False):
    from PIL import Image

    try:
        with Image.open(path) as img:
            try:
                img.draft("RGB", (512, 512))
            except Exception:
                pass
            img.load()
            h, flip = _image_hashes(img, registry)
    except Exception:
        logger.info("image fingerprint failed for %s", path, exc_info=True)
        return None
    if h is None:
        return None
    hashes = [h] + ([flip] if flip is not None and flip != h else [])
    bands = sorted({k for x in hashes for k in _hash_bands(x)}) if registry else _hash_bands(h)
    return Fingerprint(IMAGE, DHASH, {"h": [f"{x:016x}" for x in hashes]}, bands)


def _compare_image(query, row):
    limit = int(_cfg("COPYRIGHT_FP_IMAGE_MAX_DISTANCE", 5))
    q = int(query.data["h"][0], 16)
    best = min((_hamming(q, int(x, 16)) for x in (row.data or {}).get("h", [])), default=65)
    if best <= limit:
        return 1.0 - best / 64.0, f"image hash distance {best}/64"
    return None


# --------------------------------------------------------------------------
# frame sets (video frames, scanned-PDF page images)
# --------------------------------------------------------------------------
def _run(cmd, timeout):
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, OSError):
        logger.warning("command failed or timed out: %s", cmd[0], exc_info=True)
        return None


def _video_duration(path):
    if shutil.which("ffprobe") is None:
        return None
    res = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", path], 30)
    if res is None or res.returncode != 0:
        return None
    try:
        d = float(res.stdout.decode().strip())
        return d if d > 0 else None
    except ValueError:
        return None


def video_fingerprint(path, registry=False):
    from PIL import Image, ImageOps

    if shutil.which("ffmpeg") is None:
        logger.error("ffmpeg not found - video fingerprints are skipped. Install ffmpeg.")
        return None
    n = max(4, int(_cfg("COPYRIGHT_FP_VIDEO_FRAMES", 12)))
    dur = _video_duration(path)
    times = [dur * (i + 0.5) / n for i in range(n)] if dur else [1 + 2 * i for i in range(n)]
    frames, mirrored = [], []
    for t in times:
        res = _run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                    "-vf", "scale=128:-1", "-f", "image2pipe", "-vcodec", "png", "-"], 30)
        if res is None or res.returncode != 0 or not res.stdout:
            continue
        try:
            with Image.open(io.BytesIO(res.stdout)) as img:
                img.load()
                h = _dhash(img)
                if h is None:
                    continue
                frames.append(h)
                if registry:
                    f = _dhash(ImageOps.mirror(img))
                    if f is not None:
                        mirrored.append(f)
        except Exception:
            continue
    if len(frames) < 3:
        return None
    allh = frames + mirrored
    bands = sorted({k for x in allh for k in _hash_bands(x)}) if registry else sorted(
        {k for x in frames for k in _hash_bands(x)})
    data = {"f": [f"{x:016x}" for x in frames]}
    if mirrored:
        data["ff"] = [f"{x:016x}" for x in mirrored]
    return Fingerprint(VIDEO, VFRAMES, data, bands)


def _compare_frames(query, row, label):
    dist = int(_cfg("COPYRIGHT_FP_VIDEO_FRAME_DISTANCE", 8))
    need = float(_cfg("COPYRIGHT_FP_VIDEO_MATCH_RATIO", 0.6))
    q = [int(x, 16) for x in query.data.get("f", [])]
    stored = [int(x, 16) for x in (row.data or {}).get("f", []) + (row.data or {}).get("ff", [])]
    if not q or not stored:
        return None
    hit = sum(1 for x in q if min(_hamming(x, y) for y in stored) <= dist)
    ratio = hit / len(q)
    if hit >= 3 and ratio >= need:
        return ratio, f"{label}: {hit}/{len(q)} frames match"
    return None


# --------------------------------------------------------------------------
# audio (Chromaprint)
# --------------------------------------------------------------------------
def audio_fingerprint(path):
    if shutil.which("fpcalc") is None:
        logger.error("fpcalc (Chromaprint) not found - audio fingerprints are skipped. "
                     "Install it: apt install libchromaprint-tools")
        return None
    secs = int(_cfg("COPYRIGHT_FP_AUDIO_SECONDS", 120))
    res = _run(["fpcalc", "-raw", "-json", "-length", str(secs), path], 90)
    if res is None or res.returncode != 0:
        return None
    try:
        payload = json.loads(res.stdout.decode() or "{}")
        raw = payload.get("fingerprint")
        if isinstance(raw, str):
            raw = [int(x) for x in raw.split(",") if x.strip()]
        items = [int(x) & 0xFFFFFFFF for x in raw]
    except (ValueError, TypeError):
        return None
    if len(items) * _AUDIO_ITEM_SECONDS < float(_cfg("COPYRIGHT_FP_AUDIO_MIN_SECONDS", 15)):
        return None  # too short: a jingle / short sound would match too many things
    return Fingerprint(AUDIO, CHROMA, {"a": items, "d": payload.get("duration")}, [])


def _compare_audio(query, row):
    a = query.data.get("a") or []
    b = (row.data or {}).get("a") or []
    min_items = int(float(_cfg("COPYRIGHT_FP_AUDIO_MIN_SECONDS", 15)) / _AUDIO_ITEM_SECONDS)
    if len(a) < min_items or len(b) < min_items:
        return None
    best = 1.0
    for shift in range(-40, 41):
        x, y = (a[shift:], b) if shift >= 0 else (a, b[-shift:])
        n = min(len(x), len(y))
        if n < min_items:
            continue
        errs = sum(_popcount(x[i] ^ y[i]) for i in range(n))
        best = min(best, errs / (32.0 * n))
    if best <= float(_cfg("COPYRIGHT_FP_AUDIO_MAX_BER", 0.30)):
        return 1.0 - best, f"audio bit-error rate {best:.2f}"
    return None


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------
def _seeds():
    out = []
    for i in range(_MH_N):
        d = hashlib.blake2b(f"mh{i}".encode(), digest_size=16).digest()
        out.append((int.from_bytes(d[:8], "big") | 1, int.from_bytes(d[8:], "big")))
    return out


_SEEDS = _seeds()


def _minhash(shingles):
    sig = [_MH_PRIME] * _MH_N
    for h in shingles:
        for i, (a, b) in enumerate(_SEEDS):
            v = (a * h + b) % _MH_PRIME
            if v < sig[i]:
                sig[i] = v
    return sig


def _minhash_bands(sig):
    out = []
    for b in range(_MH_N // 4):
        part = ",".join(str(x) for x in sig[b * 4:(b + 1) * 4])
        out.append(f"p{b}:" + hashlib.blake2b(part.encode(), digest_size=6).hexdigest())
    return out


def pdf_fingerprint(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.error("pypdf not installed - PDF fingerprints are skipped (pip install pypdf)")
        return None
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return None
        pages = list(reader.pages)[: int(_cfg("COPYRIGHT_FP_PDF_MAX_PAGES", 40))]
        texts = []
        for p in pages:
            try:
                texts.append(p.extract_text() or "")
            except Exception:
                continue
        text = re.sub(r"[^\w\u0900-\u097F]+", " ", " ".join(texts).lower())  # keeps Devanagari marks
        words = [w for w in text.split() if w][:30000]
        if len(words) >= int(_cfg("COPYRIGHT_FP_PDF_MIN_WORDS", 80)):
            shingles = {int.from_bytes(hashlib.blake2b(" ".join(words[i:i + _SHINGLE]).encode(),
                                                       digest_size=8).digest(), "big")
                        for i in range(len(words) - _SHINGLE + 1)}
            sig = _minhash(shingles)
            return Fingerprint(PDF, MINHASH, {"m": [format(x, "x") for x in sig]}, _minhash_bands(sig))
        return _pdf_image_fingerprint(pages)  # scanned PDF: no text layer
    except Exception:
        logger.info("pdf fingerprint failed for %s", path, exc_info=True)
        return None


def _pdf_image_fingerprint(pages):
    from PIL import Image

    hashes = []
    for p in pages[:8]:
        try:
            for im in list(p.images)[:2]:
                with Image.open(io.BytesIO(im.data)) as img:
                    img.load()
                    h = _dhash(_prepare(img))
                if h is not None:
                    hashes.append(h)
        except Exception:
            continue
    if len(hashes) < 2:
        return None
    bands = sorted({k for x in hashes for k in _hash_bands(x)})
    return Fingerprint(PDF, PDFIMG, {"f": [f"{x:016x}" for x in hashes]}, bands)


def _compare_minhash(query, row):
    a = query.data.get("m") or []
    b = (row.data or {}).get("m") or []
    if len(a) != len(b) or not a:
        return None
    jac = sum(1 for x, y in zip(a, b) if x == y) / len(a)
    if jac >= float(_cfg("COPYRIGHT_FP_PDF_MIN_JACCARD", 0.6)):
        return jac, f"pdf text similarity ~{jac:.0%}"
    return None


# --------------------------------------------------------------------------
# compute
# --------------------------------------------------------------------------
def compute(path, name="", registry=False):
    """-> [Fingerprint] for a LOCAL file. `registry=True` adds the mirrored variants (used when a
    removed file is stored); uploads are compared with registry=False."""
    if not enabled():
        return []
    kind = sniff_kind(path, name)
    out = []
    try:
        if kind == IMAGE:
            out.append(image_fingerprint(path, registry))
        elif kind == VIDEO:
            out.append(video_fingerprint(path, registry))
            out.append(audio_fingerprint(path))   # a re-edited video keeps the soundtrack
        elif kind == AUDIO:
            out.append(audio_fingerprint(path))
        elif kind == PDF:
            out.append(pdf_fingerprint(path))
    except Exception:
        logger.exception("fingerprint failed for %s", name or path)
    return [f for f in out if f is not None]


def _to_temp(name):
    fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(name)[1] or "")
    try:
        with os.fdopen(fd, "wb") as dst, default_storage.open(name, "rb") as src:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(chunk)
    except Exception:
        os.unlink(tmp)
        raise
    return tmp


def compute_for_storage(name, registry=False):
    """Same as compute() for a file inside Django storage (local disk or S3)."""
    if not name or not enabled():
        return []
    try:
        tmp = _to_temp(name)
    except Exception:
        logger.warning("could not read %s for fingerprinting", name, exc_info=True)
        return []
    try:
        return compute(tmp, name, registry)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# --------------------------------------------------------------------------
# registry (removed content)
# --------------------------------------------------------------------------
def any_active():
    from .models import MediaFingerprint

    return enabled() and MediaFingerprint.objects.filter(active=True).exists()


def register_removed(name, *, claim_id=None, legal_order_id=None, sha256=""):
    """Fingerprint a file that is being REMOVED (call before it is moved to quarantine).
    -> [MediaFingerprint ids]. Safe to repeat (same file + algo is reused, not duplicated)."""
    from .models import FingerprintBand, MediaFingerprint

    ids = []
    for fp in compute_for_storage(name, registry=True):
        row = MediaFingerprint.objects.filter(source_sha256=sha256, algo=fp.algo).first() if sha256 else None
        if row is not None:
            if not row.active or (claim_id and row.claim_id != claim_id):
                MediaFingerprint.objects.filter(pk=row.pk).update(
                    active=True, claim_id=claim_id or row.claim_id, legal_order_id=legal_order_id or row.legal_order_id)
            ids.append(row.pk)
            continue
        row = MediaFingerprint.objects.create(
            kind=fp.kind, algo=fp.algo, data=fp.data, source_sha256=sha256 or "", source_name=name[:300],
            claim_id=claim_id, legal_order_id=legal_order_id, active=True)
        FingerprintBand.objects.bulk_create(
            [FingerprintBand(fingerprint=row, key=k) for k in fp.bands], ignore_conflicts=True)
        ids.append(row.pk)
    return ids


def deactivate(ids=(), sha256s=()):
    """Stop matching against these (the content was restored / a licence was proven)."""
    from .models import MediaFingerprint

    n = 0
    if ids:
        n += MediaFingerprint.objects.filter(pk__in=list(ids)).update(active=False)
    shas = [s for s in sha256s if s]
    if shas:
        n += MediaFingerprint.objects.filter(source_sha256__in=shas, active=True).update(active=False)
    return n


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------
def _compare(query, row):
    if query.algo == DHASH:
        return _compare_image(query, row)
    if query.algo in (VFRAMES, PDFIMG):
        return _compare_frames(query, row, "video" if query.algo == VFRAMES else "scanned pdf")
    if query.algo == CHROMA:
        return _compare_audio(query, row)
    if query.algo == MINHASH:
        return _compare_minhash(query, row)
    return None


def find_match(fps):
    """Best active registry match for any of `fps`, or None."""
    from .models import FingerprintBand, MediaFingerprint

    best = None
    for fp in fps:
        if fp.algo == CHROMA:
            rows = MediaFingerprint.objects.filter(active=True, algo=CHROMA).order_by("-id")[
                : int(_cfg("COPYRIGHT_FP_AUDIO_MAX_SCAN", 2000))]
        elif fp.bands:
            ids = list(FingerprintBand.objects.filter(
                key__in=fp.bands, fingerprint__active=True, fingerprint__algo=fp.algo,
            ).values_list("fingerprint_id", flat=True).distinct()[:500])
            rows = MediaFingerprint.objects.filter(pk__in=ids, active=True)
        else:
            continue
        for row in rows:
            hit = _compare(fp, row)
            if hit and (best is None or hit[0] > best.score):
                best = Match(row.pk, row.claim_id, row.legal_order_id, fp.kind, fp.algo, hit[0], hit[1])
    return best


def match_storage_file(name):
    """Fingerprint a stored upload and look it up. Never raises."""
    try:
        if not any_active():
            return None
        return find_match(compute_for_storage(name))
    except Exception:
        logger.exception("fingerprint match failed for %s", name)
        return None
