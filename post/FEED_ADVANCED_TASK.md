# FEED_ADVANCED_TASK — T1 (advanced feed)

## Part 1 — Diversity re-rank  ✅ DONE
- NEW `post/feed_diversity.py` (pure python): `diversify()` / `diversify_posts()`. Page ke andar reorder karta hai
  (author_gap=3, max_consecutive same post_type=2, category_gap=2, lookahead=8). **Kabhi post drop/add nahi karta** →
  `count`, cursor/snapshot, legacy page numbers sab same.
- `post/views.py` — `HomeFeedView._serialize_slices`: `feed_mix.interleave` ke baad `feed_diversity.diversify_posts(...)`.
  Dono paths (snapshot cursor + legacy page) isi se guzarte hain.
- `LearnScroll/settings.py` — `FEED_DIVERSITY` (env: `FEED_DIVERSITY_ENABLED=0` band, `_AUTHOR_GAP`, `_MAX_CONSECUTIVE`,
  `_CATEGORY_GAP`, `_LOOKAHEAD`).
- `post/tests_feed_diversity.py` — 9 pure tests + 2 endpoint tests (count/ids unchanged on/off; heavy author spread).
- Migration: koi nahi. Response shape: unchanged.
- Limit (jaan-boojh ke): sirf ek page ke andar; pichle page ki tail yaad nahi (stateless). Hard author cap Part 2 me.

## Part 2 — Author cap (pool level) + cross-page memory  ✅ DONE
- `post/feed_diversity.py`: `cap_ids` / `cap_pools` (author cap), `diversify(..., history=)`, `page_tail`, `clean_history`.
- **Author cap** — `feed_mix.build_pool_ids` ab `apply_author_caps()` se return karta hai (1 extra query: id -> author).
  Ek author ke `soft_cap` se zyada posts pool ki **tail me demote** hote hain (drop nahi). recommended + trending ek
  shared counter use karte hain. Defaults: following 5, discovery 2. `discovery_hard_cap` (default **0 = off**) on karo
  to discovery ke overflow posts **drop** honge (isse `count` badalta hai, isliye opt-in). Following kabhi drop nahi hota.
  Settings: `FEED_AUTHOR_CAP` (env `FEED_AUTHOR_CAP_ENABLED=0`, `_FOLLOWING_SOFT`, `_DISCOVERY_SOFT`, `_DISCOVERY_HARD`).
  Failure par feed 500 nahi deta, uncapped pools wapas.
- **Cross-page memory** — pichle page ke aakhri posts ke (author, type, category) keys snapshot cursor me `t` key me jaate
  hain (`feed_snapshot.encode_cursor(..., tail=)`); `decode_cursor` ab `tail` bhi deta hai (galat/tampered tail ignore,
  404 nahi; purane cursor bina `t` ke chalte hain). `HomeFeedView._list_snapshot` isse `diversify_posts(history=)` ko deta hai.
  Legacy page-number path stateless hai, usme history nahi.
- Tests (`tests_feed_diversity.py`): HistoryTests, AuthorCapTests, CursorTailTests, AuthorCapPoolTests (endpoint).
- Migration: koi nahi. Response shape: unchanged (cursor thoda lamba, opaque).

## Part 3 — Exploration slots + experiment bucket + stage-wise explain  ✅ DONE
feed_experiment.py (stable A/B bucket, weighted variants, overrides), feed_explore.py (new-creator slots, test audience -> scale/drop, Thompson / epsilon-greedy, bigger share for new viewers, woven into recommended, `is_exploration` on cards), `/why/` returns `experiment` + `stages` + new reasons.

## Part 4 — Extra signals + educational / campus-class context  ✅ DONE
feed_signals.py (dwell / tap / save / share positive, quick-scroll-past negative, time decay), feed_context.py (studied subjects, study-time boost, campus / class authors). Not built: replay, profile-visit-after-view (client sends no such events).

## Part 5 — Candidate cache + metrics + Flutter  ✅ DONE
feed_cache.py (30 s per-user cache, `?refresh=1` bypass, invalidated by hide / mute / feedback / seen / interests), feed_metrics.py + `manage.py feed_metrics` + staff `GET /post/feed/metrics/`, Flutter pull-to-refresh sends refresh=1 (dwell batching already in EventTracker).
Details + test status: TASK_T1_345_STATUS.md

## Part 6 — TASK.md T1 items 6-10 (quality gates, config, performance, metrics, Flutter)  ✅ code done
Details: `TASK_T1_678910_STATUS.md`. Short version:
- **6 Quality / safety gates** — NEW `post/feed_quality.py`. Layer 1 (SQL): `reported_count >= 10` is out of EVERY source incl.
  following (`views._home_base_qs`). Layer 2 (discovery pools + exploration candidates, Python): spam (links / char-run /
  SHOUTING / hashtag stuffing / one repeated word), same author + same text > 2x in 7 days -> drop; `reported >= 3` and tiny
  plain-text posts -> demoted to the pool tail. Runs before the author cap in `feed_mix.build_pool_ids`; failure = ungated pools.
  Blocked / muted / hidden: already filtered at pool build and on EVERY hydration; NEW `user_profile/block_live.py` also drops both
  people's cached candidates. `settings.FEED_QUALITY` (gates default OFF under `manage.py test`).
- **7 Config + experiments** — NEW `post/feed_config.py` (`effective(user_id)` = every knob + bucket/variant in force). A/B variants can
  now also override `mix`, `diversity`, `author_cap`, `quality` (besides explore / signals / context). `/why/` adds a `quality`
  stage, and `config` for staff.
- **8 Performance** — 30 s candidate cache + snapshot already existed; now block-invalidated. NEW `manage.py feed_benchmark`
  (p50 / p95 / query counts, exit 1 over `--target-ms`, default 300). Tests: cache hit == miss page 1 and cursor still works, no
  rebuild on cursor requests, query count does not grow with page size (N+1 guard).
- **9 Metrics** — `feed_metrics` already had CTR / dwell / sessions / show-fewer / diversity / per variant. Added: "show fewer like
  this" (FeedFeedback) counts in the show-fewer rate, Celery task `post.tasks.feed_daily_metrics` (beat 02:45, cached
  `feed:metrics:<date>`).
- **10 Flutter** — "Why am I seeing this" info button on feed cards + tap on the Suggested/Trending strip (reuses `showReelWhySheet`).
  Skeleton, EventTracker batching (10 s) and `refresh=1` pull-to-refresh already existed.

### Benchmark note (TASK.md "Done": query count + response time)
**No latency number was measured by the author of this part** - the sandbox had no Django / database. Run on production-sized data:

    python manage.py feed_benchmark --username <real user> --runs 30 --page-size 20 --cold   # worst case: every run rebuilds pools
    python manage.py feed_benchmark --username <real user> --runs 30 --page-size 20          # warm: candidate-cache hits

It prints p50 / p95 / max, SQL query count of the cold and the warm request and of page 2 (cursor), and fails (exit 1) when p95 is
above `--target-ms` (300 = the TASK.md target for a page of 20). Put the measured numbers here:

| run | p50 | p95 | queries (cold / warm / page 2) |
|-----|-----|-----|--------------------------------|
| _fill in_ | | | |

The automated N+1 guard is `tests_feed_gates.PerformanceTests.test_query_count_does_not_grow_with_page_size`.
