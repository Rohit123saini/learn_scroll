# Ads consent / personalisation - DESIGN ONLY (decision needed, no code written)

Status today (from code): the feed shows Meta Audience Network native ads (`FeedAdSlot`). Slots appear only when the server says
`ads_enabled` (`core/me_context.py`; false for a KNOWN under-18 account, switch `ADS_OFF_FOR_MINORS`) and the app fails closed
(`me_context_service.dart`). There is NO consent screen and NO "personalised ads" switch. An account without a date of birth is treated
as an adult and gets ads.

## Decide these first
**D1. Who counts as a child for ads?**  (a) keep: only known under-18 get no ads  (b) no DOB = no ads until DOB is given
(safest for Play Families / DPDP; costs some ad revenue)  (c) no DOB = ads but non-personalised only.

**D2. What does the user control?**
- A. No control, only a disclosure (privacy policy names Meta Audience Network). Weakest; fine only if you are sure no DPDP consent is needed.
- B. One consent screen before the FIRST ad: "Show ads" (personalised) / "Show ads that are not personalised" / (optional) "No ads - use free tier limits".
- C. B + a permanent switch in Settings > Privacy ("Ads personalisation"), changeable any time, withdrawal as easy as giving.

**D3. Where is the choice stored?**  Server (new field on UserPreference, e.g. `ads_personalisation: unset|personalised|basic`, so it follows the
account across devices and is part of data export/delete)  vs  device only (simple, but lost on reinstall and invisible to DPDP export).
Recommendation: server field + cached on device; `unset` = no ads shown yet.

## Recommended default (if you just say "go")
D1 = (b), D2 = C, D3 = server. Flow: first time an ad slot WOULD show -> bottom sheet with the three choices -> answer saved -> slot
shows (or not). Settings > Privacy gets the same three options. `me/context` returns `ads_enabled` AND `ads_personalised`; the app passes
`nonPersonalizedAds` to the ad SDK when it is `basic`. Minors: never reach the sheet.

## Work list once decided
Backend: UserPreference field + migration, include in `/core/me/context/`, in data export, in delete-account wipe; log changes (audit).
App: consent sheet widget, Settings row, `FeedAdSlot` waits for a choice, SDK flag for non-personalised.
Docs: privacy policy page names the SDK, Data Safety rows (Advertising ID, Device or other IDs -> shared with Meta, purpose Advertising).
Tests: minor never sees the sheet; `unset` shows no ads; withdrawal hides personalised slots at once.
