# Study Room — Collaborative Sticky Notes

Code: `models.py::StudyRoomNote`, `sticky_notes.py` (logic), `consumers.py` (WS), `views.py::StudyRoomNotesView` (REST).
Flutter: `lib/message/services/sticky_note_sync.dart`, `lib/message/widgets/sticky_notes_layer.dart`.
Tests: `python manage.py test message.tests_sticky_notes`

## REST
`GET /message/study-room/<conversation_id>/notes/[?page_id=<id>]`  (active member + group `study_room_permission`)

```json
{ "notes": [ {"id": "<uuid>", "pageId": "page_1", "userId": "12", "x": 10.0, "y": 20.0,
              "width": 160.0, "height": 160.0, "color": 4294964637, "text": "hi", "zIndex": 3,
              "updatedAt": "2026-09-24T10:00:00.123456+00:00",
              "fieldTs": {"pos": 1790000000000, "size": ..., "color": ..., "text": ..., "z": ...}} ],
  "count": 1, "server_time_ms": 1790000000500,
  "limits": {"max_notes": 200, "max_text_length": 2000, "min_width": 80.0, ...} }
```
Notes z-order me (neeche se upar). Client `server_time_ms` se clock offset nikalta hai.

## WebSocket (`study_room_event` envelope, same socket jo study room already use karta hai)
Client -> server (persisted, har op me `opId` + `ts` (epoch ms, server-clock corrected)):

| action | data |
|---|---|
| `note_add` | `{note: {id(uuid), pageId, x, y, width, height, color(ARGB int), text}}` |
| `note_move` | `{noteId, x?, y?, width?, height?, front?}` (x+y together, width+height together) |
| `note_edit` | `{noteId, text?, color?, front?}` |
| `note_front` | `{noteId}` |
| `note_delete` | `{noteId}` |
| `note_drag` | `{noteId, x, y, width?, height?}` — ephemeral live preview, DB me nahi, ack nahi |

Server -> client:
* `note_ack` (sirf sender): `{opId, noteId, status, reason?, note?, rejected?}`
  `status`: `applied | partial | stale | deleted | rejected`. `rejected` = jo field-groups conflict me haare.
  `reason` (rejected): `not_member, forbidden, not_found, limit_reached, rate_limited, server_error, *_invalid, text_too_long`.
* `note_upsert` (baaki sab): `{note, source}`; `note_removed`: `{noteId, pageId, updatedAt}`; `note_drag`: `{noteId, x, y, width?, height?, userId}`.
* `clear_board` / `clear_board_keep_text` / `remove_page` pehle ki tarah relay hote hain, aur ab us page ke notes DB se bhi hatte hain.

## Conflict handling
Per-field-group last-write-wins. Groups: `pos`, `size`, `color`, `text`, `z`. `StudyRoomNote.field_ts[group]` = us group ka last-write ts.
Incoming `ts >= stored` => apply, warna `rejected` me + ack me authoritative `note` (client rollback). Alag groups independent hain
(A move kare, B text edit kare => dono bachte hain). Future ts `now + 5s` pe clamp. Delete = tombstone (late edit/add resurrect nahi kar sakte).
`z_index` server assign karta hai (max+1); writes per-room advisory lock se serialize.

## Rules
* Move/edit/color/resize: har participant. Delete: creator, group admin/moderator, ya 1-1 chat me dono (`sticky_notes.can_delete_note`).
* Limits: 200 notes/room, 2000 chars/note, size 80..800 x 60..800. Rate limit: 240 ops/min, 1200 drag-previews/min per user.
* Lifecycle: `join(new_session=true)` aur `DELETE .../state/` (end session) room ke saare notes purge karte hain.
* Legacy: purane `StudyRoomState.state.pages[*].stickyNotes` pehli GET par rows me import hote hain (sirf jab room me koi note row nahi), phir JSON se hat jaate hain.
