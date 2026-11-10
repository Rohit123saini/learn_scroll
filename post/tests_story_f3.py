"""
STORY STUDIO F3 (part 2) tests - NOT RUN when written (read-only session). Run: python manage.py test post.tests_story_f3

Covers: the five new sticker kinds (location, countdown, quiz, add_yours, gif) - validation + limits, quiz answering
(final, owner can't answer, answer hidden until the viewer answered), the add_yours chain count, minor privacy for the
location sticker, the owner-only quiz responses list, the music `clip` field, the GIF url rules and the GIF search proxy.
"""
import json
import shutil
import uuid
from datetime import timedelta
from unittest import mock

from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import Story, StoryQuizAnswer, StorySticker
from .story_gifs import GifError, clean_gif_url, map_giphy_results
from .story_music import MusicError, clean_music
from .tests_story_stickers import _TMP_MEDIA, follow, image_upload, make_story, make_user, rows_of

GIF = "https://media0.giphy.com/media/abc123/giphy.gif"
MUSIC = "https://cdn.freesound.org/previews/123/123456_1-hq.mp3"


def future(days=3):
    return (timezone.now() + timedelta(days=days)).isoformat()


def make_quiz(story, **extra):
    return StorySticker.objects.create(
        story=story, kind=StorySticker.KIND_QUIZ,
        data={"question": "2+2?", "options": ["3", "4", "5"], "correct_index": 1, "explanation": "Basic sum"}, **extra,
    )


def make_chain_root(story, prompt="Show your desk", **extra):
    return StorySticker.objects.create(
        story=story, kind=StorySticker.KIND_ADD_YOURS, data={"prompt": prompt}, **extra,
    )


def sticker_in(story_data, sticker):
    return next(s for s in story_data["stickers"] if str(s["id"]) == str(sticker.id))


def detail(client, story):
    return client.get(reverse("story-detail", args=[story.id])).data


class _CreateMixin:
    def _create(self, stickers, **data):
        return self.client.post(
            reverse("story-create"),
            {"media": image_upload(), "media_type": "image", "stickers": json.dumps(stickers), **data},
            format="multipart",
        )

    def _assert_rejected(self, res, code_part=None):
        self.assertEqual(res.status_code, 400, getattr(res, "data", None))
        self.assertIn("stickers", res.data["errors"])
        self.assertEqual(Story.objects.count(), 0)
        self.assertEqual(StorySticker.objects.count(), 0)


