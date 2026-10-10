"""Auto-moderation: engine (no DB) + signal wiring (DB)."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from common.moderation import screen_text
from post.models import Post, PostComment
from tuitionclass.moderation import screen_message

from .models import AutoModerationFlag

User = get_user_model()


class ScreenTextEngineTests(SimpleTestCase):
    def test_profanity_flagged_on_every_surface(self):
        for surface in ("chat", "comment", "dm", "post", "story", "bio"):
            self.assertEqual(screen_text("you are a b1tch", surface), (True, "profanity"), surface)

    def test_leet_and_word_boundary(self):
        self.assertEqual(screen_text("sh1t", "post"), (True, "profanity"))
        self.assertEqual(screen_text("I live in Scunthorpe, class assignment", "post"), (False, ""))

    def test_link_is_spam_in_comment_and_chat_but_fine_in_post_and_dm(self):
        text = "notes here https://example.com/notes"
        self.assertEqual(screen_text(text, "comment"), (True, "spam_link"))
        self.assertEqual(screen_text(text, "chat"), (True, "spam_link"))
        self.assertEqual(screen_text(text, "post"), (False, ""))
        self.assertEqual(screen_text(text, "dm"), (False, ""))

    def test_phone_number_only_flagged_where_it_is_spam(self):
        text = "call me 98765 43210"
        self.assertEqual(screen_text(text, "comment"), (True, "spam_contact_info"))
        self.assertEqual(screen_text(text, "bio"), (False, ""))

    def test_flood_flagged_everywhere(self):
        self.assertEqual(screen_text("hellooooooooo", "dm"), (True, "spam_repeated_chars"))

    def test_empty_and_non_string_never_flag(self):
        self.assertEqual(screen_text("", "post"), (False, ""))
        self.assertEqual(screen_text(None, "post"), (False, ""))

    @override_settings(MODERATION_PROFANITY_WORDS=["zzbadword"])
    def test_word_list_override(self):
        self.assertEqual(screen_text("a zzbadword here", "post"), (True, "profanity"))
        self.assertEqual(screen_text("shit", "post"), (False, ""))

    def test_tuition_wrapper_keeps_old_contract(self):
        self.assertEqual(screen_message("join my telegram 98765 43210"), (True, "spam_contact_info"))
        self.assertEqual(screen_message("WHY IS EVERYONE SHOUTING HERE"), (True, "spam_all_caps"))
        self.assertEqual(screen_message("see you in class"), (False, ""))


class AutoModSignalTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user(username="am_user", password="x")

    def _post(self, content):
        return Post.objects.create(
            user=self.u, content=content, post_type="text", visibility="public", moderation_status="approved",
        )

    def test_clean_post_not_flagged(self):
        self._post("Newton's laws revision notes")
        self.assertEqual(AutoModerationFlag.objects.count(), 0)

    def test_abusive_post_flagged_not_deleted(self):
        p = self._post("you fucking idiots")
        f = AutoModerationFlag.objects.get()
        self.assertEqual((f.target_type, f.target_id, f.reason, f.severity, f.user_id),
                         ("post", str(p.pk), "profanity", "high", self.u.pk))
        self.assertEqual(f.status, AutoModerationFlag.Status.OPEN)
        p.refresh_from_db()
        self.assertFalse(p.is_deleted)

    def test_resaving_same_text_does_not_duplicate_but_edit_does(self):
        p = self._post("you fucking idiots")
        p.save()
        self.assertEqual(AutoModerationFlag.objects.count(), 1)
        p.content = "you shit people"
        p.save()
        self.assertEqual(AutoModerationFlag.objects.count(), 2)

    def test_comment_with_link_flagged(self):
        p = self._post("question about optics")
        PostComment.objects.create(post=p, user=self.u, content="free notes www.spam.example/x")
        f = AutoModerationFlag.objects.get(target_type="comment")
        self.assertEqual(f.reason, "spam_link")

    def test_bio_flagged_but_login_style_save_is_ignored(self):
        self.u.bio = "I am a bitch"
        self.u.save(update_fields=["bio"])
        self.assertEqual(AutoModerationFlag.objects.filter(target_type="bio").count(), 1)
        AutoModerationFlag.objects.all().delete()
        self.u.save(update_fields=["last_login"])
        self.assertEqual(AutoModerationFlag.objects.count(), 0)

    def test_receiver_failure_never_breaks_the_save(self):
        with mock.patch("user_profile.automod.screen_text", side_effect=RuntimeError("boom")):
            p = self._post("anything at all")
        self.assertTrue(Post.objects.filter(pk=p.pk).exists())

    @override_settings(AUTOMOD_AI_ENABLED=True)
    def test_clean_text_queues_ai_pass_after_commit(self):
        with mock.patch("user_profile.tasks.automod_ai_screen.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                self._post("perfectly normal text")
        self.assertTrue(delay.called)

    def test_ai_task_records_flag_with_ai_source(self):
        from .tasks import automod_ai_screen

        with mock.patch("common.moderation_ai.classify", return_value=(True, "ai_harassment")):
            automod_ai_screen("post", "abc", self.u.pk, "some subtle bullying text")
        f = AutoModerationFlag.objects.get(target_id="abc")
        self.assertEqual((f.source, f.reason, f.severity), ("ai", "ai_harassment", "high"))
