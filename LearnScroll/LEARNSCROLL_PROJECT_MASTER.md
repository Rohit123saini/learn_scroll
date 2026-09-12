

## TASK 14 — `campus`: broken gamification (F-3) fix karo

**Depends on:** Task 11 (kyunki `compute_assignment_ontime_streak` ab `assignment.Assignment` pe query karega, purane `campus.Assignment` pe nahi)

**Files chahiye (mujhe do):**
- `campus/services.py`
- `campus/tasks.py`
- `campus/bridge.py` (`NotifTypes`)
- `settings.py` (relevant streak-settings section)

**Files banegi/badlengi:**
- 🔴 `campus/services.py` — `compute_attendance_streak(enrollment)`, `compute_assignment_ontime_streak(student, section)`
- 🟡 `campus/bridge.py` — `NotifTypes.CAMPUS_REWARD_EARNED`
- 🟡 `campus/tasks.py` — dono streak tasks me fraud-guard (Task 5) call
- 🟡 `settings.py` — 4 streak-settings confirm/add
- 🔴 `campus/tests.py` — dono tasks ke tests

**Acceptance checklist:**
- [ ] Dono Celery tasks bina `ImportError`/`AttributeError` ke run hote hain (abhi crash karte the)
- [ ] Same streak-length ke liye dubara reward nahi milta (idempotency via `reference`)
- [ ] Reward `CAMPUS_REWARD` transaction-type se hi aata hai (Task 5 ka fraud-rule respect karta hai — withdrawal-ineligible)

---

## TASK 15 — `testseries`: Reviews feature

**Depends on:** Task 1, Task 13

**Kya karna hai:** result properly `checked` hone ke baad hi student
series ko rate/review kar sake, aur creator apna review-dashboard dekh
sake.

**Files chahiye (mujhe do):**
- `testseries/models.py`
- `testseries/serializers.py`
- `testseries/views.py`
- `testseries/permissions.py`
- `testseries/urls.py`

**Files banegi/badlengi:**
- 🟡 `testseries/models.py` — `TestSeriesReview` model, `TestSeries.avg_rating`/`review_count` property
- 🟡 `testseries/serializers.py` — `TestSeriesReviewSerializer`
- 🟡 `testseries/views.py` — `TestSeriesReviewViewSet` (create, list, `my-view` creator-aggregate action)
- 🟡 `testseries/permissions.py` — `CanReviewCheckedAttempt`
- 🟡 `testseries/urls.py` — nested routes

**Acceptance checklist:**
- [ ] `attempt.status != "checked"` par review-create clean `400` deta hai (exact tumhara requirement)
- [ ] Ek student ek series ko sirf ek baar review kar sakta hai (`UniqueConstraint`)
- [ ] Creator ka `my-view` sirf apni series ka review-dashboard dikhata hai, doosron ka nahi
- [ ] Review create hone pe `TESTSERIES_REVIEW_RECEIVED` notification creator ko jaati hai

---

## TASK 16 — `testseries`: Post-result query-to-teacher

**Depends on:** Task 1, Task 15

**Kya karna hai:** result check hone ke baad student series-creator
(teacher) se doubt/query puch sake — `message.DoubtQuestion` ko reuse
karke (naya model nahi banana).

**Files chahiye (mujhe do):**
- `message/models.py` (`DoubtQuestion`/`DoubtUpvote` ka current shape)
- `testseries/models.py`

**Files banegi/badlengi:**
- 🔴 `testseries/bridge.py` — `ask_query_on_series()` (local-import `message.models.DoubtQuestion`)
- 🟡 `message/models.py` — `DoubtQuestion` me generic `context_type`/`context_id` fields add (agar already nahi hain)
- 🔴 `testseries/views.py` — `TestAttemptViewSet.ask_query` action
- 🔴 `testseries/permissions.py` — `CanAskQueryOnCheckedAttempt`
- 🟡 `message/services.py` — answer-path se `TESTSERIES_QUERY_ANSWERED` notification trigger

**Acceptance checklist:**
- [ ] `attempt.status != "checked"` par query-ask clean `400` deta hai
- [ ] Query teacher (series creator) ke doubt-queue me dikhta hai `context_type="testseries_attempt"` ke saath
- [ ] Teacher answer de to student ko `TESTSERIES_QUERY_ANSWERED` notification jaati hai
- [ ] `message` app abhi bhi `testseries` ko seedha import nahi karta (golden rule — sirf `testseries` → `message` direction)

