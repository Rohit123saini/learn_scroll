# Feature flags / kill switches (P-J2) - runbook

Flags: `live`, `reels_upload`, `coin_purchase`, `ai_features`, `cashout`. No row = default ON (cashout: OFF unless env COIN_CASHOUT_ENABLED=true).

## One-time setup
`python manage.py migrate` (core 0039) then `python manage.py seed_feature_flags` (idempotent; `--status` prints the state).

## Incident: switch a feature OFF (seconds, no release)
1. Django admin > Core > Feature flags > tick the row > action "KILL: switch the selected flags OFF" (or untick `enabled`).
2. Server refuses at once (403 `feature_disabled` + header `X-Feature-Disabled`); apps hide the feature on next launch/resume, or immediately on their next refused call.
3. Put your reason in `note`; every change is logged (logger `learnscroll.feature_flags`).
4. Database down? Env lever: `FEATURE_FLAGS_OFF=live,ai_features` + restart.

## What keeps working while OFF (on purpose)
Payment webhook / confirm / reconcile, ending or leaving a live, all GETs, receipts, AI free quality tips. A reel already uploading keeps its chunks; it is created only after the switch is back ON (app queue retries every 5 min).

## Staged rollout
Row `rollout_percent` 10 -> 50 -> 100 (stable per X-Device-Id); `min_app_version` limits to new builds; `platform` row beats `all`. Server only refuses on the OFF switch; the percentage only decides who SEES the feature.
Release order: internal track -> 5% -> 20% -> 50% -> 100%, watch crash-free rate and /healthz/deep/. Test plan: FORCE_UPDATE_TEST_PLAN.md.
