# login/captcha.py
"""
L1 gap 18 -- captcha for the abuse-prone login endpoints.

Before this there was no captcha anywhere: a script could request OTPs
(SMS-pumping), guess passwords and trigger reset mails as fast as the IP
throttles allowed. This adds a challenge that is shown only when it is needed.

Where it is enforced (login/views.py)
-------------------------------------
    send-otp          signup / OTP login / adding a phone     scope "send_otp"
    forgot-password   reset-code request                      scope "forgot_password"
    login             password login, after wrong passwords   scope "login"

Modes (settings.CAPTCHA_MODE, env CAPTCHA_MODE)
    off       never ask                       (default under tests)
    adaptive  ask only when the traffic looks automated (default):
                * send-otp / forgot-password: from the 3rd request an IP makes
                  inside CAPTCHA_RISK_WINDOW_SECONDS (CAPTCHA_SEND_OTP_FREE_REQUESTS /
                  CAPTCHA_FORGOT_FREE_REQUESTS free ones) -- a normal signup
                  (send + one resend) never sees a captcha;
                * login: after CAPTCHA_LOGIN_AFTER_FAILURES wrong passwords for that
                  account (default 3) -- before the 5-try lockout kicks in.
    always    ask on every call to those endpoints

Providers (settings.CAPTCHA_PROVIDER, env CAPTCHA_PROVIDER)
    builtin     (default) a server-drawn image of 5 letters. Needs no account, no
                keys and no WebView, so the Flutter app can show it natively. One
                use only, expires after CAPTCHA_TTL_SECONDS (180).
    turnstile | hcaptcha | recaptcha
                token verified server-side with CAPTCHA_SECRET_KEY (site key goes to
                the client as ``captcha_site_key``). For web / WebView clients.
                If the secret is missing we fall back to builtin so protection never
                silently turns off. A provider OUTAGE fails open (CAPTCHA_FAIL_OPEN,
                default True) -- the other limits (OTP caps, login lockout) still hold.

Wire contract
-------------
    400 {"code": "captcha_required" | "captcha_invalid",
         "captcha_provider": "builtin",
         "captcha": {"provider","captcha_id","image":"data:image/png;base64,...","expires_in"}}
    The client solves it and RETRIES THE SAME REQUEST with extra body fields
        builtin : captcha_id, captcha_answer
        others  : captcha_token
    A fresh image can also be fetched with GET /login/auth/captcha/.
    A missing/wrong captcha is answered BEFORE the password / code is looked at, so
    it never counts as a failed login and never spends an OTP.
"""
import base64
import hashlib
import hmac
import io
import logging
import secrets

import requests
from django.conf import settings
from django.core.cache import cache
from rest_framework import status

from .errors import api_error
from .login_guard import _hash, client_ip, incr_with_ttl

logger = logging.getLogger("security.captcha")

CODE_REQUIRED = "captcha_required"
CODE_INVALID = "captcha_invalid"

SCOPE_SEND_OTP = "send_otp"
SCOPE_FORGOT_PASSWORD = "forgot_password"
SCOPE_LOGIN = "login"

MODE_OFF, MODE_ADAPTIVE, MODE_ALWAYS = "off", "adaptive", "always"
PROVIDER_BUILTIN = "builtin"
_REMOTE_PROVIDERS = {
    "turnstile": "https://challenges.cloudflare.com/turnstile/v0/siteverify",
    "hcaptcha": "https://api.hcaptcha.com/siteverify",
    "recaptcha": "https://www.google.com/recaptcha/api/siteverify",
}

# No I, L, O, 0, 1 -- the letters people misread in a distorted image.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_LENGTH = 5
_IMG_W, _IMG_H = 220, 80


def _cfg_int(name, default):
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


def _cfg_bool(name, default):
    value = getattr(settings, name, default)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def mode():
    value = str(getattr(settings, "CAPTCHA_MODE", MODE_ADAPTIVE) or MODE_ADAPTIVE).strip().lower()
    return value if value in (MODE_OFF, MODE_ADAPTIVE, MODE_ALWAYS) else MODE_ADAPTIVE


