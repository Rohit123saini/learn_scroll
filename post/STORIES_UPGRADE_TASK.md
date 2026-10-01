# Stories upgrade - 3 parts

Task: Story model had only media, caption, views. Add Close Friends list, stickers (poll, question, link),
story mentions, music, and "add to highlight".

| Part | Scope | Status |
|------|-------|--------|
| 1 | **Close Friends** - `Story.audience`, `CloseFriend` list, visibility on every story read path, management API, Flutter audience picker + list screen + green ring | DONE |
| 2 | **Stickers** (poll, question, link) + **story mentions** (both are overlay elements placed on the story, share one `StorySticker` shape) | backend DONE (mention, link, poll, question); Flutter DONE (models, service, composer, viewer, notification deep link, l10n) - not compiled here, run `flutter analyze` |
| 3 | **Music** on a story + **Add to Highlight** (Highlight model, pin/unpin stories, profile row) | backend DONE (3a music, 3b highlights, tested); Flutter NOT STARTED |

---

## Part 1 - Close Friends

### Data
- `Story.audience` : `everyone` (default, old behaviour) | `close_friends`. Migration `0006_story_close_friends`.
- `CloseFriend(owner, friend)` : `unique(owner, friend)`, no self (DB check). Private + one-way: the friend is not
  notified and being on someone's list does not put them on yours.

### Visibility (single source of truth: `post/story_visibility.py`)
Owner always sees own stories. `everyone` stories: unchanged. `close_friends` stories: only if owner has a
`CloseFriend` row for the viewer. Applied to `stories/` (list), `stories/<id>/view|react|reply/`. A story the
viewer may not see is a **404** (not 403) so its existence is not leaked. Viewers list stays owner-only.
Removing someone from the list hides the story from them immediately.

### API
| Method | Path | Notes |
|--------|------|-------|
| POST | `/post/stories/create/` | new optional multipart field `audience` (`everyone`/`close_friends`); invalid -> 400 |
| GET | `/post/stories/` | every story now has `audience` |
| GET | `/post/close-friends/` | my list, paginated |
| PUT | `/post/close-friends/` | body `{"user_ids": [..]}` replaces whole list -> `{count, skipped_user_ids}` |
| POST | `/post/close-friends/<user_id>/` | add: 201 new / 200 already there; 400 self; 404 unknown/blocked |
| DELETE | `/post/close-friends/<user_id>/` | remove, 204, idempotent |
| GET | `/post/close-friends/candidates/?q=` | my accepted followers + people I follow, minus self/blocked/inactive; rows carry `is_close_friend` |

Row shape: `{id, username, name, profile_picture, is_close_friend}`.

A block (either direction) deletes close-friend rows both ways (`post/signals.py`) and blocks adding.

### Files
- `post/models.py` (`Story.audience`, `CloseFriend`), `post/migrations/0006_story_close_friends.py`
- `post/story_visibility.py`, `post/close_friends_views.py`, `post/serializers.py`, `post/views.py`, `post/urls.py`
- `post/admin.py`, `post/signals.py`, `post/tests_close_friends.py` (29 tests)
- Flutter: `lib/post/services/close_friends_service.dart`, `lib/post/screens/close_friends_screen.dart`,
  `lib/post/widgets/story_caption_sheet.dart` (audience chips, returns `StoryComposeResult`),
  `lib/post/models/story_model.dart` (`audience`, `isCloseFriends`, `StoryGroup.hasUnviewedCloseFriends`),
  `lib/post/services/story_service.dart` (`createStory(audience:)`), `lib/home.dart` (green ring),
  `lib/post/screens/story_viewer_screen.dart` (Close Friends badge), l10n en/hi (9 new strings)

### Not in Part 1
Stickers, mentions, music, highlights (Parts 2-3). No entry to the list screen from Settings/Profile yet - it opens
from the story composer ("Edit list").

---

## Part 2a - Stickers foundation + Mentions + Link sticker (IN PROGRESS)

Split of the old "Part 2": **2a** = shared `StorySticker` overlay + @mention + link (tap-to-open, no responses stored).
**2b** = poll + question (they store votes/answers, need a results view) - registers two more validators in
`post/story_stickers.py::_VALIDATORS`, no schema change to the enum.

**Backend: DONE (not run against a live Django here - run the tests below).**
**Flutter: NOT STARTED.**

### Data
`StorySticker(story, kind, x, y, rotation, scale, z_index, mentioned_user, data)`. x/y = centre as 0..1 of the canvas.
Migration `post/0007_story_stickers`; `core/0002_story_mention_notif_type` (choices only).

### API
| Method | Path | Notes |
|--------|------|-------|
| POST | `/post/stories/create/` | new multipart field `stickers` = JSON list; invalid -> 400 `errors.stickers`, nothing saved |
| GET | `/post/stories/` | every story has `stickers` |
| GET | `/post/stories/<id>/` | one active story (what a `story_mention` notification opens); same visibility as view/react/reply |
| GET | `/post/stories/mention-candidates/?q=&audience=` | people to @mention |

