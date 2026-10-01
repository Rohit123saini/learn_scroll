"""
STORIES UPGRADE - PART 2b (Poll + Question stickers) tests.

Covers: creating poll / question stickers (validation + limits), what the
owner and a viewer see on the read endpoints (results hidden until the viewer
has voted), voting (final, owner can't vote, visibility / expiry / block gates,
wrong-sticker 404s), answering questions (private, one per viewer, length and
control-character rules), the owner-only responses list, the block clean-up
signal, DB constraints and that reading does not add a query per vote.
"""
import json
import shutil
import uuid
from datetime import timedelta

from django.db import IntegrityError, connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from user_profile.models import BlockUser

from .models import CloseFriend, Story, StoryPollVote, StoryQuestionAnswer, StorySticker
from .story_sticker_responses import build_interaction_stats
from .tests_story_stickers import _TMP_MEDIA, follow, image_upload, make_story, make_user, rows_of


def make_poll(story, question="Ready?", options=("Yes", "No"), **extra):
    return StorySticker.objects.create(
        story=story, kind=StorySticker.KIND_POLL,
        data={"question": question, "options": list(options)}, **extra,
    )


def make_question(story, prompt="Ask me anything", **extra):
    return StorySticker.objects.create(
        story=story, kind=StorySticker.KIND_QUESTION, data={"prompt": prompt}, **extra,
    )


def vote_url(sticker):
    return reverse("story-poll-vote", args=[sticker.story_id, sticker.id])


def answer_url(sticker):
    return reverse("story-question-answer", args=[sticker.story_id, sticker.id])


def responses_url(sticker):
    return reverse("story-sticker-responses", args=[sticker.story_id, sticker.id])


def sticker_in(res_data, sticker):
    return next(s for s in res_data["stickers"] if str(s["id"]) == str(sticker.id))


