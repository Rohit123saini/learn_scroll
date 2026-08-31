# LearnScroll — Backend Naye Features Frontend Integration Architecture

> Ye doc `CHAT_APP_DOCUMENTATION.md` (backend, latest session) aur
> `PROJECT_ARCHITECTURE.md` (frontend, existing Flutter app) — dono ko
> compare karke bana hai. Maqsad: backend me jo naya logic add hua hai
> (poll, pin, mentions, link preview, auto-transcription, draft
> autosave, smart-reply, search, forward-with-caption, push digest) use
> Flutter app me kaha-kaha, kaise wire karna hai — screen by screen,
> file by file.

---

## 0. Sabse Pehle — Critical Mismatch (naya kaam shuru karne se pehle fix karna)

Frontend doc (`message_api_service.dart` §4.1, WS §8.1) **purane/galat
assumption** pe likha gaya tha — kuch endpoints aur socket-event naam
backend ke actual naye implementation se **match nahi karte**. Ye pehle
fix honge, warna naya feature bhi tootega.

| Area | Frontend abhi call kar raha hai | Backend actual (naya) | Action |
|---|---|---|---|
| Poll create | `POST /conversations/<id>/polls/` | `POST /conversations/<id>/poll/` (singular) | `message_api_service.dart` me path fix |
| Poll vote | `POST /polls/<id>/vote/` | `POST /messages/<id>/poll/vote/` (message id, poll id nahi) | path + param dono fix — ab `pollId` nahi, `messageId` chahiye |
| Poll close | `POST /polls/<id>/close/` | `POST /messages/<id>/poll/close/` | same as above |
| Poll get | `GET /polls/<id>/` | koi standalone `GET /polls/<id>/` endpoint backend me nahi hai — poll data `Message` payload ke andar `poll` field me aata hai (`MessageSerializer.poll`) | is call ko hata ke poll data seedha message object se lo, ya `poll_update` WS event se live update lo |
| Pins list | `GET /conversations/<id>/pins/` | `GET /conversations/<id>/pinned/` | path fix |
| Pin add/remove | `POST/DELETE /conversations/<id>/pins/<message_id>/` | `POST/DELETE /messages/<message_id>/pin/` | endpoint hi different resource pe hai (message-level, not conversation-level) |
| WS event: poll create | frontend sunta hai `poll_created` | backend bhejta hai naya poll bhi normal `chat_message` event se (poll ek normal Message hai, `poll` field ke saath) | `chat_message` handler me `payload['poll'] != null` check add karo, alag `poll_created` event ka wait mat karo |
| WS event: poll vote | frontend sunta hai `poll_voted` | backend bhejta hai `poll_update` | event name fix: `poll_voted`/`poll_created` dono ko `poll_update` se replace karo |
| WS event: pin | frontend sunta hai `message_pinned`/`message_unpinned` | backend bhejta hai `pin_event` (`{event: "pinned"|"unpinned", ...}`) | single handler `pin_event` banao, andar `event` field se pinned/unpinned decide karo |
| WS media send | frontend `sendMessage()` sirf `{text, messageType, clientId, replyTo}` bhejta hai | backend `handle_new_message` ab `file_url`, `file_urls`, `thumbnail_url`, `meta` bhi accept karta hai | `ChatSocketService.sendMessage()` signature extend karo — ab REST fallback zaroor nahi, media bhi socket se ja sakta hai |
| Search | koi frontend call nahi mila | `GET /conversations/<id>/search/?q=`, `GET /search_all/?q=` naye hain | naya service method + naya screen (§3.1) |

**Ye 8 mismatch fix karna Phase-0 hai — baaki sab isi ke upar depend
karta hai.**

---

## 1. Naye Backend Features — Frontend Status Summary

