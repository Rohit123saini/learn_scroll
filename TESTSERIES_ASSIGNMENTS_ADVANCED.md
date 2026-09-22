# LearnScroll — Test Series + Assignments: Advanced Backend

Ye doc `NEW_fixed.zip` (Django 6 + DRF) me kiye gaye upgrade ka single source of truth hai: kya rule laga, kya bug mila, kaise setup karna hai, kaunsa API kya karta hai, aur aage kya banega.

> ⚠️ **Verification status (seedhi baat):** ye sab code **Django ke bina** likha gaya — sandbox me Django/DRF/Celery/LiveKit installed nahi the. Jo verify hua: saare Python files syntax-compile hote hain; `testseries/tests_pure.py` ke **33 pure-logic tests actually chalke pass hue**; ek import-time smoke test (framework stub karke) me 44/44 modules import hue; hand-written migrations ko models se AST-level pe cross-check kiya (0 mismatch). Jo verify **nahi** hua: migrations `migrate` hue nahi, `tests_advanced.py` ke DB tests chale nahi, LiveKit/S3/PDF real me try nahi hue. Pehle §4 ke commands chalao.

---

## 1. Ek nazar me

| Area | Pehle | Ab |
|---|---|---|
| **Test series ka price** | campus force-free, baaki jo marzi | **Config-driven policy**: individual = paid, campus = free, live class = free (paid bhi ho sakta hai) |
| **Timer** | sirf phone pe (bypass ho sakta tha) | Server pe `started_at` + `deadline_at`, late-submit policy, auto-submit task, server-side autosave |
| **Marking** | sirf +ve marks | Negative marking, blank pe penalty nahi, net score ≥ 0 |
| **Certificate** | test series pe nahi tha | Pass mark + auto-issued verifiable certificate + public verify URL + PDF |
| **MCQ answer key** | ek-ek question banana | CSV / JSON bulk upload **answer key ke saath** → submit pe turant auto-check |
| **Live video** | nahi | Live test room (host video, students viewers) + **session recording** (S3) |
| **Proctoring** | nahi | Har attempt ka camera room + recording + integrity events |
| **Scheduling** | nahi | `scheduled` window aur `live` (late-entry ke saath) |
| **Results** | turant | instant / window end ke baad / creator release kare |
| **Analytics** | nahi | rank, percentile, topic accuracy, time per question, leaderboard, solutions |
| **Share link** | nahi | `/testseries/public/<slug>/` (login ke bina preview) |
| **Assignments** | banana hi crash tha; publish nahi | Bug fix + draft/publish + public URL + Explore + Join + Projects (link + rubric grading) |

---

## 2. Business rules (jo aapne bataye, jaise implement hue)

### 2.1 Pricing — kaun charge kar sakta hai

| Source | Kaun banata hai | Price | Kahan enforce |
|---|---|---|---|
| **individual** | koi bhi user khud | **Paid zaroori** (min 1 coin) | serializer (create / price change) + `publish` |
| **campus** | campus staff (bridge se) | **Hamesha free** | model `save()` + serializer + publish |
| **liveclass** | teacher (bridge se) | **Free default, paid optional** | serializer + publish |

Sab `settings.TESTSERIES_PRICING_POLICY` me hai (mode: `required` / `optional` / `forbidden`). Ek line badalke rule badal jata hai — e.g. `TESTSERIES_INDIVIDUAL_PRICING=optional` env se individual creators free test bhi bana sakte hain.

> **Meri interpretation:** "khud se krega to paid rhygi" ko maine *paid zaroori* samjha. Agar free option bhi chahiye to sirf env badlo, code nahi.
>
> **Legacy rows:** purane free individual series edit hote rehte hain (strict check sirf create / price-change / publish pe lagta hai, `save()` me nahi).
>
> **Assignments free hi rahte hain.** `assigments` app me price structurally hata diya gaya tha (design decision) — aapne assignments ke liye price nahi bola, isliye chhua nahi.

### 2.2 Teen forms — kaun kahan se aata hai

