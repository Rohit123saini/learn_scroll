# Restore drill checklist (PC2)

Do this on a **fresh server** at least once before launch, then once a year (the weekly automated drill in
`deploy/backup/restore_drill.sh` covers the Postgres part in between). Write the real times into the sign-off table:
the estimates below are only a starting point.

Commands assume user `learnscroll`, code in `/srv/learnscroll/backend`, venv in `/srv/learnscroll/venv`, env in `/srv/learnscroll/.env`.

## 0. Before you start: what you must have OFF the server
A restore is impossible without these. Check they exist in your password manager / second location **today**, not during the outage.

| Item | Why |
|---|---|
| Copy of `/srv/learnscroll/.env` | `SECRET_KEY`, JWT keys, `DATABASE_URL`, `BACKUP_REMOTE`, payment keys |
| rclone config (`~/.config/rclone/rclone.conf`) or the bucket credentials | to download the dump + media |
| GPG **private** key (only if `BACKUP_GPG_RECIPIENT` is set) | encrypted dumps cannot be read without it |
| DNS / registrar login, TLS method | to point the domain at the new server |
| Name of the Git repo / release tag | to get the code |

## 1. Prepare the server  (~20-30 min)
```bash
sudo apt update && sudo apt install -y postgresql postgresql-contrib postgresql-client rsync rclone curl git python3-venv redis-server nginx
psql --version; sudo -u postgres psql -c 'SHOW server_version;'     # server major version must be >= the one that made the dump
sudo adduser --system --group --home /srv/learnscroll learnscroll
sudo mkdir -p /var/backups/learnscroll /var/lib/learnscroll && sudo chown -R learnscroll: /var/backups/learnscroll /var/lib/learnscroll
```
Code + env:
```bash
sudo -u learnscroll git clone <repo> /srv/learnscroll/backend       # or unpack your release
sudo -u learnscroll python3 -m venv /srv/learnscroll/venv && sudo -u learnscroll /srv/learnscroll/venv/bin/pip install -r /srv/learnscroll/backend/requirements.txt
# put the saved .env at /srv/learnscroll/.env, then:
sudo chown learnscroll: /srv/learnscroll/.env && sudo chmod 600 /srv/learnscroll/.env
```
Postgres roles (the app role owns the data; the admin role restores and must be allowed to act as the app role):
```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE learnscroll LOGIN PASSWORD '<same as in DATABASE_URL>';
CREATE ROLE learnscroll_drill LOGIN CREATEDB PASSWORD '<password>';
GRANT learnscroll TO learnscroll_drill;
SQL
```
Set in `.env`: `DATABASE_URL=postgres://learnscroll:...@127.0.0.1:5432/learnscroll` (the **live** name, it does not exist yet) and
`DRILL_ADMIN_DATABASE_URL=postgres://learnscroll_drill:...@127.0.0.1:5432/postgres`.
Restore the rclone config (`sudo -u learnscroll rclone listremotes` must show the remote used in `BACKUP_REMOTE`) and the GPG private key (`sudo -u learnscroll gpg --import key.asc`).

**Verify:** `sudo -u learnscroll rclone lsf "$BACKUP_REMOTE/postgres/" | tail -3` lists dumps. If it is empty or errors, stop: the off-site copy is the problem.

## 2. Dry run  (~1 min)
```bash
cd /srv/learnscroll/backend && sudo -u learnscroll deploy/backup/restore_postgres.sh --from-remote --dry-run
```
Expected: checksum `OK`, a RESTORE PLAN block (server, live DB, new DB, owner `learnscroll`, dump size + age), then
`dry run: checksum + safety checks passed, nothing was created`. A dump older than 48 h prints a warning: write down how much data that means losing.

## 3. Restore the database  (time = dump size; roughly 5-15 min per GB, measure it)
```bash
sudo -u learnscroll deploy/backup/restore_postgres.sh --from-remote --target-db learnscroll_restored
```
You are asked to type `learnscroll_restored` - anything else aborts without changes. Expected: `restore complete into database: learnscroll_restored (NNNs)`,
then `verify: N tables, N applied migrations` and one `rows <table>: ... ok` line per sanity table. Any `VERIFY WARNING` line = investigate before going on.
**Write down NNN seconds: that is your measured restore time.**

Common failures: `CREATE DATABASE ... OWNER failed` -> `GRANT learnscroll TO learnscroll_drill;` missing. `checksum mismatch` -> the file is damaged, use the previous dump (`rclone lsf`, then `--file`).
A failed restore drops the half-built database by itself.

