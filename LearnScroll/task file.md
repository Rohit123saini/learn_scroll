




---

## ⚪ PHASE 3 — Low priority: polish, ops tuning, product decisions


### TASK 38 — Add `MIN_WITHDRAWAL_COINS` floor, `reviewed_by`, INR-conversion snapshot to `CoinWithdrawalRequest`
Deliberately deferred; `liveclass.CoinWithdrawal` already has all three. Add once an admin-facing withdrawal review UI is built.
**Files:** `user_profile/models.py`, migration









### TASK 44 — Adopt `NotificationQuerySet.for_user()`/`.unread()` convenience methods (core)
Available but unused — `views.py`/`tests.py` still use manual `.filter(...)`. Cosmetic, not a bug.
**Files:** `core/views.py`, `core/tests.py`

---

## Suggested execution order

1. **Phase 0 (Tasks 1–7)** — do these first, in this order. Task 1 can boot-block the entire project; Tasks 2–5 crash real, everyday user actions (create a section, join as a parent, generate a report card, submit a post); Task 6 affects every Celery task project-wide; Task 7 crashes a nightly sweep.
2. **Phase 1 (Tasks 8–24)** — security/config hardening (8–10) can be done in parallel with anyone; the notification-enum audit (Task 11) unblocks Tasks 12, 17, and part of 28's test-writing; the rest are independent per-app fixes — parallelize across whoever owns each app.
3. **Phase 2 (Tasks 25–36)** — schedule alongside normal feature work; none of these block a production deploy, but several (26, 30–33) are "confirm before you trust this in prod" verification tasks that are cheap to do now and expensive to debug later.
4. **Phase 3 (Tasks 37–44)** — backlog; pick up opportunistically or when the specific feature (withdrawals UI, notification preferences) is scheduled.

## One structural recommendation (not a task, a process note)

Several of the bugs above (Tasks 2, 3, 4, 5, 7) are exactly the failure shape `core/management/commands/check_config_drift.py` (F-1) was built to catch generically — missing beat-schedule entries, missing throttle rates, unregistered admin models, unwired `APIView`s. It does **not** yet catch missing-try/except-around-a-hard-import (Task 2) or wrong-attribute-name-on-an-enum (Task 4/5/11/12) — those need either targeted unit tests per bridge call site, or a stricter mypy/pyright pass with enum literals, since they're logic bugs, not wiring bugs. Worth budgeting for once Phase 0/1 are clear.