```
individual  →  POST /testseries/testseries/            (user app se, paid)
campus      →  campus app ke endpoint → bridge.create_context_testseries()   (free)
liveclass   →  liveclass app ke endpoint → bridge.create_context_testseries() (free / paid)
```
Teeno ke baad **teacher/creator** `PATCH /testseries/testseries/{id}/` se naye advanced fields (delivery_mode, certificate, proctoring…) set kar sakta hai aur questions add kar sakta hai.

### 2.3 Access — kaun dekh / shuru kar sakta hai

| Source | Published series kise dikhti hai |
|---|---|
| individual | sabko (marketplace) |
| campus | us section ke enrolled students + us campus ke active staff |
| liveclass | us classroom ke teacher / staff / pass holders |

Membership ka jawab **campus / liveclass khud** deti hain (`user_accessible_testseries_context_ids` in unke `bridge.py`) — `testseries` ne kabhi unke models import nahi kiye (golden rule). Resolver fail ho to access **band** (fail-closed).

### 2.4 Certification

`pass_percentage` set karo → pass/fail milta hai. `certificate_enabled=true` bhi karo → jaise hi attempt `checked` hua aur pass hua, **certificate khud ban jata hai**:

* ek certificate per (series, student) — retry pe duplicate nahi
* code `LS-XXXX-XXXX-XXXX` (0/O/1/I/L nahi), ~60 bit entropy
* **public verify**: `GET /testseries/certificates/verify/<code>/` (login nahi, throttled) — valid / revoked dikhata hai, student ke internal ids nahi
* PDF: `GET /testseries/attempts/{id}/certificate-pdf/` (`pip install reportlab`, warna 501)
* creator revoke kar sakta hai

### 2.5 Live video + recording

```
scheduled → host "Go live" → LiveKit room ts-live-<series> banta hai
                             ├─ host: publish (video+audio)
                             ├─ students: subscribe-only viewers (test dete waqt video dikhta hai)
                             └─ record_live=true → egress → S3 MP4
host "End" ya window ke 30 min baad task → room band, recording READY (webhook)
students (jinhone attempt kiya) → recording replay dekh sakte hain
```
**Proctored attempt** (`proctoring="camera"`): har attempt ka apna room `ts-proctor-<attempt>`, student sirf **publish** karta hai (kuch dekh nahi sakta), hamesha recorded, creator `proctor-watch` se live dekh sakta hai, recording sirf creator ko dikhti hai.

### 2.6 MCQ answer key + final check

MCQ/MSQ/Order questions me `correct_answer` pehle se hota hai → submit pe **turant auto-grade → status `checked`** (koi manual step nahi). Sirf `text` questions teacher ke paas jaate hain. `publish` tab tak refuse karta hai jab tak har objective question ka answer key bhara na ho.

### 2.7 Assignments

```
banao (draft, private) → publish {"visibility": "public" | "link"} → share URL + Explore listing
                                  ↑ same slug re-publish pe bhi (link kabhi dead nahi hota)
koi bhi → GET explore → detail → POST join → submit_freeform / submit_structured
poster → grade / grade-rubric
```
Campus aur live class ke assignments pehle jaise **roster** se chalte hain (visibility unpe lagu nahi).

---

## 3. Bugs jo mile aur fix hue

