# MASTER ROADMAP — Zero se Advanced Tak (Numbered, Sequential)

Ye chaaron pichli files (`PRODUCTION_INTEGRATION_CHECKLIST.md`,
`INTEGRATED_PRODUCTION_ARCHITECTURE.md`, `PLATFORM_ARCHITECTURE_DEEP_DIVE.md`,
`ADDITIONAL_SUGGESTIONS.md`) ka **ek hi sequential, number-wise to-do list**
hai. Upar se neeche, isi order me karo. Har item ke aage **[App]** tag hai —
kis Django app/file me kaam karna hai.

**Legend:** `[core]` = naya shared app jo tumhe banana hai. `[login]`/
`[user_profile]`/`[message]`/`[liveclass]` = existing apps. `[posts]` = naya
app (Phase 5 me). `[infra]` = server/deployment/settings level, kisi ek app
me nahi.

---

## PHASE 0 — Pehle Ye (bina iske aage kuch bhi safe nahi hai)

1. **[infra]** Staging environment banao (production ka clone, alag DB/Redis) —
   agar abhi nahi hai.
2. **[infra]** CI/CD pipeline set karo (GitHub Actions/GitLab CI) — code push
   → auto-test → staging deploy.
3. **[infra]** `settings.py` ko environment-split karo (`settings/dev.py`,
   `settings/staging.py`, `settings/prod.py`) agar abhi single file hai.
4. **[infra]** Existing automated DB backup confirm karo + ek baar **actual
   restore-test** karo (backup jo restore nahi hua wo backup nahi hai).
5. **[infra]** `SENTRY_DSN` active monitoring me lao — weekly error-review
   ka process start karo (setting already hai, sirf process banana hai).

---

## PHASE 1 — Foundation: Identity, Media, Settings

*(Sabse pehle karna hai — baaki sab isi par khada hai)*

6. **[core — NEW]** Naya Django app `core` banao (`INSTALLED_APPS` me sabse
   upar, `login` ke turant baad).
7. **[core]** `message/user_display.py` ko `core/user_display.py` me copy karo
   (abhi `message` se delete mat karo — Phase 1.5 tak dono jagah rehne do).
8. **[core]** `get_absolute_media_url()` helper likho (photo, chat-attachment,
   future post-media sab ke liye single function).
9. **[infra]** `settings.py` me `MEDIA_ABSOLUTE_BASE_URL` set karo
   (dev/staging/prod alag values).
10. **[message]** Confirm karo har REST view jo user-nested serializer call
    karta hai, `context={'request': request}` pass kar raha hai.
11. **[message]** WS consumers/Celery tasks me photo-URL calls check karo —
    `MEDIA_ABSOLUTE_BASE_URL` fallback use ho raha hai confirm karo.
12. **[user_profile]** Iska apna (agar alag/duplicate) photo-URL logic hata
    ke `core.get_absolute_media_url()` use karo.
13. **[message]** `upload_view.py`'s `MessageUploadAPIView` ka `file_url` bhi
    isi helper se banao (chat-share problem yahin fix hota hai).
14. **[core]** Shared `UserMiniSerializer` banao — `user_id` (explicit,
    integer), `username`, `full_name`, `profile_photo`, `is_verified`.
15. **[message]** Saare serializers (`ConversationParticipant`, sender,
    read-status) `UserMiniSerializer` use karne lagayein — apna ad-hoc
    user-dict hatao.
16. **[user_profile]** `UserProfileSerializer`/`TargetUserProfileSerializer`
    bhi `user_id` naming convention follow karein.
17. **[frontend — sabhi apps]** Frontend team ko batao: `user_id` field naya
    hai, purana `id` field kuch waqt tak parallel rahega — jab frontend
    update ho jaye tabhi purana hataana (Phase 1 breaking-change wali
    warning yahi hai).
18. **[infra]** `settings.py` unify karo: `AUTH_USER_MODEL`, `INSTALLED_APPS`
    order, saare 3 apps ke `DEFAULT_THROTTLE_RATES` scopes ek jagah
    (`send_otp`, `verify_otp` + message ke scopes + liveclass ke scopes).
19. **[infra]** Ek hi `SECRET_KEY`/JWT signing key confirm karo — WS
    (`Middleware.py`/`ws_auth.py`) aur DRF dono isi se verify karte hain.
