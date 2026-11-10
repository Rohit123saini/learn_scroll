# Doubts hub (T3.4 / D8)

One list, one detail shape, one ask endpoint and one set of verbs over the five doubt systems.
**No new tables, no data migration** — the hub is an aggregation + dispatch layer (like `leaderboard`/`core`),
so existing screens, notifications and WebSocket broadcasts keep working untouched.

| `source` | Backing model | Who answers |
|---|---|---|
| `classroom_chat` | `message.DoubtQuestion` (group) + `DoubtAnswer` thread (T1.3) | any member except the asker; teacher answers are auto-verified; asker/teacher marks one **best**; teacher can **verify** |
| `campus` | `campus.CampusDoubt` + replies | subject teacher / class teacher / campus admin (+ asker follow-ups) |
| `tuition_class` | `tuitionclass.ClassQuery` | teacher / co-teacher / moderator / org staff |
| `feed_post` | `post.Post(type=doubt)` + `PostAnswer` | anyone (asker marks the best answer) |
| `testseries` | `message.DoubtQuestion(context_type=testseries_attempt)` | series creator |

## API (`/doubt-hub/`, JWT — NOT `/doubts/`: that prefix is the public SEO Q&A page `doubts/<slug>/`)

| Method & path | Purpose |
|---|---|
| `GET /doubt-hub/?source=&status=open\|answered\|resolved&role=all\|asked\|to_answer&q=&parent_id=&limit=&cursor=` | merged newest-first list; `next_cursor`; `errors` lists sources that failed (partial results, never a blank hub) |
| `GET /doubt-hub/summary/` | counts per source/status + `to_answer` badge (cached 30 s, dropped on every action) |
| `GET /doubt-hub/targets/` | where the viewer can ask (groups, subject-classes, classrooms, checked attempts) |
| `POST /doubt-hub/ask/` | `{source, target_id, [subject_id], [session_id], text, is_anonymous, is_public}` |
| `GET /doubt-hub/<source>:<id>/` | one doubt + thread |
| `POST /doubt-hub/<source>:<id>/<verb>/` | verb ∈ `answer` `upvote` `unupvote` `resolve` `reopen` `reveal` `mark_best` `verify` (`{text}` / `{answer_id}`) |

Unified item: `id, source, source_id, parent{type,id,name}, text, author|null, is_anonymous, is_mine, role, status,
answers_count, answer_preview, upvotes_count, upvoted_by_me, created_at, updated_at, capabilities{…}, extra` (+ `thread` on detail/action).
`capabilities` says which buttons to show; the server re-checks every action.

## Design rules

* **One visibility rule per system.** List, detail and counts all come from the same scoped queryset, so a hidden doubt is a plain 404.
  Campus reuses `CampusDoubtViewSet.get_queryset()` verbatim (author / parent / subject teacher / class teacher / admin / opt-in public / blocks).
* **Writes are delegated** to the source app's own view in-process (`dispatch.py`): permissions, throttles (the tighter of the
  hub's and the source's bucket applies), notifications and WS broadcasts are never re-implemented. Source errors pass through verbatim.
* Anonymous askers stay hidden exactly as in `DoubtQuestionSerializer` (asker or explicit reveal only).
* Feed doubts are *created* in the post composer (media/visibility live there) — `ask` returns `ask_unsupported` for `feed_post`.
* Cross-app imports are lazy (inside functions); the app owns no migrations.

## Known limits / follow-ups

* Cursor sorts by creation time; a "recently active" ordering would need a per-source `last_activity` column.
* Campus/tuition attachments (images) still go through their native endpoints — the hub accepts text.
* Feed doubts visible in the hub are the viewer's own + other people's `public` approved ones (followers-only doubts of others are not surfaced).
* Flutter: `flutter/lib/doubts/` (models, service, `DoubtsHubScreen`, `DoubtDetailScreen`), entry = Settings → *My doubts*;
  teachers can open with `initialTab: DoubtsHubTab.toAnswer`. Strings are l10n keys `dh*` (en + hi, generated files updated).
* Tests: `python manage.py test doubts` (DB; classroom_chat + feed_post end-to-end) and `python -m unittest doubts.tests_hub` (pure).

## Status mapping

| hub status | classroom_chat | campus | tuition_class | feed_post | testseries |
|---|---|---|---|---|---|
| `open` | no answers | `open` | `open` | no answers | no answer |
| `answered` | has answers, none best | `answered` | `answered` | has answers, none best | answered |
| `resolved` | an answer is marked best | `resolved` | — | asker marked a best answer | — |

`role=to_answer` is the *teacher's queue*: open doubts in groups you administer (classroom_chat), subject-classes you teach /
class-teach / admin (campus), classrooms you manage (tuition_class), series you created (testseries). For `feed_post` it means
"unanswered public doubts you could help with". Classmates can still answer classroom doubts (`capabilities.answer`) — it just isn't their inbox.