| # | Backend feature (source doc §) | Frontend me kahin hai? | Kya karna hai |
|---|---|---|---|
| 1 | Message Search + structured filters (§7.1) | ❌ nahi hai | Naya screen + service (§3.1) |
| 2 | Message Pin, max 3 (§7.2) | ⚠️ partial, galat endpoint/event | Fix + UI already `chat_screen.dart` me hai, sirf wiring theek karo |
| 3 | @Mentions (§7.3) | ❌ nahi hai | Compose box me `@` autocomplete + render me highlight + mention-push handling |
| 4 | Link Previews (§7.5) | ❌ nahi hai | `MessageModel.meta['link_preview']` parse + `meta_update` WS listener + preview card widget |
| 5 | Auto Voice Transcription (§7.6) | ⚠️ manual `VoiceTranscribeView` shayad already tha, auto nahi | `meta['transcript']` display "View transcript" bina tap kiye bhi aa sakta hai ab, `meta_update` listener se live update |
| 6 | `meta_update` WS event (§7.7) | ❌ handler nahi hai | naya case `_handleSocketEvent` me — link preview aur transcript dono isi se aate hain |
| 7 | Poll Messages (§7.8) | ⚠️ galat endpoints (§0) | Phase-0 fix + naya poll-results ka live update via `poll_update` |
| 8 | Forward with Caption (§7.9) | ❌ caption field frontend forward call me nahi hai | `forward_message_screen.dart` me caption input add |
| 9 | Server-side Draft Autosave (§7.10) | ❌ nahi hai (draft sirf local ho to bhi persist nahi) | `chat_screen.dart` compose-box debounce-save + `conversations_screen.dart`/list screen me draft preview |
| 10 | Read-Receipt Privacy Toggle (§7.11) | ❌ frontend me kahin UI nahi mila | Naya settings toggle (profile/privacy settings screen — abhi upload nahi hua, flag karo) |
| 11 | Smart-Reply Suggestions (§7.12) | ❌ nahi hai | `chat_screen.dart` compose bar ke upar suggestion chips |
| 12 | Chat Push Digest/Batching (§7.13) | Backend-only, par push payload ka naya `type: "chat_digest"` frontend ko handle karna hoga | `push_notification_service.dart` me naya data-type case |
| 13 | Per-IP throttling (§9) | Backend-only, no frontend change | 429 response handling generic hona chahiye (already `MessageApiException` hai, bas UI me "too many requests, try again" retry-toast add karo) |

---

## 2. Naye Screens Banenge

### 2.1 `message_search_screen.dart` *(NEW)*
- Trigger: `chat_screen.dart` app-bar me search icon (single-conversation
  search) **aur** `conversations_screen.dart` me ek global search entry
  (search-all).
- Do modes: `conversationId` diya gaya to `GET /conversations/<id>/search/?q=`,
  nahi diya to `GET /conversations/search_all/?q=`.
- UI: search bar (min 2 chars — client-side bhi debounce/validate karo,
  warna backend 400 dega) + filter chips (`sender`, `date_from`,
  `date_to`, `has_media`, `media_type`) + results list.
- Global-search result me `conversation_preview` (name/photo/type) bhi
  render karo, tap karke us conversation ke us message tak scroll/jump
  (`chat_screen.dart` ko `scrollToMessageId` param dena hoga — abhi ye
  capability chat_screen me nahi hai, add karna padega).
- Result tap se agar single-conversation mode me hai to seedha message
  highlight karo; global mode me `ChatScreen(conversation, jumpToMessageId)`
  push karo.

### 2.2 `mention_suggestions_overlay.dart` *(NEW widget, screen nahi)*
- `chat_screen.dart` ke compose `TextField` ke upar overlay — jaise hi
  user `@` type kare, group ke **active members** ki list filter-as-you-type
  dikhaye (backend regex sirf active members ke username match karta hai,
  isliye frontend list bhi group member list se hi banao, random user
  suggest mat karo).
- Select karne pe `@username` text me insert + kahin ek local
  `Set<int> mentionedUserIds` track (optional — backend khud text parse
  karke resolve karta hai, but agar tumhe local "mentioned chip" UI
  dikhani hai to helpful).