20. **[infra]** URL prefixes confirm karo (`login/`, `profile/`, `message/`,
    `liveclass/`) — frontend base-URL config isi se match hona chahiye.
21. **[infra]** CORS setup: `django-cors-headers`, sirf actual frontend
    domain(s) whitelist, `DEBUG = False` prod me.
22. **[infra]** Production media storage S3/GCS pe switch karo
    (`django-storages`), local `FileSystemStorage` sirf dev ke liye.
23. **[login]** `User.save()`/`delete()` ke raw `os.path`/`os.remove` calls ko
    storage-agnostic banao (`self.profile_photo.storage.delete()`) — S3 pe
    move karte hi ye tootenge agar fix nahi kiya.
24. **[core]** Verification: Postman se photo/attachment URL check karo — full
    `https://...` aana chahiye, relative nahi. Ye Phase 1 ka exit-criteria hai.
25. **[message]** Ab `user_display.py` ko `message` se delete karo, sirf
    `core` se import (Phase 1.5 cleanup — Step 7 ka temporary dual-copy hataana).

---

## PHASE 2 — Classroom ↔ Group Auto-Bridge

26. **[liveclass]** `Classroom` model me `linked_conversation_id` (UUIDField,
    nullable) aur `chat_group_enabled` (Boolean) field add karo, migration
    chalao.
27. **[message]** `GroupViewSet.create`/`add_members`/`update_member` ka logic
    ek plain reusable function me extract karo → `message/services.py`.
28. **[core]** `core/classroom_chat_bridge.py` banao — Part-B ke saare
    functions: `create_classroom_group()`, `sync_membership_on_join_accept()`,
    `sync_membership_on_removal()`, `promote_to_moderator()`,
    `sync_group_metadata()`, `archive_group_on_classroom_close()`,
    `post_welcome_message()`, `post_session_live_announcement()`.
29. **[liveclass]** `POST /liveclass/classrooms/<id>/create_group/` endpoint
    banao (teacher ka explicit "haan" confirm) — plain `APIView`, explicit
    `path()` register karna mat bhoolna.
30. **[liveclass]** `GET /liveclass/classrooms/<id>/group/` endpoint (linked
    conversation status check).
31. **[liveclass]** `signals.py` me wire karo: `ClassJoinRequest → ACCEPTED`
    → `sync_membership_on_join_accept()` (`transaction.on_commit()` ke andar,
    apna try/except ke saath).
32. **[liveclass]** Waitlist FCFS promotion signal → same bridge function call.
33. **[liveclass]** Staff/co-teacher add signal → `promote_to_moderator()`.
34. **[liveclass]** Kick/ban signal → `sync_membership_on_removal(reason="kick")`.
35. **[liveclass]** `PassPurchase.reverse()` (refund) → `sync_membership_on_removal(reason="refund")`.
36. **[liveclass]** Classroom title/cover_image/description update signal →
    `sync_group_metadata()`.
37. **[liveclass]** Classroom close/soft-delete → `archive_group_on_classroom_close()`.
38. **[liveclass]** `notify_session_live` task me `post_session_live_announcement()`
    call add karo.
39. **[infra]** Naya throttle scope `classroom_group_create` →
    `DEFAULT_THROTTLE_RATES` me add karo (warna 500 error aayega first hit pe).
40. **[liveclass]** Naya test file `test_classroom_chat_bridge.py` — create,
    accept, kick, refund, metadata-sync, archive — sab cases cover karo.
41. **[QA]** Manual end-to-end test: classroom banao → confirm → student join
    → group me dikhe → kick karo → group se hate → material share karo → link
    kaam kare.

---

## PHASE 3 — Unified Notification Hub

42. **[core]** `liveclass.Notification`/`NotificationPreference` models ko
    `core` app me **move** karo (naya migration, purana data migrate karo,
    import paths sab jagah update karo).
43. **[core]** `create_notification()` helper bhi `core` me move karo.
44. **[message]** FCM-only push system ko `core.create_notification()` ke
    through route karo — ab bell-row bhi banega, sirf push nahi.
45. **[core]** `GET /notifications/unread-count/` endpoint — sabke liye single
    badge count (liveclass ka already-existing endpoint generalize karo).
