# `LearnScroll` — Project-Level Configuration Reference (single source of truth)

> Ye document `LearnScroll/` folder (Django project root — `settings.py`, `asgi.py`, `wsgi.py`, `celery.py`, `urls.py`, `ws_auth.py`) ka single source of truth hai — wahi tarika jo `campus_app_design.md` aur `core_app_documentation.md` already follow karte hain. Ye teeno docs ab ek doosre ko complete karte hain: `campus`/`core` apps *kya* karte hain wo un docs me hai; ye project *kaise wire hota hai* (INSTALLED_APPS, throttle rates, celery beat, auth, deployment) yahan hai. Koi bhi is project pe kaam continue kare, teeno docs ek saath padhe.
>
> **Is pass me saari 8 project-root files (`settings.py`, `asgi.py`, `__init__.py`, `celery.py`, `urls.py`, `ws_auth.py`, `wsgi.py`) dobara diff ki gayi — is baar do purane "STILL OPEN" critical items khud RESOLVE ho chuke hain, aur ek naya settings entry mila jo pehle kabhi document nahi hua tha:**
> - 🟢 **RESOLVED THIS PASS — `LearnScroll/__init__.py` ab khaali NAHI hai.** Pichli pass "🔴 CONFIRMED THIS PASS — 0 bytes" thi (§3.1/§8 item 4); is pass ka upload confirm karta hai ki file ab exactly `celery.py`'s docstring ne jo maanga tha wahi rakhti hai:
>   ```python
>   from .celery import app as celery_app
>   __all__ = ("celery_app",)
>   ```
>   `celery.py`'s apna wiring pre-requisite ab satisfied hai — §3.1, §4, §8 update kiye.
> - 🟢 **RESOLVED THIS PASS — §7.1 ka `INSTALLED_APPS` typo (`'assigments'` → `'assigments'`) fix ho gaya.** `settings.py` me typo'd string ab sahi spelling (`assigments`) me hai, apne khud ke naye inline comment ke saath jo exactly wahi failure mode explain karta hai jo is doc pehle se flag kar rahi thi (`get_app_config("assigments")` → `LookupError`). `urls.py` pehle se hi sahi spelling use karta tha, isliye ab settings.py + urls.py + `campus/bridge.py`/`core/views.py::SearchView` — sab EK hi spelling par consistent hain. §2, §5, §7.1, §8 update kiye.
> - 🆕 **NAYA — `settings.py` me ab `CONFIG_DRIFT_APPS = ["user_profile", "core", "assigments", "testseries", "campus"]` maujood hai**, jo `core_app_documentation.md` §9 item 16 / is doc ke purane §8 item 5 ("confirmed MISSING") ko RESOLVE karta hai. Inline comment khud confirm karta hai ki spelling-typo fix isi list ko add karne ke dauraan surface hua (`get_app_config()` warna `LookupError` deta) — matlab dono fixes (typo + drift-apps list) ek hi pass me saath aaye, ek-dusre se independent nahi. `CONFIG_DRIFT_ADMIN_SKIP`/`CONFIG_DRIFT_ONDEMAND_TASKS`/`CONFIG_DRIFT_URL_SKIP` teeno abhi bhi jaan-bujh kar khaali (`set()`) hain. §2, §8 me naya sub-section.
> - ✅ **STILL RESOLVED, unchanged — §7.3 (`ws_auth.py` dead code):** `asgi.py` ab bhi `from LearnScroll.ws_auth import JWTAuthMiddleware` use karta hai. `liveclass/ws_auth.py`/`message/Middleware.py` ki duplicate copies abhi bhi delete nahi hui (still-open cleanup, unchanged).
> - ✅ **STILL RESOLVED, unchanged — §7.2 (campus F-3 streak-reward tasks):** `campus-check-attendance-streak-rewards`/`campus-check-assigments-ontime-streak-rewards` dono ab bhi registered hain, 19:00/19:30 daily — no change.
> - ⚠️ **STILL UNCONFIRMED, unchanged — `message-expire-stale-parent-access`** (daily 4:00 AM) beat entry ab bhi wahi hai; task-wrapper ka existence ab bhi verify nahi ho paaya (`message/tasks.py` is pass bhi upload nahi hua). §6/§8 me as-is flag kiya.
> - `settings.py` ab **1319 lines** hai (pehle "1270 lines" likha tha — typo-fix comment block aur naya `CONFIG_DRIFT_APPS` entry ke extra lines ki wajah se badha). Line-number references jahan zaroori update kiye.

>
> Baaki poora `settings.py` (1319 lines), `asgi.py`, `celery.py`, `urls.py`, `wsgi.py` neeche as-built document kiya gaya hai.