def provider():
    """The provider actually in use (builtin when a remote one has no secret key)."""
    value = str(getattr(settings, "CAPTCHA_PROVIDER", PROVIDER_BUILTIN) or PROVIDER_BUILTIN).strip().lower()
    if value in _REMOTE_PROVIDERS:
        if getattr(settings, "CAPTCHA_SECRET_KEY", ""):
            return value
        logger.error("CAPTCHA_PROVIDER=%s but CAPTCHA_SECRET_KEY is empty -- using the built-in captcha", value)
    return PROVIDER_BUILTIN


def free_requests_for(scope):
    """How many calls an IP may make per window before adaptive mode asks. None = never by volume."""
    if scope == SCOPE_SEND_OTP:
        return _cfg_int("CAPTCHA_SEND_OTP_FREE_REQUESTS", 2)
    if scope == SCOPE_FORGOT_PASSWORD:
        return _cfg_int("CAPTCHA_FORGOT_FREE_REQUESTS", 2)
    return None


# ---------------------------------------------------------------------------
# built-in image captcha
# ---------------------------------------------------------------------------
def _sign(captcha_id, text):
    key = str(settings.SECRET_KEY).encode()
    return hmac.new(key, f"{captcha_id}:{text}".encode(), hashlib.sha256).hexdigest()


def _challenge_key(captcha_id):
    return f"captcha:ch:{captcha_id}"


def _load_font(ImageFont):
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, 46)
        except (OSError, ValueError):
            continue
    try:
        return ImageFont.load_default(size=46)  # Pillow >= 10.1 (scalable)
    except TypeError:
        return ImageFont.load_default()  # tiny bitmap font; each glyph is scaled up below


