"""P-A: poll / quiz -> Test Series question (`polls.convert_to_question`). Written, NOT RUN.

The testseries bridge is mocked (its own rules have their own tests): what is checked here is what `post` sends to it
and how `post` reacts to what it answers. DB only for the poll rows and the one-link-per-(poll, series) guard.
"""
import uuid
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import SimpleTestCase, TestCase

from . import polls
from .models import Post, PostPoll, PostPollOption, PostPollQuestionLink

User = get_user_model()
BRIDGE = "testseries.bridge.append_question_to_series"


class PlainQuestionTextTests(SimpleTestCase):
    def test_emphasis_markers_go_words_stay(self):
        self.assertEqual(polls.plain_question_text("**What** is the SI unit of *force*?"), "What is the SI unit of force?")
        self.assertEqual(polls.plain_question_text("***both*** and `code`"), "both and code")

    def test_tag_only_lines_are_dropped_inline_tags_keep_the_word(self):
        self.assertEqual(polls.plain_question_text("Pick one\n#physics #class11\n@asha"), "Pick one")
        self.assertEqual(polls.plain_question_text("Is #photosynthesis light dependent?"), "Is photosynthesis light dependent?")

    def test_hindi_hashtag_keeps_its_matras(self):
        self.assertEqual(polls.plain_question_text("सवाल #पढ़ाई"), "सवाल पढ़ाई")

    def test_c_sharp_and_email_are_untouched(self):
        self.assertEqual(polls.plain_question_text("C# or a@b.com?"), "C# or a@b.com?")

    def test_bullets_and_fences(self):
        self.assertEqual(polls.plain_question_text("* one\n\u2022 two"), "- one\n- two")
        self.assertEqual(polls.plain_question_text("```py\nprint(1)\n```"), "print(1)")

    def test_maths_and_money_stay_as_typed(self):
        self.assertEqual(polls.plain_question_text("Find $x^2$ if it costs $5"), "Find $x^2$ if it costs $5")

    def test_blank_runs_collapse_and_empty_inputs(self):
        self.assertEqual(polls.plain_question_text("a\n\n\n\n\nb"), "a\n\nb")
        self.assertEqual(polls.plain_question_text(None), "")
        self.assertEqual(polls.plain_question_text("#only #tags"), "")

    def test_idempotent(self):
        once = polls.plain_question_text("**A** #t @u\n* x")
        self.assertEqual(polls.plain_question_text(once), once)


class OptionImageTests(SimpleTestCase):
    def test_accepts_http_and_site_paths_up_to_500(self):
        self.assertEqual(polls._question_option_image("https://x.test/a.png"), "https://x.test/a.png")
        self.assertEqual(polls._question_option_image("/media/a.png"), "/media/a.png")
        ok = "https://x.test/" + "a" * (polls.QUESTION_OPTION_IMAGE_MAX - len("https://x.test/"))
        self.assertEqual(polls._question_option_image(ok), ok)

    def test_rejects_long_missing_and_odd_schemes(self):
        self.assertIsNone(polls._question_option_image("https://x.test/" + "a" * 600))
        self.assertIsNone(polls._question_option_image(""))
        self.assertIsNone(polls._question_option_image(None))
        self.assertIsNone(polls._question_option_image("javascript:alert(1)"))


class ConvertToQuestionTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="x")
        self.other = User.objects.create_user(username="other", password="x")
        self.series_id = uuid.uuid4()

    def make_poll(self, content="**Which** gas do plants absorb? #bio", is_quiz=True, options=(("CO2", True), ("O2", False), ("N2", False)),
                  title=""):
        post = Post.objects.create(user=self.owner, content=content, title=title, post_type="poll", category="education", visibility="public")
        poll = PostPoll.objects.create(post=post, is_quiz=is_quiz, explanation="Photosynthesis." if is_quiz else "")
        for i, (text, ok) in enumerate(options):
            PostPollOption.objects.create(poll=poll, text=text, is_correct=ok, display_order=i)
        return poll

    def fake_append(self, saved=None):
        def _append(*, series_id, user, question):
            _append.payload = question
            return saved or SimpleNamespace(id=uuid.uuid4(), series_id=series_id, order=1, question_type=question["question_type"])
        return _append

    def test_question_text_is_plain_not_composer_markup(self):
        poll = self.make_poll()
        fake = self.fake_append()
        with mock.patch(BRIDGE, fake):
            polls.convert_to_question(poll, self.owner, self.series_id)
        self.assertEqual(fake.payload["text"], "Which gas do plants absorb?")

    def test_explicit_question_text_wins_and_is_kept_as_typed(self):
        poll = self.make_poll()
        fake = self.fake_append()
        with mock.patch(BRIDGE, fake):
            polls.convert_to_question(poll, self.owner, self.series_id, question_text="  Typed by the teacher  ")
        self.assertEqual(fake.payload["text"], "Typed by the teacher")

    def test_post_with_only_hashtags_falls_back_to_title_then_errors(self):
        fake = self.fake_append()
        with mock.patch(BRIDGE, fake):
            polls.convert_to_question(self.make_poll(content="#a #b", title="**Title** question"), self.owner, self.series_id)
        self.assertEqual(fake.payload["text"], "Title question")
        with mock.patch(BRIDGE, fake), self.assertRaises(polls.PollError) as cm:
            polls.convert_to_question(self.make_poll(content="#a #b", title=""), self.owner, uuid.uuid4())
        self.assertEqual(cm.exception.code, "no_question_text")

    def test_quiz_becomes_mcq_with_the_right_option_and_explanation(self):
        poll = self.make_poll()
        fake = self.fake_append()
        with mock.patch(BRIDGE, fake):
            out = polls.convert_to_question(poll, self.owner, self.series_id, marks=2, negative_marks=1)
        p = fake.payload
        self.assertEqual(p["question_type"], "mcq")
        self.assertEqual([o["text"] for o in p["options"]], ["CO2", "O2", "N2"])
        self.assertEqual(p["correct_answer"], {"option_id": "o1"})
        self.assertEqual((p["marks"], p["negative_marks"], p["explanation"]), (2, 1, "Photosynthesis."))
        self.assertEqual(out["question_type"], "mcq")
        self.assertTrue(PostPollQuestionLink.objects.filter(poll=poll, series_id=self.series_id).exists())

    def test_several_correct_options_make_an_msq(self):
        poll = self.make_poll(options=(("2", True), ("3", True), ("4", False)))
        fake = self.fake_append()
        with mock.patch(BRIDGE, fake):
            polls.convert_to_question(poll, self.owner, self.series_id)
        self.assertEqual(fake.payload["question_type"], "msq")
        self.assertEqual(fake.payload["correct_answer"], {"option_ids": ["o1", "o2"]})

    def test_normal_poll_needs_correct_option_ids(self):
        poll = self.make_poll(is_quiz=False, options=(("a", False), ("b", False)))
        with mock.patch(BRIDGE, self.fake_append()), self.assertRaises(polls.PollError) as cm:
            polls.convert_to_question(poll, self.owner, self.series_id)
        self.assertEqual(cm.exception.code, "correct_option_required")
        first = poll.options.first()
        fake = self.fake_append()
        with mock.patch(BRIDGE, fake):
            polls.convert_to_question(poll, self.owner, self.series_id, correct_option_ids=[first.id])
        self.assertEqual(fake.payload["correct_answer"], {"option_id": "o1"})

    def test_all_options_correct_is_refused(self):
        poll = self.make_poll(options=(("a", True), ("b", True)))
        with mock.patch(BRIDGE, self.fake_append()), self.assertRaises(polls.PollError) as cm:
            polls.convert_to_question(poll, self.owner, self.series_id)
        self.assertEqual(cm.exception.code, "all_correct")

    def test_only_the_creator_can_convert(self):
        with mock.patch(BRIDGE, self.fake_append()), self.assertRaises(polls.PollError) as cm:
            polls.convert_to_question(self.make_poll(), self.other, self.series_id)
        self.assertEqual((cm.exception.code, cm.exception.status), ("not_owner", 403))

    def test_same_poll_into_the_same_series_twice_is_409(self):
        poll = self.make_poll()
        with mock.patch(BRIDGE, self.fake_append()):
            polls.convert_to_question(poll, self.owner, self.series_id)
            with self.assertRaises(polls.PollError) as cm:
                polls.convert_to_question(poll, self.owner, self.series_id)
        self.assertEqual((cm.exception.code, cm.exception.status), ("already_converted", 409))

    def test_long_signed_image_url_is_dropped_from_a_text_option(self):
        poll = self.make_poll()
        long_url = "https://cdn.test/o.png?sig=" + "z" * 700
        with mock.patch.object(polls, "option_image_url", side_effect=[long_url, None, None]):
            fake = self.fake_append()
            with mock.patch(BRIDGE, fake):
                polls.convert_to_question(poll, self.owner, self.series_id)
        self.assertNotIn("image", fake.payload["options"][0])
        self.assertEqual(fake.payload["options"][0]["text"], "CO2")

    def test_short_image_url_is_kept(self):
        poll = self.make_poll()
        with mock.patch.object(polls, "option_image_url", side_effect=["https://cdn.test/o.png", None, None]):
            fake = self.fake_append()
            with mock.patch(BRIDGE, fake):
                polls.convert_to_question(poll, self.owner, self.series_id)
        self.assertEqual(fake.payload["options"][0]["image"], "https://cdn.test/o.png")

    def test_image_only_option_with_unusable_image_gets_a_clear_error(self):
        poll = self.make_poll(options=(("", True), ("B", False)))
        long_url = "https://cdn.test/o.png?sig=" + "z" * 700
        with mock.patch.object(polls, "option_image_url", side_effect=[long_url, None]), \
                mock.patch(BRIDGE, self.fake_append()), self.assertRaises(polls.PollError) as cm:
            polls.convert_to_question(poll, self.owner, self.series_id)
        self.assertEqual((cm.exception.code, cm.exception.status), ("option_image_unusable", 400))
        self.assertFalse(PostPollQuestionLink.objects.filter(poll=poll).exists())

    def test_bridge_errors_map_to_poll_errors_and_leave_no_link(self):
        from testseries.bridge import SeriesLockedError, SeriesNotEditableError, SeriesNotFoundError
        cases = [
            (SeriesNotFoundError("x"), "series_not_found", 404),
            (SeriesNotEditableError("x"), "series_forbidden", 403),
            (SeriesLockedError("x"), "series_locked", 400),
            (DjangoValidationError("bad shape"), "invalid_question", 400),
        ]
        for exc, code, status in cases:
            poll = self.make_poll()
            with mock.patch(BRIDGE, side_effect=exc), self.assertRaises(polls.PollError) as cm:
                polls.convert_to_question(poll, self.owner, uuid.uuid4())
            self.assertEqual((cm.exception.code, cm.exception.status), (code, status), code)
            self.assertFalse(PostPollQuestionLink.objects.filter(poll=poll).exists(), code)