### 2.3 `read_receipt_privacy_screen.dart` *(NEW, small settings screen)*
- `GET/PATCH /message/presence/read-receipts/` `{show_read_receipts}`.
- Ek simple toggle + explanation text: "Turn off = tumhara read_at
  dusro ko nahi dikhega, AUR dusro ka read_at tumhe nahi dikhega."
  (mutual switch — copy backend ka §7.11 wording use karo).
- Entry point: kahi settings/privacy screen se — **abhi koi settings
  screen upload nahi hua hai**, isliye is screen ko standalone bana ke
  `chat_screen.dart` ya group-profile se temporarily link karo jab tak
  asli Settings screen mile.

Baaki sab naye backend features ke liye **naya full screen nahi
chahiye** — existing screens me hi widgets/logic add hoga (neeche §4).

---

## 3. Naye Models (`message_models.dart` me add)

| Model | Fields | Kyu chahiye |
|---|---|---|
| `LinkPreviewModel` | `url, title, description, image` | `MessageModel.meta['link_preview']` parse karne ke liye |
| `MentionModel` (ya `UserMini` hi reuse) | — | `MessageModel.mentionedUsers: List<UserMini>` — backend `mentioned_users` nested list bhejta hai |
| `SearchFilterModel` | `sender, dateFrom, dateTo, hasMedia, mediaType` | search screen ke query params banane ke liye |
| `SearchResultModel` | extends `MessageModel` + `conversationPreview {type,name,photoUrl}` | `search_all` response |
| `SmartReplyModel` | `List<String> suggestions` | smart-reply chips |

`MessageModel` me khud add karna:
- `meta['link_preview']` → `LinkPreviewModel? linkPreview` getter
- `meta['transcript']` → `String? transcript` getter
- `mentionedUsers: List<UserMini>`
- `poll: PollModel?` — **already model hai (§3.1 backend doc), bas
  `fromJson` me top-level `poll` key se bhi parse hona chahiye, sirf
  `meta['poll']` se nahi** — backend `MessageSerializer.poll` ek
  top-level field hai, frontend abhi `meta['poll']` maan raha tha, ye
  bhi ek mismatch hai, fix karo.

`ConversationSettings` model me add:
- `draftText: String?`, `draftUpdatedAt: DateTime?` (server-set,
  read-only client se)

---

## 4. Existing Screens — Kya Add/Remove Hoga

### 4.1 `chat_screen.dart` (sabse zyada changes yahi)

**Add:**
- `_handleSocketEvent` switch me 2 naye case: `meta_update` (link
  preview / transcript live update — us message ko in-place update karo,
  poori list reload mat karo) aur `pin_event`/`poll_update` (§0 fix ke
  saath).
- Compose box: debounce (1–2s) `PATCH /conversations/<id>/settings/`
  `{draft_text}` call jab text change ho aur user ne send na kiya ho.
  Screen open hote hi `conversation.mySettings.draftText` se compose
  box prefill karo.
- Compose box ke upar smart-reply chips row — jab last message
  requester ka apna na ho, `POST /ai/smart-replies/` call karo (debounce
  se, har naye incoming message pe nahi — throttle scope `ai_smart_reply`
  30/min hai, client bhi ek reasonable cooldown rakhe), tap karne se
  chip ka text compose box me daal do.
- `@` mention overlay wire karo (§2.2), send karte waqt kuch extra nahi
  chahiye — backend text se khud resolve karta hai.
- Message bubble widget me: agar `message.linkPreview != null` to
  preview-card render (title/description/image, tap → `url_launcher`);
  agar `message.transcript != null` (audio type) to "Transcript"
  expandable text — **ab manual tap-to-transcribe button ke sath-sath
  auto-transcript bhi aa sakta hai, dono state handle karo** (transcript
  null = "View transcript" button dikhta rahe jo manual REST call kare
  jaisa pehle tha; transcript already present = seedha text dikhao).
  ye bhi `meta_update` se live aayega agar screen open hai.
