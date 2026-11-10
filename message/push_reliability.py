# message/push_reliability.py
"""
N3 — push reliability + token hygiene. Sab pure helpers (FCM ko yahan se call NAHI jaata; sender `push_utils._send_multicast` hai).

  * classify_error()        FCM error -> "dead" (token delete) | "transient" (retry) | "other" (sirf log)
  * reserve_dedupe()/release_dedupe()   same event 2 baar push na ho (cache.add based, per token)
  * retry_countdown()       exponential backoff (5s, 20s, 80s ...), TTL se chhota hi retry hota hai
  * register_device_token() `DeviceTokenView` ka DB kaam: upsert + same device par purani rows hatao + per-user cap
  * revoke_tokens_for_session() logout / sign-out par us session ke tokens delete
  * cleanup_stale_tokens()  Celery sweep: stale / bahut fail / orphan-session / 10+ per user
"""
import hashlib
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Exists, F, OuterRef
from django.utils import timezone

logger = logging.getLogger(__name__)

# ---- knobs (settings.py me override ho sakte hain) --------------------------------------------------------------
DEDUPE_TTL_SECONDS = int(getattr(settings, 'PUSH_DEDUPE_TTL_SECONDS', 120))
MAX_RETRIES = int(getattr(settings, 'PUSH_MAX_RETRIES', 3))
RETRY_BASE_SECONDS = int(getattr(settings, 'PUSH_RETRY_BASE_SECONDS', 5))
MAX_TOKENS_PER_USER = int(getattr(settings, 'PUSH_MAX_TOKENS_PER_USER', 10))
STALE_DAYS = int(getattr(settings, 'PUSH_TOKEN_STALE_DAYS', 60))
MAX_FAILURES = int(getattr(settings, 'PUSH_TOKEN_MAX_FAILURES', 5))
FAILED_GRACE_DAYS = int(getattr(settings, 'PUSH_TOKEN_FAILED_GRACE_DAYS', 7))

DEAD = 'dead'
TRANSIENT = 'transient'
OTHER = 'other'


# ======================================================================
# Error classification
# ======================================================================
def classify_error(err) -> str:
    """FCM per-token error -> DEAD | TRANSIENT | OTHER. Kabhi raise nahi karta.

    DEAD      token ab kabhi kaam nahi karega (app uninstall / token rotate / dusre Firebase project ka token) -> delete.
    TRANSIENT FCM/network ki temporary dikkat (503, 500, quota, timeout) -> backoff ke saath retry, token rakho.
    OTHER     config / payload ka masla (APNs auth, bad payload) -> token nahi hatate (ye galti token ki nahi), sirf log.
    """
    if err is None:
        return OTHER
    try:
        from firebase_admin import exceptions as fb_exc, messaging

        if isinstance(err, (messaging.UnregisteredError, messaging.SenderIdMismatchError)):
            return DEAD
        # Purane backends "NOT_FOUND" generic bhejte the (Unregistered ki jagah).
        if isinstance(err, fb_exc.NotFoundError):
            return DEAD
        if isinstance(err, fb_exc.InvalidArgumentError):
            # INVALID_ARGUMENT payload ki galti bhi ho sakti hai - sirf tab token delete jab FCM saaf bole ki
            # *registration token* invalid hai; warna ek bad payload sab healthy tokens uda deta.
            return DEAD if 'registration token' in str(err).lower() else OTHER
        if isinstance(err, (fb_exc.UnavailableError, fb_exc.InternalError, fb_exc.DeadlineExceededError,
                            fb_exc.ResourceExhaustedError, fb_exc.UnknownError)):
            return TRANSIENT
        # messaging.QuotaExceededError ResourceExhaustedError ka subclass hai (upar cover); ThirdPartyAuthError = OTHER.
        return OTHER
    except Exception:  # firebase_admin import / attribute problem - fail safe: token mat hatao
        return OTHER


def error_code(err) -> str:
    """Short, DB-safe label (<= 40 chars) for DeviceToken.last_failure_code."""
    code = getattr(err, 'code', None)
    if code:
        return str(code)[:40]
    return type(err).__name__[:40] if err is not None else ''


