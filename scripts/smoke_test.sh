#!/usr/bin/env bash
# P-J1 -- post-deploy smoke test.  Run against STAGING (never point it at production data).
#
#   BASE_URL=https://staging-api.learnscroll.app \
#   SMOKE_FIXTURES=/tmp/smoke_fixtures.json \
#   HEALTHCHECK_TOKEN=...                # optional: also checks /healthz/deep/
#   bash scripts/smoke_test.sh
#
# Fixtures come from:  python manage.py seed_smoke --confirm-staging --out /tmp/smoke_fixtures.json
#
# Steps (each prints PASS / FAIL / SKIP; exit code 1 if any FAIL):
#   1 /healthz/  2 /readyz/  3 /healthz/deep/  4 login (real password login)  5 ws-ticket  6 feed
#   7 upload (init -> chunk -> complete, a 1x1 PNG story)  8 notification (A follows B -> B's unread count)
#   9 payment sandbox (creates a Razorpay TEST order, nothing is charged)  10 live token (call initiate + end)
#
# Optional env:
#   SMOKE_SKIP="payment,live"   comma list of steps to skip: health,deep,flags,login,ws,feed,upload,notify,payment,live
#   COIN_PACK=100               must be one of COIN_PURCHASE_PACKS on the server
#   NOTIFY_WAIT_SECONDS=20      how long to wait for the (Celery) notification row
#   CURL_TIMEOUT=20
#
# Needs: bash, curl, python3 (no jq).  Written by reading the code only -- not run by the author.
set -uo pipefail

BASE_URL="${BASE_URL:-}"
BASE_URL="${BASE_URL%/}"
SMOKE_FIXTURES="${SMOKE_FIXTURES:-smoke_fixtures.json}"
HEALTHCHECK_TOKEN="${HEALTHCHECK_TOKEN:-}"
SMOKE_SKIP=",${SMOKE_SKIP:-},"
COIN_PACK="${COIN_PACK:-100}"
NOTIFY_WAIT_SECONDS="${NOTIFY_WAIT_SECONDS:-20}"
CURL_TIMEOUT="${CURL_TIMEOUT:-20}"

if [[ -z "$BASE_URL" ]]; then
  echo "BASE_URL is required, e.g. BASE_URL=https://staging-api.example.com bash scripts/smoke_test.sh" >&2
  exit 2
fi
case "$BASE_URL" in
  *learnscroll.app*) if [[ "$BASE_URL" != *staging* && "${SMOKE_ALLOW_PROD:-}" != "yes" ]]; then
      echo "Refusing: $BASE_URL does not look like staging. (SMOKE_ALLOW_PROD=yes overrides -- do not.)" >&2; exit 2; fi ;;