# ---------------------------------------------------------------------------
# Creating polls / questions.
# ---------------------------------------------------------------------------
class PollQuestionCreateTests(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.owner = make_user("pq_owner")
        self.client.force_authenticate(self.owner)

    def _create(self, stickers):
        return self.client.post(
            reverse("story-create"),
            {"media": image_upload(), "media_type": "image", "stickers": json.dumps(stickers)},
            format="multipart",
        )

    def _assert_rejected(self, res):
        self.assertEqual(res.status_code, 400, getattr(res, "data", None))
        self.assertIn("stickers", res.data["errors"])
        self.assertEqual(Story.objects.count(), 0)
        self.assertEqual(StorySticker.objects.count(), 0)

    def test_create_poll_and_question(self):
        res = self._create([
            {"kind": "poll", "x": 0.5, "y": 0.6, "question": "Ready for the test?", "options": ["Yes", "No"]},
            {"kind": "question", "x": 0.5, "y": 0.2, "prompt": "Ask me about Physics"},
        ])
        self.assertEqual(res.status_code, 201, res.data)
        poll, question = res.data["stickers"]
        self.assertEqual(poll["kind"], "poll")
        self.assertEqual(poll["data"]["question"], "Ready for the test?")
        self.assertEqual(poll["data"]["options"], ["Yes", "No"])
        self.assertIsNone(poll["data"]["my_vote"])
        self.assertEqual(poll["data"]["results"]["counts"], [0, 0])  # owner sees results from the start
        self.assertEqual(question["data"]["prompt"], "Ask me about Physics")
        self.assertEqual(question["data"]["answers_count"], 0)

    def test_text_is_trimmed_and_control_characters_removed(self):
        res = self._create([{"kind": "poll", "question": "  Hi\x00 there \n", "options": [" A\x07 ", "B"]}])
        self.assertEqual(res.status_code, 201, res.data)
        data = res.data["stickers"][0]["data"]
        self.assertEqual(data["question"], "Hi there")
        self.assertEqual(data["options"], ["A", "B"])

    def test_poll_needs_a_question(self):
        for q in (None, "", "   ", 5, "x" * 101):
            with self.subTest(question=q):
                self._assert_rejected(self._create([{"kind": "poll", "question": q, "options": ["a", "b"]}]))

    def test_poll_option_count_bounds(self):
        for options in ([], ["only one"], ["a", "b", "c", "d", "e"], "yes/no", None):
            with self.subTest(options=options):
                self._assert_rejected(self._create([{"kind": "poll", "question": "Q", "options": options}]))

    def test_poll_accepts_up_to_four_options(self):
        res = self._create([{"kind": "poll", "question": "Q", "options": ["a", "b", "c", "d"]}])
        self.assertEqual(res.status_code, 201, res.data)

    @override_settings(STORY_POLL_MAX_OPTIONS=2)
    def test_option_limit_is_a_setting(self):
        self._assert_rejected(self._create([{"kind": "poll", "question": "Q", "options": ["a", "b", "c"]}]))

    def test_poll_options_must_be_non_empty_short_and_different(self):
        for options in (["a", ""], ["a", "  "], ["a", 3], ["a", "x" * 26], ["Yes", "yes"], ["a", " a "]):
            with self.subTest(options=options):
                self._assert_rejected(self._create([{"kind": "poll", "question": "Q", "options": options}]))

    def test_question_needs_a_prompt(self):
        for prompt in (None, "", "  ", 7, "x" * 101):
            with self.subTest(prompt=prompt):
                self._assert_rejected(self._create([{"kind": "question", "prompt": prompt}]))

    def test_only_one_poll_and_one_question(self):
        poll = {"kind": "poll", "question": "Q", "options": ["a", "b"]}
        question = {"kind": "question", "prompt": "P"}
        self._assert_rejected(self._create([poll, poll]))
        self._assert_rejected(self._create([question, question]))

    @override_settings(STORY_MAX_POLLS=2, STORY_MAX_QUESTIONS=2)
    def test_limits_are_settings(self):
        poll = {"kind": "poll", "question": "Q", "options": ["a", "b"]}
        question = {"kind": "question", "prompt": "P"}
        self.assertEqual(self._create([poll, poll, question, question]).status_code, 201)

    def test_error_names_the_offending_sticker(self):
        res = self._create([
            {"kind": "link", "url": "https://example.com"},
            {"kind": "poll", "question": "", "options": ["a", "b"]},
        ])
        self.assertEqual(res.status_code, 400)
        self.assertIn("Sticker 2", str(res.data["errors"]["stickers"]))

    def test_poll_and_question_send_no_notification(self):
        from core.models import Notification

        before = Notification.objects.count()
        self.assertEqual(self._create([{"kind": "question", "prompt": "P"}]).status_code, 201)
        self.assertEqual(Notification.objects.count(), before)

    def test_mixed_with_mention_and_link(self):
        bob = make_user("pq_bob")
        res = self._create([
            {"kind": "mention", "user_id": bob.id},
            {"kind": "link", "url": "https://example.com", "label": "Site"},
            {"kind": "poll", "question": "Q", "options": ["a", "b"]},
            {"kind": "question", "prompt": "P"},
        ])
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual([s["kind"] for s in res.data["stickers"]], ["mention", "link", "poll", "question"])


# ---------------------------------------------------------------------------
# Voting.
# ---------------------------------------------------------------------------
class PollVoteTests(APITestCase):
    def setUp(self):
        self.owner = make_user("pv_owner")
        self.fan = make_user("pv_fan")
        self.fan2 = make_user("pv_fan2")
        self.stranger = make_user("pv_stranger")
        for u in (self.fan, self.fan2):
            follow(u, self.owner)
        self.story = make_story(self.owner)
        self.poll = make_poll(self.story, options=("Yes", "No", "Maybe"))

    def _vote(self, user, option, sticker=None):
        self.client.force_authenticate(user)
        return self.client.post(vote_url(sticker or self.poll), {"option": option}, format="json")

    def test_vote_returns_the_poll_with_my_vote_and_results(self):
        res = self._vote(self.fan, 1)
        self.assertEqual(res.status_code, 201, res.data)
        data = res.data["sticker"]["data"]
        self.assertEqual(data["my_vote"], 1)
        self.assertEqual(data["results"], {"counts": [0, 1, 0], "total": 1})
        self.assertTrue(StoryPollVote.objects.filter(sticker=self.poll, user=self.fan, option_index=1).exists())

    def test_option_can_be_sent_as_a_form_field(self):
        self.client.force_authenticate(self.fan)
        res = self.client.post(vote_url(self.poll), {"option": "2"}, format="multipart")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["sticker"]["data"]["my_vote"], 2)

    def test_results_are_hidden_until_the_viewer_has_voted(self):
        self._vote(self.fan2, 0)
        self.client.force_authenticate(self.fan)
        before = sticker_in(self.client.get(reverse("story-detail", args=[self.story.id])).data, self.poll)
        self.assertIsNone(before["data"]["my_vote"])
        self.assertIsNone(before["data"]["results"])
        self.assertEqual(before["data"]["options"], ["Yes", "No", "Maybe"])

        self._vote(self.fan, 1)
        after = sticker_in(self.client.get(reverse("story-detail", args=[self.story.id])).data, self.poll)
        self.assertEqual(after["data"]["my_vote"], 1)
        self.assertEqual(after["data"]["results"], {"counts": [1, 1, 0], "total": 2})

    def test_owner_always_sees_the_counts_in_the_list(self):
        self._vote(self.fan, 0)
        self._vote(self.fan2, 0)
        self.client.force_authenticate(self.owner)
        rows = rows_of(self.client.get(reverse("story-list")))
        row = next(r for r in rows if str(r["id"]) == str(self.story.id))
        data = sticker_in(row, self.poll)["data"]
        self.assertEqual(data["results"], {"counts": [2, 0, 0], "total": 2})
        self.assertIsNone(data["my_vote"])

    def test_viewer_list_shows_own_vote_only_after_voting(self):
        self._vote(self.fan, 2)
        self.client.force_authenticate(self.fan)
        rows = rows_of(self.client.get(reverse("story-list")))
        row = next(r for r in rows if str(r["id"]) == str(self.story.id))
        self.assertEqual(sticker_in(row, self.poll)["data"]["my_vote"], 2)

    def test_a_vote_is_final(self):
        self.assertEqual(self._vote(self.fan, 0).status_code, 201)
        res = self._vote(self.fan, 1)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["code"], "already_voted")
        self.assertEqual(res.data["sticker"]["data"]["my_vote"], 0)  # existing state comes back
        self.assertEqual(StoryPollVote.objects.filter(sticker=self.poll).count(), 1)

    def test_owner_cannot_vote_on_own_poll(self):
        self.assertEqual(self._vote(self.owner, 0).status_code, 400)
        self.assertFalse(StoryPollVote.objects.exists())

    def test_bad_options_are_rejected(self):
        for option in (-1, 3, 99, "x", "1.5", True, None, [0], 1.5, ""):
            with self.subTest(option=option):
                self.assertEqual(self._vote(self.fan, option).status_code, 400)
        self.assertFalse(StoryPollVote.objects.exists())

    def test_missing_option_is_rejected(self):
        self.client.force_authenticate(self.fan)
        self.assertEqual(self.client.post(vote_url(self.poll), {}, format="json").status_code, 400)

    def test_voting_on_a_question_sticker_is_a_404(self):
        question = make_question(self.story)
        self.assertEqual(self._vote(self.fan, 0, sticker=question).status_code, 404)

    def test_sticker_from_another_story_is_a_404(self):
        other = make_poll(make_story(self.owner))
        self.client.force_authenticate(self.fan)
        url = reverse("story-poll-vote", args=[self.story.id, other.id])
        self.assertEqual(self.client.post(url, {"option": 0}, format="json").status_code, 404)

    def test_unknown_sticker_is_a_404(self):
        self.client.force_authenticate(self.fan)
        url = reverse("story-poll-vote", args=[self.story.id, uuid.uuid4()])
        self.assertEqual(self.client.post(url, {"option": 0}, format="json").status_code, 404)

    def test_expired_and_deleted_stories_are_a_404(self):
        Story.objects.filter(id=self.story.id).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self._vote(self.fan, 0).status_code, 404)
        Story.objects.filter(id=self.story.id).update(expires_at=timezone.now() + timedelta(hours=1))
        self.story.refresh_from_db()
        self.story.soft_delete()
        self.assertEqual(self._vote(self.fan, 0).status_code, 404)

    def test_close_friends_story_is_a_404_for_outsiders(self):
        story = make_story(self.owner, audience=Story.AUDIENCE_CLOSE_FRIENDS)
        poll = make_poll(story)
        CloseFriend.objects.create(owner=self.owner, friend=self.fan)
        self.assertEqual(self._vote(self.fan2, 0, sticker=poll).status_code, 404)
        self.assertEqual(self._vote(self.fan, 0, sticker=poll).status_code, 201)

    def test_blocked_either_way_is_a_404(self):
        BlockUser.objects.create(blocker=self.owner, blocked=self.stranger)
        self.assertEqual(self._vote(self.stranger, 0).status_code, 404)
        BlockUser.objects.create(blocker=self.fan2, blocked=self.owner)
        self.assertEqual(self._vote(self.fan2, 0).status_code, 404)

    def test_non_follower_can_vote_on_a_public_story(self):
        self.assertEqual(self._vote(self.stranger, 0).status_code, 201)

    def test_requires_login(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.post(vote_url(self.poll), {"option": 0}, format="json").status_code, (401, 403))