| # | Severity | Kahan | Kya tha |
|---|---|---|---|
| 1 | 🔴 crash | `assigments/serializers.py::create` | local `assigments` ne model class ko shadow kiya → **har assignment create pe `UnboundLocalError`** (isi wajah se app se assignment nahi ban raha tha) |
| 2 | 🔴 crash | `assigments/bridge.py::create_context_assigments` | same bug → **campus + live class dono se assignment banana crash** |
| 3 | 🔴 crash | `liveclass/chunked_upload_views.py` (4 jagah) | same bug (legacy liveclass assignment attachment/submission chunked upload) |
| 4 | 🔴 500 | settings | `assigments_public_page` throttle rate missing → public share URL **pehli request pe 500** |
| 5 | 🟠 money | settings (beat) | testseries ke `refund_unchecked_paid_attempts` / reminders **kabhi schedule hi nahi hue** → escrow ke coins auto-refund nahi hote the |
| 6 | 🟠 leak | `TestSeriesSerializer` | series payload me **saare questions** inline → paid test bina khareede padha ja sakta tha |
| 7 | 🟠 leak | `QuestionViewSet` | kisi bhi series (draft bhi) ke questions kisi bhi logged-in user ko |
| 8 | 🟠 leak | `TestSeriesViewSet` / `start` | campus / liveclass series sabko dikhti aur koi bhi start kar sakta tha |
| 9 | 🟠 security | `assigmentsViewSet` | jis user ke paas sirf submission ho wo poster ki assignment **PATCH / DELETE** kar sakta tha |
| 10 | 🟡 500 | `TestAttempt.submit` | answer_data dict na ho (string/list/unhashable) to grader 500 |
| 11 | 🟡 integrity | `TestAttempt` | timer ka koi server-side record nahi |
| 12 | 🟡 race | `submit` | double-submit pe duplicate response rows / 400 — ab row-lock + idempotent |

`python manage.py check_config_drift` (aapka apna command) ab #4 aur #5 wali categories me clean aana chahiye.

---

## 4. Setup (order me)

```bash
# 0. backup / branch. Phir overlay ko project root pe copy karo (NEW/ ke andar ke paths)
python manage.py makemigrations --check --dry-run      # koi diff aaye to `makemigrations testseries assigments` chalao aur wo files rakho
python manage.py migrate
python manage.py test testseries.tests_pure testseries.tests_advanced assigments.tests_advanced
python manage.py check_config_drift
```

**Naye migrations:** `testseries/0003_advanced_delivery_certificates_live`, `assigments/0003_publishing_projects_rubric` — sab columns nullable / default ke saath, purani rows pe safe (data backfill nahi). Purani in-progress attempts ka `started_at/deadline_at` NULL rehta hai → wo **untimed** maani jaati hain, kabhi auto-close nahi hoti.

**Env / settings**

| Variable | Default | Kaam |
|---|---|---|
| `TESTSERIES_INDIVIDUAL_PRICING` | `required` | individual: `required` / `optional` |
| `TESTSERIES_LIVECLASS_PRICING` | `optional` | live class: `required` / `optional` / `forbidden` |
| `TESTSERIES_MIN_PRICE_COINS` / `MAX_…` | `1` / `100000` | price range |
| `TESTSERIES_SUBMIT_GRACE_SECONDS` | `30` | deadline ke baad kitna late chalega |
| `TESTSERIES_LATE_SUBMIT` | `use_draft` | `use_draft` (last autosave grade) · `accept` (flag) · `reject` |
| `TESTSERIES_ENFORCE_CONTEXT_ACCESS` | `1` | campus/liveclass membership check |
| `TESTSERIES_SHARE_URL_TEMPLATE` | `""` | e.g. `https://learnscroll.app/test/{slug}` |
| `ASSIGNMENTS_SHARE_URL_TEMPLATE` | `""` | e.g. `https://learnscroll.app/a/{slug}` |
| `TESTSERIES_AUTO_REFUND_DAYS` / `_REMINDER_DAYS` | `14` / `3` | escrow safety net |
| `TESTSERIES_STALE_RECORDING_HOURS` / `_LIVE_OVERRUN_MINUTES` | `6` / `30` | recording / live cleanup |
| `LIVEKIT_API_KEY`, `_API_SECRET`, `_WS_URL`, `LIVEKIT_EGRESS_S3_*` | (aapke paas pehle se) | **wahi** jo liveclass use karta hai — naya kuch provision nahi |

**LiveKit webhook:** LiveKit project settings me **ek aur** webhook URL add karo: `https://<api>/testseries/livekit-webhook/` (liveclass wala rehne do — LiveKit multiple URLs bhejta hai). Iske bina recording `ready` nahi hogi; `expire_stale_recordings` 6 ghante baad use `failed` mark kar deta hai.

**Optional dependency:** `pip install reportlab` (certificate PDF). Baaki sab pehle se hai.

**Celery beat:** 5 naye entries settings me already daale hain (auto-submit har minute, live cleanup har 10 min, stale recordings hourly, refund daily, reminders daily). Worker + beat chalna zaroori hai.

