# Coin cash-out: re-enable checklist (P-G1)

Cash-out (coins -> real money) ships **OFF**. Flag: `COIN_CASHOUT_ENABLED` (env, default `false`).

## OFF means
- `POST /profile/coin-withdrawals/` -> 403 `{"code": "feature_disabled"}` (no debit, no row)
- Staff actions `processing` / `success` -> 403 `feature_disabled`; `reject` (refund) still works
- `GET /core/app-config/` -> `coin_cashout_enabled: false`; `GET /profile/wallet/summary/` -> `cashout_enabled: false`, `withdrawable: 0`
- Flutter: Withdraw button disabled + "Coming soon"; withdraw form shows "Coming soon"
- Unchanged: coin purchase, spending, refunds, withdrawal history and payout slips

## Sign-off before setting `COIN_CASHOUT_ENABLED=true`
- [ ] GST invoice treatment decided (CA sign-off)
- [ ] TDS decided: 194R / 194BA (CA sign-off), deduction + Form 16A flow
- [ ] KYC / PAN collected and verified before first payout
- [ ] Minimum withdrawal limit final (`CoinWithdrawalRequest.MIN_WITHDRAWAL_COINS`) and `COIN_TO_INR_RATE` final
- [ ] Fraud rules reviewed (`user_profile/fraud.py`), payout ops owner named, reject/refund runbook ready
- [ ] Privacy policy / Terms / PLAY_DATA_SAFETY updated for payout data
- [ ] Staging test: request -> processing -> success -> slip; reject -> refund
- [ ] Then set env `COIN_CASHOUT_ENABLED=true`, restart, check app-config shows `true`
