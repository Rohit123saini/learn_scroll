// loadtest/k6.js — T3.6 load test (k6 flavour; same scenarios & thresholds as locustfile.py).
//
//   k6 run -e BASE=https://staging.example.com -e PROFILE=1k loadtest/k6.js
//   k6 run -e BASE=http://localhost:8000 -e PROFILE=smoke loadtest/k6.js
//
// X12 L-D #6: add `-e LIVE_CHAT=1` for C1 (normal live chat) + C2 (abuse spike, ABUSE_RPS req/s, default 100) of
// loadtest/LIVE_CLASS_PLAN.md. Run it a second time with `-e LIVE_CHAT=1 -e LIVE_ABUSE_OFF=1` for the C1-only baseline and
// compare live_chat_normal p95 (must stay within +/-10 %).
//
// X2: `-e CALLS=1` switches the feed/chat/live mix OFF and runs message calls + study rooms instead (CL1 1:1 call loop, SR1 join
// storm of 50-person rooms + SR2 hold with a WS listener). Needs loadtest/calls_fixtures.json (`manage.py seed_calls_loadtest
// --confirm-staging`). k6 covers the REST + WS-connect side only; draw relay latency (SR3), busy race (CL3), group cap (CL4),
// board PUT contention (SR4) and webhook replay (WH1) are Locust-only (`CALLS_ONLY=1 locust ...`), see CALLS_STUDYROOM_PLAN.md.
//   k6 run -e BASE=https://staging.example.com -e CALLS=1 -e PROFILE=calls_smoke loadtest/k6.js
//
// Needs loadtest/fixtures.json from `manage.py seed_loadtest` (tokens + ids). k6 exits non-zero when
// a threshold fails, so this works as a release gate.
import http from 'k6/http';
import ws from 'k6/ws';
import { check, sleep } from 'k6';
import { Trend, Rate, Counter } from 'k6/metrics';
import { SharedArray } from 'k6/data';

const BASE = __ENV.BASE || 'http://localhost:8000';
const WS_BASE = __ENV.WS_BASE || BASE.replace(/^http/, 'ws');
const PROFILE = __ENV.PROFILE || 'smoke';

const CALLS = __ENV.CALLS === '1';
// feed/chat/live fixtures are not needed (and may not exist) in a CALLS run; calls fixtures are not needed in the old runs
const fx = CALLS ? { users: [] } : JSON.parse(open(__ENV.FIXTURES || './fixtures.json'));
const users = new SharedArray('users', () => fx.users);
const cfx = CALLS ? JSON.parse(open(__ENV.CALLS_FIXTURES || './calls_fixtures.json')) : { pairs: [], rooms: [] };
const pairs = new SharedArray('pairs', () => cfx.pairs);
const rooms = new SharedArray('rooms', () => cfx.rooms);

const chatEcho = new Trend('chat_echo_ms', true);
const wsFail = new Rate('ws_connect_failed');
const joinFail = new Rate('live_join_failed');
const chatEchoLost = new Counter('chat_echo_lost');

// VUs per scenario: 60 / 25 / 15 %  -> at 1k: 600 feed, 250 chat, 150 live
const LIVE_CHAT = __ENV.LIVE_CHAT === '1';
const ABUSE_ON = LIVE_CHAT && __ENV.LIVE_ABUSE_OFF !== '1';
const ABUSE_RPS = Number(__ENV.ABUSE_RPS || 100);
const BLOCKED_WORD = __ENV.LIVE_BLOCKED_WORD || 'shit';          // must be in common/moderation.py word list
// answers the guards are SUPPOSED to give; anything else (5xx, 401, 404) counts as a failed request
const ABUSE_EXPECTED = http.expectedStatuses(201, 400, 403, 409, 429);
const abuseCodes = new Counter('live_abuse_codes');

