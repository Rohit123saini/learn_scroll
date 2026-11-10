# Login app — error envelope (L1 gap 16)

Every failure from `login/` views is one shape:

```json
{
  "detail":  "Human-readable text (English fallback)",
  "code":    "stable_machine_code",
  "message": "same as detail  (legacy alias for older app builds)",
  "errors":  {"field": ["..."]},      // field validation only
  "attempts_left": 3,                  // wrong password / wrong code
  "retry_after": 25,                   // 429s (also the Retry-After header)
  "blocking_reasons": ["..."]          // delete_blocked
}
```

`code` is the contract; the app writes its own sentence per code in English/Hindi
(`lib/utils/app_error.dart`). Helpers: `login/errors.py` (`api_error`, `retry_error`,
`UnifiedErrorsMixin`). Success bodies are unchanged.

| code | HTTP | where |
|---|---|---|
| validation_error | 400 | any serializer failure (`errors` has the fields) |
| invalid_credentials | 401 | login (`attempts_left`) |
| login_locked | 429 | login (`retry_after`) |
| invalid_identifier | 400 | send-otp / forgot-password |
| otp_cooldown, otp_hourly_limit, otp_ip_limit | 429 | send-otp / forgot-password (`retry_after`) |
| disposable_email | 400 | send-otp / google (new account on a throw-away mailbox domain) — gap 18 |
| disposable_phone, phone_invalid_number | 400 | send-otp (virtual / blocked-range number, fake-looking or non-mobile number) — gap 18 |
| captcha_required, captcha_invalid | 400 | send-otp / forgot-password / login (`captcha_provider`, `captcha` = fresh challenge) — gap 18 |
| otp_invalid, otp_expired, otp_locked, otp_not_found | 400/429 | verify-otp / reset-password / complete-profile |
| otp_delivery_failed | 503 | send-otp / forgot-password |
| reset_code_invalid | 400 | reset-password |
| weak_password | 400 | reset-password (`errors.new_password`) |
| password_unchanged | 400 | change-password |
| invalid_password | 400 | change-password (wrong `current_password`), 2fa/setup, 2fa/disable, 2fa/backup-codes (S3) |
| two_factor_invalid_code | 400 | 2fa/enable, 2fa/disable, 2fa/backup-codes, 2fa/verify (wrong 6-digit / backup code) (S3) |
| two_factor_locked | 429 | any 2FA code check after 5 wrong codes in 15 min (`retry_after`) (S3) |
| two_factor_challenge_expired | 401 | 2fa/verify (the 5-minute login challenge died or was already used - sign in again) (S3) |
| two_factor_already_enabled / two_factor_not_enabled | 409 | 2fa/setup + 2fa/enable / 2fa/disable + 2fa/backup-codes (S3) |
| google_not_configured / google_token_invalid / google_no_email / google_email_unverified | 500/401/400/400 | google auth |
| phone_exists | 409 | complete-profile |
| dob_already_set | 409 | set-dob |
| delete_confirm_required / incorrect_password / delete_blocked | 400/403/409 | delete-account |
| session_not_found | 404 | sessions/<id>/ |
| TOKEN_EXPIRED | 401 | any authenticated call (session timed out / ended) |
| throttled | 429 | DRF throttle (`retry_after`) |
| not_authenticated, authentication_failed, permission_denied, not_found | 401/401/403/404 | DRF defaults |

## Field rule codes (`error_codes`, same shape as `errors`) — L1 gap 17

Validation failures also carry `error_codes`, e.g. `{"password": ["password_too_common"]}`.
The app has a Hindi/English sentence for each (`AppError._fieldMessages`); unknown codes
fall back to the server text.

`password_edge_spaces`, `password_min_length`, `password_no_upper`, `password_no_lower`,
`password_no_digit`, `password_no_special`, `password_too_short`, `password_too_common`,
`password_entirely_numeric`, `password_too_similar`, `password_contains_phone`,
`passwords_mismatch`, `username_exists`, `username_has_at`, `username_phone_like`,
`email_exists`, `phone_exists`, `phone_invalid_format`, `phone_not_verified`,
`email_not_verified`, `dob_future`, `dob_invalid`, `dob_too_young`,
`email_disposable`, `phone_disposable`, `phone_invalid_number` (gap 18, signup / complete-profile).

## Captcha (gap 18)

`captcha_required` / `captcha_invalid` come back as **400 before** the password / code is looked at, so they never
count as a wrong password and never spend an OTP. The body already contains a fresh challenge:

```json
{"code": "captcha_required", "captcha_provider": "builtin",
 "captcha": {"provider": "builtin", "captcha_id": "...", "image": "data:image/png;base64,...", "expires_in": 180}}
```