---

## 5. API (mount: `/testseries/…`)

### Series (`/testseries/testseries/`)
| Method | Path | Kaun | Kaam |
|---|---|---|---|
| POST/PATCH | `/` , `/{id}/` | creator | naye fields: `delivery_mode, starts_at, ends_at, late_entry_minutes, proctoring, record_live, pass_percentage, certificate_enabled, certificate_title, result_release, show_solutions` |
| GET | `/{id}/answer-key/` | creator | `{complete, missing_question_orders}` |
| POST | `/{id}/questions-bulk/` | creator | JSON list, all-or-nothing, per-index errors |
| POST | `/{id}/questions-import/` | creator | CSV (multipart `file`) answer key ke saath |
| POST | `/{id}/publish/` | creator | answer-key + pricing + window check, `share_slug` mint |
| POST | `/{id}/release-results/` | creator | manual release |
| GET | `/{id}/leaderboard/?limit=` | access wale | rank (ties shared) |
| GET | `/{id}/certificates/` , POST `/{id}/revoke-certificate/` | creator | |
| POST | `/{id}/live-start/` , `/live-end/` , `/live-token/` | creator | host token + recording |
| GET | `/{id}/recordings/` | creator: sab · student: sirf READY live | |

### Attempts (`/testseries/attempts/`)
| Method | Path | Kaam |
|---|---|---|
| POST | `/start/{series_id}/` | access + window check; response me `started_at, deadline_at, server_time` |
| PATCH | `/{id}/save/` | server-side autosave (deadline ke baad 400) |
| POST | `/{id}/submit/` | **idempotent**; body `answers`, optional `timings` |
| GET | `/{id}/solutions/` | correct answer + explanation (release + `show_solutions` ke baad) |
| GET | `/{id}/analytics/` | rank, percentile, avg, topper, topic accuracy, time |
| GET | `/{id}/certificate/` , `/certificate-pdf/` | |
| POST | `/{id}/live-token/` | student: live viewer token + proctor candidate token (recording start, once) |
| POST | `/{id}/proctor-events/` | `tab_switch, app_background, face_missing, multiple_faces, camera_off, network_drop, other` |
| GET | `/{id}/integrity/` , POST `/{id}/proctor-watch/` | creator / reviewer |

### Public / misc
`GET /testseries/public/{slug}/` · `GET /testseries/certificates/verify/{code}/` · `GET /testseries/certificates/mine/` · `POST /testseries/livekit-webhook/`

### Assignments (`/assigments/…`)
`POST assigmentss/{id}/publish/ {"visibility":"public"|"link"}` · `POST …/unpublish/` · `POST …/join/` · `GET assigmentss/explore/?search=&tag=&kind=&difficulty=&ordering=new|popular` · `POST …/questions-import/` · `PATCH submissions/{id}/grade-rubric/ {"scores":{…}}` · `GET p/{slug}/` (public) · `GET submissions/?assigments=<id>`

**Naye error codes (400 body me `code`):** `not_started`, `late_closed`, `ended` (start), `deadline_passed` (save / submit). Purana behaviour badla: **already-submitted attempt pe submit ab 400 nahi, wahi attempt 200 me deta hai.**

**CSV columns:** `type` (mcq/msq/order/text), `question`, `option_a … option_h`, `correct` (`B` / `A,C` / `C,A,B`), `marks`, `negative`, `topic`, `difficulty`, `explanation`. Ek galat row → **poori file reject**, error me row number.

---

## 6. Frontend (Flutter) — kya karna baaki hai

Maine `lib/testseries/` me sirf **compat** patch kiya (updated zip me): mount = `/testseries`, `deadline_at` parse, `question_count` use. Naye screens abhi **nahi** bane; mockups page inhi ke design hain.