const SHAPES = {
  smoke: { feed: 6, chat: 2, live: 2, ramp: '10s', hold: '40s' },
  baseline: { feed: 60, chat: 25, live: 15, ramp: '2m', hold: '8m' },
  '1k': { feed: 600, chat: 250, live: 150, ramp: '8m', hold: '15m' },
};
// X2 call profiles: VUs = pairs (each VU = one caller+callee pair) / room members
const CALL_SHAPES = {
  calls_smoke: { pairs: 5, rooms: 50, ramp: '10s', hold: '60s' },
  calls_200: { pairs: 200, rooms: 0, ramp: '3m', hold: '12m' },
  rooms_1k: { pairs: 0, rooms: 1000, ramp: '4m', hold: '16m' },
  rooms_storm: { pairs: 0, rooms: 1000, ramp: '20s', hold: '9m' },   // everyone inside ~20 s = SR1
};
const S = CALLS ? CALL_SHAPES[PROFILE] : SHAPES[PROFILE];
if (!S) throw new Error(`unknown PROFILE ${PROFILE} (${CALLS ? Object.keys(CALL_SHAPES) : Object.keys(SHAPES)})`);
const stages = (target) => [
  { duration: S.ramp, target },
  { duration: S.hold, target },
  { duration: '1m', target: 0 },
];

export const options = CALLS ? { scenarios: {}, thresholds: {} } : {
  scenarios: {
    feed: { executor: 'ramping-vus', exec: 'feed', startVUs: 0, stages: stages(S.feed), gracefulRampDown: '30s' },
    chat: { executor: 'ramping-vus', exec: 'chat', startVUs: 0, stages: stages(S.chat), gracefulRampDown: '30s' },
    live: { executor: 'ramping-vus', exec: 'live', startVUs: 0, stages: stages(S.live), gracefulRampDown: '30s' },
  },
  thresholds: {
    'http_req_duration{name:feed}': ['p(95)<800'],
    'http_req_failed{name:feed}': ['rate<0.01'],
    'http_req_duration{name:live_join}': ['p(95)<1500'],
    live_join_failed: ['rate<0.01'],
    ws_connecting: ['p(95)<1000'],
    ws_connect_failed: ['rate<0.01'],
    chat_echo_ms: ['p(95)<500'],
  },
};

if (CALLS && LIVE_CHAT) throw new Error('CALLS=1 and LIVE_CHAT=1 are exclusive');
if (LIVE_CHAT) {   // C1: 1 msg / 20-40 s per student over REST (the path with mute / flood / filter checks)
  options.scenarios.live_chat = { executor: 'ramping-vus', exec: 'liveChat', startVUs: 0, stages: stages(S.chat), gracefulRampDown: '30s' };
  options.thresholds['http_req_duration{name:live_chat_normal}'] = ['p(95)<400'];
  options.thresholds['http_req_failed{name:live_chat_normal}'] = ['rate<0.005'];
}
if (ABUSE_ON) {    // C2: 60 s spike from a pool of abuser accounts, starts once the ramp is done
  options.scenarios.live_abuse = {
    executor: 'constant-arrival-rate', exec: 'liveAbuse', rate: ABUSE_RPS, timeUnit: '1s', duration: '60s',
    preAllocatedVUs: 50, maxVUs: 300, startTime: S.ramp,
  };
  options.thresholds['http_req_failed{name:live_abuse}'] = ['rate<0.005'];
}

// each VU owns one seeded account (VU ids are 1-based and unique per run)
const acct = () => users[(__VU - 1) % users.length];
const auth = () => ({ headers: { Authorization: `Bearer ${acct().access}` } });

export function feed() {
  let url = `${BASE}/post/feed/`;
  for (let page = 0; page < 4; page++) {
    const r = http.get(url, { ...auth(), tags: { name: 'feed' } });
    check(r, { 'feed 200': (x) => x.status === 200 });
    let next = null;
    try { next = r.json('next'); } catch (e) { /* non-JSON -> counted by http_req_failed/check */ }
    if (!next) break;
    url = next.startsWith('http') ? BASE + next.replace(/^https?:\/\/[^/]+/, '') : BASE + next;
    sleep(0.5 + Math.random() * 1.5);
  }
  sleep(1.5 + Math.random() * 2.5);
}