# ---------------------------------------------------------------------------
# Answering questions.
# ---------------------------------------------------------------------------
class QuestionAnswerTests(APITestCase):
    def setUp(self):
        self.owner = make_user("qa_owner")
        self.fan = make_user("qa_fan")
        self.fan2 = make_user("qa_fan2")
        for u in (self.fan, self.fan2):
            follow(u, self.owner)
        self.story = make_story(self.owner)
        self.question = make_question(self.story)

    def _answer(self, user, text, sticker=None):
        self.client.force_authenticate(user)
        return self.client.post(answer_url(sticker or self.question), {"text": text}, format="json")

    def test_answer_is_saved_and_marks_the_viewer_as_answered(self):
        res = self._answer(self.fan, "  Newton  ")
        self.assertEqual(res.status_code, 201, res.data)
        data = res.data["sticker"]["data"]
        self.assertTrue(data["my_answered"])
        self.assertIsNone(data["answers_count"])  # not the viewer's business
        self.assertEqual(StoryQuestionAnswer.objects.get(sticker=self.question, user=self.fan).text, "Newton")

    def test_only_the_owner_sees_the_answer_count(self):
        self._answer(self.fan, "one")
        self._answer(self.fan2, "two")
        self.client.force_authenticate(self.owner)
        data = sticker_in(self.client.get(reverse("story-detail", args=[self.story.id])).data, self.question)["data"]
        self.assertEqual(data["answers_count"], 2)
        self.assertFalse(data["my_answered"])
        self.client.force_authenticate(self.fan)
        data = sticker_in(self.client.get(reverse("story-detail", args=[self.story.id])).data, self.question)["data"]
        self.assertIsNone(data["answers_count"])
        self.assertTrue(data["my_answered"])

    def test_one_answer_per_viewer(self):
        self.assertEqual(self._answer(self.fan, "first").status_code, 201)
        res = self._answer(self.fan, "second")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["code"], "already_answered")
        self.assertEqual(StoryQuestionAnswer.objects.get(sticker=self.question, user=self.fan).text, "first")

    def test_owner_cannot_answer_own_question(self):
        self.assertEqual(self._answer(self.owner, "hi").status_code, 400)

    def test_text_rules(self):
        for text in ("", "   ", None, 5, ["a"], "x" * 301, "\x00\x01"):
            with self.subTest(text=text):
                self.assertEqual(self._answer(self.fan, text).status_code, 400)
        self.assertFalse(StoryQuestionAnswer.objects.exists())

    def test_boundary_length_and_line_breaks_are_kept(self):
        self.assertEqual(self._answer(self.fan, "x" * 300).status_code, 201)
        self.assertEqual(self._answer(self.fan2, "line1\nline2\x07").status_code, 201)
        self.assertEqual(StoryQuestionAnswer.objects.get(user=self.fan2).text, "line1\nline2")

    def test_answering_a_poll_sticker_is_a_404(self):
        poll = make_poll(self.story)
        self.assertEqual(self._answer(self.fan, "hi", sticker=poll).status_code, 404)

    def test_visibility_expiry_and_block_gates(self):
        cf_story = make_story(self.owner, audience=Story.AUDIENCE_CLOSE_FRIENDS)
        cf_question = make_question(cf_story)
        self.assertEqual(self._answer(self.fan, "hi", sticker=cf_question).status_code, 404)
        CloseFriend.objects.create(owner=self.owner, friend=self.fan)
        self.assertEqual(self._answer(self.fan, "hi", sticker=cf_question).status_code, 201)

        BlockUser.objects.create(blocker=self.owner, blocked=self.fan2)
        self.assertEqual(self._answer(self.fan2, "hi").status_code, 404)

        Story.objects.filter(id=self.story.id).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self._answer(self.fan, "late").status_code, 404)

    def test_requires_login(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.post(answer_url(self.question), {"text": "x"}, format="json").status_code, (401, 403))