- Message bubble me `mentionedUsers` highlight — agar current user
  usme hai to bubble ko subtle highlight (WhatsApp jaisa) + mention
  push already alag se handle hoga (§4.6).
- Forward flow me caption input (§4.3 me detail).
- Poll bubble: ab poll data `poll_update` se bhi aa sakta hai — jo bhi
  local `PollModel` state hai use replace karo full object se (backend
  poll_update `{...full updated Poll...}` bhejta hai, partial merge mat
  karo).
- Search icon in app-bar → push `MessageSearchScreen(conversationId)`.
- Naya param `jumpToMessageId` accept karo — agar diya gaya hai to
  history load karke us message tak scroll + highlight (§2.1 se link).

**Remove/Fix:**
- `poll_created`/`poll_voted` handlers **hata do**, `poll_update` +
  normal `chat_message` (poll field check) se replace.
- `message_pinned`/`message_unpinned` handlers **hata do**, single
  `pin_event` handler se replace.
- Pin/poll REST calls jo `message_api_service.dart` se aate hain — naye
  path use karo (§0).
- `_sendMessage()` — agar media bhi ab socket se bhej sakte ho to
  "socket connected → REST fallback" logic revisit karo: chhoti/text
  ke liye socket hi rakho, bade file uploads abhi bhi REST-upload (Dio
  progress) ke through hi behtar hai (backend WS bhi accept karta hai
  file_url but upload khud REST/multipart se hi hota hai — pehle file
  upload karo, phir uska URL socket message me bhejo, agar socket path
  use karna hai).
- `initialParticipants: const []` ka `// TODO` — agar Study Room me
  `mentionedUsers` ya group member list ka istemal karna ho to isi
  waqt fix karo (unrelated to backend session but adjacent code).

### 4.2 `conversations_screen.dart` (aur agar `conversations_list_screen.dart`
zinda rakha to wahan bhi)

**Add:**
- Chat-list row me draft preview: agar `conversation.mySettings.draftText`
  non-empty hai to last-message text ki jagah `"Draft: <text>"` gray/italic
  me dikhao (WhatsApp jaisa) — data already `ConversationListSerializer.my_settings`
  se aata hai, koi extra call nahi chahiye.
- App-bar/search-tab me global search entry → `MessageSearchScreen()`
  (no conversationId).
- `_onInboxUpdate` handler me naya push-digest data-type ignore/handle
  karo agar inbox socket bhi digest count bhejta hai (confirm karna
  hoga — abhi backend doc me `inbox_update` unchanged bataya gaya hai,
  push digest sirf FCM payload ke liye hai, socket ke liye nahi — **is
  wajah se `InboxConsumer` me koi change nahi**, sirf `push_notification_service.dart`
  affected hai, §4.6 dekho).

