# login/disposable.py
"""
L1 gap 18 -- block disposable e-mail domains and throw-away / fake phone numbers.

WHY: signup and the OTP "send code" endpoint accepted ANY address / number, so one
person could mint unlimited accounts from 10-minute mailboxes, or pump SMS to
made-up numbers (SMS-pumping costs real money on MSG91). This file answers one
question for the views and serializers: "is this NEW identifier acceptable?".

What is checked
---------------
E-mail   the domain (and every parent domain, so ``x.mailinator.com`` is caught
         too) against
            1. BUILTIN_DISPOSABLE_DOMAINS below (well-known throw-away services),
            2. DISPOSABLE_EMAIL_EXTRA_DOMAINS   (env, comma separated),
            3. DISPOSABLE_EMAIL_LIST_FILE       (env path, one domain per line --
               drop the public "disposable-email-domains" list there and refresh it
               whenever you like, no code change or redeploy of this file needed).
         DISPOSABLE_EMAIL_ALLOWED_DOMAINS always wins (false-positive escape hatch).
Phone    * fake looking numbers: last 8 digits all the same / "abababab"
         * Indian numbers (+91) whose national part is not a valid mobile range
           (real Indian mobiles start with 6-9 and have 10 digits)
         * BLOCKED_PHONE_PREFIXES (env, comma separated, digits only)  -> "virtual"
           ranges you decide to refuse
         * OPTIONAL carrier lookup (PHONE_LOOKUP_PROVIDER=twilio): numbers whose
           line type is in PHONE_BLOCKED_LINE_TYPES (default: VoIP) are refused.
           Cached, and FAILS OPEN -- a lookup outage never blocks a real signup.

Only NEW identifiers are judged. An existing account keeps working even if its
address was added before this rule existed (OTP login, forgot-password).

Every function returns ``None`` when fine, or ``(code, message)`` where ``code`` is
what the app translates (see ERROR_CODES.md) -- same contract as login/errors.py.
Master switch: DISPOSABLE_BLOCK_ENABLED (default True).
"""
import logging
import os
import re

import requests
from django.conf import settings
from django.core.cache import cache

from .login_guard import _hash

logger = logging.getLogger("security.signup")

# ---- error codes (top-level, from send-otp) ------------------------------------
CODE_DISPOSABLE_EMAIL = "disposable_email"
CODE_DISPOSABLE_PHONE = "disposable_phone"
CODE_INVALID_PHONE = "phone_invalid_number"

# ---- field rule codes (inside ``error_codes`` of a signup validation failure) ---
FIELD_EMAIL_DISPOSABLE = "email_disposable"
FIELD_PHONE_DISPOSABLE = "phone_disposable"
FIELD_PHONE_INVALID = "phone_invalid_number"

MSG_DISPOSABLE_EMAIL = "Temporary or disposable email addresses aren't allowed. Please use your real email."
MSG_DISPOSABLE_PHONE = "This looks like a virtual or temporary phone number. Please use your own mobile number."
MSG_INVALID_PHONE = "This doesn't look like a real mobile number. Please check it and try again."


def _cfg_bool(name, default=True):
    value = getattr(settings, name, default)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _cfg_set(name):
    """A settings value that may be a list/tuple/set or a comma separated string."""
    raw = getattr(settings, name, None) or ()
    if isinstance(raw, str):
        raw = raw.split(",")
    return {str(item).strip().lower().lstrip("@.") for item in raw if str(item).strip()}


def enabled():
    return _cfg_bool("DISPOSABLE_BLOCK_ENABLED", True)


