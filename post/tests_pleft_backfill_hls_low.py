"""P-LEFT: python manage.py test post.tests_pleft_backfill_hls_low  (WRITTEN, NOT RUN)"""
from django.test import SimpleTestCase

from post.management.commands.backfill_hls_low import parse_master_rungs

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720
720p/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
360p/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=960x540
540p/index.m3u8
"""


class ParseMasterTests(SimpleTestCase):
    def test_lowest_first(self):
        self.assertEqual(parse_master_rungs(MASTER), ["360p", "540p", "720p"])

    def test_junk_and_unsafe_uris_are_ignored(self):
        bad = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n../x/index.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=2\nhttp://e.com/a/index.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=3\na/b/index.m3u8\n"
        self.assertEqual(parse_master_rungs(bad), [])
        self.assertEqual(parse_master_rungs(""), [])
        self.assertEqual(parse_master_rungs(None), [])

    def test_same_rung_twice_is_listed_once(self):
        t = MASTER + "#EXT-X-STREAM-INF:BANDWIDTH=900000\n360p/index.m3u8\n"
        self.assertEqual(parse_master_rungs(t), ["360p", "540p", "720p"])