---

## TASK 17 — `testseries`: sequencing regression-lock tests

**Depends on:** Task 15, Task 16

**Files chahiye (mujhe do):**
- Task 15/16 ka poora output

**Files banegi/badlengi:**
- 🟡 `testseries/tests.py` — naye tests

**Acceptance checklist:**
- [ ] `test_review_rejected_before_checked_status`
- [ ] `test_query_rejected_before_checked_status`
- [ ] `test_payout_only_releases_on_checked_not_partially_checked`
- [ ] Poora `testseries` test-suite (purana + naya) green

---

## TASK 18 — `core.search`: assignment + testseries wire karo

**Depends on:** Task 10, Task 15

**Files chahiye (mujhe do):**
- `core/search.py`
- `core/views.py`, `core/urls.py`

**Files banegi/badlengi:**
- 🟡 `core/search.py` — `assignment`, `testseries` ke naye `SearchSource` entries (stub se wired)
- 🔴 `core/views.py` — naya `GET /core/search/` endpoint
- 🟡 `core/urls.py` — route add

**Acceptance checklist:**
- [ ] Caller (view) har source ke liye khud-scoped queryset banata hai, `core.search` khud kisi model ko directly query nahi karta (existing principle)
- [ ] Permission-leak nahi — dusre user ka private assignment/testseries search-result me nahi aata

---

## TASK 19 — Org vs Individual paid/unpaid central config

**Depends on:** Task 13

**Files chahiye (mujhe do):**
- `campus/models.py` (`Campus`)
- `testseries/models.py`

**Files banegi/badlengi:**
- 🟡 `campus/models.py` — `Campus.testseries_paid_allowed` (default `False`, future-proofing toggle — behavior nahi badalta abhi)
- 🔴 `docs/ORG_VS_INDIVIDUAL_MATRIX.md` — reference table

**Acceptance checklist:**
- [ ] Koi existing behavior nahi badla (regression-safe, sirf naya admin-visible flag)
- [ ] Matrix doc me har app/source/paid-status clearly documented hai future reference ke liye

---

## TASK 20 — Full regression + rollout QA

**Depends on:** Task 1 se 19 tak sab complete

**Files chahiye (mujhe do):**
- Poora updated codebase (ya jo bhi tasks complete hue unke final files)

**Files banegi/badlengi:**
- 🔴 `docs/wallet_consolidation_preflight.md`
- 🔴 `docs/assignment_preflight.md`
- 🔴 integration test classes: individual-paid-testseries end-to-end, campus-assignment end-to-end, fee-wallet-withdrawal end-to-end

**Acceptance checklist:**
- [ ] Sab per-task acceptance checklists dobara verify (regression)
- [ ] `python manage.py check` aur `makemigrations --check --dry-run` dono clean
- [ ] Full test-suite (`pytest`/`manage.py test`) green, zero failures
- [ ] Koi bhi golden-rule violation nahi (cross-app direct model import kahin nahi, sirf bridge/CoinLedger/DoubtQuestion ke documented exceptions)

---

## Quick index

| # | Task | Depends on |
|---|---|---|
| 1 | Missing enums | — |
| 2 | Shared grading utility | — |
| 3 | Buy-coin flow | 1 |
| 4 | Withdrawal flow | 3 |
| 5 | Fraud/anti-abuse layer | 4 |
| 6 | Migrate liveclass coin models | 5 |
| 7 | assignment: models | 2 |
| 8 | assignment: serializers/permissions | 7 |
| 9 | assignment: views/urls/bridge | 8 |
| 10 | assignment: tasks/admin/tests | 9 |
| 11 | campus: migrate to unified assignment | 10 |
| 12 | liveclass: migrate to unified assignment | 10 |
| 13 | campus: wire testseries | 1 |
| 14 | campus: fix gamification | 11 |
| 15 | testseries: reviews | 1, 13 |
| 16 | testseries: post-result query | 1, 15 |
| 17 | testseries: sequencing lock tests | 15, 16 |
| 18 | core.search: wire assignment+testseries | 10, 15 |
| 19 | org/individual matrix | 13 |
| 20 | full regression QA | 1–19 |

Ab jab bhi ready ho, bolo **"Task 1"** (ya jo bhi number) + us task ke
"Files chahiye" section me di gayi files attach kar do.