Retry the SAME request with `captcha_id` + `captcha_answer` (builtin) or `captcha_token` (turnstile / hcaptcha /
recaptcha). `GET /login/auth/captcha/` returns a new challenge (refresh button). Each challenge is single-use.
When asked: send-otp / forgot-password from an IP's 3rd call per hour, login after 3 wrong passwords
(`CAPTCHA_MODE=off|adaptive|always`, see `login/captcha.py`).

Staff-policy password messages (`common/password_policy.py`) have no code yet and show the server text.

## Two-step verification login step (S3)

Login / Google / OTP-login answer **200** `{"two_factor_required": true, "challenge": "<signed, 5 min>"}` (no tokens) when the
account has 2FA on; the app then calls `POST /login/auth/2fa/verify/` `{challenge, code}` and gets the normal login payload.
This is a success body, not an error - an app build without the code prompt cannot log such an account in, so ship the
Flutter side before anyone turns 2FA on. `two_factor_cancelled` is app-side only (person closed the code dialog), never sent by the server.

## Privacy controls (S4)

`PrivacySettings` (`GET/PATCH /profile/privacy/`) lets a person limit who may reach them. The refusals below are plain
`403 {"detail": "...", "code": "..."}` (not the login envelope); the app writes its own Hindi / English sentence per `code`
(`lib/utils/app_error.dart`).

| code | HTTP | where |
|---|---|---|
| privacy_message_blocked | 403 | `POST /message/conversations/start_private/` and story reply, when the other person limited who may START a chat (an existing chat is never cut off) |
| privacy_call_blocked | 403 | `POST /message/calls/initiate/` for a 1-1 call, when the callee limited who may call them |
| privacy_minor_locked | 400 | `PATCH /profile/privacy/` by an account under 18 that tries to open message / call / tag / mention / group-add / online-status to `everyone`, or turn find-by-phone / find-by-email on (body: `{status:false, code, message, errors:{field:[msg]}}`) |

Minor locks (S4): for an account with a date of birth under ADULT_AGE the **effective** level of those six controls is at
least `connections` and find-by-phone/e-mail are off. `GET /profile/privacy/` returns the effective values plus
`is_minor` and `locked_fields`; the stored choice is not rewritten. Switch: `PRIVACY_MINOR_LOCKS` (default True).

Everything else is skipped without an error, on purpose: a tag / mention the person did not allow is dropped (tag:
`400` "You can't tag this person." -- same text as a block), group add is skipped and listed in `not_added_user_ids`
of the add-members answer, a hidden online status shows as offline, a hidden followers list answers `200` with
`hidden: true` and an empty page.

## Data rights - DPDP Act 2023 (S5)

All under `/login/auth/data/` (login required, login envelope `{detail, code, message, ...}`). The app writes its own
Hindi / English sentence per `code` (`lib/utils/app_error.dart`).

| endpoint | what |
|---|---|
| `GET overview/` | one call for Settings > Data & privacy: `export`, `can_request_export_at`, `deletion`, `consents`, `erasure {erased, kept, grace_days}`, `grievance_path` |
| `POST export/` -> 202 | queue "Download my data" (Celery `login.build_data_export`; if the broker is down the 30-min sweeper builds it) |
| `GET export/` | latest export (`status` queued / processing / ready / failed / expired, `can_download`, `download_path`, `expires_at`) |
| `GET export/<id>/download/` | the ZIP, owner only, until `expires_at` (7 days) |
| `GET deletion/` / `POST deletion/cancel/` | grace-period status / cancel |
| `GET consents/` / `POST consents/ {purpose, granted}` | current answer per purpose + history / append a row |
| `POST /login/auth/delete-account/` | now **schedules** a deletion (200 `{scheduled: true, scheduled_for, days_left, grace_days}`); `ACCOUNT_DELETION_GRACE_DAYS=0` = old "erase now" (`{scheduled: false}`) |

| code | HTTP | where |
|---|---|---|
| export_in_progress | 409 | `POST export/` while a job is queued / running |
| export_too_soon | 429 | `POST export/` - one new request per `DATA_EXPORT_MIN_INTERVAL_HOURS` (24); body + `Retry-After` carry `retry_after` seconds |
| export_not_found | 404 | download of an id that is not yours / does not exist |
| export_not_ready | 409 | download while the job is still running |
| export_expired | 410 | download after `expires_at` (file already deleted) or when the file is gone |
| deletion_not_scheduled | 404 | `POST deletion/cancel/` with nothing scheduled |
| consent_purpose_invalid | 400 | purpose is not `marketing` / `analytics` / `personalisation` |
| consent_value_invalid | 400 | `granted` is not true / false |
| delete_blocked | 409 | (existing) coin withdrawal in progress - also at scheduling time; the sweeper waits a day instead of erasing under a payout |

