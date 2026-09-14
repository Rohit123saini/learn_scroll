

### TASK 31 — Reconcile `user_profile.UserPreference.for_user()` vs `core.NotificationPreference`
**Problem:** Built as a "reasonable guess" at matching `core.NotificationPreference`'s pattern, never confirmed since `core/models.py` wasn't in that app's upload.
**Fix:** Diff both directly; align classmethod name / get-or-create keying / default-population behavior if they differ.
**Files:** `user_profile/models.py`, `core/models.py` (verify)



### TASK 33 — Confirm `core.services.create_bulk_notifications()` signature matches all callers
**Problem:** `testseries/tasks.py::notify_followers_new_testseries()` assumes a specific signature (`recipient_ids, notif_type, title, message, data=...`), reused from `post`/`liveclass`'s equivalent fan-outs but not independently re-verified for `testseries` specifically.
**Fix:** Direct diff against `core/services.py`.
**Files:** `testseries/tasks.py`, `core/services.py` (verify)

### TASK 34 — Reconcile duplicate `comments_count`/`replies_count` bookkeeping (post)
**Problem:** `CommentDeleteAPIView` and the `update_comments_count` signal both adjust the count on delete — harmless but doubled work. Separately, `CommentHideAPIView`'s manual adjustment is load-bearing (the signal doesn't know about `is_hidden`) — do not remove it without updating the signal too.
**Fix:** Remove the redundant adjustment in `CommentDeleteAPIView` OR the signal (pick one owner), leave `CommentHideAPIView`'s manual adjustment untouched.
**Files:** `post/views.py` (or `comment_view.py`), `post/signals.py`

### TASK 35 — Add `TestSeriesReviewAdmin`
**Problem:** Reviews aren't editable/inspectable from Django admin yet.
**Files:** `testseries/admin.py`

### TASK 36 — Notify student when a new pending parent-device request exists (message)
**Problem:** `ParentPendingRequestsView` is the only way to discover a pending parent-device-approval request — no push/in-app notification tells the student one exists; they'd have to think to poll it.
**Files:** `message/views_parent.py`, `message/services.py`

---

## ⚪ PHASE 3 — Low priority: polish, ops tuning, product decisions

### TASK 37 — Move `user_profile.fraud` rate-limit constants to Django settings
Currently hardcoded (`EARN_RATE_LIMIT_WINDOW`/`_MAX_TRANSACTIONS`/`_MAX_COINS`) in `fraud.py`. Move to settings once there's real production signal to tune against.
**Files:** `user_profile/fraud.py`, `LearnScroll/settings.py`

### TASK 38 — Add `MIN_WITHDRAWAL_COINS` floor, `reviewed_by`, INR-conversion snapshot to `CoinWithdrawalRequest`
Deliberately deferred; `liveclass.CoinWithdrawal` already has all three. Add once an admin-facing withdrawal review UI is built.
**Files:** `user_profile/models.py`, migration

### TASK 39 — Use Django's `UserAdmin` for the custom `User` model (login)
Optional UX improvement — current registration doesn't use the built-in nicer admin.
**Files:** `login/admin.py`

### TASK 40 — Reconcile `message/cleanup_expired_messages.py` vs the Celery beat task
Both do the same hard-delete sweep (1000/batch vs 500/batch) — confirm the management command isn't also cron-scheduled (redundant with Celery beat), and align batch sizes if they're meant to be interchangeable.
**Files:** `message/management/commands/cleanup_expired_messages.py`, `message/tasks.py`

### TASK 41 — Decide on `generate_revision_deck`'s cache-hit-still-inserts-a-row behavior (message)
A 24h content-hash cache hit still creates a fresh `RevisionDeck` row — saves the Gemini API call but not the duplicate-row creation. Confirm this is intended vs. should return the existing recent row instead.
**Files:** `message/views_ai.py`

### TASK 42 — Product decision: platform fee/commission on `testseries` sales
Currently 100% of `price_coins` goes to the creator, no cut. Separate pricing/business decision, not a bug.

### TASK 43 — Product decision: per-follower notification opt-out for fan-out notifications
`post`, `liveclass`, and `testseries`'s "new content from someone you follow" fan-outs all notify every follower unconditionally (no per-follower mute/opt-in yet). Consistent MVP trade-off across three apps — worth revisiting together if/when a real notification-preferences feature ships.

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