export function chat() {
  const url = `${WS_BASE}/ws/chat/${fx.conversation_id}/?token=${acct().access}`;
  const pending = {};
  const res = ws.connect(url, {}, (socket) => {
    socket.on('message', (data) => {
      let m; try { m = JSON.parse(data); } catch (e) { return; }
      if (m.client_id && pending[m.client_id]) {
        chatEcho.add(Date.now() - pending[m.client_id]);
        delete pending[m.client_id];
      }
    });
    socket.setInterval(() => {
      const cid = `${__VU}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      pending[cid] = Date.now();
      socket.send(JSON.stringify({ type: 'message', message_type: 'text', text: `k6 ${cid}`, client_id: cid }));
      // server limit is 60/min/user -> 15-30 s cadence keeps us far under it
    }, 15000 + Math.random() * 15000);
    socket.setTimeout(() => {
      chatEchoLost.add(Object.keys(pending).length);
      socket.close();
    }, 120000 + Math.random() * 120000);
  });
  wsFail.add(!(res && res.status === 101));
  sleep(1);
}

export function live() {
  const j = http.post(`${BASE}/tuitionclass/sessions/${fx.session_id}/join/`, null, {
    ...auth(), tags: { name: 'live_join' },
  });
  const ok = j.status === 200 || j.status === 201;
  joinFail.add(!ok);
  if (!ok) { sleep(5); return; }
  const url = `${WS_BASE}/ws/tuitionclass/session/${fx.session_id}/?token=${acct().access}`;
  const res = ws.connect(url, {}, (socket) => {
    socket.on('open', () => socket.setInterval(() => socket.ping(), 20000));
    socket.setTimeout(() => socket.close(), 120000 + Math.random() * 480000); // hold 2-10 min
  });
  wsFail.add(!(res && res.status === 101));
}

// ---- X12 L-D #6: live chat safety (C1 / C2) -------------------------------------------------------------------------
// users[0] is the teacher (seed_loadtest). Normal chatters use the lower half of the accounts, abusers the upper half, so a
// strike -> auto-mute on an abuser never lands on a C1 chatter.
const HALF = () => Math.max(2, Math.floor(users.length / 2));
const chatAcct = () => users[1 + ((__VU - 1) % (HALF() - 1))];
const abuseAcct = () => users[HALF() + (__VU % Math.max(1, users.length - HALF()))];
const jsonHeaders = (a, extra = {}) => ({ 'Content-Type': 'application/json', Authorization: `Bearer ${a.access}`, ...extra });
const chatUrl = `${BASE}/tuitionclass/chat-messages/`;

export function liveChat() {
  const r = http.post(chatUrl, JSON.stringify({ session: fx.session_id, message: `hello ${__VU}-${__ITER}` }),
    { headers: jsonHeaders(chatAcct()), tags: { name: 'live_chat_normal' } });
  check(r, { 'chat 201': (x) => x.status === 201 });
  sleep(20 + Math.random() * 20);
}

export function liveAbuse() {
  const kind = Math.floor(Math.random() * 4);
  const message = [
    `spam ${__VU}-${__ITER}-${Math.random().toString(36).slice(2, 8)}`,   // flood: always a new line
    `same line ${__VU}`,                                                 // duplicate
    `you are ${BLOCKED_WORD}`,                                           // word filter
    'call me on 9876543210 or https://example.com/x',                    // contact info (minor rule / filter)
  ][kind];
  const r = http.post(chatUrl, JSON.stringify({ session: fx.session_id, message }),
    { headers: jsonHeaders(abuseAcct()), tags: { name: 'live_abuse' }, responseCallback: ABUSE_EXPECTED });
  abuseCodes.add(1, { kind: ['flood', 'duplicate', 'blocked_word', 'contact_info'][kind], status: String(r.status) });
}

// ---- X2: message calls + study rooms (CALLS=1) -----------------------------------------------------------------------
// Scenario ids follow loadtest/CALLS_STUDYROOM_PLAN.md. NOT RUN while writing; URLs / bodies / status codes mirror
// locustfile.py (which was read against message/urls.py + message/views/calls.py + study_room*.py).
const SPOOF_XFF = __ENV.CALLS_SPOOF_XFF !== '0';     // call_initiate_ip = 20/min PER IP: one injector = one IP, so send a per-VU address
const CALL_HOLD_MIN = Number(__ENV.CALL_HOLD_MIN || 20);
const CALL_HOLD_MAX = Number(__ENV.CALL_HOLD_MAX || 60);
const CALL_MIN_CYCLE = Number(__ENV.CALL_MIN_CYCLE || 7);   // call_initiate is 10/min per user
const JOIN_WINDOW = Number(__ENV.STUDY_JOIN_WINDOW || 10);  // SR1: all members join inside this many seconds (300 = SR2 steady)
const HOLD_MIN = Number(__ENV.STUDY_HOLD_MIN || 240);
const HOLD_MAX = Number(__ENV.STUDY_HOLD_MAX || 600);
const ROOM_POLL = Number(__ENV.STUDY_ROOM_POLL || 30);

const callFailed = new Rate('call_failed');
const roomJoinFailed = new Rate('study_join_failed');
const roomWsFailed = new Rate('study_ws_failed');
const callCodes = new Counter('call_codes');           // designed non-200 answers (409 / 429 ...) by status

if (CALLS) {
  if (S.pairs > 0) {
    options.scenarios.calls_pairs = { executor: 'ramping-vus', exec: 'callPair', startVUs: 0, stages: stages(S.pairs), gracefulRampDown: '60s' };
    options.thresholds['http_req_duration{name:call_initiate}'] = ['p(95)<800'];
    options.thresholds['http_req_duration{name:call_accept}'] = ['p(95)<600'];
    options.thresholds['http_req_duration{name:call_end}'] = ['p(95)<600'];
    options.thresholds.call_failed = ['rate<0.01'];
  }
  if (S.rooms > 0) {
    options.scenarios.study_rooms = { executor: 'ramping-vus', exec: 'studyMember', startVUs: 0, stages: stages(S.rooms), gracefulRampDown: '60s' };
    options.thresholds['http_req_duration{name:study_join}'] = ['p(95)<2000'];
    options.thresholds['http_req_duration{name:study_room}'] = ['p(95)<800'];
    options.thresholds.study_join_failed = ['rate<0.01'];
    options.thresholds.study_ws_failed = ['rate<0.01'];
    options.thresholds.ws_connecting = ['p(95)<1000'];
  }
}

// FNV-ish stable hash -> 100.64.0.0/10 (shared address space), same idea as locustfile._xff
const xff = (id) => {
  let n = 2166136261; const t = String(id);
  for (let i = 0; i < t.length; i++) { n ^= t.charCodeAt(i); n = Math.imul(n, 16777619) >>> 0; }
  return `100.${64 + ((n >>> 22) % 64)}.${(n >>> 8) & 255}.${n & 255}`;
};
const hdr = (a) => {
  const h = { 'Content-Type': 'application/json', Authorization: `Bearer ${a.access}` };
  if (SPOOF_XFF) h['X-Forwarded-For'] = xff(a.user_id);
  return h;
};
const post = (path, a, body, name) => http.post(`${BASE}${path}`, JSON.stringify(body || {}),
  { headers: hdr(a), tags: { name }, responseCallback: http.expectedStatuses(200, 201, 202, 409, 429) });
const jbody = (r) => { try { const b = r.json(); return b && typeof b === 'object' ? b : {}; } catch (e) { return {}; } };

// CL1 (+ CL2 mix): one VU = one pair; a -> b rings, b accepts, hold, end. VUs beyond the fixtures just idle.
export function callPair() {
  const pair = pairs[(__VU - 1) % pairs.length];
  if (__VU > pairs.length) { sleep(30); return; }        // two VUs on one pair would turn every call into 409 already_in_call
  const t0 = Date.now();
  const init = post('/message/calls/initiate/', pair.a, { conversation_id: pair.conversation_id, type: Math.random() < 0.5 ? 'audio' : 'video' }, 'call_initiate');
  const ib = jbody(init);
  const ok = init.status === 200 && ib.call_id && ib.livekit_token;
  callFailed.add(!ok);
  if (!ok) { callCodes.add(1, { status: String(init.status) }); sleep(5); return; }
  sleep(1 + Math.random() * 3);
  const acc = post(`/message/calls/${ib.call_id}/action/`, pair.b, { action: 'accept' }, 'call_accept');
  const accOk = acc.status === 200 && !!jbody(acc).livekit_token;
  callFailed.add(!accOk);
  if (accOk) {
    sleep(CALL_HOLD_MIN + Math.random() * (CALL_HOLD_MAX - CALL_HOLD_MIN));
  }
  const end = post(`/message/calls/${ib.call_id}/action/`, Math.random() < 0.5 ? pair.a : pair.b, { action: 'end' }, 'call_end');
  callFailed.add(end.status !== 200);
  sleep(Math.max(CALL_MIN_CYCLE - (Date.now() - t0) / 1000, 0) + 2 + Math.random() * 6);
}

// SR1 / SR2 / SR5: one VU = one room member. Host (member 0) opens the session, the rest join inside JOIN_WINDOW, open the
// chat socket the Flutter client uses for study_room_event frames, poll room/ and leave. The relay of draw_point frames is
// NOT sent from here (use Locust STUDY_DRAWERS) - this keeps k6 a pure join-storm + socket-count tool.
export function studyMember() {
  if (rooms.length === 0) { sleep(30); return; }
  const per = rooms[0].members.length;
  const slot = __VU - 1;
  if (slot >= rooms.length * per) { sleep(30); return; }
  const room = rooms[Math.floor(slot / per)];
  const mi = slot % per;
  const me = room.members[mi];
  if (mi === 0) {
    const j = post(`/message/study-room/${room.conversation_id}/join/`, me, { new_session: true }, 'study_join_host');
    roomJoinFailed.add(j.status !== 200);
  } else {
    sleep(2 + Math.random() * JOIN_WINDOW);               // SR1: this window IS the join storm
    const j = post(`/message/study-room/${room.conversation_id}/join/`, me, { new_session: false }, 'study_join');
    const ok = j.status === 200 && !!jbody(j).livekit_token;
    roomJoinFailed.add(!ok);
    if (!ok) { callCodes.add(1, { status: String(j.status) }); sleep(10); return; }
  }
  const url = `${WS_BASE}/ws/chat/${room.conversation_id}/?token=${me.access}`;
  const res = ws.connect(url, {}, (socket) => {
    socket.on('open', () => {
      socket.setInterval(() => socket.ping(), 20000);
      socket.setInterval(() => {
        const r = http.get(`${BASE}/message/study-room/${room.conversation_id}/room/`, { headers: hdr(me), tags: { name: 'study_room' } });
        check(r, { 'room 200': (x) => x.status === 200 });
      }, ROOM_POLL * 1000);
    });
    socket.setTimeout(() => socket.close(), (HOLD_MIN + Math.random() * (HOLD_MAX - HOLD_MIN)) * 1000);
  });
  roomWsFailed.add(!(res && res.status === 101));
  sleep(20 + Math.random() * 70);                         // leave -> rejoin gap (STUDY_REJOIN_*)
}