# ---------------------------------------------------------------------------
# E-mail
# ---------------------------------------------------------------------------
# Well-known throw-away mailbox services. Kept deliberately to domains that are
# disposable-only (never a normal provider) so real users are not blocked. The
# list is only a floor: extend it with DISPOSABLE_EMAIL_EXTRA_DOMAINS /
# DISPOSABLE_EMAIL_LIST_FILE.
BUILTIN_DISPOSABLE_DOMAINS = frozenset(
    """
    mailinator.com mailinator.net mailinator2.com mailinater.com tmailinator.com
    guerrillamail.com guerrillamail.net guerrillamail.org guerrillamail.biz guerrillamail.de
    guerrillamail.info guerrillamailblock.com sharklasers.com grr.la pokemail.net spam4.me
    10minutemail.com 10minutemail.net 10minutemail.co.uk 10minutemail.org 20minutemail.com
    tempmail.com tempmail.net tempmail.plus tempmail.dev tempmailo.com temp-mail.org temp-mail.io
    temp-mail.ru tempail.com tempr.email tempinbox.com tempinbox.co.uk tempemail.com tempemail.net
    tempemail.biz tempe-mail.com tempomail.fr temporaryemail.net temporaryforwarding.com
    temporaryinbox.com tmpmail.org tmpmail.net tmpeml.com emltmp.com
    throwawaymail.com throwam.com trashmail.com trashmail.net trashmail.org trashmail.de
    trashmail.me trashmail.io trashmail.ws trashmail.at trash-mail.com trash-mail.at trash-amil.com
    trash2009.com trashdevil.com trashemail.de trashymail.com mytrashmail.com
    yopmail.com yopmail.fr yopmail.net cool.fr.nf jetable.org nospam.ze.tc nomail.xl.cx
    mega.zik.dj speed.1s.fr courriel.fr.nf moncourrier.fr.nf monemail.fr.nf monmail.fr.nf
    dispostable.com maildrop.cc mailnesia.com mailcatch.com mintemail.com mytemp.email
    mohmal.com getnada.com nada.email nada.ltd abyssmail.com boximail.com clrmail.com
    dropjar.com getairmail.com givmail.com inboxbear.com robot-mail.com tafmail.com vomoto.com
    zetmail.com fakeinbox.com fakemailgenerator.com fakemail.net email-fake.com emailfake.com
    emailondeck.com mail-temp.com mailpoof.com mailforspam.com spamgourmet.com spambox.us
    spamex.com spamfree24.org spamhole.com spaml.com spaml.de spamavert.com spambob.com
    spambog.com spamcero.com spamcon.org spamcorptastic.com spamday.com spameater.com
    spamify.com spaminator.de spamkill.info spamobox.com spamslicer.com spamspot.com
    spamthis.co.uk spamtroll.net sendspamhere.com flyspam.com haltospam.com whyspam.me
    discard.email discardmail.com discardmail.de burnermail.io burnthespam.info
    anonbox.net anonymbox.com binkmail.com bobmail.info bugmenot.com byom.de chammy.info
    deadaddress.com despam.it devnullmail.com dodgeit.com dodgit.com dump-email.info dumpmail.de
    e4ward.com einrot.com emailias.com emailsensei.com emailtemporanea.com emailtemporario.com.br
    evopo.com filzmail.com fixmail.tk get2mail.fr gishpuppy.com great-host.in harakirimail.com
    hulapla.de imails.info incognitomail.com instant-mail.de kasmail.com kurzepost.de
    lookugly.com lortemail.dk mail-temporaire.fr mailexpire.com mailfreeonline.com mailguard.me
    mailin8r.com mailmoat.com mailms.com mailnull.com mailsiphon.com mailzilla.com meltmail.com
    messagebeamer.de mt2015.com mypartyclip.de neverbox.com no-spam.ws nobulk.com noclickemail.com
    nogmailspam.info nowmymail.com objectmail.com obobbo.com oneoffemail.com onewaymail.com
    pjjkp.com plexolan.de pookmail.com privy-mail.com proxymail.eu putthisinyourspamdatabase.com
    quickinbox.com rcpt.at recode.me regbypass.com rmqkr.net safetymail.info sandelf.de
    selfdestructingmail.com shieldedmail.com shiftmail.com skeefmail.com slopsbox.com
    smellfear.com snakemail.com sneakemail.com sofort-mail.de sogetthis.com soodonims.com
    supergreatmail.com suremail.info thankyou2010.com thisisnotmyrealemail.com throam.com
    tilien.com tmail.ws toomail.biz turual.com twinmail.de tyldd.com uggsrock.com upliftnow.com
    uplipht.com venompen.com veryrealemail.com viditag.com viralplays.com vpn.st vsimcard.com
    wegwerfadresse.de wegwerfemail.de wegwerfmail.de wegwerfmail.net wegwerfmail.org wh4f.org
    willhackforfood.biz willselfdestruct.com winemaven.info wronghead.com wuzup.net wuzupmail.net
    xagloo.com xemaps.com xents.com xmaily.com xoxy.net yapped.net yep.it yogamaven.com
    yuurok.com zehnminutenmail.de zippymail.info zoaxe.com zoemail.org mailtothis.com
    1secmail.com 1secmail.org 1secmail.net esiix.com wwjmp.com xojxe.com yoggm.com kzccv.com
    qiott.com wuuvo.com icznn.com ezztt.com vjuum.com laafd.com txcct.com dcctb.com
    mailto.plus fexpost.com fexbox.org rover.info inpwa.com chitthi.in any.pink merepost.com
    moakt.com moakt.ws moakt.cc crazymailing.com tempsky.com luxusmail.org ephemail.net
    owlymail.com mvrht.com dropmail.me emlhub.com mail7.io inboxkitten.com yomail.info
    armyspy.com cuvox.de dayrep.com fleckens.hu gustr.com jourrapide.com rhyta.com
    superrito.com teleworm.us spam.la mail.tm
    """.split()
)