def _render_png(text):
    """Distorted PNG of ``text`` (bytes). Pillow only -- no extra dependency."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    resample = getattr(Image, "Resampling", Image).BICUBIC
    rnd = secrets.SystemRandom()

    def colour(lo, hi):
        return (rnd.randint(lo, hi), rnd.randint(lo, hi), rnd.randint(lo, hi))

    img = Image.new("RGB", (_IMG_W, _IMG_H), (244, 244, 246))
    draw = ImageDraw.Draw(img)
    for _ in range(350):  # speckle
        draw.point((rnd.randrange(_IMG_W), rnd.randrange(_IMG_H)), fill=colour(140, 225))
    for _ in range(4):  # strike-through lines behind the text
        draw.line(
            [(rnd.randrange(_IMG_W), rnd.randrange(_IMG_H)), (rnd.randrange(_IMG_W), rnd.randrange(_IMG_H))],
            fill=colour(120, 200),
            width=2,
        )

    font = _load_font(ImageFont)
    slot = _IMG_W / (len(text) + 1)
    for i, ch in enumerate(text):
        tile = Image.new("L", (96, 96), 0)
        ImageDraw.Draw(tile).text((14, 10), ch, fill=255, font=font)
        box = tile.getbbox()
        if box:
            tile = tile.crop(box)
        target_h = rnd.randint(38, 52)
        scale = target_h / max(tile.height, 1)
        tile = tile.resize((max(1, int(tile.width * scale)), target_h), resample)
        tile = tile.rotate(rnd.uniform(-24, 24), expand=True, resample=resample)
        solid = Image.new("RGB", tile.size, colour(0, 95))
        x = int(slot * (i + 1) - tile.width / 2 + rnd.randint(-4, 4))
        y = int((_IMG_H - tile.height) / 2 + rnd.randint(-6, 6))
        img.paste(solid, (x, y), tile)

    for _ in range(3):  # lines in front of the text
        draw.line(
            [(rnd.randrange(_IMG_W // 4), rnd.randrange(_IMG_H)), (rnd.randrange(_IMG_W // 2, _IMG_W), rnd.randrange(_IMG_H))],
            fill=colour(60, 140),
            width=2,
        )
    img = img.filter(ImageFilter.GaussianBlur(0.5))

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def new_builtin_challenge():
    ttl = _cfg_int("CAPTCHA_TTL_SECONDS", 180)
    text = "".join(secrets.choice(_ALPHABET) for _ in range(_LENGTH))
    captcha_id = secrets.token_urlsafe(18)
    # Only a keyed hash of the answer is stored -- the cache never holds the text itself.
    cache.set(_challenge_key(captcha_id), _sign(captcha_id, text), ttl)
    png = _render_png(text)
    return {
        "provider": PROVIDER_BUILTIN,
        "captcha_id": captcha_id,
        "image": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
        "expires_in": ttl,
    }


def _verify_builtin(captcha_id, answer):
    """One attempt per challenge: it is spent whether or not the answer is right."""
    if not captcha_id or not answer:
        return False
    key = _challenge_key(str(captcha_id)[:80])
    stored = cache.get(key)
    if stored is None:
        return False
    if cache.delete(key) is False:  # a parallel request spent it first
        return False
    guess = "".join(str(answer).split()).upper()
    return hmac.compare_digest(stored, _sign(str(captcha_id)[:80], guess))


# ---------------------------------------------------------------------------
# third-party providers (Turnstile / hCaptcha / reCAPTCHA)
# ---------------------------------------------------------------------------
def _verify_remote(name, token, ip):
    if not token:
        return False
    try:
        response = requests.post(
            _REMOTE_PROVIDERS[name],
            data={"secret": settings.CAPTCHA_SECRET_KEY, "response": str(token)[:4096], "remoteip": ip},
            timeout=5,
        )
        return bool((response.json() or {}).get("success"))
    except (requests.RequestException, ValueError):
        logger.warning("captcha provider %s unreachable", name, exc_info=True)
        return _cfg_bool("CAPTCHA_FAIL_OPEN", True)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def challenge_payload():
    """What a client needs to show a captcha right now."""
    name = provider()
    if name == PROVIDER_BUILTIN:
        return new_builtin_challenge()
    return {"provider": name, "site_key": getattr(settings, "CAPTCHA_SITE_KEY", "") or None}


def _fields(request):
    data = getattr(request, "data", None)
    get = getattr(data, "get", None)
    if get is None:
        return "", "", ""
    return (str(get("captcha_id") or ""), str(get("captcha_answer") or ""), str(get("captcha_token") or ""))


def _challenge_error(code, message):
    return api_error(
        message,
        code,
        status.HTTP_400_BAD_REQUEST,
        captcha_provider=provider(),
        captcha=challenge_payload(),
    )


def enforce(request, scope, *, force=False):
    """Decide whether this call needs a captcha and, if so, check it.

    Returns ``None`` when the request may go on, otherwise a ready-made error
    ``Response`` (``captcha_required`` / ``captcha_invalid``) for the view to return.

    ``force`` (login: wrong passwords so far) asks regardless of volume. Otherwise
    adaptive mode counts this IP's calls for ``scope`` and asks past the free limit.
    """
    current = mode()
    if current == MODE_OFF:
        return None

    required = force or current == MODE_ALWAYS
    if not required:
        free = free_requests_for(scope)
        if free is not None:
            window = _cfg_int("CAPTCHA_RISK_WINDOW_SECONDS", 3600)
            seen = incr_with_ttl(f"captcha:risk:{scope}:{_hash(client_ip(request))}", window)
            required = seen > free
    if not required:
        return None

    captcha_id, answer, token = _fields(request)
    if not (captcha_id or answer or token):
        return _challenge_error(CODE_REQUIRED, "Please complete the security check to continue.")

    name = provider()
    ok = (
        _verify_builtin(captcha_id, answer)
        if name == PROVIDER_BUILTIN
        else _verify_remote(name, token, client_ip(request))
    )
    if ok:
        return None
    logger.info("captcha failed: scope=%s provider=%s ip=%s", scope, name, _hash(client_ip(request))[:8])
    return _challenge_error(CODE_INVALID, "The security check was wrong. Please try again.")