## 4. Go live with it  (~5 min)
Fresh server, so there is no old live database to rename:
```bash
sudo -u postgres psql -d postgres -c 'ALTER DATABASE learnscroll_restored RENAME TO learnscroll;'
sudo -u postgres vacuumdb --analyze-only -d learnscroll
cd /srv/learnscroll/backend && sudo -u learnscroll ../venv/bin/python manage.py migrate --check
```
`migrate --check` exits 0, or lists migrations that were added after the dump (then run `manage.py migrate`). Start Redis, Celery worker + beat, the web service (see `deploy/systemd/`).

## 5. Restore media  (time = media size / bandwidth)
Local-disk media:
```bash
sudo -u learnscroll rclone copy "$BACKUP_REMOTE/media/current" /srv/learnscroll/backend/media --checksum --progress
sudo -u learnscroll rclone size "$BACKUP_REMOTE/media/current"; find /srv/learnscroll/backend/media -type f | wc -l     # counts must match
```
`USE_S3_STORAGE=true`: nothing to copy; check the bucket still has the objects and versioning is on.

## 6. Verify the app really works  (~10 min)
| Check | Command / action | Expected |
|---|---|---|
| Readiness | `curl -s https://<host>/readyz/` | `{"status":"ok",...}` HTTP 200 |
| Row counts | `sudo -u learnscroll ../venv/bin/python manage.py shell -c "from django.db import connection as c; cur=c.cursor(); [print(t, (cur.execute('select count(*) from '+t), cur.fetchone()[0])[1]) for t in ['login_user','core_notification','django_migrations']]"` | close to `row_counts` in `/var/backups/learnscroll/postgres/LATEST.json` (98%+) |
| Login | log in to the app with a test account | works, feed loads |
| Media | open a profile photo and a post image uploaded before the dump | images load |
| Admin | `/admin/` login | works |
| Background jobs | `manage.py check_system_health` | DB, cache, Celery ok |
| Push/WebSocket | open a chat, send a message | delivered |
Anything failing here goes into the sign-off table as a finding; fix the runbook or the scripts, do not just fix the server.

## 7. Turn the backups back on
```bash
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl enable --now learnscroll-backup-postgres.timer learnscroll-backup-media.timer learnscroll-restore-drill.timer learnscroll-system-health.timer
sudo -u learnscroll deploy/backup/backup_postgres.sh && sudo -u learnscroll deploy/backup/backup_media.sh
sudo -u learnscroll ../venv/bin/python manage.py check_backups     # (the drill is still missing until Sunday, or run restore_drill.sh)
```
For a **drill** (not a real disaster): delete the test server afterwards and make sure no DNS record points at it.

## When an alert fires
| Alert | First look | Typical cause / fix |
|---|---|---|
| BackupPostgresLate / BackupLastRunFailed | `journalctl -u learnscroll-backup-postgres -n 80`; `cat /var/backups/learnscroll/postgres/LATEST.json` | disk full (`df -h /var/backups`), DB password changed, rclone token expired (`sudo -u learnscroll rclone lsf $BACKUP_REMOTE`). Fix, then run `deploy/backup/backup_postgres.sh` by hand |
| BackupMediaLate | `journalctl -u learnscroll-backup-media -n 80` | MEDIA_ROOT path changed, disk full |
| BackupNotOffsite | `grep remote_uploaded /var/backups/learnscroll/postgres/LATEST.json` | `BACKUP_REMOTE` unset or rclone failing - the only copy is on this server, fix today |
| BackupDumpShrunk | compare `bytes` / `prev_bytes` in LATEST.json, `psql -c '\dt+'` | data deleted, or `DATABASE_URL` points at the wrong/empty DB. Do NOT let retention delete the older dumps: copy them away first |
| RestoreDrillFailed | `cat /var/backups/learnscroll/drills/LAST_DRILL.json`; `cat .../drills/last_restore.stderr` | until fixed treat the backups as unverified; run the drill by hand after the fix |
| RestoreDrillLate | `systemctl list-timers 'learnscroll-*'` | timer disabled / server was off |
| RestoreDrillSlow | `restore_seconds` in `history.jsonl` | database grew: update the RTO number below |
| BackupMetricsMissing | `ls $NODE_EXPORTER_TEXTFILE_DIR`; node_exporter flags | textfile collector not configured |

## Sign-off (fill in at every full drill)
| Item | Value |
|---|---|
| Date / who | |
| Dump used (name, size, age at restore) | |
| Server prepare time | |
| Restore time (step 3) | |
| Media restore time | |
| Total time until the app worked (= RTO measured) | |
| Data lost = age of the dump (= RPO measured) | |
| Row counts matched? | |
| Findings / fixes made to scripts or this file | |
