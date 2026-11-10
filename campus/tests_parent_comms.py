# campus/tests_parent_comms.py
"""
Parents se baat — PTM scheduling + rules-based teacher<->parent chat.

Run: `python manage.py test campus.tests_parent_comms`

Celery / FCM are never touched: `dispatch_after_commit` is mocked on the API
paths and `campus.bridge.notify` on the push paths.
"""
from datetime import datetime, time, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from . import parent_chat, parent_comms as pc, ptm
from . import tasks as campus_tasks
from .bridge import NotifTypes
from .models import (
    CampusParentLink,
    ParentChatMessage,
    ParentChatThread,
    PTMBooking,
    PTMEvent,
    PTMSlot,
)
from .parent_comms import CommsError
from .tests_t4_efg import T4Base

User = get_user_model()


class CommsBase(T4Base):
    """T4Base gives: admin, teacher (approved Maths teacher of secA), other_teacher
    (teaches secB), students sa1/sa2 in secA (sb in secB), parent linked to sa1."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.today = timezone.localdate()
        self.parent2 = User.objects.create_user(username="parent2", password="pass12345")
        CampusParentLink.objects.create(campus=self.campus, parent=self.parent2, student=self.sa2)
        self.stranger = User.objects.create_user(username="stranger", password="pass12345")

    # ---- chat helpers ------------------------------------------------------
    def thread(self, **kw):
        values = dict(campus=self.campus, student=self.sa1, parent=self.parent, teacher=self.t_staff)
        values.update(kw)
        return ParentChatThread.objects.create(**values)

    def at(self, hour, minute=0):
        return timezone.make_aware(datetime.combine(self.today, time(hour, minute)))

    # ---- ptm helpers -------------------------------------------------------
    def make_event(self, **kw):
        values = dict(
            campus=self.campus,
            title="PTM",
            date=self.today + timedelta(days=2),
            start_time=time(10, 0),
            end_time=time(11, 0),
            slot_minutes=10,
            status=PTMEvent.Status.PUBLISHED,
            created_by=self.admin,
        )
        values.update(kw)
        return PTMEvent.objects.create(**values)

    def make_slot(self, event, staff=None, start=time(10, 0), end=time(10, 10), **kw):
        return PTMSlot.objects.create(event=event, staff=staff or self.t_staff, start_time=start, end_time=end, **kw)


# =====================================================================
# chat rules
# =====================================================================
class ChatRuleTests(CommsBase):
    def test_validate_body(self):
        s = pc.get_settings(self.campus.id)
        self.assertEqual(parent_chat.validate_body("  hello  ", s), "hello")
        for bad, code in (
            ("", "empty_message"),
            ("   ", "empty_message"),
            (None, "empty_message"),
            ("see https://example.com now", "links_not_allowed"),
            ("visit www.example.org", "links_not_allowed"),
            ("x" * (parent_chat.max_chars(s) + 1), "message_too_long"),
        ):
            with self.assertRaises(CommsError) as ctx:
                parent_chat.validate_body(bad, s)
            self.assertEqual(ctx.exception.code, code, bad)
        # "ok.in the evening" must NOT be mistaken for a link
        self.assertEqual(parent_chat.validate_body("ok.in the evening", s), "ok.in the evening")

    def test_open_thread_rules(self):
        thread, created = parent_chat.open_thread(
            campus_id=self.campus.id, student=self.sa1, parent=self.parent, teacher=self.t_staff, actor=self.parent
        )
        self.assertTrue(created)
        again, created = parent_chat.open_thread(
            campus_id=self.campus.id, student=self.sa1, parent=self.parent, teacher=self.t_staff, actor=self.teacher
        )
        self.assertFalse(created)
        self.assertEqual(again.pk, thread.pk)
        # a teacher of another section does not teach sa1
        with self.assertRaises(CommsError) as ctx:
            parent_chat.open_thread(
                campus_id=self.campus.id, student=self.sa1, parent=self.parent, teacher=self.t2_staff, actor=self.parent
            )
        self.assertEqual(ctx.exception.code, "teacher_not_for_student")
        # a user who is not linked to the student is not a parent
        with self.assertRaises(CommsError) as ctx:
            parent_chat.open_thread(
                campus_id=self.campus.id, student=self.sa1, parent=self.stranger, teacher=self.t_staff, actor=self.stranger
            )
        self.assertEqual(ctx.exception.code, "not_a_parent")

    def test_unanswered_in_a_row_limit_resets_on_reply(self):
        thread = self.thread()
        limit = parent_chat.max_unanswered(pc.get_settings(self.campus.id))
        for i in range(limit):
            parent_chat.send_message(thread, self.parent, f"msg {i}", now=self.at(10))
        with self.assertRaises(CommsError) as ctx:
            parent_chat.send_message(thread, self.parent, "one more", now=self.at(10))
        self.assertEqual(ctx.exception.code, "too_many_unanswered")
        parent_chat.send_message(thread, self.teacher, "Replying", now=self.at(10, 5))
        parent_chat.send_message(thread, self.parent, "Thanks", now=self.at(10, 6))  # allowed again
        self.assertEqual(thread.messages.count(), limit + 2)

    def test_teacher_blocked_outside_window_parent_after_hours_is_silent(self):
        thread = self.thread()
        late = self.at(22, 0)  # default window 08:00-20:00
        msg = parent_chat.send_message(thread, self.parent, "Late question", now=late)
        self.assertTrue(msg.after_hours)
        with self.assertRaises(CommsError) as ctx:
            parent_chat.send_message(thread, self.teacher, "Reply at night", now=late)
        self.assertEqual(ctx.exception.code, "outside_chat_hours")
        ok = parent_chat.send_message(thread, self.teacher, "Morning reply", now=self.at(9))
        self.assertFalse(ok.after_hours)

    def test_stranger_and_admin_cannot_see_unflagged_thread(self):
        thread = self.thread()
        self.assertEqual(parent_chat.role_for(self.parent, thread), "parent")
        self.assertEqual(parent_chat.role_for(self.teacher, thread), "teacher")
        self.assertIsNone(parent_chat.role_for(self.stranger, thread))
        self.assertIsNone(parent_chat.role_for(self.admin, thread))  # oversight only after a report
        parent_chat.flag_thread(thread, self.parent, "rude")
        thread.refresh_from_db()
        self.assertEqual(parent_chat.role_for(self.admin, thread), "admin")
        parent_chat.resolve_flag(thread, self.admin)
        thread.refresh_from_db()
        self.assertIsNone(parent_chat.role_for(self.admin, thread))

    def test_parent_cannot_close_teacher_can_and_closed_blocks_sending(self):
        thread = self.thread()
        with self.assertRaises(CommsError):
            parent_chat.close_thread(thread, self.parent)
        self.assertTrue(parent_chat.close_thread(thread, self.teacher, "Resolved"))
        with self.assertRaises(CommsError) as ctx:
            parent_chat.send_message(thread, self.parent, "hello", now=self.at(10))
        self.assertEqual(ctx.exception.code, "thread_closed")
        self.assertTrue(parent_chat.reopen_thread(thread, self.teacher))

    def test_chat_goes_read_only_when_parent_unlinked(self):
        thread = self.thread()
        CampusParentLink.objects.filter(parent=self.parent, student=self.sa1).delete()
        with self.assertRaises(CommsError) as ctx:
            parent_chat.send_message(thread, self.teacher, "hello", now=self.at(10))
        self.assertEqual(ctx.exception.code, "not_a_parent")

    def test_unread_counts(self):
        thread = self.thread()
        parent_chat.send_message(thread, self.parent, "a", now=self.at(10))
        parent_chat.send_message(thread, self.parent, "b", now=self.at(10, 1))
        thread.refresh_from_db()
        self.assertEqual(parent_chat.total_unread(self.teacher), 2)
        self.assertEqual(parent_chat.total_unread(self.parent), 0)
        parent_chat.mark_read(thread, self.teacher)
        self.assertEqual(parent_chat.total_unread(self.teacher), 0)


# =====================================================================
# chat API
# =====================================================================
class ChatApiTests(CommsBase):
    def open_as_parent(self):
        self.client.force_authenticate(self.parent)
        return self.client.post(
            reverse("campus-parent-chat-thread-open-thread"),
            {"campus": str(self.campus.id), "student": str(self.sa1.id), "teacher": str(self.t_staff.id)},
            format="json",
        )

    def test_parent_opens_sends_teacher_sees_unread_and_reads(self):
        resp = self.open_as_parent()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        tid = resp.data["id"]
        self.assertEqual(resp.data["side"], "parent")

        with mock.patch("campus.parent_chat_views.dispatch_after_commit") as dispatch:
            sent = self.client.post(
                reverse("campus-parent-chat-thread-send", args=[tid]), {"body": "How is he doing?"}, format="json"
            )
        self.assertEqual(sent.status_code, status.HTTP_201_CREATED, sent.data)
        self.assertTrue(sent.data["message"]["mine"])
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.args[0], campus_tasks.send_parent_chat_notification)

        self.client.force_authenticate(self.teacher)
        listing = self.client.get(reverse("campus-parent-chat-thread-list"))
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.data["count"], 1)
        self.assertEqual(listing.data["results"][0]["unread"], 1)
        msgs = self.client.get(reverse("campus-parent-chat-thread-messages", args=[tid]))
        self.assertEqual(msgs.status_code, 200)
        self.assertEqual([m["body"] for m in msgs.data["messages"]], ["How is he doing?"])
        self.assertFalse(msgs.data["messages"][0]["mine"])
        self.client.post(reverse("campus-parent-chat-thread-read", args=[tid]))
        unread = self.client.get(reverse("campus-parent-chat-unread"))
        self.assertEqual(unread.data["unread"], 0)

    def test_link_is_rejected_with_a_code(self):
        tid = self.open_as_parent().data["id"]
        resp = self.client.post(
            reverse("campus-parent-chat-thread-send", args=[tid]), {"body": "go to https://x.com"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(ParentChatMessage.objects.count(), 0)

    def test_stranger_gets_404_and_admin_404_until_flagged(self):
        tid = self.open_as_parent().data["id"]
        url = reverse("campus-parent-chat-thread-detail", args=[tid])
        for user in (self.stranger, self.admin, self.parent2, self.other_teacher):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(url).status_code, 404, user.username)

        self.client.force_authenticate(self.parent)
        with mock.patch("campus.parent_chat_views.dispatch_after_commit") as dispatch:
            flagged = self.client.post(
                reverse("campus-parent-chat-thread-flag", args=[tid]), {"reason": "abusive"}, format="json"
            )
        self.assertEqual(flagged.status_code, 200, flagged.data)
        self.assertEqual(dispatch.call_args.args[0], campus_tasks.send_parent_chat_flag_notification)

        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(url).status_code, 200)
        listing = self.client.get(reverse("campus-parent-chat-thread-list"), {"flagged": "1"})
        self.assertEqual(listing.data["count"], 1)
        # admin oversight is read + close, never writing as a participant
        sent = self.client.post(reverse("campus-parent-chat-thread-send", args=[tid]), {"body": "hi"}, format="json")
        self.assertEqual(sent.status_code, 403)
        resolved = self.client.post(reverse("campus-parent-chat-thread-resolve-flag", args=[tid]))
        self.assertEqual(resolved.status_code, 200)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_contacts_for_parent_and_teacher(self):
        self.client.force_authenticate(self.parent)
        resp = self.client.get(reverse("campus-parent-chat-contacts"), {"campus": str(self.campus.id)})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(len(resp.data["children"]), 1)
        self.assertEqual(resp.data["children"][0]["student"]["id"], str(self.sa1.id))
        self.assertEqual(len(resp.data["children"][0]["teachers"]), 1)

        self.client.force_authenticate(self.teacher)
        resp = self.client.get(reverse("campus-parent-chat-contacts"), {"campus": str(self.campus.id)})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["as"], "teacher")
        student_ids = {r["student"]["id"] for r in resp.data["students"]}
        self.assertEqual(student_ids, {str(self.sa1.id), str(self.sa2.id)})  # both in secA, none from secB

    def test_settings_admin_can_patch_parent_cannot(self):
        url = reverse("campus-parent-comms-settings")
        self.client.force_authenticate(self.parent)
        self.assertEqual(self.client.get(url, {"campus": str(self.campus.id)}).status_code, 200)
        denied = self.client.patch(url, {"campus": str(self.campus.id), "chat_max_unanswered": 5}, format="json")
        self.assertEqual(denied.status_code, 403)
        self.client.force_authenticate(self.admin)
        ok = self.client.patch(
            url, {"campus": str(self.campus.id), "chat_max_unanswered": 5, "chat_end_time": "21:30"}, format="json"
        )
        self.assertEqual(ok.status_code, 200, ok.data)
        self.assertEqual(ok.data["chat_max_unanswered"], 5)
        self.assertEqual(ok.data["chat_end_time"], "21:30")
        bad = self.client.patch(url, {"campus": str(self.campus.id), "chat_max_unanswered": 999}, format="json")
        self.assertEqual(bad.status_code, 400)


# =====================================================================
# PTM
# =====================================================================
class PtmApiTests(CommsBase):
    def test_full_flow_create_open_publish_board_book(self):
        # 1. admin creates a draft
        self.client.force_authenticate(self.admin)
        day = (self.today + timedelta(days=3)).isoformat()
        with mock.patch("campus.ptm_views.dispatch_after_commit"):
            created = self.client.post(
                reverse("campus-ptm-event-list"),
                {
                    "campus": str(self.campus.id), "title": "Term 1 PTM", "date": day,
                    "start_time": "10:00", "end_time": "11:00", "slot_minutes": 10, "all_sections": True,
                },
                format="json",
            )
        self.assertEqual(created.status_code, 201, created.data)
        eid = created.data["id"]
        self.assertEqual(created.data["status"], "draft")

        # cannot publish with no slots
        with mock.patch("campus.ptm_views.dispatch_after_commit"):
            self.assertEqual(self.client.post(reverse("campus-ptm-event-publish", args=[eid])).status_code, 400)

        # 2. the teacher opens slots (6 x 10 min)
        self.client.force_authenticate(self.teacher)
        opened = self.client.post(reverse("campus-ptm-event-open-slots", args=[eid]), {}, format="json")
        self.assertEqual(opened.status_code, 200, opened.data)
        self.assertEqual(opened.data["created"], 6)
        # opening again creates nothing new
        again = self.client.post(reverse("campus-ptm-event-open-slots", args=[eid]), {}, format="json")
        self.assertEqual(again.data["created"], 0)
        # a parent can't see a draft
        self.client.force_authenticate(self.parent)
        self.assertEqual(self.client.get(reverse("campus-ptm-event-detail", args=[eid])).status_code, 404)

        # 3. admin publishes -> parents are told once
        self.client.force_authenticate(self.admin)
        with mock.patch("campus.ptm_views.dispatch_after_commit") as dispatch:
            published = self.client.post(reverse("campus-ptm-event-publish", args=[eid]))
            self.client.post(reverse("campus-ptm-event-publish", args=[eid]))  # idempotent
        self.assertEqual(published.status_code, 200, published.data)
        self.assertEqual(published.data["status"], "published")
        self.assertEqual(dispatch.call_count, 1)

        # 4. the parent sees the board and books
        self.client.force_authenticate(self.parent)
        board = self.client.get(reverse("campus-ptm-event-board", args=[eid]), {"student": str(self.sa1.id)})
        self.assertEqual(board.status_code, 200, board.data)
        self.assertEqual(len(board.data["teachers"]), 1)
        slot_id = board.data["teachers"][0]["slots"][0]["id"]
        with mock.patch("campus.ptm_views.dispatch_after_commit"):
            booked = self.client.post(
                reverse("campus-ptm-booking-list"),
                {"slot": slot_id, "student": str(self.sa1.id), "topic": "Maths marks"},
                format="json",
            )
        self.assertEqual(booked.status_code, 201, booked.data)
        self.assertEqual(booked.data["status"], "booked")
        self.assertNotIn("private_note", booked.data)

        # 5. the same slot is gone for the other parent; one teacher per child is enforced
        self.client.force_authenticate(self.parent2)
        with mock.patch("campus.ptm_views.dispatch_after_commit"):
            clash = self.client.post(
                reverse("campus-ptm-booking-list"),
                {"slot": slot_id, "student": str(self.sa2.id)},
                format="json",
            )
        self.assertEqual(clash.status_code, 409, clash.data)

    def test_parent_cannot_book_for_unrelated_child_or_teacher(self):
        event = self.make_event()
        slot = self.make_slot(event)
        self.client.force_authenticate(self.parent)  # linked to sa1 only
        with mock.patch("campus.ptm_views.dispatch_after_commit"):
            resp = self.client.post(
                reverse("campus-ptm-booking-list"), {"slot": str(slot.id), "student": str(self.sa2.id)}, format="json"
            )
        self.assertEqual(resp.status_code, 403, resp.data)
        other = self.make_slot(event, staff=self.t2_staff)  # teaches secB, not sa1
        with mock.patch("campus.ptm_views.dispatch_after_commit"):
            resp = self.client.post(
                reverse("campus-ptm-booking-list"), {"slot": str(other.id), "student": str(self.sa1.id)}, format="json"
            )
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(PTMBooking.objects.count(), 0)

    def test_booking_cutoff_and_parent_cancel_cutoff(self):
        settings = pc.get_settings(self.campus.id)
        event = self.make_event()
        slot = self.make_slot(event)
        # parent may cancel when well before the cut-off ...
        booking = ptm.book_slot(slot.id, self.sa1, self.parent)
        self.assertTrue(ptm.cancel_booking(booking, self.parent, "clash"))
        booking.refresh_from_db()
        self.assertEqual(booking.status, PTMBooking.Status.CANCELLED)
        self.assertEqual(booking.cancelled_by_role, "parent")
        # ... and the slot is free again
        booking2 = ptm.book_slot(slot.id, self.sa1, self.parent)
        # ... but not inside the cancel cut-off
        settings.ptm_cancel_cutoff_minutes = 60 * 24 * 7
        settings.save()
        with self.assertRaises(CommsError) as ctx:
            ptm.cancel_booking(booking2, self.parent)
        self.assertEqual(ctx.exception.code, "too_late_to_change")
        # staff are not bound by the parent cut-off
        self.assertTrue(ptm.cancel_booking(booking2, self.teacher, "teacher unwell"))

    def test_booking_closed_close_to_start(self):
        event = self.make_event(date=self.today)
        start = (timezone.localtime() + timedelta(minutes=20))
        if start.date() != self.today:  # too close to midnight for this scenario
            self.skipTest("near midnight")
        slot = self.make_slot(event, start=start.time(), end=(start + timedelta(minutes=10)).time())
        with self.assertRaises(CommsError) as ctx:
            ptm.book_slot(slot.id, self.sa1, self.parent)  # default cut-off is 60 minutes
        self.assertEqual(ctx.exception.code, "booking_closed_for_slot")

    def test_reschedule_same_teacher_only_and_frees_old_slot(self):
        event = self.make_event()
        s1 = self.make_slot(event, start=time(10, 0), end=time(10, 10))
        s2 = self.make_slot(event, start=time(10, 10), end=time(10, 20))
        s_other = self.make_slot(event, staff=self.t2_staff, start=time(10, 0), end=time(10, 10))
        booking = ptm.book_slot(s1.id, self.sa1, self.parent)
        with self.assertRaises(CommsError) as ctx:
            ptm.reschedule_booking(booking, s_other.id, self.parent)
        self.assertEqual(ctx.exception.code, "same_teacher_only")
        new, old = ptm.reschedule_booking(booking, s2.id, self.parent)
        old.refresh_from_db()
        self.assertEqual(old.status, PTMBooking.Status.CANCELLED)
        self.assertEqual(new.slot_id, s2.id)
        self.assertEqual(new.rescheduled_from_id, old.id)
        # the old slot can be taken by another parent now
        self.assertEqual(ptm.book_slot(s1.id, self.sa2, self.parent2).slot_id, s1.id)

    def test_private_note_never_reaches_the_parent(self):
        event = self.make_event(date=self.today - timedelta(days=0))
        # a meeting that has already started so an outcome can be recorded
        past = timezone.localtime() - timedelta(minutes=30)
        if past.date() != self.today:
            self.skipTest("near midnight")
        slot = self.make_slot(event, start=past.time(), end=(past + timedelta(minutes=10)).time())
        booking = PTMBooking.objects.create(
            campus=self.campus, event=event, slot=slot, staff=self.t_staff, student=self.sa1, parent=self.parent
        )
        self.client.force_authenticate(self.teacher)
        with mock.patch("campus.ptm_views.dispatch_after_commit") as dispatch:
            resp = self.client.post(
                reverse("campus-ptm-booking-outcome", args=[booking.id]),
                {"outcome": "completed", "summary": "Needs more practice", "private_note": "parents quarrelling"},
                format="json",
            )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["private_note"], "parents quarrelling")  # staff see it
        self.assertEqual(dispatch.call_args.args[0], campus_tasks.send_ptm_booking_notifications)

        # correcting only the outcome keeps the notes
        with mock.patch("campus.ptm_views.dispatch_after_commit"):
            self.client.post(
                reverse("campus-ptm-booking-outcome", args=[booking.id]), {"outcome": "no_show"}, format="json"
            )
        booking.refresh_from_db()
        self.assertEqual(booking.private_note, "parents quarrelling")

        self.client.force_authenticate(self.parent)
        detail = self.client.get(reverse("campus-ptm-booking-detail", args=[booking.id]))
        self.assertEqual(detail.status_code, 200)
        self.assertNotIn("private_note", detail.data)
        self.assertEqual(detail.data["summary_for_parent"], "Needs more practice")
        listing = self.client.get(reverse("campus-ptm-booking-list"), {"status": "all"})
        self.assertNotIn("parents quarrelling", str(listing.data))

    def test_cancel_event_cancels_every_booking(self):
        event = self.make_event()
        slot = self.make_slot(event)
        booking = ptm.book_slot(slot.id, self.sa1, self.parent)
        self.client.force_authenticate(self.admin)
        with mock.patch("campus.ptm_views.dispatch_after_commit") as dispatch:
            resp = self.client.post(reverse("campus-ptm-event-cancel", args=[event.id]))
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["bookings_cancelled"], 1)
        booking.refresh_from_db()
        self.assertEqual(booking.status, PTMBooking.Status.CANCELLED)
        self.assertEqual(dispatch.call_args.args[:1], (campus_tasks.send_ptm_event_notifications,))
        # booking after cancel is impossible
        with self.assertRaises(CommsError):
            ptm.book_slot(slot.id, self.sa1, self.parent)

    def test_only_admin_creates_events(self):
        self.client.force_authenticate(self.teacher)
        resp = self.client.post(
            reverse("campus-ptm-event-list"),
            {
                "campus": str(self.campus.id), "title": "x", "date": (self.today + timedelta(days=2)).isoformat(),
                "start_time": "10:00", "end_time": "11:00",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 403, resp.data)


# =====================================================================
# notifications + tasks
# =====================================================================
class PushAndTaskTests(CommsBase):
    def test_chat_message_notifies_other_side_and_after_hours_is_silent(self):
        thread = self.thread()
        day_msg = parent_chat.send_message(thread, self.parent, "Hello", now=self.at(10))
        with mock.patch("campus.parent_chat_push.bridge.notify") as notify:
            campus_tasks.send_parent_chat_notification(str(day_msg.pk))
        kw = notify.call_args.kwargs
        self.assertEqual(kw["users"], [self.teacher])
        self.assertEqual(kw["notif_type"], NotifTypes.CAMPUS_PARENT_MESSAGE)
        self.assertTrue(kw["push"])

        night = parent_chat.send_message(thread, self.parent, "Late", now=self.at(22))
        with mock.patch("campus.parent_chat_push.bridge.notify") as notify:
            campus_tasks.send_parent_chat_notification(str(night.pk))
        self.assertFalse(notify.call_args.kwargs["push"])

    def test_chat_notification_body_is_a_short_preview(self):
        thread = self.thread()
        s = pc.get_settings(self.campus.id)
        msg = parent_chat.send_message(thread, self.parent, "word " * 150, now=self.at(10))
        with mock.patch("campus.parent_chat_push.bridge.notify") as notify:
            campus_tasks.send_parent_chat_notification(str(msg.pk))
        self.assertLessEqual(len(notify.call_args.kwargs["body"]), 90)
        self.assertGreater(parent_chat.max_chars(s), 90)

    def test_flag_notifies_admins_but_not_the_reporter_and_carries_no_text(self):
        thread = self.thread()
        parent_chat.flag_thread(thread, self.teacher, "abusive")
        with mock.patch("campus.parent_chat_push.bridge.notify") as notify:
            campus_tasks.send_parent_chat_flag_notification(str(thread.pk))
        kw = notify.call_args.kwargs
        self.assertEqual(kw["users"], [self.admin])
        self.assertNotIn("abusive", kw["body"])

    def test_ptm_booked_notifies_the_teacher_only(self):
        event = self.make_event()
        booking = ptm.book_slot(self.make_slot(event).id, self.sa1, self.parent, "marks")
        with mock.patch("campus.ptm_push.bridge.notify") as notify:
            campus_tasks.send_ptm_booking_notifications(str(booking.pk), "booked")
        self.assertEqual(notify.call_args.kwargs["users"], [self.teacher])

    def test_ptm_booking_notification_skipped_when_state_changed(self):
        event = self.make_event()
        booking = ptm.book_slot(self.make_slot(event).id, self.sa1, self.parent)
        ptm.cancel_booking(booking, self.parent)
        with mock.patch("campus.ptm_push.bridge.notify") as notify:
            campus_tasks.send_ptm_booking_notifications(str(booking.pk), "booked")  # now cancelled
        notify.assert_not_called()

    def test_reminders_1h_sent_once(self):
        start = timezone.localtime() + timedelta(minutes=30)
        if start.date() != self.today or (start + timedelta(minutes=10)).date() != self.today:
            self.skipTest("near midnight")
        event = self.make_event(date=self.today)
        slot = self.make_slot(event, start=start.time(), end=(start + timedelta(minutes=10)).time())
        booking = PTMBooking.objects.create(
            campus=self.campus, event=event, slot=slot, staff=self.t_staff, student=self.sa1, parent=self.parent,
            reminder_24h_sent_at=timezone.now(),
        )
        with mock.patch("campus.ptm_push.notify_reminder") as notify:
            first = campus_tasks.send_ptm_reminders()
            second = campus_tasks.send_ptm_reminders()
        self.assertEqual(first["reminded"], 1)
        self.assertEqual(second["reminded"], 0)
        notify.assert_called_once()
        booking.refresh_from_db()
        self.assertIsNotNone(booking.reminder_1h_sent_at)

    def test_reminders_skip_cancelled_bookings(self):
        start = timezone.localtime() + timedelta(minutes=30)
        if start.date() != self.today or (start + timedelta(minutes=10)).date() != self.today:
            self.skipTest("near midnight")
        event = self.make_event(date=self.today)
        slot = self.make_slot(event, start=start.time(), end=(start + timedelta(minutes=10)).time())
        PTMBooking.objects.create(
            campus=self.campus, event=event, slot=slot, staff=self.t_staff, student=self.sa1, parent=self.parent,
            status=PTMBooking.Status.CANCELLED,
        )
        with mock.patch("campus.ptm_push.notify_reminder") as notify:
            campus_tasks.send_ptm_reminders()
        notify.assert_not_called()

    def test_morning_digest_runs_only_on_the_tick_the_window_opens(self):
        s = pc.get_settings(self.campus.id)
        now = pc.local_now()
        opened = now - timedelta(minutes=3)
        if opened.date() != now.date():
            self.skipTest("near midnight")
        s.chat_start_time = opened.time().replace(second=0, microsecond=0)
        s.chat_end_time = (now + timedelta(hours=1)).time().replace(second=0, microsecond=0)
        s.save()
        with mock.patch("campus.parent_chat_push.notify_morning_digest", return_value=1) as digest:
            campus_tasks.send_parent_chat_morning_digest()
        digest.assert_called_once_with(self.campus.id)

        s.chat_start_time = (now - timedelta(minutes=45)).time().replace(second=0, microsecond=0)
        s.save()
        with mock.patch("campus.parent_chat_push.notify_morning_digest", return_value=1) as digest:
            campus_tasks.send_parent_chat_morning_digest()
        digest.assert_not_called()

    def test_morning_digest_one_push_per_teacher_for_after_hours_messages(self):
        thread = self.thread()
        parent_chat.send_message(thread, self.parent, "q1", now=self.at(22))
        parent_chat.send_message(thread, self.parent, "q2", now=self.at(22, 5))
        from .parent_chat_push import notify_morning_digest

        with mock.patch("campus.parent_chat_push.bridge.notify") as notify:
            told = notify_morning_digest(self.campus.id)
        self.assertEqual(told, 1)
        self.assertEqual(notify.call_args.kwargs["users"], [self.teacher])
        self.assertIn("2 messages", notify.call_args.kwargs["body"])
        # once the teacher has read the thread there is nothing to digest
        parent_chat.mark_read(ParentChatThread.objects.get(pk=thread.pk), self.teacher)
        with mock.patch("campus.parent_chat_push.bridge.notify") as notify:
            self.assertEqual(notify_morning_digest(self.campus.id), 0)
        notify.assert_not_called()