Sticker in payload: `{"kind":"mention","x":..,"y":..,"rotation":..,"scale":..,"user_id":12}` or
`{"kind":"link",...,"url":"...","label":"..."}`. Limits: 10 stickers, 5 mentions, 1 link (settings
`STORY_MAX_STICKERS` / `STORY_MAX_MENTIONS` / `STORY_MAX_LINKS`, `STORY_LINK_BLOCKED_DOMAINS`).

### Rules
No self / blocked / inactive / duplicate mentions; Close Friends story -> only Close Friends can be mentioned.
Links: http/https only, no credentials, no IP/localhost. Mention -> `story_mention` bell notification.
Block removes mention tags both ways.

### Files (backend)
`post/story_stickers.py`, `post/models.py`, `post/migrations/0007_story_stickers.py`, `post/serializers.py`,
`post/views.py`, `post/urls.py`, `post/services.py` (`notify_story_mentions`), `post/signals.py`, `post/admin.py`,
`core/models.py`, `core/migrations/0002_story_mention_notif_type.py`, `post/tests_story_stickers.py`.

### Still to do (Flutter)
sticker model in `story_model.dart`; `stickers` param in `story_service.dart`; sticker editor + mention picker + link
sheet in the composer; overlay renderer + tap handling in `story_viewer_screen.dart` (change outer `onTapDown` to
`onTapUp` so sticker taps do not advance the story); `story_mention` deep-link in `notifications_screen.dart`;
l10n en/hi.

---

## Part 2b - Poll + Question (backend DONE, tested)

Models `StoryPollVote(sticker, user, option_index)` and `StoryQuestionAnswer(sticker, user, text<=300)`, both unique per
(sticker, user). Migration `post/0008_story_poll_question_responses`. Validators in `post/story_stickers.py`
(`poll`: question<=100, 2-4 options<=25 each, distinct; `question`: prompt<=100; limits 1 poll + 1 question per story,
settings `STORY_MAX_POLLS`, `STORY_MAX_QUESTIONS`, `STORY_POLL_MAX_OPTIONS`). Rules live in `post/story_sticker_responses.py`.

| Method | Path | Notes |
|--------|------|-------|
| POST | `/post/stories/<id>/stickers/<sid>/vote/` | `{"option": 0}` -> 201 `{sticker}`; vote is final (409 + current state); owner 400 |
| POST | `/post/stories/<id>/stickers/<sid>/answer/` | `{"text": "..."}` -> 201 `{sticker}`; one per viewer (409); owner 400 |
| GET | `/post/stories/<id>/stickers/<sid>/responses/` | owner only; paginated + `summary` (poll `{counts,total}` / question `{count}`) |

Sticker `data`: poll `{question, options, my_vote, results|null}` (results only for owner or after voting);
question `{prompt, my_answered, answers_count|null}` (count owner only). Same visibility/expiry/block gate as react
(404). Block deletes votes/answers both ways. Stats are batched (no N+1). Tests: `post/tests_story_poll_question.py`.
Verified in sandbox (sqlite): `makemigrations --check` clean; 150 tests pass (poll/question + stickers + close friends).

### Flutter status - Part 2 (2a + 2b) DONE
No Flutter SDK in the sandbox, so nothing was compiled or run on a device: every new / edited Dart file was only
parsed with a Dart syntax checker and every `l10n.*` key was cross-checked against `AppLocalizations`.
**Run `flutter analyze` and try the flow on a device before shipping.**

Model + service (earlier): `story_model.dart` (`StorySticker`, `StickerDraft`, `StoryMentionCandidate`,
`StickerResponses`), `story_service.dart` (`createStory(stickers:)`, `getStory`, `getMentionCandidates`, `votePoll`,
`answerQuestion`, `getStickerResponses`).

New in this pass:
- `lib/post/widgets/story_sticker_widgets.dart` - ONE renderer for all four kinds (`StoryStickerView`) +
  `StoryCanvas` (largest centred 9:16 box) + `StickerPlacement` (centre x/y as 0..1, rotation, scale; sticker natural
  size = 360-px-wide canvas, scales with canvas width). Composer preview and viewer both use it.
- `lib/post/widgets/story_sticker_pickers.dart` - `pickStickerDraft()`: tray (limits from the `kStoryMax*` constants,
  full tiles dim + "Limit reached"), mention picker (debounced search via `mention-candidates`, audience-aware, already
  tagged people disabled), link sheet (adds `https://` if missing, http/https only, no IP / credentials), poll sheet
  (question + 2-4 distinct options), question sheet.
- `story_caption_sheet.dart` - sticker button (top right); stickers drag / pinch-scale / rotate, touch brings to front,
  drop on the trash zone to delete; `StoryComposeResult.stickers` (list order = stacking order). Media preview is now
  `contain` inside the 9:16 canvas (same as the viewer). Sheet `enableDrag: false` (X / back still close it) so a vertical
  drag moves the sticker instead of the sheet. Switching to Close Friends with mention stickers on asks, then removes
  the mentions (server only allows Close Friends there).
