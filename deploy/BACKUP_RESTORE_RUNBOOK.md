# Backups, restore drill & monitoring (T3.7)

## What runs automatically (systemd timers)
| Timer | When | What | Alert if it fails |
|---|---|---|---|
| `learnscroll-backup-postgres` | daily 02:30 | `pg_dump -Fc` → verify → sha256 → [gpg] → [rclone] → `postgres/LATEST.json` | Sentry Cron `learnscroll-backup-postgres` (missed / error) + Sentry message |
| `learnscroll-backup-media` | daily 03:15 | hard-linked rsync snapshot of `MEDIA_ROOT` + off-site `rclone sync` (deleted files kept) | Sentry Cron `learnscroll-backup-media` |
| `learnscroll-restore-drill` | Sunday 04:30 | restores the newest dump into a scratch DB, sanity-checks it, samples media, drops the scratch DB, records the **measured restore time** | Sentry Cron `learnscroll-restore-drill` + failed `LAST_DRILL.json` (refuses to run against the live DB; fails on a dump older than `DRILL_MAX_DUMP_AGE_HOURS`) |
| `learnscroll-system-health` | every 5 min | `manage.py check_system_health --alert` (DB, cache, Celery worker, disk, backup freshness, last drill) | Sentry error + Cron `learnscroll-system-health` |
| `learnscroll-celery-healthcheck` | every 15 min | (already existed) copyright automation heartbeat | Sentry |

## One-time setup
```bash
sudo mkdir -p /var/backups/learnscroll /var/lib/learnscroll && sudo chown -R learnscroll: /var/backups/learnscroll
sudo apt install postgresql-client rsync rclone curl      # client version >= server version
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl enable --now learnscroll-backup-postgres.timer learnscroll-backup-media.timer \
                            learnscroll-restore-drill.timer learnscroll-system-health.timer
```
1. Add the `# T3.7` block of `deploy/env.production.example` to `/srv/learnscroll/.env` (`BACKUP_REMOTE` is the important one – a backup on the same disk as the DB is not a backup).
2. `sudo -u learnscroll rclone config` → create the remote named in `BACKUP_REMOTE` (S3 / Backblaze B2 / Google Drive …). Use a bucket **in a different account/region** than production, with versioning + a lifecycle rule (e.g. 90 days).
3. The drill needs a role that may `CREATE/DROP DATABASE`: `CREATE ROLE learnscroll_drill LOGIN CREATEDB PASSWORD '…';` → `DRILL_ADMIN_DATABASE_URL=postgres://learnscroll_drill:…@HOST:5432/postgres`.
4. Run each script once by hand (`sudo -u learnscroll deploy/backup/backup_postgres.sh`, then `backup_media.sh`, then `restore_drill.sh`) and confirm `manage.py check_backups` is green **before** relying on the timers.
5. Sentry → Crons: monitors are created automatically on first check-in. Set the expected schedule (daily / weekly / every 5 min) + a grace period of ~2 h.
6. Uptime monitor (UptimeRobot / BetterStack, 1-min interval): `GET https://<host>/readyz/` (expects 200). Optional keyword monitor on `/healthz/deep/?token=<HEALTHCHECK_TOKEN>` – checks Celery + backup freshness from outside.

## Disaster recovery — restore procedure
Full step-by-step (fresh server, timings, verification, alert handling): **`deploy/RESTORE_DRILL_CHECKLIST.md`**.
```bash
# 1. check first, creates nothing (add --from-remote on a new server)
sudo -u learnscroll deploy/backup/restore_postgres.sh --dry-run
# 2. real restore into a NEW database; you must type its name to confirm (--yes only for automation)
sudo -u learnscroll deploy/backup/restore_postgres.sh
sudo -u learnscroll deploy/backup/restore_postgres.sh --from-remote          # server/disk lost
```
The script verifies the checksum, refuses the live DB / system DBs / existing DBs, restores into `learnscroll_restored_<ts>` owned by the app role
(`--owner ROLE` to override; the admin role needs `GRANT <app role> TO <admin role>`), checks table / migration / row counts and prints the exact rename/swap + restart steps.
A failed restore drops its half-built database. Media: copy the newest `snap_*` back into `MEDIA_ROOT`, or `rclone copy $BACKUP_REMOTE/media/current $MEDIA_ROOT`.

**Targets** (adjust to your reality): RPO ≈ 24 h (nightly dump; add WAL archiving / `pgBackRest` when you need minutes), RTO = the `restore_seconds` of the last drill + ~15 min for the swap.

## Alerts (three independent layers, PC2)
1. Sentry Crons + error message when any script fails (also `die`, SIGTERM/timeout) or does not check in.
2. `check_system_health --alert` every 5 min (backup age, last drill, off-site flag).
3. Prometheus: `deploy/monitoring/prometheus_backup_rules.yml` via `NODE_EXPORTER_TEXTFILE_DIR` (see `deploy/monitoring/README.md`).
What to do per alert: `RESTORE_DRILL_CHECKLIST.md#when-an-alert-fires`.

## S3 media
With `USE_S3_STORAGE=true` the media script does nothing. Instead: enable **bucket versioning**, add a lifecycle rule that expires noncurrent versions after 30–90 days, and (optional) cross-region replication. A delete in the app then never destroys the object.

## Reading the status
```bash
python manage.py check_backups          # exit 1 + message if anything is stale / failed / not off-site
python manage.py check_system_health    # everything incl. Celery + disk
cat /var/backups/learnscroll/drills/LAST_DRILL.json     # last drill incl. restore_seconds
tail /var/backups/learnscroll/drills/history.jsonl      # drill history
journalctl -u learnscroll-backup-postgres -n 50
```