---

## 0. File map

| File | Kya hai isme |
|---|---|
| `settings.py` | Poora Django config — security, DB, cache, channels, REST framework, JWT, CORS, email/SMS, Celery + beat schedule, feature-flags, storage — §1-§6 |
| `urls.py` | Root urlconf — har app ka mount-prefix, Swagger/Redoc schema, media serving — §2 |
| `asgi.py` | WebSocket entrypoint — `message` + `liveclass` dono apps ke Channels routes ko EK URLRouter me combine karta hai, JWT-over-querystring auth — §3 |
| `wsgi.py` | Standard Django WSGI entrypoint (plain HTTP, sync) — §3 |
| `celery.py` | Celery app bootstrap — `autodiscover_tasks()` se har app ka `tasks.py` register hota hai — §4 |
| `ws_auth.py` | Project-level shared `JWTAuthMiddleware` for Channels — **✅ ab `asgi.py` se wired hai (RESOLVED this pass) — §3, §7.3. `liveclass`/`message` ki apni duplicate copies abhi delete karna baaki hai.** |
| `__init__.py` | **🟢 RESOLVED THIS PASS — ab khaali nahi hai.** `celery.py`'s wiring-requirement (`from .celery import app as celery_app` + `__all__ = ("celery_app",)`) ab dono lines present hain — §3.1, §4, §8. |

---

## 1. `settings.py` — Security & environment

- **`SECRET_KEY`/`DEBUG`/`ALLOWED_HOSTS`** — sab `.env`-driven (`load_dotenv(BASE_DIR / ".env")`). `DEBUG` default `"False"` (fail-safe). `ALLOWED_HOSTS` default hardcoded `["*"]` **[⚠️ see below]**, lekin agar `DEBUG=True` aur `.env` me `ALLOWED_HOSTS` na ho to explicitly `["*"]` set hota hai — ye ek narrow, DEBUG-gated local-dev convenience hai jo production me kabhi trigger nahi hoga jab tak `DEBUG=False` set hai.
  - **⚠️ Flag (not necessarily a bug, verify in your `.env`):** module-level `ALLOWED_HOSTS = ["*"]` (line 22) **unconditionally** executes before the `if DEBUG` guard below it — ye guard sirf ek dusra assigments karta hai jab `DEBUG` True ho aur env-var na ho. Iska matlab: agar production `.env` me `ALLOWED_HOSTS` explicitly set NAHI hai, to yehi module-level `["*"]` hi effectively active rehta hai chahe `DEBUG=False` ho — code ka apna comment khud is exact CRITICAL bug (wildcard host in production) ko "reverted"/"fixed" bolta hai, lekin jo line abhi file me hai wildcard hi hai. **Verify karo `.env` me `ALLOWED_HOSTS` explicitly set hai production me** — is doc ne is line ko as-written report kiya hai, guess nahi kiya ki `.env` me kya hoga.
