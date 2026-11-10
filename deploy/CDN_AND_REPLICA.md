# P-C8 — CDN cache headers + read-replica routing

Dono cheezein **optional** hain. Defaults me behaviour pehle jaisa hai, sirf API responses par ek safety header aaya hai (§1.3).

## 1. CDN cache headers

### 1.1 Kya cache hota hai
| Path | Policy | Kaun set karta hai |
|---|---|---|
| `/static/` | 1 saal, immutable | WhiteNoise (+ nginx example) |
| `/media/` images, video, `.ts/.m4s` | 1 saal (`public, max-age=31536000`) | `serve_media_with_range` (`post/views/media.py:98,119`) khud set karta hai — isliye `media_cache.py` (immutable) is route par kabhi lagta nahi (header pehle se hai). S3/bucket origin par settings C5 block ka `CacheControl` lagta hai |
| `/media/**/*.m3u8` | 30 sec | **sirf nginx example** — Django ye alag nahi karta, abhi playlist bhi 1 saal wala header pata hai (§1.4) |
| `/pmedia/` (signed private) | `private, no-store` | `common/private_media_views.py` |
| API, admin, ws | kabhi shared cache nahi | naya `common/api_cache_headers.py` (§1.3) |
| `/p/`, `/u/`, `/classroom/`, `/test-series/`, `/certificate/`, `/assignments/`, `/doubts/`, sitemap, robots | view ka apna header (agar nahi to koi header nahi) | views |

### 1.2 CDN setup (CloudFront / Cloudflare)
1. Origin = bucket (`USE_S3_STORAGE=true`) ya apna server ka `/media/` (C5 block).
2. Cache rule sirf `/media/*` aur `/static/*` par. **"Cache everything" rule poore domain par mat lagao.**
3. Cache key me query string ignore sirf media par; API par kuch cache hi nahi.
4. `deploy/nginx/cdn-cache.conf.example` — nginx ke liye ready snippet (tested nahi).

### 1.3 `ApiCacheSafetyMiddleware` (naya)
Jis response me `Cache-Control` nahi hai (2xx–4xx) us par `private, no-store` + `Vary: Authorization`. `/static/`, `/media/`, `/pmedia/`, `/.well-known/`, robots/sitemap aur public landing pages chhode gaye. Jis view ne khud header diya (ETag feed ka `private, no-cache`, etc.) wo kabhi override nahi hota. Band karna: `API_CACHE_DEFAULT_PRIVATE=false`.

### 1.4 Jo main nahi kar paya (jaan-boojh kar)
`.m3u8` playlist ko Django me alag short TTL: `serve_media_with_range` `/media/` ki har file (m3u8 bhi) ko 1 saal deta hai. Agar tumhara transcoder playlist ko usi naam par overwrite karta hai to **nginx example use karo** ya CDN me `*.m3u8` par TTL override. Maine transcoder ka naming check nahi kiya.

## 2. Read replica

### 2.1 Kab chahiye
Primary CPU/IO feed + reels + search ke reads se bhar jaye (p95 gate fail, `LOAD_TEST_PLAN.md`). Pehle indexes / N+1 (`STATUS_PC5.md`) aur pgbouncer (`PGBOUNCER.md`) dekho — replica sabse aakhri.

### 2.2 Env
```
DB_REPLICA_ENABLED=true
DB_REPLICA_URL=postgres://USER:PASS@REPLICA_HOST:5432/DBNAME
DB_REPLICA_PATH_PREFIXES=/post/feed/,/post/reels/,/profile/search/    # khaali = koi endpoint replica nahi use karta
DB_REPLICA_PIN_SECONDS=5        # write ke baad itni der client primary par
DB_REPLICA_DOWN_SECONDS=30      # replica error ke baad itni der primary par
DB_REPLICA_EXCLUDE_APPS=sessions,auth,admin,contenttypes,authtoken,login,django_otp,otp_totp,otp_static,token_blacklist
```
Toggle ON par `DB_REPLICA_URL` galat/khaali ho to boot fail (`ImproperlyConfigured`). `release_check` bhi ERROR/WARN deta hai.

### 2.3 Routing rules (`core/db_routing.py`)
- Writes, `select_for_update`, migrations, Celery, management commands = **hamesha primary**.
- Read replica par tabhi jab: GET/HEAD **aur** path prefix list me **aur** client ne pichle `PIN_SECONDS` me write nahi kiya **aur** model ka app exclude list me nahi **aur** primary par atomic block nahi **aur** replica "down" nahi.
- Read-your-writes: POST/PUT/PATCH/DELETE ke baad client (Authorization header ka hash, warna session cookie) `PIN_SECONDS` ke liye primary par. Pin store = Django cache (Redis). Cache error = primary.
- Replica DB error (`OperationalError`/`InterfaceError`): wo request fail hogi, uske baad `DOWN_SECONDS` tak primary. Process-wise state hai (har gunicorn worker khud seekhta hai).
- Replica me migrate kabhi nahi (`allow_migrate`). Tests me alias primary ka mirror hai.
- Kisi task/report me opt-in: `with replica_reads(): ...`.

### 2.4 Replica lag ka asli risk
Prefix list sirf **read-only aur thoda-stale-chalega** endpoints ki rakho (feed, reels, search, public profile). Counts jo user abhi dekh raha ho (like/comment/notification badge, wallet, fees, chat) ko list me **mat** daalo. Pin 5 sec sirf *usi client* ko bachata hai; dusre user ko purani like count dikh sakti hai. Replica lag monitor karo (`pg_stat_replication`, alert > 5 sec).

### 2.5 Rollout
1. Postgres streaming replica banao (managed DB me "read replica" button). Lag dekho.
2. Staging: `DB_REPLICA_ENABLED=true`, prefixes sirf `/post/reels/`. Smoke test (`scripts/smoke_test.sh`) + `loadtest/locustfile_pc6.py` feed scenario.
3. Primary ka `pg_stat_activity` vs replica ka — reads shift ho rahe hain? Phir `/post/feed/` jodo.
4. Rollback: `DB_REPLICA_ENABLED=false` + restart. Kuch migrate/data change nahi.

### 2.6 pgbouncer ke saath
Replica ke aage alag pgbouncer (ya seedha 5432). `DB_REPLICA_URL` me uska host/port. `DISABLE_SERVER_SIDE_CURSORS` primary ki value copy hoti hai.