Withdrawing **marketing** consent also switches SMS, WhatsApp and the study digest off in `NotificationPreference`
(the consent row is the legal record, the switch-off is best effort). Consent rows are append-only.
Throttle scopes: `data_rights_write`, `data_rights_download` (20/hour each).

## Connected accounts & devices (S10)

All under `/login/auth/` (`connected_views.py`, rules in `connected_accounts.py`).

| endpoint | what |
|---|---|
| `GET connections/` | one call for the screen: `google`, `email`, `phone`, `has_password`, `two_factor_enabled`, `reauth {required, otp_target_masked, ttl_seconds}`, `linked_apps`, `qr_login {enabled, ttl_seconds}` |
| `POST connections/reauth/send-code/` | accounts without a password / 2FA: a code to their CURRENT e-mail / phone |
| `POST connections/reauth/` `{password?, two_factor_code?, reauth_code?}` | -> `{reauth_token, expires_in}` (short-lived, bound to this session) |
| `POST connections/google/link/` `{id_token, <reauth>}` / `google/unlink/` `{<reauth>}` | answer = fresh overview |
| `POST connections/contact/send-code/` `{kind, value}` | the code goes to the NEW address |
| `POST connections/contact/confirm/` `{kind, value, otp, <reauth>}` | answer = fresh overview; old e-mail gets a notice |
| `POST connections/phone/remove/` `{<reauth>}` | only while another way to sign in is left |
| `GET connections/apps/` / `DELETE connections/apps/<id>/` | "Linked apps" = sessions made by a QR sign-in / sign one out |
| `POST qr/create/` (no auth) | computer asks for a QR: `{code, secret, match_code, qr_payload, expires_in, poll_interval}` |
| `POST qr/poll/` (no auth) `{code, secret}` | `{state: pending / approved / denied / expired / consumed}`; `approved` carries the normal login JSON, exactly once |
| `POST qr/lookup/` `{code}` / `POST qr/answer/` `{code, approve, match_code}` | phone side: who is asking / approve or decline |

`<reauth>` = `reauth_token` OR the raw fields it is made of. Web landing for the QR: `GET /qr-login?c=<code>`.

| code | HTTP | where |
|---|---|---|
| invalid_password | 400 | re-auth (wrong password) |
| two_factor_invalid | 400 | re-auth (wrong 6-digit / backup code) |
| two_factor_locked | 429 | re-auth after too many wrong 2FA codes (`retry_after`) |
| reauth_code_required | 400 | account has no password / 2FA and sent no `reauth_code` |
| google_not_configured | 500 | `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_IDS` missing on the server |
| google_unreachable | 503 | Google certs could not be fetched (timeout / network) - safe to retry |
| google_token_invalid | 401 | link: ID token bad / expired / wrong audience |
| google_no_email / google_email_unverified | 400 | link: Google gave no e-mail / an unverified one |
| google_already_linked | 409 | link: this Google account is already linked to another LearnScroll account (also when two requests race) |
| google_email_other_account | 409 | link: the Google e-mail belongs to a different LearnScroll account |
| google_other_linked | 409 | link: a different Google account is already linked to THIS account - unlink it first |
| google_not_linked | 404 | unlink with nothing linked |
| google_unlinked | 403 | `auth/google/`: the owner unlinked Google, so an e-mail match alone no longer signs in (use password / code, link again from Settings) |
| last_sign_in_method | 409 | unlink Google / remove phone when it is the only way in |
| invalid_kind | 400 | contact kind is not `email` / `phone` |
| same_as_current | 400 | new e-mail / phone equals the current one |
| email_exists / phone_exists | 409 | the new address belongs to another account |
| phone_not_set | 404 | remove phone with no phone |
| linked_app_not_found | 404 | `DELETE connections/apps/<id>/` - already signed out / not yours |
| qr_disabled | 403 | `QR_LOGIN_ENABLED = False`; settings `QR_LOGIN_TTL_SECONDS` (120), `QR_LOGIN_COLLECT_SECONDS` (120) |
| qr_not_found | 404 | wrong / unknown code or secret |
| qr_used | 409 | already approved / declined / collected |
| qr_expired | 410 | older than `QR_LOGIN_TTL_SECONDS` |
| qr_match_invalid / qr_match_locked | 400/429 | wrong 4-digit match code / too many tries (the request is then cancelled) |

Throttle scopes: `connections`, `connections_write`, `qr_login`, `qr_poll`. Celery: `login.purge_old_qr_requests` (hourly).
Audit events: `google_linked`, `google_unlinked`, `contact_changed`, `contact_removed`, `qr_login_approved`, `qr_login_denied`, `qr_login`, `linked_app_revoked` (shown in Security centre > Recent activity).