# ======================================================================
# Dedupe
# ======================================================================
def event_fingerprint(data):
    """Push data se "ye kaunsa event hai" ka stable naam, ya None (dedupe nahi).

    Jaan-boojh kar CONSERVATIVE: sirf wahi pushes dedupe hote hain jinme koi pakka event id hai (notification row id /
    call id / message id). `{'type': 'chat_digest', 'count': 5}` jaisa push (count badhta rehta hai, koi event id nahi)
    kabhi dedupe nahi hota - warna "5 messages" wala update kha jaata.
    """
    if not data:
        return None
    ptype = str(data.get('type') or data.get('notif_type') or '')
    for key in ('notification_id', 'call_id', 'message_id'):
        value = data.get(key)
        if value not in (None, ''):
            return f'{ptype}:{key}:{value}'
    return None


def _dedupe_cache_key(fingerprint, token) -> str:
    digest = hashlib.sha1(f'{fingerprint}|{token}'.encode('utf-8')).hexdigest()[:32]
    return f'pushdd:{digest}'


def reserve_dedupe(tokens, fingerprint, ttl=None):
    """(to_send, reserved_keys). `cache.add` atomic hai => do parallel sender me se sirf ek ko token milta hai.
    fingerprint None => sab tokens pass, kuch reserve nahi."""
    if not fingerprint:
        return list(tokens), {}
    ttl = ttl or DEDUPE_TTL_SECONDS
    to_send, keys = [], {}
    for token in tokens:
        key = _dedupe_cache_key(fingerprint, token)
        try:
            fresh = cache.add(key, 1, timeout=ttl)
        except Exception:  # cache down => fail-open (duplicate se achha hai ki push hi na jaye)
            logger.warning('push dedupe cache unavailable; sending without dedupe', exc_info=True)
            fresh = True
        if fresh:
            to_send.append(token)
            keys[token] = key
    return to_send, keys


def release_dedupe(keys, tokens):
    """Fail hue tokens ki reservation hatao taaki retry / agla sender wahi event dobara bhej sake."""
    for token in tokens:
        key = keys.get(token)
        if key:
            try:
                cache.delete(key)
            except Exception:
                pass


# ======================================================================
# Retry / backoff
# ======================================================================
def retry_countdown(attempt: int) -> int:
    """attempt 0 -> 5s, 1 -> 20s, 2 -> 80s (x4 har baar)."""
    return RETRY_BASE_SECONDS * (4 ** max(0, int(attempt)))


def should_retry(attempt: int, remaining_ttl=None) -> bool:
    """Retry tab tak jab tak attempts bache hain AUR push ki apni TTL (call ring ~30s) retry se pehle khatam na ho."""
    if attempt >= MAX_RETRIES:
        return False
    if remaining_ttl is not None and remaining_ttl <= retry_countdown(attempt) + 1:
        return False
    return True


# ======================================================================
# Token bookkeeping after a send
# ======================================================================
def record_send_result(ok_tokens, dead_tokens, failed_tokens, failure_codes=None):
    """Ek multicast ke baad DB hygiene. Kabhi raise nahi karta (push ka kaam isse fail nahi hona chahiye).

    dead_tokens   -> delete.
    ok_tokens     -> failure_count reset (sirf jinka count > 0 tha; healthy path par UPDATE 0 rows, cheap).
    failed_tokens -> FINAL transient failure (retries khatam) -> failure_count += 1.
    """
    from .models import DeviceToken

    failure_codes = failure_codes or {}
    try:
        if dead_tokens:
            deleted, _ = DeviceToken.objects.filter(token__in=list(dead_tokens)).delete()
            logger.info('push: removed %s dead FCM token(s)', deleted)
        if ok_tokens:
            DeviceToken.objects.filter(token__in=list(ok_tokens), failure_count__gt=0).update(
                failure_count=0, last_failure_code='',
            )
        now = timezone.now()
        for token in failed_tokens or ():
            DeviceToken.objects.filter(token=token).update(
                failure_count=F('failure_count') + 1, last_failure_at=now,
                last_failure_code=failure_codes.get(token, '')[:40],
            )
    except Exception:
        logger.warning('push: token bookkeeping failed', exc_info=True)


# ======================================================================
# Register / revoke
# ======================================================================
_CTRL = re.compile(r'[\x00-\x1f\x7f]')


