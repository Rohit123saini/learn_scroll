"""P-F11 - the lecture quality menu data with the 240p rung (written, NOT RUN).

`GET /post/media/<id>/lecture/` -> `renditions` = [auto (master), then one entry per built rung]. The 240p rung is built
by P-E2 (post.services.HLS_LITE_RUNG); the Flutter menu is data driven, so it only shows 240p if the server lists it.
"""
from django.test import override_settings
from rest_framework.test import APITestCase

from . import lectures
from .models import PostMedia
from .tests_lectures import _lecture, _user


@override_settings(MEDIA_URL="/media/")
class LectureQuality240pTests(APITestCase):
    def setUp(self):
        self.owner, self.viewer = _user("q240_owner"), _user("q240_viewer")
        self.post, self.media = _lecture(self.owner, "Optics")

    def _give_hls(self, rungs):
        PostMedia.objects.filter(pk=self.media.pk).update(
            hls_playlist="posts/hls/m1/r1/master.m3u8", metadata={"hls": {"rungs": rungs}})
        self.media.refresh_from_db()

    def _bundle(self):
        self.client.force_authenticate(self.viewer)
        r = self.client.get(f"/post/media/{self.media.id}/lecture/")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()["data"]

    def test_hls_renditions_lists_the_240p_rung_first(self):
        self._give_hls(["240p", "360p", "540p", "720p"])
        names = [x["name"] for x in lectures.hls_renditions(self.media)]
        self.assertEqual(names, ["240p", "360p", "540p", "720p"])

    def test_each_rung_points_at_its_own_playlist(self):
        self._give_hls(["240p", "360p"])
        urls = {x["name"]: x["url"] for x in lectures.hls_renditions(self.media)}
        self.assertTrue(urls["240p"].endswith("posts/hls/m1/r1/240p/index.m3u8"), urls)
        self.assertTrue(urls["360p"].endswith("posts/hls/m1/r1/360p/index.m3u8"), urls)

    def test_bundle_has_auto_then_every_rung(self):
        self._give_hls(["240p", "360p", "540p"])
        d = self._bundle()
        self.assertEqual([x["name"] for x in d["renditions"]], ["auto", "240p", "360p", "540p"])
        self.assertTrue(d["renditions"][0]["url"].endswith("master.m3u8"))
        self.assertTrue(d["hls_url"].endswith("master.m3u8"))

    def test_old_video_without_240p_is_unchanged(self):
        self._give_hls(["360p", "540p", "720p"])
        self.assertEqual([x["name"] for x in self._bundle()["renditions"]], ["auto", "360p", "540p", "720p"])

    def test_tiny_source_has_only_the_240p_rung(self):
        self._give_hls(["240p"])
        self.assertEqual([x["name"] for x in self._bundle()["renditions"]], ["auto", "240p"])

    def test_video_without_hls_has_no_renditions(self):
        self.assertEqual(self._bundle()["renditions"], [])

    def test_row_that_does_not_remember_its_rungs_lists_none(self):
        PostMedia.objects.filter(pk=self.media.pk).update(hls_playlist="posts/hls/m1/r1/master.m3u8", metadata={})
        self.media.refresh_from_db()
        self.assertEqual(lectures.hls_renditions(self.media), [])
        # the master alone is still offered as "auto"
        self.assertEqual([x["name"] for x in self._bundle()["renditions"]], ["auto"])