- `home.dart` - passes `composed.stickers` to `createStory`.
- `story_viewer_screen.dart` - sticker layer over the media; outer tap zones now `onTapUp`; a story that has stickers
  shows its media inside the same 9:16 canvas (stories without stickers behave exactly as before). Mention -> profile
  (not for yourself), link -> "Open this link?" dialog -> browser, poll -> one final vote then results, question ->
  answer sheet (one per viewer), owner tap on poll / question -> responses sheet (poll totals + who voted what / answers,
  "Load more" pagination). Playback pauses while any sheet / dialog / page is open.
- `notifications_screen.dart` - `story_mention` opens the story via `GET /post/stories/<id>/` in the viewer (expired /
  hidden -> "This story is no longer available"); the row also gets the small media thumbnail. `notification_model.dart`:
  `story_mention` added to the Social mute category.
- l10n en + hi: 37 new keys (`storyAddSticker`, `sticker*`, `storyMentionUnavailable`) added to both `.arb` files and,
  by hand, to `app_localizations.dart` / `_en.dart` / `_hi.dart` (regenerate with `flutter gen-l10n` if you prefer).

Known limits: a story's canvas is 9:16 - on a taller phone the sticker area is letter-boxed a little; the viewer
canvas math is `storyCanvasSize()` if you ever want another ratio. Part 3 (music, highlights) is still not started.

---

## Part 3 - Music + Highlights (backend DONE, tested; Flutter NOT STARTED)

Migration `post/0009_story_music_highlights` (`Story.music` JSON, `Highlight`, `HighlightItem`).
Verified in sandbox (sqlite): `post.tests_story_music` + `post.tests_highlights` = 92 tests pass; `makemigrations --check`
is clean for the `post` app.

### 3a - Music on a story
One background track per story. Client picks it from the existing CC0 Freesound proxy (`GET /post/music/search/`) and
sends it with `POST /post/stories/create/` as JSON multipart field `music`:
`{"id","title","artist","preview_url","duration","start","license"}` (`url` = alias of `preview_url`, `name` = alias of
`title`). Stored cleaned on `Story.music` (`{id,title,artist,url,duration,start,license}`, NULL = no music) and returned on
every story. Rules in `post/story_music.py`: https only, no credentials / custom port, host must be Freesound
(`STORY_MUSIC_ALLOWED_HOSTS`), `.mp3`/`.ogg` path, query string dropped, CC0 only, text capped, `start` inside the
track. Invalid -> 400, nothing saved.

### 3b - Highlights
`Highlight(user, title<=30, cover_item, ...)` + `HighlightItem(highlight, story, position)`. A story can go in a
highlight if it is live or expired within the last 30 days ("archive"); a story the owner deleted before it expired
cannot. Stories in a highlight are never hard-deleted (`post/tasks.py::hard_delete_ancient_stories`). Limits: 50
highlights per user, 100 stories per highlight (`STORY_MAX_HIGHLIGHTS`, `STORY_MAX_HIGHLIGHT_ITEMS`).

| Method | Path | Notes |
|--------|------|-------|
| GET | `/post/stories/archive/` | my stories that can go into a highlight (picker), paginated |
| GET | `/post/highlights/?user_id=` | a person's highlights (default: mine); empty list if not visible |
| POST | `/post/highlights/` | create from archived stories |
| GET / PATCH / DELETE | `/post/highlights/<id>/` | one highlight with its stories / edit / delete (owner) |
| POST | `/post/highlights/<id>/stories/` | `{"story_id": "<uuid>"}` - the viewer's "Highlight" button |
| DELETE | `/post/highlights/<id>/stories/<story_id>/` | unpin a story |

Visibility: owner sees all; others not on block / inactive owner / private owner without accepted follow; per item the
story's own audience applies (Close Friends story only shows to Close Friends); a highlight with nothing visible to the
viewer is a 404 / left out of the list. Highlight stories are READ-ONLY for viewers (view / react / reply / vote /
answer endpoints answer 404 because the story is past 24 h) - the client must not call them from a highlight.

Files: `post/story_music.py`, `post/highlights.py`, `post/highlight_views.py`, `post/models.py`, `post/serializers.py`,
`post/urls.py`, `post/tasks.py`, `post/admin.py`, `post/migrations/0009_story_music_highlights.py`,
`post/tests_story_music.py`, `post/tests_highlights.py`.

### Still to do (Flutter, Part 3)
Music: picker in the story composer (reuse the reel music search), send `music` in `createStory`, play the track in
`story_viewer_screen.dart` (start at `music.start`, pause with the story). Highlights: `highlight_service.dart` +
model, profile row of highlight circles, create/edit screen using `stories/archive/`, "Highlight" button in the story
viewer (owner), highlight viewer that skips view/react/reply/vote calls, l10n en/hi.