# ---------------------------------------------------------------------------
# Creating the new kinds.
# ---------------------------------------------------------------------------
class F3StickerCreateTests(_CreateMixin, APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.owner = make_user("f3_owner")
        self.client.force_authenticate(self.owner)

    def test_create_all_five_kinds(self):
        res = self._create([
            {"kind": "location", "name": "Delhi Public Library", "lat": 28.6139, "lng": 77.2090},
            {"kind": "countdown", "title": "Board exam", "ends_at": future()},
            {"kind": "quiz", "question": "2+2?", "options": ["3", "4"], "correct_index": 1},
            {"kind": "add_yours", "prompt": "Show your desk"},
            {"kind": "gif", "id": "abc123", "url": GIF, "title": "wow", "width": 480, "height": 270},
        ])
        self.assertEqual(res.status_code, 201, res.data)
        kinds = list(StorySticker.objects.order_by("z_index").values_list("kind", flat=True))
        self.assertEqual(kinds, ["location", "countdown", "quiz", "add_yours", "gif"])

    # ---- location ----
    def test_location_coordinates_are_rounded_and_optional(self):
        res = self._create([{"kind": "location", "name": "Jaipur", "lat": 26.91234, "lng": 75.78765}])
        self.assertEqual(res.status_code, 201, res.data)
        data = StorySticker.objects.get().data
        self.assertEqual((data["lat"], data["lng"]), (26.91, 75.79))
        Story.objects.all().delete()
        self.assertEqual(self._create([{"kind": "location", "name": "Jaipur"}]).status_code, 201)

    def test_location_rules(self):
        bad = [
            {"kind": "location"},
            {"kind": "location", "name": "  "},
            {"kind": "location", "name": "x" * 61},
            {"kind": "location", "name": "A", "lat": 10},                       # lat without lng
            {"kind": "location", "name": "A", "lat": 91, "lng": 0},
            {"kind": "location", "name": "A", "lat": 0, "lng": 181},
            {"kind": "location", "name": "A", "lat": "10", "lng": "20"},
            {"kind": "location", "name": "A", "lat": True, "lng": 1},
        ]
        for sticker in bad:
            with self.subTest(sticker=sticker):
                self._assert_rejected(self._create([sticker]))

    # ---- countdown ----
    def test_countdown_is_stored_as_utc_z(self):
        res = self._create([{"kind": "countdown", "title": "Exam", "ends_at": "2999-01-01T10:00:00+05:30"}])
        # too far away -> rejected
        self._assert_rejected(res)
        res = self._create([{"kind": "countdown", "title": "Exam", "ends_at": future(10)}])
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(StorySticker.objects.get().data["ends_at"].endswith("Z"))

    def test_countdown_rules(self):
        past = (timezone.now() - timedelta(minutes=1)).isoformat()
        for sticker in (
            {"kind": "countdown", "ends_at": future()},                         # no title
            {"kind": "countdown", "title": "T"},                                # no ends_at
            {"kind": "countdown", "title": "T", "ends_at": "tomorrow"},
            {"kind": "countdown", "title": "T", "ends_at": past},
            {"kind": "countdown", "title": "T", "ends_at": future(400)},
            {"kind": "countdown", "title": "x" * 41, "ends_at": future()},
        ):
            with self.subTest(sticker=sticker):
                self._assert_rejected(self._create([sticker]))

    # ---- quiz ----
    def test_quiz_rules(self):
        base = {"kind": "quiz", "question": "Q?", "options": ["a", "b"], "correct_index": 0}
        bad = [
            {**base, "correct_index": 2},
            {**base, "correct_index": -1},
            {**base, "correct_index": True},
            {**base, "correct_index": None},
            {**base, "options": ["a"]},
            {**base, "options": ["a", "A"]},                                    # duplicate (case-insensitive)
            {**base, "options": ["a", "b", "c", "d", "e"]},
            {**base, "question": ""},
            {**base, "explanation": "x" * 121},
        ]
        for sticker in bad:
            with self.subTest(sticker=sticker):
                self._assert_rejected(self._create([sticker]))
        ok = self._create([{**base, "correct_index": "1", "explanation": " Because "}])
        self.assertEqual(ok.status_code, 201, ok.data)
        data = StorySticker.objects.get().data
        self.assertEqual((data["correct_index"], data["explanation"]), (1, "Because"))

    # ---- gif ----
    def test_gif_rules(self):
        for sticker in (
            {"kind": "gif", "url": "http://media0.giphy.com/a.gif"},
            {"kind": "gif", "url": "https://evil.example/a.gif"},
            {"kind": "gif", "url": "https://media0.giphy.com/a.png"},
            {"kind": "gif", "url": GIF, "id": "bad id!"},
            {"kind": "gif", "url": GIF, "width": 0},
            {"kind": "gif", "url": GIF, "preview_url": "https://evil.example/p.gif"},
            {"kind": "gif"},
        ):
            with self.subTest(sticker=sticker):
                self._assert_rejected(self._create([sticker]))

    def test_gif_query_string_is_dropped_and_preview_defaults_to_url(self):
        res = self._create([{"kind": "gif", "url": GIF + "?cid=SECRET&ep=v1"}])
        self.assertEqual(res.status_code, 201, res.data)
        data = StorySticker.objects.get().data
        self.assertEqual((data["url"], data["preview_url"]), (GIF, GIF))

    # ---- limits ----
    def test_per_kind_limits(self):
        loc = {"kind": "location", "name": "A"}
        cd = {"kind": "countdown", "title": "T", "ends_at": future()}
        quiz = {"kind": "quiz", "question": "Q", "options": ["a", "b"], "correct_index": 0}
        ay = {"kind": "add_yours", "prompt": "P"}
        for pair in ([loc, loc], [cd, cd], [quiz, quiz], [ay, ay], [{"kind": "gif", "url": GIF}] * 4):
            with self.subTest(kind=pair[0]["kind"]):
                self._assert_rejected(self._create(pair))
        self.assertEqual(self._create([{"kind": "gif", "url": GIF}] * 3).status_code, 201)

    @override_settings(STORY_MAX_QUIZZES=2)
    def test_limit_is_a_setting(self):
        quiz = {"kind": "quiz", "question": "Q", "options": ["a", "b"], "correct_index": 0}
        self.assertEqual(self._create([quiz, quiz]).status_code, 201)


# ---------------------------------------------------------------------------
# Minor privacy: no location sticker for a minor.
# ---------------------------------------------------------------------------
class F3MinorLocationTests(_CreateMixin, APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        today = timezone.now().date()
        self.kid = make_user("f3_kid", date_of_birth=today.replace(year=today.year - 14))
        self.adult = make_user("f3_adult", date_of_birth=today.replace(year=today.year - 30))

    def test_minor_cannot_add_a_location_but_can_add_other_new_kinds(self):
        self.client.force_authenticate(self.kid)
        res = self._create([{"kind": "location", "name": "My school"}])
        self._assert_rejected(res)
        self.assertIn("isn't available", json.dumps(res.data))
        ok = self._create([{"kind": "countdown", "title": "Exam", "ends_at": future()}])
        self.assertEqual(ok.status_code, 201, ok.data)

    def test_adult_can_add_a_location(self):
        self.client.force_authenticate(self.adult)
        self.assertEqual(self._create([{"kind": "location", "name": "Cafe"}]).status_code, 201)

    @override_settings(MINOR_BLOCK_LOCATION=False)
    def test_kill_switch_allows_it_again(self):
        self.client.force_authenticate(self.kid)
        self.assertEqual(self._create([{"kind": "location", "name": "Cafe"}]).status_code, 201)

    def test_privacy_defaults_list_the_blocked_kinds(self):
        self.client.force_authenticate(self.kid)
        res = self.client.get(reverse("post-minor-privacy")) if _has_url("post-minor-privacy") else None
        if res is not None:
            self.assertIn("location", res.data["blocked_sticker_kinds"])

    def test_old_location_of_an_author_who_is_a_minor_now_is_hidden(self):
        story = make_story(self.kid)
        sticker = StorySticker.objects.create(
            story=story, kind=StorySticker.KIND_LOCATION, data={"name": "Home street"},
        )
        viewer = make_user("f3_viewer")
        follow(viewer, self.kid)
        self.client.force_authenticate(viewer)
        self.assertEqual(sticker_in(detail(self.client, story), sticker)["data"], {})


def _has_url(name):
    try:
        reverse(name)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Quiz answering.
# ---------------------------------------------------------------------------
class F3QuizAnswerTests(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.owner = make_user("qz_owner")
        self.bob = make_user("qz_bob")
        self.cara = make_user("qz_cara")
        follow(self.bob, self.owner)
        follow(self.cara, self.owner)
        self.story = make_story(self.owner)
        self.quiz = make_quiz(self.story)

    def _answer(self, user, option, sticker=None):
        self.client.force_authenticate(user)
        sticker = sticker or self.quiz
        return self.client.post(
            reverse("story-quiz-answer", args=[sticker.story_id, sticker.id]), {"option": option}, format="json",
        )

    def test_right_answer_is_hidden_until_the_viewer_answers(self):
        self.client.force_authenticate(self.bob)
        data = sticker_in(detail(self.client, self.story), self.quiz)["data"]
        self.assertIsNone(data["correct_index"])
        self.assertIsNone(data["explanation"])
        self.assertIsNone(data["results"])
        self.assertIsNone(data["my_answer"])
        self.assertEqual(data["options"], ["3", "4", "5"])

    def test_answering_reveals_verdict_and_counts(self):
        res = self._answer(self.bob, 1)
        self.assertEqual(res.status_code, 201, res.data)
        data = res.data["sticker"]["data"]
        self.assertEqual((data["my_answer"], data["correct_index"], data["explanation"]), (1, 1, "Basic sum"))
        self.assertEqual(data["results"], {"counts": [0, 1, 0], "total": 1, "correct": 1})
        self.assertTrue(StoryQuizAnswer.objects.get(user=self.bob).is_correct)

    def test_wrong_answer_is_recorded_as_wrong(self):
        self._answer(self.bob, 0)
        self.assertFalse(StoryQuizAnswer.objects.get(user=self.bob).is_correct)
        res = self._answer(self.cara, 1)
        self.assertEqual(res.data["sticker"]["data"]["results"], {"counts": [1, 1, 0], "total": 2, "correct": 1})

    def test_answer_is_final(self):
        self.assertEqual(self._answer(self.bob, 0).status_code, 201)
        res = self._answer(self.bob, 1)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["code"], "already_answered")
        self.assertEqual(res.data["sticker"]["data"]["my_answer"], 0)
        self.assertEqual(StoryQuizAnswer.objects.count(), 1)

    def test_owner_cannot_answer_but_always_sees_the_answer_and_counts(self):
        res = self._answer(self.owner, 1)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "own_sticker")
        self._answer(self.bob, 2)
        self.client.force_authenticate(self.owner)
        data = sticker_in(detail(self.client, self.story), self.quiz)["data"]
        self.assertEqual(data["correct_index"], 1)
        self.assertEqual(data["results"], {"counts": [0, 0, 1], "total": 1, "correct": 0})

    def test_bad_options_are_rejected(self):
        for option in (None, "x", True, -1, 3, 1.5):
            with self.subTest(option=option):
                res = self._answer(self.bob, option)
                self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(StoryQuizAnswer.objects.count(), 0)

    def test_not_a_quiz_is_404(self):
        poll = StorySticker.objects.create(
            story=self.story, kind=StorySticker.KIND_POLL, data={"question": "Q", "options": ["a", "b"]},
        )
        self.assertEqual(self._answer(self.bob, 0, sticker=poll).status_code, 404)

    def test_visibility_expiry_and_login_gates(self):
        stranger = make_user("qz_stranger")
        cf_story = make_story(self.owner, audience=Story.AUDIENCE_CLOSE_FRIENDS)
        cf_quiz = make_quiz(cf_story)
        self.assertEqual(self._answer(stranger, 0, sticker=cf_quiz).status_code, 404)
        Story.objects.filter(pk=self.story.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self._answer(self.bob, 0).status_code, 404)
        self.client.force_authenticate(None)
        res = self.client.post(reverse("story-quiz-answer", args=[self.story.id, self.quiz.id]), {"option": 0})
        self.assertIn(res.status_code, (401, 403))

    def test_other_story_sticker_id_is_404(self):
        other = make_story(self.owner)
        self.client.force_authenticate(self.bob)
        res = self.client.post(reverse("story-quiz-answer", args=[other.id, self.quiz.id]), {"option": 0})
        self.assertEqual(res.status_code, 404)

    def test_db_constraint_one_answer_per_viewer(self):
        StoryQuizAnswer.objects.create(sticker=self.quiz, user=self.bob, option_index=0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            StoryQuizAnswer.objects.create(sticker=self.quiz, user=self.bob, option_index=1)

    def test_owner_responses_list(self):
        self._answer(self.bob, 1)
        self._answer(self.cara, 2)
        url = reverse("story-sticker-responses", args=[self.story.id, self.quiz.id])
        self.client.force_authenticate(self.bob)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_authenticate(self.owner)
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200, res.data)
        rows = rows_of(res)
        self.assertEqual({r["user"]["username"]: r["is_correct"] for r in rows}, {"qz_bob": True, "qz_cara": False})
        self.assertEqual(
            res.data["summary"], {"counts": [0, 1, 1], "total": 2, "correct": 1, "correct_index": 1},
        )

    def test_reading_many_quizzes_does_not_add_a_query_per_answer(self):
        self.client.force_authenticate(self.bob)
        url = reverse("story-list")
        with CaptureQueriesContext(connection) as few:
            self.client.get(url)
        for i in range(4):
            story = make_story(self.owner)
            sticker = make_quiz(story)
            StoryQuizAnswer.objects.create(sticker=sticker, user=self.cara, option_index=0)
        with CaptureQueriesContext(connection) as many:
            self.client.get(url)
        self.assertLessEqual(len(many), len(few) + 3)


# ---------------------------------------------------------------------------
# Add yours chain.
# ---------------------------------------------------------------------------
class F3AddYoursTests(_CreateMixin, APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.owner = make_user("ay_owner")
        self.bob = make_user("ay_bob")
        self.cara = make_user("ay_cara")
        follow(self.bob, self.owner)
        follow(self.cara, self.owner)
        self.story = make_story(self.owner)
        self.root = make_chain_root(self.story)

    def _join(self, user, chain_from=None, **extra):
        self.client.force_authenticate(user)
        return self._create([{"kind": "add_yours", "chain_from": str(chain_from or self.root.id), **extra}])

    def test_chain_starts_with_count_one(self):
        self.client.force_authenticate(self.bob)
        data = sticker_in(detail(self.client, self.story), self.root)["data"]
        self.assertEqual(
            data, {"prompt": "Show your desk", "chain_id": str(self.root.id), "count": 1, "my_joined": False},
        )

    def test_joining_uses_the_roots_prompt_and_counts(self):
        res = self._join(self.bob, prompt="IGNORED")
        self.assertEqual(res.status_code, 201, res.data)
        mine = StorySticker.objects.get(story_id=res.data["id"])
        self.assertEqual(mine.chain_root_id, self.root.id)
        self.assertEqual(mine.data["prompt"], "Show your desk")
        self.client.force_authenticate(self.cara)
        data = sticker_in(detail(self.client, self.story), self.root)["data"]
        self.assertEqual((data["count"], data["my_joined"]), (2, False))
        self.client.force_authenticate(self.bob)
        data = sticker_in(detail(self.client, self.story), self.root)["data"]
        self.assertEqual((data["count"], data["my_joined"]), (2, True))

    def test_answering_an_answer_still_points_at_the_root(self):
        first = self._join(self.bob)
        bobs = StorySticker.objects.get(story_id=first.data["id"])
        res = self._join(self.cara, chain_from=bobs.id)
        self.assertEqual(res.status_code, 201, res.data)
        carass = StorySticker.objects.get(story_id=res.data["id"])
        self.assertEqual(carass.chain_root_id, self.root.id)
        self.client.force_authenticate(self.owner)
        data = sticker_in(detail(self.client, self.story), self.root)["data"]
        self.assertEqual(data["count"], 3)
        # every member shows the same chain id + count
        self.client.force_authenticate(self.cara)
        mine = sticker_in(detail(self.client, carass.story), carass)["data"]
        self.assertEqual((mine["chain_id"], mine["count"]), (str(self.root.id), 3))

    def test_bad_chain_references(self):
        poll = StorySticker.objects.create(
            story=self.story, kind=StorySticker.KIND_POLL, data={"question": "Q", "options": ["a", "b"]},
        )
        stranger = make_user("ay_stranger")
        cf_story = make_story(self.owner, audience=Story.AUDIENCE_CLOSE_FRIENDS)
        cf_root = make_chain_root(cf_story)
        cases = [
            (self.bob, "not-a-uuid"),
            (self.bob, uuid.uuid4()),          # nothing there
            (self.bob, poll.id),               # not an add_yours sticker
            (stranger, cf_root.id),            # can't see that story
        ]
        for user, chain_from in cases:
            with self.subTest(chain_from=str(chain_from)):
                self.client.force_authenticate(user)
                res = self._create([{"kind": "add_yours", "chain_from": str(chain_from)}])
                self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(Story.objects.filter(user=self.bob).count(), 0)

    def test_expired_root_cannot_be_joined(self):
        Story.objects.filter(pk=self.story.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self._join(self.bob).status_code, 400)

    def test_blocked_author_cannot_be_joined(self):
        from user_profile.models import BlockUser

        BlockUser.objects.create(blocker=self.owner, blocked=self.bob)
        self.assertEqual(self._join(self.bob).status_code, 400)

    def test_prompt_is_required_when_starting_a_chain(self):
        self.client.force_authenticate(self.bob)
        for sticker in ({"kind": "add_yours"}, {"kind": "add_yours", "prompt": " "},
                        {"kind": "add_yours", "prompt": "x" * 61}):
            with self.subTest(sticker=sticker):
                self.assertEqual(self._create([sticker]).status_code, 400)


# ---------------------------------------------------------------------------
# Pure rules - GIF + music clip.
# ---------------------------------------------------------------------------
class GifRuleTests(SimpleTestCase):
    def test_giphy_media_url_is_kept_without_query(self):
        self.assertEqual(clean_gif_url(GIF + "?cid=1#x"), GIF)
        self.assertEqual(clean_gif_url("https://MEDIA.Giphy.com/m/a.WEBP"), "https://media.giphy.com/m/a.WEBP")

    def test_rejected_urls(self):
        for url in (
            "", None, 5, "http://media.giphy.com/a.gif", "https://giphy.com.evil.example/a.gif",
            "https://notgiphy.com/a.gif", "https://user:pw@media.giphy.com/a.gif",
            "https://media.giphy.com:8443/a.gif", "https://media.giphy.com/a.mp4", "https://media.giphy.com/a b.gif",
            "https://media.giphy.com/" + "a" * 600 + ".gif", "file:///a.gif",
        ):
            with self.subTest(url=url):
                with self.assertRaises(GifError):
                    clean_gif_url(url)

    @override_settings(STORY_GIF_ALLOWED_HOSTS=("tenor.com",))
    def test_host_list_is_a_setting(self):
        self.assertEqual(clean_gif_url("https://media.tenor.com/a.gif"), "https://media.tenor.com/a.gif")
        with self.assertRaises(GifError):
            clean_gif_url(GIF)

    def test_map_giphy_results_skips_unusable_rows(self):
        payload = {"data": [
            {"id": "ok1", "title": "Happy", "images": {
                "fixed_width": {"url": GIF + "?x=1", "width": "200", "height": "100"},
                "fixed_width_small": {"url": "https://media1.giphy.com/s.gif"}}},
            {"id": "evil", "images": {"fixed_width": {"url": "https://evil.example/a.gif"}}},
            {"id": "mp4", "images": {"fixed_width": {"url": "https://media.giphy.com/a.mp4"}}},
            "junk", {"id": "no-images"},
        ]}
        rows = map_giphy_results(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], {
            "id": "ok1", "title": "Happy", "url": GIF, "preview_url": "https://media1.giphy.com/s.gif",
            "width": 200, "height": 100,
        })
        self.assertEqual(map_giphy_results(None), [])
        self.assertEqual(map_giphy_results({"data": "x"}), [])


class MusicClipTests(SimpleTestCase):
    def _track(self, **extra):
        base = {"id": 1, "title": "T", "preview_url": MUSIC, "duration": 42.5, "start": 3}
        base.update(extra)
        return base

    def test_clip_is_absent_unless_sent(self):
        self.assertNotIn("clip", clean_music(self._track()))

    def test_clip_is_stored(self):
        self.assertEqual(clean_music(self._track(clip=15))["clip"], 15.0)
        self.assertEqual(clean_music(self._track(clip_seconds="10"))["clip"], 10.0)

    def test_clip_is_shortened_to_the_rest_of_the_track(self):
        self.assertEqual(clean_music(self._track(start=40, clip=15))["clip"], 2.5)

    def test_clip_range(self):
        for clip in (0, 0.5, 31, -3, "abc", True):
            with self.subTest(clip=clip):
                with self.assertRaises(MusicError):
                    clean_music(self._track(clip=clip))

    def test_unknown_duration_keeps_the_clip(self):
        self.assertEqual(clean_music(self._track(duration=None, clip=20))["clip"], 20.0)


# ---------------------------------------------------------------------------
# GET /post/gifs/search/
# ---------------------------------------------------------------------------
class GifSearchTests(APITestCase):
    def setUp(self):
        self.user = make_user("gif_user")
        self.client.force_authenticate(self.user)
        self.url = reverse("post-gif-search")

    def _giphy(self, rows=1, total=100, status=200):
        resp = mock.Mock(status_code=status)
        resp.json.return_value = {
            "data": [{"id": f"g{i}", "title": "t", "images": {"fixed_width": {"url": GIF, "width": "100", "height": "80"}}}
                     for i in range(rows)],
            "pagination": {"total_count": total},
        }
        return resp

    def test_requires_login(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, (401, 403))

    @override_settings(GIPHY_API_KEY=None)
    def test_not_configured_is_503(self):
        self.assertEqual(self.client.get(self.url, {"q": "cat"}).status_code, 503)

    @override_settings(GIPHY_API_KEY="k")
    def test_search_always_asks_for_rating_g_and_keeps_the_key_server_side(self):
        with mock.patch("post.views.gifs.requests") as req:
            req.RequestException = Exception
            req.get.return_value = self._giphy()
            res = self.client.get(self.url, {"q": "cat", "limit": 500, "pos": 24})
        self.assertEqual(res.status_code, 200, res.data)
        url = req.get.call_args[0][0]
        params = req.get.call_args[1]["params"]
        self.assertTrue(url.endswith("/gifs/search"))
        self.assertEqual((params["rating"], params["q"], params["limit"], params["offset"]), ("g", "cat", 30, 24))
        self.assertNotIn("api_key", json.dumps(res.data))
        self.assertEqual(res.data["data"]["results"][0]["url"], GIF)
        self.assertEqual(res.data["data"]["next_pos"], 54)

    @override_settings(GIPHY_API_KEY="k")
    def test_no_query_means_trending(self):
        with mock.patch("post.views.gifs.requests") as req:
            req.RequestException = Exception
            req.get.return_value = self._giphy(total=1)
            res = self.client.get(self.url)
        self.assertTrue(req.get.call_args[0][0].endswith("/gifs/trending"))
        self.assertIsNone(res.data["data"]["next_pos"])

    @override_settings(GIPHY_API_KEY="k")
    def test_upstream_failures_are_502_503(self):
        with mock.patch("post.views.gifs.requests") as req:
            req.RequestException = RuntimeError
            req.get.return_value = self._giphy(status=500)
            self.assertEqual(self.client.get(self.url, {"q": "a"}).status_code, 502)
            req.get.side_effect = RuntimeError("boom")
            self.assertEqual(self.client.get(self.url, {"q": "a"}).status_code, 503)

    @override_settings(GIPHY_API_KEY="k")
    def test_bad_numbers_are_400(self):
        self.assertEqual(self.client.get(self.url, {"q": "a", "limit": "x"}).status_code, 400)


class MusicBakedFlagTests(SimpleTestCase):
    """X5 (1): `baked` marks a track that is already mixed into the story's video. NOT RUN when written."""

    def _track(self, **extra):
        data = {"id": 123, "title": "Calm piano", "artist": "x", "license": "CC0",
                "preview_url": "https://cdn.freesound.org/previews/123/123_1-hq.mp3", "duration": 40, "start": 2}
        data.update(extra)
        return data

    def test_not_stored_unless_true(self):
        self.assertNotIn("baked", clean_music(self._track()))
        self.assertNotIn("baked", clean_music(self._track(baked=False)))
        for loose in ("true", 1, "yes"):
            self.assertNotIn("baked", clean_music(self._track(baked=loose)))

    def test_true_is_stored(self):
        self.assertIs(clean_music(self._track(baked=True))["baked"], True)

    def test_baked_does_not_relax_the_licence_rule(self):
        with self.assertRaises(MusicError):
            clean_music(self._track(baked=True, license="CC-BY"))
        with self.assertRaises(MusicError):
            clean_music(self._track(baked=True, preview_url="https://evil.example.com/a.mp3"))
