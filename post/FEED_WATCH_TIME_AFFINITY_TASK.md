# TASK - Feed ranking: watch-time + author-affinity

Two ranking signals, split into two independent parts (each ships with its own tests + docs).

## Part 1 - Watch-time in the ranking  ✅ DONE

### What was already there (and what was missing)
`video_completion_rate` WAS already in the formula: `views._video_and_velocity_boost()` used
`rate * 30` for videos in Home (all 3 pools) and Explore. Three gaps remained:
1. plain average, no evidence: ONE viewer who finished a video gave it the full +30;
2. no absolute watch time: finishing a 5 s loop == finishing a 10 min lesson;
3. (per-user watch behaviour is NOT part 1 - it feeds Part 2's affinity.)

### New formula (`feed_mix.video_watch_boost()`, pure twin `watch_boost_points()`)
```
boost = confidence * ( completion_rate * 30  +  min(avg_watch_seconds, 60) / 60 * 10 )
confidence = n / (n + 5)        n = viewers with watch data = Post.video_watch_count
```
| video | old | new |
|---|---|---|
| 1 viewer, finished | 30 | 5.8 |
| 10 viewers, 50% of a minute | 15 | 13.3 |
| 45 viewers, finished, avg >= 60 s | 30 | 36 |
| many viewers, finished, avg >= 60 s | 30 | ~40 (max) |
| unwatched / non-video | 0 | 0 |

Still additive: a boost is never negative, nothing is removed or demoted below a plain post.
Used everywhere the old term was (Home following/recommended/trending, Explore, and the
trending rank behind `GET /post/<id>/why/`), because they all call `_video_and_velocity_boost`.

### Data
* `Post.video_watch_count` (int) + `Post.video_avg_watch_seconds` (float), migration
  `0005_post_watch_time_stats.py` (adds the fields AND backfills from existing `PostView` rows, so
  currently-ranked videos keep their boost on deploy).
* Kept in sync by the existing `PostView` post_save signal (`update_video_completion_rate`), which now
  writes rate + count + avg seconds in one pass. Watch seconds are clamped to the row's own duration.
* `python manage.py recompute_video_watch_stats [--reset]` recomputes everything (bulk imports / safety net).

### Settings (`FEED_WATCH_TIME`, `settings.py`)
`enabled` (env `FEED_WATCH_TIME_ENABLED=0` -> old `rate * 30`, instant rollback), `completion_points` 30,
`watch_seconds_points` 10, `watch_seconds_cap` 60, `confidence_k` 5. `confidence_k <= 0` = no damping,
`watch_seconds_cap <= 0` = no seconds term.

### Flutter
Nothing to change: `api_service.dart` already reports `POST /post/<id>/video-progress/ {watched_seconds}`.
More reports = better data; report on pause / dispose / scroll-away (the furthest point only moves forward).

### Known limits
* The rate/avg count each viewer once (furthest point). A viewer who only skims the first second still
  counts as a low-completion viewer - that is intended (it is what "scrolled past" means).
* Already-frozen Home snapshots (cursor sessions) keep their order; new sessions use the new formula.

Tests: `post/tests_watch_time.py` (pure formula, signal, SQL == pure-formula mirror, ranking, Explore, backfill).
Run: `python manage.py test post.tests_watch_time`.

## Part 2 - Author-affinity  ✅ DONE

Posts of authors the user likes / comments on rank higher.

### Signal (`feed_mix.load_author_affinity`)
Per author, over the caller's last **30 days**: a like counts **1** (a `wrong` reaction never counts),
a comment or reply counts **3** (deleted / hidden comments don't). Every interaction decays with a
**14-day half-life**. `points = min(20, score * 2)` (`feed_mix.affinity_points`); strongest **50** authors are
used. Own posts never count. Examples: 1 fresh like = 2 pts, 1 comment = 6, a comment + 3 likes = 12,
7+ fresh likes (or 4 comments) = the 20 cap.
Why cap 20: it stays BELOW one "show fewer" tap on an author (25), and near the other personal signals
(interest +15, friend-of-follow +12), so affinity nudges but never dominates.

### Where it applies (`build_pool_ids`, `trending_rank`)
| pool | effect |
|---|---|
| following | +points inside the existing `-is_recent, is_seen` bands only: re-orders among recent unseen posts, can never lift an old / seen post over a recent / unseen one |
| recommended | + points on the score |
| trending | + points on the score (`_discovery_querysets(bonus=...)`, so `trending_rank` / "why" agree) |
Explore is unchanged (it has its own scoring). Additive: never removes a post.

### Interplay with "Show fewer" (feed feedback, Part 2)
An author with an ACTIVE `FeedFeedback(kind=author)` row gets NO affinity at all (`exclude_authors`), so
"show fewer" always wins - it does not just net out (-25 + 20). Once that row decayed away, affinity works again.

### "Why am I seeing this"
New reason `author_affinity` (`meta: user_id, username, likes, comments`; text "You've recently liked 4 of and
commented on 2 of @ann's posts."). Unlike the taste signals it scores in EVERY pool, so it is added to `following`
and `trending` too (after the headline) and LEADS the list in `recommended` (replacing `popular`).

### Settings (`FEED_AUTHOR_AFFINITY`)
`enabled` (env `FEED_AUTHOR_AFFINITY_ENABLED=0`), `window_days` 30, `half_life_days` 14 (0 = no decay),
`like_weight` 1, `comment_weight` 3, `points_per_unit` 2, `max_points` 20, `max_authors` 50, `row_cap` 1000.

### Flutter
No new endpoint. `GET /post/<id>/why/` may now return `code == "author_affinity"` (use the same icon slot
as the other reasons; `text` is display-ready).

### Known limits
* Read per request: 2 small queries (likes, comments, newest `row_cap` each) - no denormalised table. If this
  ever shows up in profiles, cache the map per user for a few minutes.
* Frozen Home snapshots keep their order; a like made now affects the NEXT session.
* Not used: the user's own watch time on an author's videos (Part 1's per-post stats are global). Easy add:
  another weight in `load_author_affinity` from `PostView.watch_seconds / video_duration_seconds`.

Tests: `post/tests_author_affinity.py`. Run: `python manage.py test post.tests_author_affinity`.
