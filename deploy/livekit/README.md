# LiveKit production setup (calls + study rooms) — C4

Pehle: code me `ws://<LAN-IP>` default hata diya gaya hai. Production me **wss://<domain>** + **TURN** zaroori hai, warna
iOS/Android block karenge aur college/office WiFi par call connect nahi hogi.

## 1. DNS
| Record | Points to | Use |
|---|---|---|
| `<LIVEKIT_DOMAIN>` (e.g. `lk.learnscroll.app`) | LiveKit server public IP | wss signalling (Caddy, 443) |
| `<TURN_DOMAIN>` (e.g. `turn.learnscroll.app`) | same IP | TURN/TLS |

## 2. Firewall (security group) — open on the LiveKit host
| Port | Proto | Why |
|---|---|---|
| 80, 443 | TCP | Caddy (cert + wss) |
| 7881 | TCP | ICE/TCP fallback |
| 50000-60000 | UDP | media |
| 3478 | UDP | TURN |
| 5349 | TCP | TURN/TLS |
| 30000-40000 | UDP | TURN relay |
7880 ko bahar mat kholo (sirf Caddy/localhost).

## 3. Files
1. `livekit.yaml`, `egress.yaml`, `Caddyfile` me `<PLACEHOLDER>` bharo (`API_KEY/SECRET` = Django `.env` wali).
2. TURN cert: `certbot certonly --standalone -d <TURN_DOMAIN>` → `./certs/fullchain.pem`, `./certs/privkey.pem`.
3. `docker compose up -d`  (image tags pin karo).

## 4. Django `.env` (see `deploy/env.production.example`)
```
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
LIVEKIT_WS_URL=wss://<LIVEKIT_DOMAIN>          # clients ko jaata hai — wss + public domain ZAROORI (boot par validate hota hai)
LIVEKIT_HTTP_URL=http://127.0.0.1:7880         # server-to-server (egress/delete_room). Optional: unset => WS URL se derive
LIVEKIT_EGRESS_S3_BUCKET=...                   # recording ke liye
LIVEKIT_EGRESS_PUBLIC_BASE_URL=https://cdn.../ # recording_url banane ke liye (C2)
```
Boot behaviour: `LIVEKIT_WS_URL` set hai par ws:// / localhost / private IP → app start hi nahi hoga (ImproperlyConfigured).
Unset → app chalta hai, calls/study-room `503` dete hain (chat unaffected).

## 5. Verify (deploy ke baad)
1. `curl -i https://<LIVEKIT_DOMAIN>/` → `OK` (LiveKit health).
2. App se 2 phones me call (alag networks: ek WiFi, ek 4G). Dono taraf audio.
3. Phone 1 ko restrictive WiFi / VPN par rakho (UDP blocked) → call phir bhi connect honi chahiye (TURN/TLS).
4. Call end karo → Django log me `livekit_webhook` events; `CallSession.status=ended`, duration set (C2).
5. Recording start/stop → `recording_url` populated (egress_ended webhook).
6. Flutter release build: `--dart-define=API_BASE_URL=https://<API_HOST>`; server `livekit_url` wss hona chahiye warna app call start se pehle refuse karta hai.

## 6. Capacity
`deploy/LIVE_MEDIA_CAPACITY.md` + `loadtest/LIVE_CLASS_PLAN.md` dekho. Calls + study rooms ke liye: `loadtest/CALLS_STUDYROOM_PLAN.md` (scenarios + `lk load-test` M1-M6) aur `python loadtest/livekit_sizing.py` (node count: CPU / NIC / UDP ports / TURN). Ek node par numbers abhi **measure nahi hue** (X2): default `mbps_per_core` ek assumption hai. Port range 50000-60000 = ~10k UDP ports = ~5k participants/node (2 PeerConnections each) jab tak `rtc.udp_port` mux set na ho.