**Remove:**
- Ye decide karna zaroori hai: `conversations_list_screen.dart`
  (simpler, no search/pin/rename/inbox-socket) **is naye feature-set ke
  liye maintain karna extra cost hai** — agar `main.dart` isko route
  nahi karta (frontend doc §5.1/§10 me already "confirm before
  deleting" flag hai), to ye poora file delete karo, sirf
  `conversations_screen.dart` ko primary rakho aur usi me sab naya
  logic dalo. Do jagah duplicate maintain karna naye features (search,
  draft-preview) ke saath aur mushkil ho jayega.

### 4.3 `forward_message_screen.dart`

**Add:**
- Caption `TextField` (optional) upar/niche — jab user forward confirm
  kare to `caption` bhi body me bhejo:
  `POST /messages/forward/ {message_ids, conversation_ids, caption}`.
- UI hint: caption sirf un forwarded messages pe apply hota hai jinka
  khud ka text nahi tha (media/location) — agar sab selected messages
  text-type hain to caption field disable/hide kar do (backend text
  wale message ka apna text overwrite nahi karta, UI me confusion na ho
  isliye).
- Poll messages ko forward-selection se hi exclude kar do (checkbox
  disabled + "Polls can't be forwarded" note) — backend already silently
  drop karta hai, but UI me pehle hi rok dena better UX hai.

### 4.4 `message_api_service.dart`

**Add (naye methods):**
- `searchMessages(conversationId, query, filters)` → `GET .../search/`
- `searchAllMessages(query, filters)` → `GET .../search_all/`
- `getPinnedMessages(conversationId)` → `GET .../pinned/`
- `getSmartReplies(conversationId)` → `POST /ai/smart-replies/`
- `updateDraft(conversationId, draftText)` → existing `settings` PATCH
  ka hi ek naya param, alag method nahi chahiye, `updateConversationSettings()`
  ko extend karo
- `getReadReceiptSetting()` / `setReadReceiptSetting(bool)` → `GET/PATCH
  /presence/read-receipts/`

**Fix (path corrections — §0 se):**
- `createPoll`, `votePoll`, `closePoll`, `pinMessage`, `unpinMessage`,
  `getPins` — sab ke path/param upar table ke hisaab se fix.
- `forward()` method signature me `caption` optional param add.

### 4.5 `chat_socket_service.dart`

**Add:**
- `sendMessage()` signature extend: `{text, messageType, clientId,
  replyTo, fileUrl, fileUrls, thumbnailUrl, meta}` — sab optional, taki
  purana text-only call bhi kaam kare.
- `sendPin(messageId, bool pin)` → `{type:'pin', message_id, pin}`
  naya outgoing method (backend `pin` client→server event accept karta
  hai, frontend me abhi nahi tha).

**Remove:**
- Raw `print()` debug logs (already frontend doc me flagged "hata dena
  baad me") — is refactor ke waqt hi clean kar do, naya code isi file
  me touch ho hi raha hai.

### 4.6 `push_notification_service.dart`

**Add:**
- FCM data payload ke naye `type` values handle karo:
  - `type: "mention"` — priority/bypass-mute notification (already
    backend bhejta hai `send_mention_push`, frontend abhi generic chat
    push jaisa treat kar raha tha shayad — alag channel/sound de sakte
    ho taki mute chat me bhi mention dikhe)
  - `type: "chat_digest"` — `"X sent N messages"` — koi `message_id`
    nahi hoga isme (single-message ke against), tap → seedha us
    conversation me khol do (message id na hone ki wajah se "jump to
    message" possible nahi, sirf conversation open hoga)
- `MissedCallWatcher`/normal call push logic unchanged rehta hai — sirf
  chat-message push ka data shape change hua hai.

### 4.7 `message_cache_service.dart`

Koi structural change zaroori nahi, bas:
- Cache-write karte waqt naya fields (`linkPreview`, `transcript`,
  `mentionedUsers`, `poll` top-level) `toJson()`/`fromJson()` me already
  cover ho jayenge agar §3 ke model changes sahi se `MessageModel`
  serialization me add kiye — is service ko khud kuch nahi karna, sirf
  model ka round-trip test kar lena (draft text cache me store karne ki
  zaroorat nahi, wo hamesha server se live aata hai).

---

## 5. Files/Screens — Remove ya Deprecate

| File | Kya karna hai | Kyu |
|---|---|---|
| `conversations_list_screen.dart` | **Delete** (confirm `main.dart` routing pehle) | Duplicate of `conversations_screen.dart`, naye features (search/draft-preview) do jagah maintain karna waste |
| `floating_call_bar.dart` | **Delete** (confirm not wired anywhere) | Frontend doc §10 khud kehta hai `minimized_call_bar.dart` fixed version hai, purana ALT hai |
| `chat_screen_pagination_fix.dart` | **Merge into `chat_screen.dart`, phir delete patch file** | Ye ek loose patch file hai, ab jab chat_screen.dart me itna refactor ho raha hai, isko properly inline kar do, alag "patch" file rakhna confusing hai |
| Poll-related dead code: `GET /polls/<id>/` call, agar kahin standalone poll-fetch UI hai | **Remove**, poll data ab sirf message payload / `poll_update` se aata hai | Endpoint backend me exist hi nahi karta (§0) |

---

## 6. WebSocket Event Catalog — Updated (Client Consumption)

Server → Client, jo `_handleSocketEvent` (chat_screen) ab actually
handle karega:

| type | Payload shape | Action |
|---|---|---|
| `chat_message` | full message incl. `mentioned_user_ids`, `poll` (if poll) | insert into list; agar `poll` non-null, poll-bubble render |
| `typing_event` | — | unchanged |
| `read_event` | — | unchanged |
| `delete_event` | — | unchanged |
| `reaction_event` | — | unchanged |
| `pin_event` *(renamed)* | `{event:"pinned"\|"unpinned", message_id, conversation_id, actor_id}` | update local message's `isPinned` flag |
| `presence_update` | — | unchanged |
| `call_event` | — | unchanged (CallManager handles) |
| `study_room_broadcast` | — | unchanged |
| `disappearing_messages_updated` | `{conversation_id, duration, updated_by}` | update conversation setting locally |
| `group_deleted` | `{group_id, conversation_id, deleted_by}` | pop screen + toast |
| `meta_update` *(NEW)* | `{message_id, meta}` | find message by id, replace `.meta`, re-derive `linkPreview`/`transcript` getters |
| `poll_update` *(renamed from poll_created/poll_voted)* | `{message_id, poll: {...}, voted_by \| closed_by}` | replace that message's `poll` field entirely with new object |

Client → Server naya:

| type | Fields |
|---|---|
| `pin` | `{message_id, pin: bool}` |
| `message` (extended) | ab `file_url`, `file_urls`, `thumbnail_url`, `meta` bhi optional fields ke saath |

---

## 7. Suggested Build Order (Phased)

1. **Phase 0 — Fix mismatches (§0).** Kuch naya nahi, sirf existing
   pin/poll code ko backend ke real contract se match karo. Ye sabse
   pehle isliye ki agle phases isi ke upar depend karte hain aur bina
   isके fix kiye poll/pin currently silently fail ho rahe honge.
2. **Phase 1 — Models + services.** `message_models.dart` me naye
   fields/models (§3), `message_api_service.dart` naye methods (§4.4).
   Koi UI change nahi abhi, sirf data layer.
3. **Phase 2 — Passive display features.** Link preview card, transcript
   display, mention highlight, `meta_update`/`pin_event`/`poll_update`
   socket handlers — sab "receive & render", koi naya user-action nahi.
4. **Phase 3 — Active features.** Mention `@` autocomplete + send, draft
   autosave, forward-with-caption, smart-reply chips — ye sab user
   input/action involve karte hain.
5. **Phase 4 — New screens.** `MessageSearchScreen`,
   `ReadReceiptPrivacyScreen` — standalone hai, kabhi bhi ban sakte
   hain, but Phase 1 (models/services) ke baad karna better hai taki
   reuse ho sake.
6. **Phase 5 — Cleanup.** Dead files delete (§5), duplicate
   conversations-list screen decision, print-log cleanup.

---

## 8. Open Questions (backend/frontend dono doc me flag hui, resolve karna hai kaam shuru karne se pehle)

- `main.dart` actually kaunsa conversations-list screen route karta hai
  — confirm before deleting the other.
- `GroupMedia` gallery abhi bhi kuch purane sessions me "not
  auto-populated" tha, is session ke fix (`create_group_media_for_message`)
  ke baad confirm karo `GroupViewSet.media`/frontend gallery screen me
  data aana shuru hua ya nahi.
- Read-receipt privacy settings screen kaha link hogi — asli "Settings"
  screen abhi upload nahi hui, uska structure pata chalte hi §2.3 ka
  entry-point update karna.
- `ai_smart_reply` throttle scope `settings.py` me confirm nahi hai
  (backend doc §7.12/§9.4) — agar missing hai to smart-reply endpoint
  har call pe crash karega jaise pehle poll/pin scopes karte the;
  backend se confirm karwao pehle isko frontend me wire karne se.