46. **[core]** `GET /notifications/` — unified list endpoint (message + class
    + follow + like — sab ek jagah, jab Phase 5 ke Posts/Follow bhi is system
    se jud jayein).
47. **[QA]** Regression test: `NotificationPreferenceTests` (already existing)
    naye location se bhi pass hone chahiye.

---

## PHASE 4 — Smoothness Improvements (parallel me kar sakte ho)

*(Ye Phase 3 ke turant baad karna best hai — kyunki inme se kaafi Notification
Hub ka hi extension hain)*

48. **[core]** Smart notification batching — jaisa `message` app me chat-push
    ke liye already hai, waise hi like/follow/comment jaisi events ke liye
    bhi batch karo ("5 logon ne like kiya" — ek push).
49. **[message]** Offline-first chat queue — local queue + retry-on-reconnect
    (existing `scheduled_messages.py` pattern reuse karke).
50. **[liveclass]** Live class audio-only fallback mode (LiveKit simulcast
    already supports — frontend toggle add karo).
51. **[liveclass]** Class materials/recordings download-for-offline option.
52. **[frontend — sab apps]** Progressive image loading (blur-placeholder →
    full image).
53. **[login]** Session/device management — "logged in devices" list +
    remote logout.

---

## PHASE 5 — Posts/Feed (naya core social loop)

54. **[posts — NEW APP]** Naya Django app `posts` banao.
55. **[posts]** Models: `Post`, `PostMedia`, `Like`, `Comment`, `SavedPost`,
    `Hashtag`, `PostHashtag`, `Story`, `StoryView` (`BaseModel`-jaisa pattern
    follow karo — UUID PK, soft-delete, `ordering`).
56. **[posts]** Serializers — nested user field `core.UserMiniSerializer` use
    karo (Phase 1 se already ready hai).
57. **[posts]** Views: create/list/delete Post, like/unlike, comment/reply,
    save/unsave, Story create + auto-expiry (24h).
58. **[user_profile]** `Post.save()`/`.delete()` signal se
    `User.posts_count` ko `F()` update karo (finally is orphaned field ko use
    milega).
59. **[posts]** Home feed endpoint — Phase-1 simple version: `Post.objects
    .filter(author__in=following_ids).order_by('-created_at')` + Redis
    page-1 cache (60s TTL).
60. **[posts]** (Future-scale, note karke rakho abhi implement zaroori nahi)
    fan-out-on-write Redis sorted-set design — jab DAU badhe tab.
61. **[posts]** Post ko chat me "share/forward" karne ka endpoint —
    `message`'s attachment-message flow reuse karke.
62. **[posts]** (Optional) `Post.classroom_ref` nullable FK — teacher ka
    classroom-linked showcase-post.
63. **[core]** Like/Comment/Follow events ko `core.create_notification()` se
    jodo (Phase 3 ka hub yahan use hota hai).
64. **[posts]** Tests likho — post create/delete, like idempotency, comment
    threading, story expiry.

---

## PHASE 6 — Growth Features (Posts ke saath ya turant baad)

65. **[liveclass]** Referral-rewards **poora flow** banao — `referral_urls`
    field already hai, ab: referral-tracking, dono taraf coins reward,
    referral-dashboard ("aapne itne log invite kiye").
66. **[posts/liveclass]** Gamification — badges/achievements model, opt-in
    per-classroom leaderboard, existing attendance-streak + coins se jodo.
67. **[liveclass]** Public, SEO-friendly classroom landing pages
    (`/classroom/<slug>/`) — Google-discoverable.
68. **[liveclass]** Weekly Parent-digest email/WhatsApp (existing
    `send_notification_digests` task ka naya digest-type).
69. **[liveclass]** Parent → Teacher structured-template messaging (predefined
    templates, free-text nahi — misuse-prevention).

---

## PHASE 7 — Unified Search & Discovery

*(Posts ke baad karna better hai — warna search karne layak content hi nahi hoga)*

70. **[core]** `core/search.py` — orchestrator jo `user_profile.UserSearchView`
    + naya Post-search + `Classroom`-search + `message.search_utils` (jab user
    apni chats me search kare) — sabko parallel query karke merge kare.