# ---------------------------------------------------------------------------
# Owner-only responses list.
# ---------------------------------------------------------------------------
class StickerResponsesListTests(APITestCase):
    def setUp(self):
        self.owner = make_user("rl_owner")
        self.fan = make_user("rl_fan")
        self.fan2 = make_user("rl_fan2")
        self.story = make_story(self.owner)
        self.poll = make_poll(self.story)
        self.question = make_question(self.story)
        StoryPollVote.objects.create(sticker=self.poll, user=self.fan, option_index=0)
        StoryPollVote.objects.create(sticker=self.poll, user=self.fan2, option_index=1)
        StoryQuestionAnswer.objects.create(sticker=self.question, user=self.fan, text="hello")

    def test_owner_sees_who_voted_what(self):
        self.client.force_authenticate(self.owner)
        res = self.client.get(responses_url(self.poll))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["count"], 2)
        self.assertEqual(res.data["summary"], {"counts": [1, 1], "total": 2})
        by_user = {r["user"]["username"]: r for r in res.data["results"]}
        self.assertEqual(by_user["rl_fan"]["option"], "Yes")
        self.assertEqual(by_user["rl_fan2"]["option_index"], 1)
        self.assertEqual(set(by_user["rl_fan"]), {"id", "user", "option_index", "option", "created_at"})

    def test_owner_reads_the_answers(self):
        self.client.force_authenticate(self.owner)
        res = self.client.get(responses_url(self.question))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["summary"], {"count": 1})
        self.assertEqual(res.data["results"][0]["text"], "hello")
        self.assertEqual(res.data["results"][0]["user"]["username"], "rl_fan")

    def test_empty_lists(self):
        empty_story = make_story(self.owner)
        poll, question = make_poll(empty_story), make_question(empty_story)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(responses_url(poll)).data["summary"], {"counts": [0, 0], "total": 0})
        self.assertEqual(self.client.get(responses_url(question)).data["results"], [])

    def test_only_the_owner_may_read_them(self):
        for user in (self.fan, self.fan2):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(responses_url(self.poll)).status_code, 403)
            self.assertEqual(self.client.get(responses_url(self.question)).status_code, 403)

    def test_requires_login(self):
        self.assertIn(self.client.get(responses_url(self.poll)).status_code, (401, 403))

    def test_other_sticker_kinds_are_a_404(self):
        mention = StorySticker.objects.create(
            story=self.story, kind="mention", mentioned_user=self.fan2,
        )
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(responses_url(mention)).status_code, 404)

    def test_sticker_from_another_story_is_a_404(self):
        other_story = make_story(self.owner)
        self.client.force_authenticate(self.owner)
        url = reverse("story-sticker-responses", args=[other_story.id, self.poll.id])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_owner_can_still_read_after_the_story_expired(self):
        Story.objects.filter(id=self.story.id).update(expires_at=timezone.now() - timedelta(hours=1))
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(responses_url(self.poll)).status_code, 200)

    def test_a_deleted_story_is_a_404(self):
        self.story.soft_delete()
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(responses_url(self.poll)).status_code, 404)


