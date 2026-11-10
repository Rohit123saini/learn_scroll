# Force-update + kill-switch test plan (P-J2) - run on STAGING first

Setup: staging API up, migration `core 0039` applied, a staging build with version X (e.g. 1.0.0).

## A. Force update (soft -> hard)
1. Admin > Core > App configs > platform=android: `latest_version` = 1.1.0, `min_supported_version` blank. Open app -> dismissable "Update available" once. PASS if "Later" never re-shows for 1.1.0.
2. Set `min_supported_version` = 1.1.0. Reopen app / resume after 10 min -> blocking "Update now" dialog, back button does nothing, store button opens `store_url`.
3. Airplane mode, reopen app -> still blocked (cached floor). Turn network on, set `min_supported_version` back to blank -> reopen -> dialog gone.
4. Garbage value `abc` in min version -> NO block (fail-open).
5. Hard gate: set env `APP_HARD_MIN_VERSION_GATE=true`, restart web, min = 1.1.0. With the old build logged in, any screen action -> API 426 `UPDATE_REQUIRED` -> blocking dialog appears without reopening. `curl` without `X-App-Version` and `/core/app-config/` must still return 200.
6. Maintenance: tick `maintenance_mode` -> blocking dialog + "Try again"; untick -> clears.

## B. Kill switches (Admin > Core > Feature flags; effect within ~30 s, app picks up on launch/resume)
For each key `live`, `reels_upload`, `coin_purchase`, `ai_features`: create row `enabled=OFF`.
- Server: the call returns 403 `{"code":"feature_disabled","feature":"<key>"}` (curl with a test token).
  live: POST /tuitionclass/live/go/ ; reels_upload: POST /post/uploads/ kind=reel ; coin_purchase: POST /profile/buy-coin/order/ ; ai_features: POST /post/ai-assist/caption/
- Must STILL work while OFF: razorpay webhook / confirm, ending a live session, `/post/ai-assist/quality/`, all GETs.
- Switch back ON -> works again within ~30 s.
- Platform row: `ios` OFF + `all` ON -> iOS refused, Android fine.
- `cashout`: with env COIN_CASHOUT_ENABLED=true, row OFF -> withdraw refused; row ON never opens it if env is false.
- Emergency: env `FEATURE_FLAGS_OFF=live` + restart -> refused with no DB row.

## C. Staged rollout
1. Row `reels_upload`, rollout_percent=10. With 3+ test devices (different X-Device-Id) ~10% see it; the same device always gets the same answer.
2. Raise to 50 -> devices that had it keep it. 100 -> everyone. 0 -> nobody sees it (server still allows).
3. `min_app_version` = 1.1.0 -> older build / unknown version does not get it.
4. While a partial rollout exists /core/app-config/ answers `Cache-Control: private` + `Vary: X-Device-Id`.

## D. Staged release order
internal track -> 5% -> 20% -> 50% -> 100%. Watch crash-free rate + `/healthz/deep/`. Anything wrong: flip the flag OFF in admin (seconds), no release.
