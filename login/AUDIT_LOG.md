# Auth audit log (L1 gap 19)

One append-only table, `login.AuthAuditLog`, records login / OTP / password-reset / session
events. Before this only a few `logger.warning` lines existed (login lockout, OTP hourly cap,
admin login) and nothing at all was written for a *successful* login, a reset or a logout.

Written ONLY through `login/audit.py -> record()`. It never raises, never stores secrets, stores
the identifier masked + as a keyed hash, and de-duplicates attacker-triggerable "blocked" events.

## What is logged

| Event | Outcome | Where |
|---|---|---|
| `login_success` / `login_failed` / `login_locked` | success / failure / blocked | `Login` |
| `signup`, `google_login`, `google_signup`, `google_failed` | | `Signup`, `GoogleAuthView` |
| `otp_sent`, `otp_send_blocked` (rate limit, disposable, captcha, virtual number), `otp_send_failed` | | `SendOTPView`, `ForgotPasswordView` |
| `otp_login` (code matched an account), `otp_verified` (signup branch), `otp_failed` (`otp_invalid` / `otp_expired` / `otp_locked` / `otp_not_found`) | | `VerifyOTPView`, `CompleteProfileView` |
| `phone_verified` | success | `CompleteProfileView` |
| `captcha_required`, `captcha_failed` | blocked | login, send-otp, forgot-password |
| `reset_requested` (`reason=no_account` = probing for registered emails), `reset_success`, `reset_failed` | | forgot / reset password |
| `password_changed` | success | `ChangePasswordAPIView` |
| `logout`, `logout_all`, `session_revoked` | success | `session_views.py` |
| `token_refresh_denied` (`session_ended` / `session_expired`) | blocked | `token_views.py` |
| `account_deleted`, `account_delete_failed` | | `DeleteAccountView` |
| `admin_login`, `admin_login_failed`, `admin_login_locked`, `admin_login_weak_password` | | `common/admin_site.py` |

Not logged on purpose: normal token refreshes, every authenticated request (sliding-session touch),
plain validation errors (empty field, bad email format).

## Row fields

`created_at`, `event`, `outcome` (`success` / `failure` / `blocked`), `user` (nullable, no DB FK
constraint), `identifier_kind`, `identifier_masked` (`r***@gmail.com`, `+91********10`, `ri***`),
`identifier_hash` (HMAC-SHA256 with `SECRET_KEY`), `ip_address`, `user_agent`, `device_id`, `platform`,
`reason` (stable machine code), `metadata` (small JSON: `attempts_left`, `retry_after`, `sessions_ended` ...).

## Privacy / safety rules

* No password, OTP code, token or captcha answer is ever passed in; `**meta` keys containing
  `password`, `otp`, `token`, `secret` are dropped as a second line of defence.
* Account deletion keeps the history (event / outcome / ip / time) but blanks `identifier_masked`,
  `identifier_hash` and `user_agent` on that user's rows (`account_deletion.py`).
* Rows cannot be edited (`save()` refuses) and the admin page is read-only (no add / change / delete).
* Rotating `SECRET_KEY` only means old rows stop matching a full-email search; nothing breaks.

## Settings (all optional)

| Setting / env | Default | Meaning |
|---|---|---|
| `AUTH_AUDIT_ENABLED` | `true` | master switch |
| `AUTH_AUDIT_RETENTION_DAYS` | `180` | nightly purge (`login.purge_auth_audit_logs`, 04:15); `0` = keep forever |
| `AUTH_AUDIT_NOISE_DEDUPE_SECONDS` | `60` | same blocked event from the same IP + identifier is written once per window |

## Reading it

Django admin -> **Auth audit logs**. Filter `outcome = failure / blocked` to see attacks,
`event = reset_success` to see who reset a password and from which `ip_address` / `device_id`.
Search accepts username, masked identifier, IP, device id, reason - or paste a FULL email / phone
to find every row for it (matched by hash).

Or in a shell:

```python
from login.models import AuthAuditLog
AuthAuditLog.objects.filter(user__username="rita").order_by("-created_at")[:20]
```

## Files

new: `login/audit.py`, `login/migrations/0008_authauditlog.py`, `login/tests_audit_log.py`, this file.
changed: `login/models.py`, `login/views.py`, `login/session_views.py`, `login/token_views.py`,
`login/admin.py`, `login/tasks.py`, `login/account_deletion.py`, `common/admin_site.py`,
`LearnScroll/settings.py`, `deploy/env.production.example`.

Tests: `python manage.py test login.tests_audit_log` (written, not yet run).
