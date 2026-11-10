# Two-wallet consolidation (T3.10)

`User.coin` is the ONE balance. The "two wallets" are two **ledgers** describing it:
`user_profile.CoinLedger` (sanctioned: `CoinLedger.objects.record_transaction()`) and the legacy
`tuitionclass.CoinTransaction` (still written by the class-pass money paths).

## Done in this pass (read-only, cannot change any balance or fraud decision)
1. `GET /profile/wallet/history/?limit=&offset=` — ONE chronological history merging both ledgers (legacy rows that
   `migrate_tuitionclass_coin_models` already copied are not duplicated; legacy ids are negative so they never collide).
   Same envelope/fields as `/profile/coin-ledger/` + `source`. The Flutter `WalletService.getLedger()` now uses it, so the
   wallet screen shows pass purchases, class earnings, gifts, referral bonuses, refunds, etc. that used to be invisible there.
2. `python manage.py reconcile_wallets [--user-id N] [--limit N] [--fail-on-drift]` — compares `User.coin` with the newest
   `balance_after` across both ledgers; reports `drift` and `unexplained` balances. Run it on production BEFORE touching
   the write paths and put it on a nightly timer.
3. Top-ups and withdrawals already live only in `user_profile` (Task 6); the legacy models for them are raising stubs.

## Not done: moving the remaining legacy WRITE sites onto `record_transaction()`
Sites (each does `user.coin ±= n; save(); CoinTransaction.objects.create(...)`):
`tuitionclass/models.py` ~1417 (class earning), ~1434 (class referral commission), ~1525 (refund), ~1629 (auto-renew),
~1790 (gift refund); `tuitionclass/views.py` ~3587 (pass purchase), ~3674 (referral join bonus), ~4187 (gift sent),
~6021/6032 (referral signup bonus).

Why this is not a mechanical swap — decide these first:
* **Earn rate-limit.** `record_transaction` rate-limits `EARN`/`CAMPUS_REWARD` credits. A teacher receiving many
  students' daily class earnings would hit it and the *student's purchase/charge could fail*. Class earnings need a new
  non-limited `TransactionType` (e.g. `CLASS_EARNING`) before they can go through it.
* **Withdrawal-eligible replay** (`fraud.get_withdrawal_eligible_balance`) replays ledger rows in order. Once pass spends
  appear in `CoinLedger` the eligible amount changes (becomes more accurate/stricter). Needs product sign-off + tests.
* Each site sits inside its own `select_for_update` transaction; `record_transaction` takes its own row lock — keep
  one lock owner per site (pass the locked user in, or let `record_transaction` do the locking).
* Backfill: old `CoinTransaction` rows would have to be copied into `CoinLedger` (as `migrate_tuitionclass_coin_models`
  does for top-ups/withdrawals) with `source_coin_transaction_id` in metadata so `wallet/history` keeps de-duplicating.

Suggested sequence: new `TransactionType`s (+migration) → convert ONE site with tests (class earning is the highest
volume) → run `reconcile_wallets` → backfill → convert the rest → delete `CoinTransaction` writes → retire
`/tuitionclass/coin-transactions/` and the second wallet screen.
