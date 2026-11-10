"""Pure unit tests for doubts/hub.py — no database, no Django settings.
Run:  python manage.py test doubts.tests_hub   (or: python -m unittest doubts.tests_hub)"""
import unittest
from datetime import datetime, timedelta, timezone

from doubts import hub

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def item(source, sid, minutes, status=hub.STATUS_OPEN):
    return hub.make_item(source=source, source_id=sid, text=f"{source}-{sid}", status=status,
                         created_at=T0 + timedelta(minutes=minutes))


class IdCodecTests(unittest.TestCase):
    def test_roundtrip_uuid_and_int(self):
        for sid in ("0b0e5a5e-6c4e-4a43-9a43-9d2d3e1c8a10", "42"):
            hid = hub.encode_id(hub.SOURCE_TUITION, sid)
            self.assertEqual(hub.decode_id(hid), (hub.SOURCE_TUITION, sid))

    def test_rejects_garbage(self):
        for bad in ("", "nocolon", "bogus:1", "campus:", "campus:   ", None):
            with self.assertRaises(hub.HubError) as cm:
                hub.decode_id(bad)
            self.assertEqual(cm.exception.status, 400)


class FilterTests(unittest.TestCase):
    def test_defaults(self):
        f = hub.HubFilter.from_params({})
        self.assertEqual(f.sources, hub.SOURCES)
        self.assertEqual((f.status, f.role, f.limit, f.cursor), (None, "all", hub.DEFAULT_PAGE_SIZE, None))

    def test_parsing_and_clamping(self):
        f = hub.HubFilter.from_params({"source": "campus, feed_post", "status": "open",
                                       "role": "to_answer", "limit": "9999", "q": "x" * 500})
        self.assertEqual(f.sources, ("campus", "feed_post"))
        self.assertEqual(f.limit, hub.MAX_PAGE_SIZE)
        self.assertEqual(len(f.q), 100)
        self.assertEqual(hub.HubFilter.from_params({"limit": "0"}).limit, 1)

    def test_validation_errors(self):
        for params in ({"source": "nope"}, {"status": "closed"}, {"role": "boss"},
                       {"limit": "abc"}, {"cursor": "!!!"}):
            with self.assertRaises(hub.HubError):
                hub.HubFilter.from_params(params)


class CursorTests(unittest.TestCase):
    def test_roundtrip(self):
        tok = hub.encode_cursor(T0, "campus:abc")
        self.assertNotIn("=", tok)
        self.assertEqual(hub.decode_cursor(tok), (T0, "campus:abc"))

    def test_tampered(self):
        with self.assertRaises(hub.HubError):
            hub.decode_cursor("eyJ0Ijoibm90LWEtZGF0ZSIsImsiOiJ4In0")  # t = "not-a-date"


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.a = [item("campus", "a3", 30), item("campus", "a1", 10)]
        self.b = [item("feed_post", "b4", 40), item("feed_post", "b2", 20), item("feed_post", "b0", 0)]

    def test_global_newest_first_order(self):
        page, nxt = hub.merge_pages([self.a, self.b], limit=10)
        self.assertEqual([i["source_id"] for i in page], ["b4", "a3", "b2", "a1", "b0"])
        self.assertIsNone(nxt)

    def test_has_more_gives_cursor_at_last_item(self):
        page, nxt = hub.merge_pages([self.a, self.b], limit=3)
        self.assertEqual([i["source_id"] for i in page], ["b4", "a3", "b2"])
        self.assertEqual(nxt, hub.sort_key(page[-1]))

    def test_exact_fit_has_no_next(self):
        page, nxt = hub.merge_pages([self.a, self.b], limit=5)
        self.assertEqual(len(page), 5)
        self.assertIsNone(nxt)

    def test_paging_through_everything_yields_each_item_once(self):
        seen, cursor = [], None
        for _ in range(10):
            lists = [[i for i in src if hub.after_cursor(i, cursor)] for src in (self.a, self.b)]
            page, nxt = hub.merge_pages(lists, limit=2)
            seen += [i["source_id"] for i in page]
            if not nxt:
                break
            cursor = nxt
        self.assertEqual(seen, ["b4", "a3", "b2", "a1", "b0"])

    def test_tie_on_timestamp_is_stable_by_id(self):
        x, y = item("campus", "x", 5), item("feed_post", "y", 5)
        page, _ = hub.merge_pages([[x], [y]], limit=5)
        self.assertEqual([i["id"] for i in page], sorted([x["id"], y["id"]], reverse=True))

    def test_empty(self):
        self.assertEqual(hub.merge_pages([[], []], 5), ([], None))


class ItemShapeTests(unittest.TestCase):
    def test_capabilities_default_false_and_override(self):
        i = hub.make_item(source="campus", source_id="1", text="t", status="open", created_at=T0,
                          capabilities={hub.ACTION_ANSWER: True})
        self.assertTrue(i["capabilities"][hub.ACTION_ANSWER])
        for a in hub.ACTIONS:
            if a != hub.ACTION_ANSWER:
                self.assertFalse(i["capabilities"][a])
        self.assertEqual(i["id"], "campus:1")

    def test_answer_preview_truncated(self):
        i = hub.make_item(source="campus", source_id="1", text="t", status="open", created_at=T0,
                          answer_preview="z" * 999)
        self.assertEqual(len(i["answer_preview"]), 200)


class SummaryTests(unittest.TestCase):
    def test_rollup(self):
        s = hub.summarize(
            {"campus": {"open": 2, "answered": 1, "resolved": 0}, "feed_post": {"open": 1, "answered": 0, "resolved": 4}},
            {"campus": 2, "feed_post": 1},
        )
        self.assertEqual(s["total"], 8)
        self.assertEqual(s["by_status"], {"open": 3, "answered": 1, "resolved": 4})
        self.assertEqual(s["by_source"]["feed_post"]["total"], 5)
        self.assertEqual(s["to_answer"], {"total": 3, "by_source": {"campus": 2, "feed_post": 1}})


if __name__ == "__main__":
    unittest.main()
