# TASK - Feed feedback controls

"Not interested", "Show fewer posts like this", "Mute this account", "Why am I seeing this".
Needs a `PostHide` / `NotInterested` model.

## Part 1 - Hide + Mute  ✅ DONE

Immediate, private, per-user controls. Nothing is sent to the other side.

Models (`post/models.py`, migration `0003_post_feed_feedback_controls.py`)
* `PostHide(user, post, reason, created_at)` - unique `(user, post)`; reasons: `not_interested`
  (default), `not_relevant`, `seen_too_often`, `other`. Table `post_hides`.
* `MutedAccount(user, muted_user, created_at)` - unique `(user, muted_user)`, DB check "no self mute".
  Table `post_muted_accounts`. Mute != block: follow relationship, profile access, DMs, search and
  notifications are NOT touched; only the account's posts leave the caller's feeds.

Endpoints (all under `/post/`, auth required)
| Method | URL | Notes |
|---|---|---|
| POST | `<post_id>/not-interested/` `{reason?}` | 201 first time, 200 repeat (reason overwritten); 400 own post / bad reason; 404 missing, deleted, private, or connections-only-not-followed |
| DELETE | `<post_id>/not-interested/` | undo, always 200 (`removed` true/false) |
| GET | `not-interested/` | my hidden posts, paginated (`id, post_id, reason, created_at`) |
| POST | `muted-accounts/` `{user_id}` | 201 first time, 200 repeat; 400 self / bad id; 404 unknown user |
| GET | `muted-accounts/` | paginated, with `user{id, username, names, profile_photo}` |
| DELETE | `muted-accounts/<user_id>/` | unmute, always 200 |

Where it is applied (`post.services.exclude_hidden_and_muted`, sub-selects, no python id lists)
* Home feed - `HomeFeedView._base_qs()`; so it covers all 3 pools AND the frozen Redis snapshot
  (ids are re-fetched through `_base_qs()` on every page, so a post hidden mid-scroll disappears from
  the next page at once).
* Explore, Hashtag feeds.
* Rules: a hidden post also hides REPOSTS of it; a muted account also hides reposts of ITS posts by others;
  muting a followed account removes it from Home even though the follow stays.
* NOT applied: profile post lists, search, post detail, saved posts, notifications (deliberate - the user
  actively went there).

Known limits
* `count` and pool sizes of an already-frozen Home snapshot still include a post hidden after it was
  built; the page just comes back one short. A new session (pull-to-refresh) is exact.
* Mute does not stop notifications from that account (separate feature).

Flutter contract: 3-dot menu -> `POST /post/<id>/not-interested/` (+ Undo snackbar -> `DELETE`),
"Mute @user" -> `POST /post/muted-accounts/ {"user_id"}`; manage screens use the two GET lists.

Tests: `post/tests_feed_feedback.py`.

## Part 2 - "Show fewer like this" + "Why am I seeing this"  ✅ DONE

### Decision: decaying, not permanent
A feedback weight halves every **30 days** (`FEED_FEEDBACK["half_life_days"]`, env `FEED_FEEDBACK_HALF_LIFE_DAYS`).
Taste changes, and a permanent penalty can quietly bury a whole category forever. `0` = permanent if product
prefers that. Decay is computed at read time from `updated_at`; no cron job.

### Model (`post/models.py`, migration `0004_feedfeedback.py`)
`FeedFeedback(user, kind = category|hashtag|author, key, weight, updated_at)`, table `post_feed_feedback`,
unique `(user, kind, key)`, DB check `weight > 0`.
* `key`: category slug (`tech`) | lower-case tag without `#` (`python`) | author user id (uuid string).
* `weight` = strength AT `updated_at`. Each tap: decayed weight + `step` (1.0), capped at `max_weight` (3.0).
* Rows that decayed below `min_effective` (0.05) are ignored and deleted on the user's next write.

### Ranking (`post/feed_mix.py`)
Minus points in **recommended + trending only** (`build_pool_ids`); **following is never touched**.

| kind | points at weight 1.0 | note |
|---|---|---|
| category | 15 | mirrors `UserInterest` +15: one tap cancels the interest bonus; skipped when `?category=` is set |
| hashtag | 8 | a post takes only its STRONGEST matching disliked tag (not summed); whole-tag match (`py` != `pythonista`) |
| author | 25 | most specific signal |

Points scale linearly with the decayed weight (3 taps on an author = -75). Like every other signal it is additive:
it pushes down, it never drops a post. Settings: `FEED_FEEDBACK` in `settings.py` (`enabled`, `half_life_days`,
`step`, `max_weight`, `min_effective`, `points`, `load_cap`). `enabled=False` keeps rows but ignores them.
Hashtag matching uses `hashtags__icontains` on the JSON-encoded quoted tag (portable; `hashtags__contains` is
PostgreSQL-only). It applies to the frozen Home snapshot only from the NEXT session (pools are built once) -
the hidden post itself disappears at once via Part 1.