def _clean(value, limit):
    if not isinstance(value, str):
        return ''
    return _CTRL.sub('', value).strip()[:limit]


def clean_device_meta(body) -> dict:
    """Client-supplied device metadata -> length-capped strings (informational only, kabhi authz me nahi)."""
    body = body if isinstance(body, dict) else {}
    device_id = _clean(body.get('device_id'), 64)
    if device_id and not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', device_id):
        device_id = ''
    return {
        'device_id': device_id,
        'device_name': _clean(body.get('device_name'), 120),
        'app_version': _clean(body.get('app_version'), 32),
        'os_version': _clean(body.get('os_version'), 32),
    }


_TOKEN_RE = re.compile(r'[\w:.\-=+/]{1,255}')
PLATFORMS = ('android', 'ios', 'web')


def valid_fcm_token(token) -> bool:
    """N3b - an FCM registration token is a short string without whitespace / control characters. DeviceToken.token is
    max_length=255 + unique: anything else would be a DB error (500) or a silently truncated key, so the API refuses it."""
    return isinstance(token, str) and bool(_TOKEN_RE.fullmatch(token))


def clean_platform(value, default='android'):
    """'android' | 'ios' | 'web' (case-insensitive); a missing value is the old default, anything else -> None (400)."""
    if value in (None, ''):
        return default
    value = str(value).strip().lower()
    return value if value in PLATFORMS else None


def revoke_tokens_for_request(user, *, token=None, session_jti='', device_id='') -> int:
    """N3b - DELETE /message/device-token/. With `token`: that one row (own rows only, as before). WITHOUT a token (the
    phone could not read its FCM token at sign-out) this session's rows go: same login session, else same device_id.
    Never touches another user's rows. Returns how many rows were removed."""
    from .models import DeviceToken

    if token:
        deleted, _ = DeviceToken.objects.filter(token=token, user=user).delete()
        return deleted
    if session_jti:
        return revoke_tokens_for_session(session_jti, user_id=user.id)
    if device_id:
        deleted, _ = DeviceToken.objects.filter(user=user, device_id=device_id).delete()
        return deleted
    return 0


def enforce_token_cap(user_id, keep_token=None, cap=None):
    """Ek user ke paas `cap` se zyada token ho to sabse purane (last_seen_at) hatao. Returns kitne hate."""
    from .models import DeviceToken

    cap = cap or MAX_TOKENS_PER_USER
    ids = list(
        DeviceToken.objects.filter(user_id=user_id)
        .order_by('-last_seen_at', '-created_at')
        .values_list('id', flat=True)[cap:]
    )
    if not ids:
        return 0
    qs = DeviceToken.objects.filter(id__in=ids)
    if keep_token:
        qs = qs.exclude(token=keep_token)
    deleted, _ = qs.delete()
    return deleted


def revoke_tokens_for_session(session_jti, user_id=None) -> int:
    """Is login session (AuthToken.jti) ke register kiye saare FCM tokens delete. Returns count."""
    from .models import DeviceToken

    if not session_jti:
        return 0
    qs = DeviceToken.objects.filter(session_jti=session_jti)
    if user_id is not None:
        qs = qs.filter(user_id=user_id)
    deleted, _ = qs.delete()
    return deleted


def on_session_deleted(auth_token) -> None:
    """`login.AuthToken` row delete hone par (logout / logout-all / sign-out one device / password change / replace).

    Normal case: us session ke tokens delete. Ek apvaad: SAME device par dobara login `issue_tokens_for_user` me purana
    row delete karke naya banata hai - tab device ko naya session mil gaya, token hatana galat hoga (client 12h tak
    dobara register nahi karta), isliye commit ke baad dekhte hain: usi (user, device_id) ka live session ho to
    tokens naye session par re-bind, warna delete.
    """
    from login.models import AuthToken

    from .models import DeviceToken

    jti = getattr(auth_token, 'jti', '') or ''
    if not jti:
        return
    user_id = getattr(auth_token, 'user_id', None)
    device_id = getattr(auth_token, 'device_id', '') or ''

    def _apply():
        try:
            if device_id and user_id:
                successor = (
                    AuthToken.objects.filter(user_id=user_id, device_id=device_id).exclude(jti=jti)
                    .order_by('-created_at').values_list('jti', flat=True).first()
                )
                if successor:
                    DeviceToken.objects.filter(session_jti=jti).update(session_jti=successor)
                    return
            revoke_tokens_for_session(jti)
        except Exception:
            logger.warning('push: session-token revoke failed (jti=%s)', jti, exc_info=True)

    transaction.on_commit(_apply)