| Feature | Screen / kaam | API |
|---|---|---|
| Create test (creator) | pricing rule dikhao (individual: paid field locked ON; live class: free/paid toggle) + delivery / proctoring / certificate settings | `POST/PATCH testseries` |
| Answer-key upload | CSV pick + row-wise error list | `questions-import` |
| Live test | student player me host video (subscribe-only) + "REC" badge | `attempts/{id}/live-token` → LiveKit SDK |
| Proctored test | camera preview + events (`proctor-events`) | same |
| Lobby (scheduled) | countdown + checks | `window_state` / `starts_at` |
| Result | pass/fail banner, **Certificate** button, rank + percentile | `analytics`, `certificate` |
| Solutions | filter wrong/skipped, explanation, "watch recording" | `solutions`, `recordings` |
| Leaderboard | scope tabs | `leaderboard` |
| Assignments | Explore, project detail, Join, link hand-in, publish sheet, rubric grading | `assigments/…` |
| Timer | `deadline_at − server_time` se countdown; autosave har 10-15 s | `save` |

LiveKit ke liye Flutter me `livekit_client` package chahiye (naya dependency).

---

## 7. Aage kaise aur advanced ban sakta hai

| Idea | Value | Effort | Backend dependency |
|---|---|---|---|
| **Question bank** + tags, reuse across series | 🔥 | M | `QuestionBank` model |
| **AI question generator** (PDF / lesson → MCQ with answer key) | 🔥 | M | LLM service + review screen |
| **AI-assisted grading** for text answers (suggest, teacher approves) | 🔥 | M | rubric per question |
| **Sections** (per-section timer, cutoffs), shuffle questions/options | 🔥 | M | `Section` model, seeded shuffle |
| **Fractional marks / partial marking for MSQ** | M | M | marks fields → Decimal |
| **Cross-device resume** ke saath live sync (WebSocket) | M | M | autosave already hai |
| **AI proctoring** (face-missing / multiple faces on-device) | 🔥 | L | `proctor-events` already ready |
| **Adaptive difficulty**, "retry my mistakes" practice sets | 🔥 | M | topic + difficulty already stored |
| **Coins for completion / streaks / badges** | M | S | CoinLedger hook |
| **Item analysis** for teachers (discrimination, distractor stats) | M | M | responses already stored |
| **Bulk enrol + notification fan-out** for scheduled tests | M | S | Notification |
| **Offline test package** (signed download) | M | L | crypto + sync |
| **Question plagiarism / similarity check** on assignments | M | L | embeddings |
| **Team projects, milestones, peer review** | M | L | new models |
| **Certificate templates**, LinkedIn share, QR on PDF | S | S | `qrcode` |

---

## 8. Jo ab bhi khula hai (decide karo)

1. **Individual = paid zaroori** sahi hai, ya free ka option bhi? (env se badal jata hai.)
2. Live class ke test ki **membership**: abhi classroom ke teacher/staff/kabhi pass kharida hua (`is_enrolled` jaisa). Marketplace jaisa "koi bhi kharid sake" chahiye to `TESTSERIES_CONTEXT_ACCESS["classroom"]="public"`.
3. Late submit policy (`use_draft` default) theek?
4. Certificate: pehla passing attempt hi count hota hai — best attempt chahiye?
5. Campus se ayi `Campus.testseries_paid_allowed` field ab **kaam ki nahi** (campus hamesha free) — purani wo command / field cleanup karni ho to batao.
6. `liveclass/chunked_upload_views.py` wale legacy assignment paths abhi bhi **purane liveclass `assigments` model** pe hain (unified app pe nahi) — migrate karna hai ya hata dena?

---

## 9. Files

**Naye:** `testseries/{policy, access, live, views_advanced, certificate_pdf, throttling, csv_import}.py`, `common/question_csv.py`, `testseries/migrations/0003_…`, `assigments/migrations/0003_…`, `testseries/tests_pure.py`, `testseries/tests_advanced.py`, `assigments/tests_advanced.py`

**Badle:** `testseries/{models, serializers, views, urls, tasks, admin}.py`, `assigments/{models, serializers, views, urls, permissions, throttling, bridge, admin}.py`, `campus/bridge.py`, `liveclass/{bridge, chunked_upload_views}.py`, `LearnScroll/settings.py`

Poora diff `advanced.patch` me hai (`git apply --check advanced.patch` se pehle dry-run karo).