### Endpoints (all under `/post/`, auth required)
| Method | URL | Notes |
|---|---|---|
| POST | `<post_id>/show-fewer/` `{targets:[{kind, key?}], reason?}` | hide (Part 1) + dampen. `targets` 1-5; `category`/`author` derived from the post (a supplied `key` is ignored), `hashtag` needs `key` = a tag OF THIS POST (`#Py` normalised). All-or-nothing: one bad target -> 400 and nothing is written. 201 = post hidden now, 200 = already hidden (signals still added). 400 own post / bad input, 404 like Not interested. Response `data: {hidden, feedback:[{id, kind, key, label, strength, updated_at}]}` |
| GET | `feedback/?kind=` | my show-fewer list, paginated, decayed rows not shown. `label`: "Technology" / "#python" / "@user"; `strength` = decayed weight |
| DELETE | `feedback/<feedback_id>/` | stop dampening one item; always 200 (`removed`), owner only |
| GET | `<post_id>/why/` | reasons, see below. Same 404 rules as Not interested |

`GET /post/<id>/why/` -> `{success, data: {post_id, feed_source, reasons:[{code, text, meta}], dampened:[{kind, key, strength}]}}`

| `code` | when | `meta` |
|---|---|---|
| `following` | you follow the author (only reason given) | `user_id, username` |
| `trending` | in the trending pool right now (only reason given) | - |
| `interest_category` | category is one you picked | `category, label` |
| `liked_category` | you reacted to that category in the last 30 days | `category, label` |
| `friend_of_follow` | people you follow also follow the author | `via:[{id, username}]` (max 3) |
| `author_affinity` | you like / comment on this author a lot (added to any pool; leads in recommended) | `user_id, username, likes, comments` |
| `popular` | recommended, no personal signal | - |
| `own_post` / `not_in_feed` | your post / not part of your suggestions (link, search, private account, blocked) | - |

`feed_source` = `following | recommended | trending | null`. `reasons[0]` is the headline; recommended can list
several. `dampened` = your active show-fewer rows that match the post ("You asked for fewer #python posts").

### How the explanation stays honest (`post/feed_explain.py`)
The ranking sums signals into one number, so the explain helper re-derives them per post from the SAME code:
pool decision = following author -> `following`; else `feed_mix.trending_rank(...) < trending_pool_cap` (same
querysets, score incl. penalty, tie-breaks, seen-exclusion) -> `trending`; else public post of a public account ->
`recommended`. Recommended signals come from `feed_mix.load_taste_signals` and the `POINTS_*` constants that
`build_pool_ids` uses. Only signals that really score in that pool are reported. `_home_base_qs(user)` is the
shared queryset.

Known limits
* `why` is computed live: after a session was frozen it can differ slightly from what the snapshot ranked
  (score changed, new seen posts). It answers "why would this be suggested now".
* `trending_rank` does a COUNT query; fine for a per-tap bottom sheet, not for bulk use.
* Show-fewer on a repost dampens the repost row's own category/author (the reposter), not the original's.

### Flutter contract
Client: `lib/post/services/feed_feedback_service.dart` (Part 1 + Part 2).
1. 3-dot menu on a feed card: **Not interested** -> `notInterested` (+ Undo), **Mute @user** -> `mute`,
   **Show fewer like this** -> bottom sheet with checkboxes: category (`label` from `post.category`), each of
   `post.hashtags`, author -> `showFewer(postId, targets)`. Remove the card at once. Undo snackbar ->
   `undoNotInterested` + `removeFeedback(id)` for every item in the result.
2. **Why am I seeing this** -> `why(postId)` bottom sheet: list `reasons[].text` with an icon per `code`;
   if `dampened` is non-empty add a "You asked for fewer ..." line. `feed_source` may drive a "Suggested for you" tag.
3. Settings -> "Feed preferences": `myFeedback()` list with a strength indicator (`strength` 0-3) and a remove
   button -> `removeFeedback`; Part 1's lists (`GET /post/not-interested/`, `GET /post/muted-accounts/`) alongside.

Tests: `post/tests_show_fewer_why.py` (pure logic, model, loaders/decay, services, ranking, show-fewer API,
manage/undo, end-to-end feed, why API). Part 1: `post/tests_feed_feedback.py`.
Run: `python manage.py test post.tests_show_fewer_why` (then the whole `post` app).