def register_device_token(*, user, token, platform, meta, session_jti='', voip_provided=False, voip_token=None):
    """Upsert one device row. Returns (obj, created, moved_from_user_or_None).

    * same token, naya user (same phone, naya account) => row naye user par move (purana behaviour).
    * same (user, device_id), alag token => FCM token rotate hua: purani rows delete (stale-token ka sabse bada source).
    * last_seen_at har call par refresh; session_jti is login session ka.
    * per-user cap (MAX_TOKENS_PER_USER) baad me enforce.
    """
    from .models import DeviceToken

    now = timezone.now()
    defaults = {
        'user': user, 'platform': platform, 'voip_token': voip_token if voip_provided else None,
        'last_seen_at': now, 'session_jti': session_jti or '', 'failure_count': 0,
        **{k: v for k, v in meta.items()},
    }
    obj, created = DeviceToken.objects.select_related('user').get_or_create(token=token, defaults=defaults)
    moved_from = None
    if not created:
        if obj.user_id != user.id:
            moved_from = obj.user
        updates = {'last_seen_at': now, 'failure_count': 0, 'last_failure_code': ''}
        if moved_from is not None or obj.platform != platform:
            updates.update(user=user, platform=platform)
        if session_jti and obj.session_jti != session_jti:
            updates['session_jti'] = session_jti
        for key, value in meta.items():
            if value and getattr(obj, key) != value:  # blank metadata (purana app) pichli jaankari nahi mitata
                updates[key] = value
        if voip_provided and obj.voip_token != voip_token:
            updates['voip_token'] = voip_token
        updates['updated_at'] = now
        DeviceToken.objects.filter(pk=obj.pk).update(**updates)

    device_id = meta.get('device_id')
    if device_id:
        DeviceToken.objects.filter(user=user, device_id=device_id).exclude(token=token).delete()
    if voip_provided and voip_token:
        DeviceToken.objects.filter(voip_token=voip_token).exclude(token=token).update(voip_token=None)
    enforce_token_cap(user.id, keep_token=token)
    return obj, created, moved_from


# ======================================================================
# Periodic cleanup (Celery: message.cleanup_stale_device_tokens)
# ======================================================================
def cleanup_stale_tokens(now=None) -> dict:
    """Returns {"stale": n, "failing": n, "orphaned": n, "over_cap": n}."""
    from login.models import AuthToken

    from .models import DeviceToken

    now = now or timezone.now()
    out = {'stale': 0, 'failing': 0, 'orphaned': 0, 'over_cap': 0}

    # 1) bahut dino se dobara register hi nahi hua (app uninstall / phone band).
    out['stale'], _ = DeviceToken.objects.filter(last_seen_at__lt=now - timedelta(days=STALE_DAYS)).delete()

    # 2) lagataar fail + kuch dino se app khuli bhi nahi (khuli hoti to last_seen_at taaza hota).
    out['failing'], _ = DeviceToken.objects.filter(
        failure_count__gte=MAX_FAILURES, last_seen_at__lt=now - timedelta(days=FAILED_GRACE_DAYS),
    ).delete()

    # 3) jis login session ne register kiya wo ab hai hi nahi (signal miss / sliding-expiry purge).
    live = AuthToken.objects.filter(jti=OuterRef('session_jti'))
    orphan_ids = list(
        DeviceToken.objects.exclude(session_jti='').annotate(_live=Exists(live)).filter(_live=False)
        .values_list('id', flat=True)
    )
    if orphan_ids:
        out['orphaned'], _ = DeviceToken.objects.filter(id__in=orphan_ids).delete()

    # 4) ek user ke 10+ token => sabse purane hatao.
    heavy = (
        DeviceToken.objects.values('user_id').annotate(n=Count('id')).filter(n__gt=MAX_TOKENS_PER_USER)
        .values_list('user_id', flat=True)
    )
    for user_id in list(heavy):
        out['over_cap'] += enforce_token_cap(user_id)
    return out