def email_domain(email):
    """Lower-cased domain of ``email`` ('' if there isn't one)."""
    return (email or "").strip().lower().rpartition("@")[2].strip(".")


def _domain_chain(domain):
    """``a.b.c.com`` -> ['a.b.c.com', 'b.c.com', 'c.com'] (never the bare TLD)."""
    labels = [part for part in domain.split(".") if part]
    return [".".join(labels[i:]) for i in range(max(len(labels) - 1, 0))]


_file_cache = {"path": None, "mtime": None, "domains": frozenset()}


def _file_domains():
    """Domains from DISPOSABLE_EMAIL_LIST_FILE, re-read only when the file changes."""
    path = getattr(settings, "DISPOSABLE_EMAIL_LIST_FILE", "") or ""
    if not path:
        return frozenset()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        logger.warning("DISPOSABLE_EMAIL_LIST_FILE %s is not readable", path)
        return frozenset()
    if _file_cache["path"] == path and _file_cache["mtime"] == mtime:
        return _file_cache["domains"]
    domains = set()
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.split("#", 1)[0].strip().lower().lstrip("@.")
                if line:
                    domains.add(line)
    except OSError:
        logger.warning("DISPOSABLE_EMAIL_LIST_FILE %s could not be read", path)
        return frozenset()
    _file_cache.update(path=path, mtime=mtime, domains=frozenset(domains))
    return _file_cache["domains"]


def is_disposable_email(email):
    """True if ``email`` belongs to a throw-away mailbox provider."""
    if not enabled():
        return False
    domain = email_domain(email)
    if not domain:
        return False
    chain = _domain_chain(domain) or [domain]
    allowed = _cfg_set("DISPOSABLE_EMAIL_ALLOWED_DOMAINS")
    if any(candidate in allowed for candidate in chain):
        return False
    blocked = BUILTIN_DISPOSABLE_DOMAINS | _cfg_set("DISPOSABLE_EMAIL_EXTRA_DOMAINS") | _file_domains()
    return any(candidate in blocked for candidate in chain)


def email_problem(email):
    """``(code, message)`` when ``email`` must be refused, else None."""
    if is_disposable_email(email):
        logger.info("disposable email refused: domain=%s", email_domain(email))
        return CODE_DISPOSABLE_EMAIL, MSG_DISPOSABLE_EMAIL
    return None


# ---------------------------------------------------------------------------
# Phone
# ---------------------------------------------------------------------------
_REPEATING_TAIL = re.compile(r"(\d{1,2})\1+")          # 77777777 / 12121212
_INDIA_MOBILE = re.compile(r"^[6-9]\d{9}$")


def _digits(phone):
    return re.sub(r"\D", "", phone or "")


def _is_indian(digits):
    """``+91`` / ``91`` followed by exactly 10 national digits (91 is only India's code)."""
    return digits.startswith("91") and len(digits) == 12