- **HTTPS hardening** (sab `not DEBUG` gated, local dev http:// pe unaffected): `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_PROXY_SSL_HEADER` (`X-Forwarded-Proto`), `SECURE_HSTS_SECONDS` (30 din), `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`, `SECURE_CONTENT_TYPE_NOSNIFF`, `X_FRAME_OPTIONS = "DENY"`. `SECURE_SSL_REDIRECT` hardcoded `False` (redirect terminating proxy/CDN level pe hone ki assumption).
- **`CSRF_TRUSTED_ORIGINS`** — `.env` ke comma-separated `CSRF_TRUSTED_ORIGINS` se banta hai; ye sirf session/cookie-authenticated unsafe requests (`/admin/`) ke liye matter karta hai, JWT Bearer flow (mobile/Flutter client) ke liye nahi.
- **Sentry** — `SENTRY_DSN` set ho to `DjangoIntegration` + `CeleryIntegration` + `LoggingIntegration(event_level="ERROR")` wire hote hain, `send_default_pii=False` (coin balances/coupon codes/join notes jaisa PII capture nahi hota). Unset ho to poora block inert hai.

---

## 2. `settings.py` — Apps, DB, cache, channels

### `INSTALLED_APPS` (as-built)
```
daphne, django.contrib.{admin,auth,contenttypes,sessions,messages,staticfiles},
django.contrib.postgres, django_filters, rest_framework, drf_spectacular, corsheaders,
login, user_profile, post, message, liveclass, campus, testseries, assigments, core
[+ 'storages' if USE_S3_STORAGE]
```
**🟢 RESOLVED THIS PASS** — `testseries` pehle se correctly registered tha; ab `assigments` bhi sahi spelling ke saath registered hai (pichli pass ka typo fix ho gaya) — `campus/bridge.py`/`core/views.py::SearchView` ke hard-import (`assigments.bridge`/`assigments.models`) se ab match karta hai. Poora detail **§7.1** me.

### Database
`DATABASE_URL` env-var se driven — set ho to Postgres (`CONN_MAX_AGE=60`), na ho to SQLite fallback (`db.sqlite3`, WAL mode + `busy_timeout=30000` `connection_created` signal se activate hota hai). Postgres ka `psycopg2-binary`/`psycopg[binary]` install hona chahiye jab `DATABASE_URL` set karo.

### Channels (`CHANNEL_LAYERS`)
`REDIS_URL` (ya `CELERY_BROKER_URL` fallback) set ho to `channels_redis.core.RedisChannelLayer`, warna `channels.layers.InMemoryChannelLayer`. **Production me Redis zaroori hai** — `InMemoryChannelLayer` sirf single-process me kaam karta hai; ek se zyada daphne/gunicorn worker ke peeche do users alag workers pe land karke ek-dusre ke realtime messages miss karenge (chat/poll ke liye silent breakage, koi error nahi).

### Cache (`CACHES`)
`REDIS_URL` set ho to `django_redis.cache.RedisCache`, warna `LocMemCache` (per-process, single-worker-only). **`core.notification_batching`'s sliding-window batching (`core_app_documentation.md` §5) aur `core.search`'s abhi-anregistered rate-limiting jaisi kisi bhi future cache-based feature ko production me Redis-backed cache chahiye** — `LocMemCache` ke saath multi-worker deployment me batching window inconsistent hoga (worker A ka batch worker B ko nahi dikhega).

### Static/Media
`STATIC_ROOT`/`MEDIA_ROOT` standard. `SERVE_MEDIA_VIA_DJANGO` (default `True`, `.env`-overridable) — deliberate low-traffic trade-off: `post/views.py::serve_media_with_range` abhi real media traffic serve karta hai (worker-blocking) kyunki nginx/S3+CloudFront setup ka budget/traffic nahi hai abhi. `USE_S3_STORAGE=true` ke saath ye flag ON hona **`ImproperlyConfigured` raise karta hai startup pe hi** (dono contradictory hain — S3 on hone par `MEDIA_ROOT` khaali hota, Django fallback sirf 404 dega). Scale badhne par do options: (a) nginx `location /media/` + flag off, (b) `USE_S3_STORAGE=true` + `deploy/S3_CLOUDFRONT_SETUP.md`.

---

## 3. Entry points — `asgi.py` / `wsgi.py` / `ws_auth.py`

### `wsgi.py`
Standard, boilerplate — sirf plain HTTP (admin, REST API) ke liye. Koi custom logic nahi.

### `asgi.py`
```python
application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AllowedHostsOriginValidator(
        JWTAuthMiddleware(
            URLRouter(
                message_routing.websocket_urlpatterns
                + liveclass_routing.websocket_urlpatterns
            )
        )
    ),
})
```
- **`AuthMiddlewareStack` deliberately NAHI use hota** — wo sirf Django session-cookie padhta hai; is project ka `REST_FRAMEWORK` sirf `JWTAuthentication` use karta hai (mobile/SPA client cookie nahi, JWT access-token bhejta hai), isliye `AuthMiddlewareStack` ke saath `scope['user']` hamesha `AnonymousUser` milta aur har consumer connect reject ho jaata.
- **✅ Auth — RESOLVED this pass (§7.3):** `from LearnScroll.ws_auth import JWTAuthMiddleware` — ab `LearnScroll/ws_auth.py`'s apne "single canonical copy" intent se match karta hai. Pehle ye `message.Middleware.JWTAuthMiddleware` tha; `asgi.py`'s apna comment is exact fix ko is doc ke §7.3 reference ke saath explicitly document karta hai. **Abhi bhi open**: `liveclass/ws_auth.py` aur `message/Middleware.py` ki apni duplicate `JWTAuthMiddleware` classes delete nahi hui — `asgi.py`'s apna comment khud flag karta hai ki ye ab dead code hain (kahin se import nahi ho rahi) aur inhe delete karna ek follow-up pass me baaki hai, jab tak wo do files khud upload na ho.
- **Routing**: `message_routing.websocket_urlpatterns + liveclass_routing.websocket_urlpatterns` — dono apps ke routes ek hi `URLRouter` me. Iska apna module-comment ek **CRITICAL, production-breaking fix** document karta hai: pehle sirf `message`'s routing wired tha, `liveclass/routing.py` (`ws/liveclass/session/<id>/` → `SessionConsumer` — live-session chat/raise-hand/polls/presence) kabhi import hi nahi hota tha, isliye har `liveclass` WebSocket connection Channels me "no route matches" se fail hota (consumer/`ws_auth.py`'s apni logic kitni bhi sahi ho, farak nahi padta). **Ab fixed hai** — ye upar ka code already dono routing modules include karta hai.
- Ek hi `URLRouter` sirf ek middleware instance ke peeche baith sakta hai, isliye dono apps' routes ek hi (ab `LearnScroll.ws_auth.JWTAuthMiddleware`) ke peeche combine kiye gaye.