# ---------------------------------------------------------------------------
# Block clean-up, constraints, query counts.
# ---------------------------------------------------------------------------
class PollQuestionBlockAndConstraintTests(APITestCase):
    def setUp(self):
        self.a = make_user("pb_a")
        self.b = make_user("pb_b")
        self.c = make_user("pb_c")
        self.a_story, self.b_story = make_story(self.a), make_story(self.b)
        self.a_poll, self.b_poll = make_poll(self.a_story), make_poll(self.b_story)
        self.a_question = make_question(self.a_story)

    def test_block_removes_votes_and_answers_in_both_directions(self):
        StoryPollVote.objects.create(sticker=self.a_poll, user=self.b, option_index=0)
        StoryPollVote.objects.create(sticker=self.b_poll, user=self.a, option_index=1)
        StoryQuestionAnswer.objects.create(sticker=self.a_question, user=self.b, text="hi")
        keep_vote = StoryPollVote.objects.create(sticker=self.a_poll, user=self.c, option_index=1)
        keep_answer = StoryQuestionAnswer.objects.create(sticker=self.a_question, user=self.c, text="yo")

        BlockUser.objects.create(blocker=self.a, blocked=self.b)

        self.assertEqual(list(StoryPollVote.objects.values_list("id", flat=True)), [keep_vote.id])
        self.assertEqual(list(StoryQuestionAnswer.objects.values_list("id", flat=True)), [keep_answer.id])
        self.assertTrue(StorySticker.objects.filter(id=self.a_poll.id).exists())  # stickers stay

    def test_one_vote_and_one_answer_per_person_at_the_db_level(self):
        StoryPollVote.objects.create(sticker=self.a_poll, user=self.b, option_index=0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            StoryPollVote.objects.create(sticker=self.a_poll, user=self.b, option_index=1)
        StoryQuestionAnswer.objects.create(sticker=self.a_question, user=self.b, text="x")
        with self.assertRaises(IntegrityError), transaction.atomic():
            StoryQuestionAnswer.objects.create(sticker=self.a_question, user=self.b, text="y")

    def test_deleting_a_story_deletes_its_votes_and_answers(self):
        StoryPollVote.objects.create(sticker=self.a_poll, user=self.b, option_index=0)
        StoryQuestionAnswer.objects.create(sticker=self.a_question, user=self.b, text="x")
        self.a_story.delete()
        self.assertEqual(StoryPollVote.objects.filter(sticker=self.a_poll).count(), 0)
        self.assertEqual(StoryQuestionAnswer.objects.filter(sticker=self.a_question).count(), 0)

    def test_stats_ignore_other_sticker_kinds_and_out_of_range_votes(self):
        mention = StorySticker.objects.create(story=self.a_story, kind="mention", mentioned_user=self.c)
        StoryPollVote.objects.create(sticker=self.a_poll, user=self.b, option_index=7)  # stale index
        stats = build_interaction_stats([self.a_poll, mention, self.a_question], self.b)
        self.assertEqual(set(stats), {self.a_poll.id, self.a_question.id})
        self.assertEqual(stats[self.a_poll.id]["counts"], [0, 0])
        self.assertEqual(stats[self.a_poll.id]["my_vote"], 7)

    def test_read_cost_does_not_grow_with_votes(self):
        voters = [make_user(f"pb_v{i}") for i in range(6)]
        for u in voters[:1]:
            follow(u, self.a)
        StoryPollVote.objects.create(sticker=self.a_poll, user=voters[0], option_index=0)
        self.client.force_authenticate(voters[0])
        self.client.get(reverse("story-list"))  # warm-up

        with CaptureQueriesContext(connection) as few:
            self.client.get(reverse("story-list"))

        for i, u in enumerate(voters[1:]):
            StoryPollVote.objects.create(sticker=self.a_poll, user=u, option_index=i % 2)
            StoryQuestionAnswer.objects.create(sticker=self.a_question, user=u, text="a")

        with CaptureQueriesContext(connection) as many:
            self.client.get(reverse("story-list"))
        self.assertEqual(len(few), len(many))