def _looks_fake(digits):
    tail = digits[-8:]
    return len(tail) == 8 and _REPEATING_TAIL.fullmatch(tail) is not None


def phone_static_problem(phone):
    """Cheap, offline checks: fake patterns, bad Indian range, blocked prefixes."""
    if not enabled():
        return None
    digits = _digits(phone)
    if not digits:
        return None

    if _looks_fake(digits):
        return CODE_INVALID_PHONE, MSG_INVALID_PHONE

    if _is_indian(digits) and not _INDIA_MOBILE.match(digits[2:]):
        return CODE_INVALID_PHONE, MSG_INVALID_PHONE

    for prefix in _cfg_set("BLOCKED_PHONE_PREFIXES"):
        prefix = _digits(prefix)
        if prefix and digits.startswith(prefix):
            logger.info("blocked phone prefix refused: prefix=%s", prefix)
            return CODE_DISPOSABLE_PHONE, MSG_DISPOSABLE_PHONE
    return None


# --- optional carrier lookup (Twilio Lookup v2, line type intelligence) -------
_LOOKUP_URL = "https://lookups.twilio.com/v2/PhoneNumbers/{number}"
_LOOKUP_TIMEOUT = 4
_LOOKUP_CACHE_SECONDS = 7 * 24 * 3600


def _blocked_line_types():
    raw = getattr(settings, "PHONE_BLOCKED_LINE_TYPES", "nonFixedVoip,voip")
    if isinstance(raw, str):
        raw = raw.split(",")
    return {str(item).strip().lower() for item in raw if str(item).strip()}


def lookup_line_type(phone):
    """Line type of ``phone`` ("mobile", "nonFixedVoip", ...) or None if unknown.

    Never raises: a missing config, a provider outage or a bad answer is "unknown".
    """
    provider = (getattr(settings, "PHONE_LOOKUP_PROVIDER", "") or "").strip().lower()
    if provider != "twilio":
        return None
    sid = getattr(settings, "TWILIO_ACCOUNT_SID", "") or ""
    token = getattr(settings, "TWILIO_AUTH_TOKEN", "") or ""
    digits = _digits(phone)
    if not (sid and token and digits):
        return None

    cache_key = f"phonelookup:{_hash(digits)}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached or None  # "" = looked up before, type unknown

    try:
        response = requests.get(
            _LOOKUP_URL.format(number="+" + digits),
            params={"Fields": "line_type_intelligence"},
            auth=(sid, token),
            timeout=_LOOKUP_TIMEOUT,
        )
        if response.status_code != 200:
            logger.warning("phone lookup answered %s", response.status_code)
            return None
        info = (response.json() or {}).get("line_type_intelligence") or {}
        line_type = (info.get("type") or "").strip()
    except (requests.RequestException, ValueError):
        logger.warning("phone lookup failed", exc_info=True)
        return None

    cache.set(cache_key, line_type, _LOOKUP_CACHE_SECONDS)
    return line_type or None


def phone_lookup_problem(phone):
    """Online check (paid, cached, fail-open). Call it AFTER the cheap checks and
    after captcha / rate limits so it is only spent on a request that is going on."""
    if not enabled():
        return None
    line_type = lookup_line_type(phone)
    if line_type and line_type.lower() in _blocked_line_types():
        logger.info("virtual phone refused: line_type=%s", line_type)
        return CODE_DISPOSABLE_PHONE, MSG_DISPOSABLE_PHONE
    return None


def phone_problem(phone):
    """Offline checks only -- what serializers use. Returns ``(code, message)`` or None."""
    return phone_static_problem(phone)


# ---------------------------------------------------------------------------
# One entry point for "a NEW email/phone is being registered"
# ---------------------------------------------------------------------------
def new_identifier_problem(value, kind, *, online=False):
    """``kind`` is identifiers.KIND_EMAIL / KIND_PHONE. ``online=True`` adds the
    carrier lookup for phones."""
    if kind == "email":
        return email_problem(value)
    if kind == "phone":
        return phone_static_problem(value) or (phone_lookup_problem(value) if online else None)
    return None