### `ws_auth.py` (`LearnScroll/ws_auth.py`)
Project-level `JWTAuthMiddleware` — same contract (`?token=<jwt>` query-string, `rest_framework_simplejwt.AccessToken` validate, `scope['user']` set). **Iska poora purpose** apna hi docstring explicitly bolta hai: pehle `message`/`liveclass` dono ke paas apna-apna independent, kabhi-compare-na-hui copy tha (`message/Middleware.py` vs `liveclass/ws_auth.py`) — ye file unhe ek jagah consolidate karne ke liye likhi gayi, taaki future me sirf ek jagah update karni pade.

`get_user_from_token()` har failure mode (bad signature, expired, malformed, deleted/deactivated user) ko catch karke `AnonymousUser` degrade karta hai — kabhi raise nahi karta, taaki ek bura token WebSocket connection crash na kare (bas unauthenticated connect ho jaata hai, no-token jaisa hi).

**🟡 Is file ka apna wiring-instruction docstring `asgi.py` me ab follow ho chuka hai (§3, §7.3 — RESOLVED). Cleanup abhi bhi baaki hai**: `liveclass/ws_auth.py`/`message/Middleware.py` ki duplicate copies delete karna.

### New section — 3.1 `LearnScroll/__init__.py` 🔴 CONFIRMED EMPTY

**🟢 RESOLVED THIS PASS** — `__init__.py` upload hua aur ab khaali nahi hai. `celery.py`'s apna module docstring jo maanga tha, file ab exactly wahi content rakhti hai:
```python
from .celery import app as celery_app
__all__ = ("celery_app",)
```
Isse har app ka `@shared_task` is Celery app ko automatically pick karega, aur Django admin/shell se `from LearnScroll.celery import app`-style auto-discovery bhi ab is line par depend kar sakti hai — pehle ye "confirmed empty" tha (open item 4), ab RESOLVED. Koi further action item nahi bacha is file ke liye.

---

## 4. `celery.py` — Task queue bootstrap

```python
app = Celery("LearnScroll")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
```
- `namespace="CELERY"` — har `CELERY_*` setting Celery ke apne naam pe map hoti hai (`CELERY_BROKER_URL` → `broker_url`).
- `autodiscover_tasks()` — har `INSTALLED_APPS` app ka `tasks.py` auto-register hota hai, manual registration ki zaroorat nahi. **(Isi wajah se `assigments`/`testseries` INSTALLED_APPS me na hone ka asar sirf models/admin tak seemit nahi — agar in apps ka apna `tasks.py` hai, uska bhi auto-discovery nahi hoga — §7.1.)**
- **Wiring pre-requisite** (khud command ke docstring ke mutabik): `LearnScroll/__init__.py` me `from .celery import app as celery_app` + `__all__ = ("celery_app",)` — **🟢 RESOLVED THIS PASS: dono lines ab `__init__.py` me maujood hain.** Pehle ye khaali (0 bytes) tha — dekho §3.1.
- Production me **worker aur beat dono alag long-lived processes** chalane zaroori hain (`celery -A LearnScroll worker`, `celery -A LearnScroll beat`) — sirf worker se koi periodic task khud kabhi nahi chalega; sirf beat se tasks queue hote rehte hain, kabhi execute nahi hote.

---

## 5. `urls.py` — Root URL configuration — 🔧 UPDATED THIS PASS