71. **[core]** `GET /search/?q=...&type=all|users|posts|classes|messages`
    endpoint.
72. **[core]** Explore/Discovery tab — trending posts (like-velocity),
    recommended classrooms (`Follow` graph + `PassPurchase` history se),
    suggested-users (`accepted_connection_ids()` reuse).
73. **[infra]** (Sirf scale badhne par) Postgres FTS se Elasticsearch/
    Meilisearch migrate karne ka evaluation.

---

## PHASE 8 — Trust & Safety (Posts/Comments live hone se PEHLE zaroori)

74. **[core]** Generic `Report` model (`content_type`, `object_id`, `reason`,
    `status`) — `liveclass`'s existing chat-report flow (file → review →
    soft-delete) ka shape generalize karke.
75. **[core]** `UserStrike` model — progressive enforcement.
76. **[core]** Single Moderation Queue (Django admin custom view ya internal
    tool) — sab content-type ek jagah review ho.
77. **[posts/message]** Existing Gemini integration (`ai_service.py`) ko
    report-queue pe bhi lagao — auto-flag likely-spam/abuse.
78. **[login]** Optional 2FA post-login (high-value/teacher accounts ke liye).

---

## PHASE 9 — Analytics, Activity, AI Companion (continuous, kabhi bhi parallel)

79. **[core]** User-facing "Activity" screen — `Notification`/event-log se
    derive karo (follows, likes, comments, class-joins).
80. **[liveclass]** `TeacherEarningsView`/`StudentProgressView` me engagement
    metrics add karo (post-reach, profile-views, class-group activity —
    existing denormalized counters se hi).
81. **[liveclass]** AI Study Companion — 24x7 doubt-chatbot (existing
    `ClassroomCopilotView` extend karo), "escalate to teacher" → existing
    Doubt Queue.
82. **[liveclass]** Personalized study-plan suggestions (`StudentProgressView`
    data + AI analysis).
83. **[liveclass]** Auto-generated class notes from `ClassTranscriptSegment`
    (already recorded, sirf summarize karna hai).

---

## PHASE 10 — Consistency Cleanup (kabhi bhi, low-risk se high-risk order me)

84. **[user_profile]** `Follow.Meta` me `ordering = ["-created_at"]` add karo
    (chhota, safe fix — known issue already documented).
85. **[user_profile]** `RestrictUser`/`coins` unused models — ya wire karo ya
    explicitly "intentionally parked" document karo.
86. **[infra]** Feature-flag system add karo (naye risky migrations ke liye,
    jaisa Phase 3 ka Notification-move).
87. **[infra]** Postgres read-replica setup (jab traffic/feed-query load badhe).
88. **[ALL APPS — sabse risky, sabse aakhri]** `user_profile`/`liveclass` ke
    models ko `BaseModel`-jaisa pattern (UUID/soft-delete) dena — **sirf naye
    models ke liye enforce karo** (Post, Comment, Report), purane
    (`Follow`, `Classroom`) ka PK type **kabhi mat badlo** production me — ye
    item sirf documentation/convention ke liye hai, migration ke liye nahi.

---

## Quick Reference — "Kis App Me Kya Naya Banega"

| App | Naya kya milega |
|---|---|
| `core` (naya) | Identity helpers, `UserMiniSerializer`, Notification Hub, Search orchestrator, Classroom-Chat Bridge, Report/Moderation |
| `posts` (naya) | Post, Comment, Like, Story, Hashtag, Feed, Explore-content |
| `login` | Storage-agnostic photo cleanup, 2FA, device-management |
| `user_profile` | `user_id` convention, `posts_count` finally wired, `Follow` ordering fix |
| `message` | Absolute media URLs, `UserMiniSerializer` adoption, offline-queue, Notification-hub se jud jayega |
| `liveclass` | Classroom-Group bridge endpoints, referral-flow, SEO pages, parent-digest, AI companion, engagement analytics |

---

**Sabse pehla concrete kaam (aaj hi shuru kar sakte ho):** Phase 0 (agar staging/
CI-CD missing hai) → warna seedha **Step 6** (`core` app banao) se shuru karo.
Har phase apne aap me ek working milestone hai — Phase 2 khatam karke bhi
production me deploy kar sakte ho, Phase 5 ka wait zaroori nahi.