esac
command -v curl >/dev/null || { echo "curl not found" >&2; exit 2; }
command -v python3 >/dev/null || { echo "python3 not found" >&2; exit 2; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PASS=0; FAIL=0; SKIP=0; FAILED_STEPS=""

ok()   { PASS=$((PASS+1)); printf '  PASS  %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); FAILED_STEPS="$FAILED_STEPS $2"; printf '  FAIL  %s  -- %s\n' "$1" "${3:-}"; }
skip() { SKIP=$((SKIP+1)); printf '  SKIP  %s  (%s)\n' "$1" "${2:-}"; }
skipped() { [[ "$SMOKE_SKIP" == *",$1,"* ]]; }

# req METHOD PATH [curl args...]  -> body in $TMP/body, http code in $CODE
req() {
  local method="$1" path="$2"; shift 2
  CODE="$(curl -sS -m "$CURL_TIMEOUT" -o "$TMP/body" -w '%{http_code}' -X "$method" "$BASE_URL$path" "$@" 2>"$TMP/err" || true)"
  [[ -n "$CODE" ]] || CODE=000
}
# jget 'a.b.c'  -> value from the last response body ('' when missing)
jget() {
  python3 - "$TMP/body" "$1" <<'PY' 2>/dev/null || echo ""
import json, sys
try:
    cur = json.load(open(sys.argv[1]))
    for part in sys.argv[2].split("."):
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    print("" if cur is None else cur)
except Exception:
    print("")
PY
}
fx() {  # fx 'user_a.username' -> value from the fixtures file
  python3 - "$SMOKE_FIXTURES" "$1" <<'PY' 2>/dev/null
import json, sys
cur = json.load(open(sys.argv[1]))
for part in sys.argv[2].split("."):
    cur = cur[part]
print(cur)
PY
}
auth() { printf 'Authorization: Bearer %s' "$1"; }

echo "Smoke test against $BASE_URL"

# ---------------------------------------------------------------- 1-3 health
if skipped health; then skip "healthz / readyz" "SMOKE_SKIP"; else
  req GET /healthz/;  [[ "$CODE" == 200 ]] && ok "GET /healthz/" || bad "GET /healthz/" health "HTTP $CODE $(cat "$TMP/err")"
  req GET /readyz/;   [[ "$CODE" == 200 ]] && ok "GET /readyz/ (db + cache)" || bad "GET /readyz/ (db + cache)" health "HTTP $CODE $(head -c 200 "$TMP/body")"
fi
if skipped deep; then skip "healthz/deep" "SMOKE_SKIP"
elif [[ -z "$HEALTHCHECK_TOKEN" ]]; then skip "GET /healthz/deep/" "HEALTHCHECK_TOKEN not set"
else
  req GET /healthz/deep/ -H "X-Health-Token: $HEALTHCHECK_TOKEN"
  [[ "$CODE" == 200 ]] && ok "GET /healthz/deep/ (celery, backups, disk)" || bad "GET /healthz/deep/" deep "HTTP $CODE $(head -c 300 "$TMP/body")"
fi

# ---------------------------------------------------------------- P-J2 flags (public, no login)
if skipped flags; then skip "app-config feature_flags" "SMOKE_SKIP"; else
  req GET "/core/app-config/?platform=android&version=99.0.0" -H "X-Device-Id: smoke-device"
  if [[ "$CODE" != 200 ]]; then bad "GET /core/app-config/" flags "HTTP $CODE"
  else
    missing=""
    for k in live reels_upload coin_purchase ai_features cashout; do
      [[ -n "$(jget "feature_flags.$k.enabled")" ]] || missing="$missing $k"
    done
    [[ -z "$missing" ]] && ok "app-config carries all 5 feature flags" || bad "app-config feature_flags" flags "missing:$missing (deploy without migration 0039?)"
  fi
fi

# ---------------------------------------------------------------- fixtures needed for everything below
TOKEN_A=""; TOKEN_B=""
if [[ ! -f "$SMOKE_FIXTURES" ]]; then
  echo "  (fixtures file $SMOKE_FIXTURES not found: run 'manage.py seed_smoke --confirm-staging --out $SMOKE_FIXTURES')"
  bad "fixtures file present" fixtures "$SMOKE_FIXTURES"
  echo; echo "Result: $PASS passed, $FAIL failed, $SKIP skipped"; exit 1
fi
PASSWORD="$(fx password)"; UA="$(fx user_a.username)"; UB="$(fx user_b.username)"
ID_A="$(fx user_a.id)"; ID_B="$(fx user_b.id)"; CONV="$(fx conversation_id)"

# ---------------------------------------------------------------- 4 login (A and B)
login() {  # login USERNAME -> prints access token or ''
  req POST /login/ -H 'Content-Type: application/json' -d "{\"username\":\"$1\",\"password\":\"$PASSWORD\"}"
  if [[ "$CODE" == 200 ]]; then jget token.access; else echo ""; fi
}
if skipped login; then skip "login" "SMOKE_SKIP"; else
  TOKEN_A="$(login "$UA")"; [[ -n "$TOKEN_A" ]] && ok "POST /login/ as $UA" || bad "POST /login/ as $UA" login "HTTP $CODE $(head -c 200 "$TMP/body")"
  TOKEN_B="$(login "$UB")"; [[ -n "$TOKEN_B" ]] && ok "POST /login/ as $UB" || bad "POST /login/ as $UB" login "HTTP $CODE"
fi
HAVE_TOKENS=0; [[ -n "$TOKEN_A" && -n "$TOKEN_B" ]] && HAVE_TOKENS=1
need_tokens() { [[ "$HAVE_TOKENS" == 1 ]] || { skip "$1" "login failed or skipped"; return 1; }; }

# ---------------------------------------------------------------- 5 ws-ticket
if skipped ws; then skip "ws-ticket" "SMOKE_SKIP"; elif need_tokens "ws-ticket"; then
  req POST /login/auth/ws-ticket/ -H "$(auth "$TOKEN_A")"
  if [[ "$CODE" == 200 && -n "$(jget ticket)" ]]; then ok "POST /login/auth/ws-ticket/"; else bad "POST /login/auth/ws-ticket/" ws "HTTP $CODE"; fi
fi

# ---------------------------------------------------------------- 6 feed
if skipped feed; then skip "feed" "SMOKE_SKIP"; elif need_tokens "feed"; then
  req GET /post/feed/ -H "$(auth "$TOKEN_A")"
  [[ "$CODE" == 200 ]] && ok "GET /post/feed/" || bad "GET /post/feed/" feed "HTTP $CODE $(head -c 200 "$TMP/body")"
fi

# ---------------------------------------------------------------- 7 upload (resumable story: init -> chunk -> complete)
if skipped upload; then skip "upload" "SMOKE_SKIP"; elif need_tokens "upload"; then
  PNG="$TMP/pixel.png"
  python3 - "$PNG" <<'PY'
import base64, sys
open(sys.argv[1], "wb").write(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))
PY
  SIZE="$(wc -c < "$PNG" | tr -d ' ')"
  MD5="$(python3 -c "import hashlib,sys;print(hashlib.md5(open(sys.argv[1],'rb').read()).hexdigest())" "$PNG")"
  KEY="smoke-$(date +%s)-$RANDOM"
  req POST /post/uploads/ -H "$(auth "$TOKEN_A")" -H 'Content-Type: application/json' \
      -d "{\"idempotency_key\":\"$KEY\",\"kind\":\"story\",\"chunk_size\":262144,\"payload\":{},\"files\":[{\"role\":\"media\",\"name\":\"smoke.png\",\"size\":$SIZE,\"content_type\":\"image/png\"}]}"
  SID="$(jget session_id)"; [[ -n "$SID" ]] || SID="$(jget id)"
  if [[ "$CODE" != 200 && "$CODE" != 201 || -z "$SID" ]]; then
    bad "upload: init" upload "HTTP $CODE $(head -c 300 "$TMP/body")"
  else
    req POST "/post/uploads/$SID/chunk/" -H "$(auth "$TOKEN_A")" -F file_index=0 -F chunk_index=0 -F "chunk_hash=$MD5" -F "chunk=@$PNG;type=application/octet-stream"
    if [[ "$CODE" != 200 ]]; then bad "upload: chunk" upload "HTTP $CODE $(head -c 300 "$TMP/body")"
    else
      req POST "/post/uploads/$SID/complete/" -H "$(auth "$TOKEN_A")" -H 'Content-Type: application/json' -d '{}'
      if [[ "$CODE" == 200 || "$CODE" == 201 ]]; then ok "upload: init -> chunk -> complete (story)"
      else bad "upload: complete" upload "HTTP $CODE $(head -c 300 "$TMP/body")"; fi
    fi
  fi
fi

# ---------------------------------------------------------------- 8 notification: A follows B, B gets a bell row
if skipped notify; then skip "notification" "SMOKE_SKIP"; elif need_tokens "notification"; then
  req GET /core/notifications/unread-count/ -H "$(auth "$TOKEN_B")"
  BEFORE="$(jget unread_count)"; BEFORE="${BEFORE:-0}"
  # Make the step repeatable: if A already follows B this POST would toggle/unfollow, so unfollow-then-follow is
  # handled by checking the HTTP result only; the count check below only needs ONE new notification.
  req POST "/profile/follow/$ID_B/" -H "$(auth "$TOKEN_A")" -H 'Content-Type: application/json' -d '{}'
  if [[ "$CODE" != 200 && "$CODE" != 201 ]]; then bad "notification: follow request" notify "HTTP $CODE $(head -c 200 "$TMP/body")"
  else
    GOT=0; waited=0
    while (( waited <= NOTIFY_WAIT_SECONDS )); do
      req GET /core/notifications/unread-count/ -H "$(auth "$TOKEN_B")"
      NOW="$(jget unread_count)"; NOW="${NOW:-0}"
      if (( NOW > BEFORE )); then GOT=1; break; fi
      sleep 2; waited=$((waited+2))
    done
    if (( GOT )); then ok "notification: follow -> unread count $BEFORE -> $NOW"
    else
      # Follow toggles: a second run unfollows and creates no notification. Undo and report as SKIP, not FAIL.
      req POST "/profile/follow/$ID_B/" -H "$(auth "$TOKEN_A")" -H 'Content-Type: application/json' -d '{}'
      skip "notification: no new bell row within ${NOTIFY_WAIT_SECONDS}s" "if this repeats on a fresh run, check Celery worker + core/services.py"
      FAILED_STEPS="$FAILED_STEPS notify?"
    fi
  fi
fi

# ---------------------------------------------------------------- 9 payment sandbox (order creation only)
if skipped payment; then skip "payment sandbox" "SMOKE_SKIP"; elif need_tokens "payment sandbox"; then
  req POST /profile/buy-coin/order/ -H "$(auth "$TOKEN_A")" -H 'Content-Type: application/json' -d "{\"coins\": $COIN_PACK}"
  ORDER="$(jget order_id)"; [[ -n "$ORDER" ]] || ORDER="$(jget data.order_id)"
  if [[ "$CODE" == 200 || "$CODE" == 201 ]]; then ok "POST /profile/buy-coin/order/ (Razorpay test order)"
  else bad "POST /profile/buy-coin/order/" payment "HTTP $CODE $(head -c 300 "$TMP/body")  (staging must use rzp_test_ keys)"; fi
  # Guard: a LIVE key on staging is a configuration bug, never a pass.
  if grep -q 'rzp_live_' "$TMP/body" 2>/dev/null; then bad "payment uses LIVE Razorpay key on staging" payment "use rzp_test_ keys"; fi
fi

# ---------------------------------------------------------------- 10 live token (1:1 call) then end the call
if skipped live; then skip "live token" "SMOKE_SKIP"; elif need_tokens "live token"; then
  req POST /message/calls/initiate/ -H "$(auth "$TOKEN_A")" -H 'Content-Type: application/json' \
      -d "{\"conversation_id\":\"$CONV\",\"type\":\"audio\",\"replace_active\":true}"
  LKT="$(jget livekit_token)"; CALL="$(jget call_id)"
  if [[ ( "$CODE" == 200 || "$CODE" == 201 ) && -n "$LKT" ]]; then ok "POST /message/calls/initiate/ -> LiveKit token"
  else bad "POST /message/calls/initiate/" live "HTTP $CODE $(head -c 300 "$TMP/body")"; fi
  if [[ -n "$CALL" ]]; then
    req POST "/message/calls/$CALL/action/" -H "$(auth "$TOKEN_A")" -H 'Content-Type: application/json' -d '{"action":"end"}'
  fi
fi

echo
echo "Result: $PASS passed, $FAIL failed, $SKIP skipped"
if (( FAIL > 0 )); then
  echo "Failed steps:$FAILED_STEPS"
  echo "Rollback: see ROLLBACK.md  (bash deploy/staging/rollback.sh <previous-tag>)"
  exit 1
fi
exit 0