```python
urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", include("login.urls")),
    path("profile/", include("user_profile.urls")),
    path("post/", include("post.urls")),
    path("api/schema/", SpectacularAPIView...),
    path("swagger/", SpectacularSwaggerView...),
    path("redoc/", SpectacularRedocView...),
    path("media/<path:path>", serve_media_with_range, name="media"),
    path("message/", include("message.urls")),
    path("liveclass/", include("liveclass.urls")),
    path("core/", include("core.urls")),
    path("campus/", include("campus.urls")),
    path("testseries/", include("testseries.urls")),
    path("assigments/", include("assigments.urls")),
]
```
- **`core.urls` yahan wire ho chuka hai** — `core_app_documentation.md` §9's open item #4 ("root urlconf me `path("core/", include("core.urls"))` add karna hai") **ab RESOLVED hai**, is upload se confirm hua. Prefix `"core/"` hai, jaisa `core/urls.py`'s apne docstring me suggest kiya gaya tha.
- **`campus.urls` bhi wired hai**, prefix `"campus/"`.
- **`testseries.urls`/`assigments.urls` dono yahan wired hain**, prefixes `"testseries/"`/`"assigments/"` — `urls.py` hamesha se sahi spelling (`assigments`) use kar raha tha; `settings.py`'s `INSTALLED_APPS` ka typo (jo pehle inconsistent tha) is pass **fix ho chuka hai** (§7.1) — ab dono files ek hi sahi spelling par consistent hain.
- Do `settings`-import lines duplicate hain (`from django.conf import settings` do baar) — harmless, cosmetic.
- File ke end me commented-out `if settings.DEBUG: urlpatterns += static(...)` aur ek `re_path` media fallback — dono inactive, kyunki `path("media/<path:path>", serve_media_with_range, ...)` upar already unconditionally wired hai (§2's `SERVE_MEDIA_VIA_DJANGO` flag view-level pe decide karta hai, url-level pe nahi).
- **⚠️ Cleanup reminder** (`core_app_documentation.md` §9 se bhi): agar `liveclass/urls.py` me abhi bhi purana `notifications`/`notification-preferences/me/` router registered hai, to `core.urls` wire ho jaane ke baad wo hata dena hai — warna do endpoints ek hi `Notification` table serve karenge.


---

## 6. `settings.py` — REST framework, JWT, throttling

- **`DEFAULT_AUTHENTICATION_CLASSES`**: sirf `JWTAuthentication` (`rest_framework_simplejwt`) — koi `SessionAuthentication` nahi (isi wajah se §3's Channels `AuthMiddlewareStack` incompatibility).
- **`DEFAULT_PERMISSION_CLASSES`**: `[IsAuthenticated]` — project-wide floor, "fail closed" default agar koi naya view apna `permission_classes` set karna bhool jaye.
- **`DEFAULT_THROTTLE_CLASSES`**: `UserRateThrottle` + `AnonRateThrottle`.
- **`DEFAULT_THROTTLE_RATES`** — 30+ scopes registered (`user`, `anon`, liveclass ka `session_join`/`session_token`/`chat_message_create`/`coin_withdrawal`/`coin_purchase`/`classroom_share`/`chat_reaction`/chunked-upload triplet, message ka `message_send`/`call_initiate`/`group_create`/`reaction`/`ai_transcribe`/`ai_smart_reply`/IP-keyed variants/`translate`/`parent_code_*`/`ai_class_transcript_*`/`ai_classroom_copilot`/`ai_revision_deck`/`focus_session`). Har entry ke comment me confirm hai ki "iske bina pehli hi request `ImproperlyConfigured` degi" — is codebase me ye exact bug-class 10+ baar mil chuka hai.
  - **✅ `campus`'s chaaro throttle scope yahan register hain — `campus_app_design.md`'s apna open item is against RESOLVED hai:** `campus_fee_payment` (`10/min`), `campus_live_session_join` (`20/min`), `campus_notice_post` (`10/min`), `campus_parent_link_verify` (`10/min`) — `campus/throttles.py`'s chaaro `ScopedRateThrottle` subclasses ke exact scope-names se match karte hain.
- **`DEFAULT_PAGINATION_CLASS`**: `PageNumberPagination`, `PAGE_SIZE=20` — project-wide default. **(`core/views.py::NotificationPagination` isse override karta hai apne `LimitOffsetPagination` se — `core_app_documentation.md` §7 me already noted, ye project-default se alag hona intentional hai.)**
- **`EXCEPTION_HANDLER`**: `liveclass.exceptions.liveclass_exception_handler` — project-wide error-envelope normalizer.
- **`SIMPLE_JWT`**: access token 1 din, refresh 30 din, `ROTATE_REFRESH_TOKENS=False`, `BLACKLIST_AFTER_ROTATION=True`, `HS256`, `SIGNING_KEY=SECRET_KEY`.
- **`AUTH_USER_MODEL = "login.User"`** — `ws_auth.py`'s `get_user_model()` isi ko resolve karta hai.
- **`CORS_ALLOWED_ORIGINS`**: `.env`-driven allowlist; `CORS_ALLOW_ALL_ORIGINS = DEBUG and not CORS_ALLOWED_ORIGINS` — production me kabhi wildcard nahi (jab tak `DEBUG=False`).

### `CELERY_BEAT_SCHEDULE` — as-built entries
| Beat key | Task | Cadence |
|---|---|---|
| `liveclass-generate-upcoming-sessions` | `liveclass.generate_upcoming_sessions` | hourly |
| `liveclass-auto-complete-overdue-sessions` | `liveclass.auto_complete_overdue_sessions` | `*/5` min |
| `liveclass-send-due-reminders` | `liveclass.send_due_reminders` | every min |
| `liveclass-refresh-stale-enrolled-counts` | `liveclass.refresh_stale_enrolled_counts` | `*/15` min |
| `liveclass-expire-and-refund-passes` | `liveclass.expire_and_refund_passes` | `*/15` min |
| `liveclass-cleanup-stale-chunked-uploads` | `liveclass.cleanup_stale_chunked_uploads` | hourly |
| `liveclass-reconcile-stuck-coin-purchases` | `liveclass.reconcile_stuck_coin_purchases` | `*/30` min |
| `liveclass-run-auto-renewals` | `liveclass.run_auto_renewals` | `*/30` min |
| `liveclass-expire-unclaimed-gifts` | `liveclass.expire_unclaimed_gifts` | `*/30` min |
| `liveclass-send-notification-digests` | `liveclass.send_notification_digests` | hourly |
| `message-send-scheduled-messages` | `message.send_scheduled_messages` | every min |
| `message-cleanup-expired-messages` | `message.cleanup_expired_messages` | `*/15` min |
| `message-purge-soft-deleted-conversations` | `message.purge_soft_deleted_conversations` | daily 3:30 |
| `message-expire-stale-parent-access` 🆕 | `message.expire_stale_parent_access` | daily 4:00 |
| `user-profile-reconcile-follow-counts` | `user_profile.tasks.reconcile_follow_counts` | every 6h @ :15 |
| `campus-send-fee-due-reminders` | `campus.tasks.send_fee_due_reminders` | daily 8:00 |
| `campus-check-low-attendance` | `campus.tasks.check_low_attendance` | daily 18:00 |
| `campus-send-assigments-due-reminders` | `campus.tasks.send_assigments_due_reminders` | daily 8:30 |
| `campus-check-attendance-streak-rewards` | `campus.tasks.check_attendance_streak_rewards` | daily 19:00 |
| `campus-check-assigments-ontime-streak-rewards` | `campus.tasks.check_assigments_ontime_streak_rewards` | daily 19:30 |

**🆕 `message-expire-stale-parent-access` — NAYA, is pass me pehli baar document hua** (settings.py, staggered 30min after `message-purge-soft-deleted-conversations` so both message-app hygiene sweeps don't land in the same minute). Wires `message`'s `expire_stale_parent_access` management command (Parent Mode DB hygiene — stale `ParentToken`/`ParentAccessCode` cleanup, grace windows `TOKEN_DELETE_GRACE_DAYS=14`/`CODE_DEACTIVATE_GRACE_DAYS=30` inside the command itself) into Celery Beat. Low urgency functionally — `HasValidParentToken` already rejects expired tokens/codes live on every request regardless of this sweep — pure DB hygiene. **⚠️ Unconfirmed assumption (settings.py's own comment flags this):** unlike `message-send-scheduled-messages`/`message-cleanup-expired-messages` above (each has both a management command AND a matching `@shared_task` in `message/tasks.py`), this one is currently a management command ONLY as far as this doc can verify — `message/tasks.py` hasn't been part of any upload yet. A `CELERY_BEAT_SCHEDULE` `"task"` string only fires if something is registered under that exact name (`@shared_task(name="message.expire_stale_parent_access")`); if that wrapper doesn't exist in `message/tasks.py`, this entry sends a tick nobody picks up — silently dropped, no error, not that the cleanup runs. Verify `message/tasks.py` has this wrapper before relying on this entry.

**Deliberately NOT scheduled** (per settings.py's own comments): `campus.tasks.rollover_session(campus_id, new_session_id)` — required positional args, only ever called on-demand from `AcademicSessionViewSet.rollover`, correctly left out. `campus.tasks.refresh_analytics_snapshot(campus_id, session_id)` — also required-args, flagged as needing a new "loop every active campus+session" wrapper task before it can be scheduled (wrapper doesn't exist yet).

✅ **RESOLVED THIS PASS** — `campus.tasks.check_attendance_streak_rewards` aur `campus.tasks.check_assigments_ontime_streak_rewards` (F-3) ab `CELERY_BEAT_SCHEDULE` me registered hain (upar table me, 19:00/19:30 daily) — dono args-less, self-looping shape confirm karte hue apne khud ke comment me (`settings.py` lines ~1116-1137). Pura detail **§7.2** me updated.

---

## 7. Cross-app config audit — `campus_app_design.md` aur `core_app_documentation.md` ke open items is against verify kiye

Is section ka maksad: dono app-level docs ke "kya settings.py me hona chahiye" wale open items ko is asli `settings.py` ke against check karna — same reconciliation jo `campus`/`core` docs khud apne code ke liye karte hain.

### 7.1 🟢 RESOLVED THIS PASS — `assigments` spelling ab `INSTALLED_APPS` me sahi hai

**Pichli pass ka issue (history ke liye rakha gaya):** `INSTALLED_APPS` me galat-spelling `'assigments'` (typo — missing 'n') registered tha, jabki `campus/bridge.py` (`create_assigments()`/`get_assigments_submissions()`) aur `core/views.py::SearchView` (`from assigments.models import assigments, assigmentsSource`) dono sahi-spelling `assigments` hi hard-import karte the — matlab asli app app-registry me kabhi registered hi nahi thi, sirf ek galat-naam ki entry thi.

**✅ CONFIRMED FIXED is pass:** `settings.py` me ab sahi spelling (`assigments`) hai, apne khud ke naye inline comment ke saath jo exact isi purani mismatch-risk ko document karta hai (`get_app_config("assigments")` → `LookupError` hota agar fix na hota). `urls.py` pehle se hi sahi spelling use kar raha tha (§5), isliye ab **settings.py, urls.py, `campus/bridge.py`, aur `core/views.py::SearchView` — chaaro jagah ek hi spelling par consistent hain**. Koi further action item nahi bacha.

### 7.2 ✅ RESOLVED — Campus F-3 streak-reward tasks ab beat schedule me hain

`campus_app_design.md` (§7a, §12) confirm karta hai: `check_attendance_streak_rewards`/`check_assigments_ontime_streak_rewards` dono ab **poore functional** hain (`services.py`'s streak functions ab exist karte hain, `bridge.NotifTypes.CAMPUS_REWARD_EARNED` bhi define hai) — pehle ye dono crash karte the, ab nahi. Dono `campus.tasks.rollover_session`/`refresh_analytics_snapshot` ki tarah "ek specific campus/session ke liye" nahi hain — `compute_attendance_streak(enrollment)`/`compute_assigments_ontime_streak(student, section)` (services.py) per-enrollment/per-student compute karte hain, isliye in do tasks ka apna khud ka "har relevant student/enrollment par loop karo" wrapper hona chahiye, `check_low_attendance`/`send_assigments_due_reminders` jaisa hi.

**✅ CONFIRMED FIXED, still true this pass:** `CELERY_BEAT_SCHEDULE` me ab dono registered hain, args-less/self-looping shape confirm karte hue apne khud ke comment me (settings.py lines ~1236-1242 — shifted again is pass kyunki file 1270 se 1319 lines ho gayi hai, `'assigments'` typo-fix comment block aur naya `CONFIG_DRIFT_APPS` entry ki wajah se, §6/§7.1/§7.4 dekho):
```python
"campus-check-attendance-streak-rewards": {
    "task": "campus.tasks.check_attendance_streak_rewards",
    "schedule": crontab(hour=19, minute=0),
},
"campus-check-assigments-ontime-streak-rewards": {
    "task": "campus.tasks.check_assigments_ontime_streak_rewards",
    "schedule": crontab(hour=19, minute=30),
},
```
`campus/tasks.py` khud is upload me bhi nahi aaya, isliye in tasks ke actual signature (args-less hai ya nahi) ab bhi independently verify nahi hui — sirf `settings.py`'s apna comment is baat ko confirm karta hai. Cadence bhi thoda alag hai jo pehle suggest kiya gaya tha (19:00/19:30 dono, na ki 19:00/9:00) — ye ek product-decision hai, functional issue nahi.

### 7.3 ✅ RESOLVED — `LearnScroll/ws_auth.py` ab `asgi.py` me wired hai

`ws_auth.py`'s apna module docstring: *"Yeh project ki EK hi copy hai — `message` aur `liveclass` dono isi se apna WS auth lete hain. Pehle dono apps ke paas apna-apna independent, kabhi-compare-na-hui copy tha ... ab dono sirf yahan se import karte hain."* — aur khud apna wiring-example bhi deta hai (`from LearnScroll.ws_auth import JWTAuthMiddleware`).

**✅ CONFIRMED FIXED — is pass ka `asgi.py` upload:** `asgi.py` ab `from LearnScroll.ws_auth import JWTAuthMiddleware` use karta hai — `message.Middleware.JWTAuthMiddleware` ko replace kar diya gaya. `asgi.py`'s apna comment explicitly is exact fix ko is doc ke §7.3 reference ke saath document karta hai ("🔧 FIX (this pass, LearnScroll_project_documentation.md §7.3)").

**Abhi bhi baaki (chhota, non-functional cleanup):** `liveclass/ws_auth.py` aur `message/Middleware.py` ki apni duplicate `JWTAuthMiddleware` classes delete nahi hui — `asgi.py`'s apna comment khud isse "flagged, not done here" bolta hai, kyunki wo do files is pass ke upload me nahi aayi. Dono ab genuinely dead code hain (kahin se import nahi ho rahi), sirf delete karna baaki hai jab wo files khud available ho.

### 7.4 ✅ RESOLVED THIS PASS — `CONFIG_DRIFT_APPS` ab `settings.py` me define hai

`core_app_documentation.md` §9 item 16 aur is doc ka purana §8 item 5 dono flag karte the ki `core/management/commands/check_config_drift.py` ye setting expect karta hai lekin `settings.py` me kahin nahi thi — command apne hardcoded fallback (`["user_profile", "core"]`) par chal raha tha, matlab baaki koi bhi app (throttle-scope/celery-beat/admin-registration/urls-wiring drift checks) coverage me nahi tha.

**✅ CONFIRMED FIXED is pass:**
```python
CONFIG_DRIFT_APPS = ["user_profile", "core", "assigments", "testseries", "campus"]
CONFIG_DRIFT_ADMIN_SKIP = set()        # {"app_label.ModelName", ...}
CONFIG_DRIFT_ONDEMAND_TASKS = set()    # {"task_function_name", ...}
CONFIG_DRIFT_URL_SKIP = set()          # {"app_label.ViewClassName", ...}
```
Inline comment khud confirm karta hai ki ye list add karne ke dauraan hi §7.1 ka typo bug pakda gaya (`assigments` ko is list me daalne se pehle `get_app_config("assigments")` `LookupError` deta agar `INSTALLED_APPS` ka typo fix na hota) — dono fixes ek hi pass me saath aaye. `'liveclass'/'message'/'post'/'login'` jaan-bujh kar is list me nahi hain — koi signal nahi mila ki unhe include/exclude karna decide kiya gaya ho, isliye guess nahi kiya gaya, future pass me explicit decision ke saath add honge. Escape-hatch sets (`CONFIG_DRIFT_ADMIN_SKIP` etc.) abhi bhi jaan-bujh kar khaali hain — sirf jab koi specific check genuinely deliberate cheez ko flag kare tab entry add hogi, preemptively nahi.

---

## 8. Open items (agla kaam yahi se shuru hoga)

1. ✅ ~~`INSTALLED_APPS` me `'assigments'` ko `'assigments'` se fix karna (spelling)~~ — **RESOLVED is pass, §7.1.** `settings.py`, `urls.py`, `campus/bridge.py`, aur `core/views.py::SearchView` ab sab ek hi sahi spelling par consistent hain.
2. ✅ ~~Campus streak-reward tasks ko `CELERY_BEAT_SCHEDULE` me add karna~~ — **RESOLVED, §7.2.**
3. ✅ ~~`ws_auth.py` ko `asgi.py` me actually wire karna~~ — **RESOLVED, §3, §7.3.** Duplicate copies (`liveclass/ws_auth.py`/`message/Middleware.py`) delete karna abhi bhi baaki hai — chhota cleanup item, functional impact nahi.
4. ✅ ~~`LearnScroll/__init__.py` khaali hai — Celery wiring lines add karna~~ — **RESOLVED is pass, §3.1, §4.** File ab `from .celery import app as celery_app` + `__all__ = ("celery_app",)` rakhti hai.
5. ✅ ~~`core_app_documentation.md` §9 item 16 (`check_config_drift.py`'s `CONFIG_DRIFT_APPS` setting) — confirmed MISSING~~ — **RESOLVED is pass, §7.4.** `CONFIG_DRIFT_APPS = ["user_profile", "core", "assigments", "testseries", "campus"]` ab set hai.
6. `ALLOWED_HOSTS` ka module-level `["*"]` default — §1 me flag kiya, verify karo production `.env` me explicit value set hai.
7. `liveclass/urls.py` cleanup — agar `core.urls` wire hone se pehle ka purana `notifications`/`notification-preferences/me/` router abhi bhi wahan hai to hatao (§5, `core_app_documentation.md` §9 se carried over).
8. 🆕 **Verify `message/tasks.py` has an `@shared_task(name="message.expire_stale_parent_access")` wrapper** for the newly-documented `message-expire-stale-parent-access` beat entry (§6) — `message/tasks.py` hasn't been uploaded yet, so this can't be confirmed from this doc alone. Without that wrapper, the beat entry is a silent no-op (dropped tick, no error, no cleanup).

---