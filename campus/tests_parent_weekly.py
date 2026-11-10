# campus/tests_parent_weekly.py
"""P-F14 — parent weekly summary (builder, endpoint, WhatsApp/email text, weekly push).
Run: `python manage.py test campus.tests_parent_weekly` (NOT RUN when written)."""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from . import parent_weekly, parent_weekly_push
from .models import Attendance, CampusParentLink, ExamTerm, FeeInvoice, FeePayment, FeeStructure, ResultEntry
from .tests_t4_efg import T4Base


class WeeklyBase(T4Base):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.today = timezone.localdate()
        self.url = reverse("campus-parent-weekly")
        self.client.force_authenticate(self.parent)

    def _att(self, days_ago, status, enrollment=None, subject=None):
        return Attendance.objects.create(
            enrollment=enrollment or self.e_a1, date=self.today - timedelta(days=days_ago), subject=subject,
            status=status, marked_by=self.teacher,
        )

    def _term(self, name, start_days_ago):
        start = self.today - timedelta(days=start_days_ago)
        return ExamTerm.objects.create(session=self.session, name=name, start_date=start, end_date=start + timedelta(days=5))

    def _result(self, term, got, mx, status=ResultEntry.Status.PUBLISHED, published_days_ago=None, subject=None):
        return ResultEntry.objects.create(
            enrollment=self.e_a1, subject=subject or self.math, exam_term=term, marks_obtained=Decimal(got),
            max_marks=Decimal(mx), status=status,
            published_at=(timezone.now() - timedelta(days=published_days_ago)) if published_days_ago is not None else None,
        )

    def _invoice(self, amount="1000", due_in=5, paid="0", enrollment=None, title="T1"):
        fs = FeeStructure.objects.create(
            campus=self.campus, session=self.session, title=title, amount=Decimal(amount),
            due_date=self.today + timedelta(days=due_in),
        )
        inv = FeeInvoice.objects.create(enrollment=enrollment or self.e_a1, fee_structure=fs, amount_due=Decimal(amount))
        if Decimal(paid) > 0:
            FeePayment.objects.create(
                invoice=inv, amount=Decimal(paid), payer_role="parent", payment_mode="cash",
                status=FeePayment.Status.SUCCESS, paid_by=self.parent,
            )
            inv.recompute_status()
        return inv

    def _child(self, data=None):
        data = data or self.client.get(self.url).data
        rows = [c for c in data["children"] if c["student_id"] == str(self.sa1.pk)]
        self.assertEqual(len(rows), 1, data)
        return rows[0]


class WeeklyEndpointTests(WeeklyBase):
    def test_requires_login(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, (401, 403))

    def test_only_my_linked_children(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual([c["student_id"] for c in r.data["children"]], [str(self.sa1.pk)])

    def test_other_user_gets_empty_report(self):
        self.client.force_authenticate(self.sa2)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["children"], [])
        self.assertEqual(r.data["share_text"], "")
        self.assertEqual(r.data["whatsapp_url"], "")
        self.assertIsNone(r.data["email"])

    def test_invalid_campus_param(self):
        self.assertEqual(self.client.get(self.url, {"campus": "nope"}).status_code, 400)

    def test_campus_filter(self):
        r = self.client.get(self.url, {"campus": str(self.campus.id)})
        self.assertEqual(len(r.data["children"]), 1)

    def test_multiple_children(self):
        CampusParentLink.objects.create(campus=self.campus, parent=self.parent, student=self.sa2)
        r = self.client.get(self.url)
        self.assertEqual({c["student_id"] for c in r.data["children"]}, {str(self.sa1.pk), str(self.sa2.pk)})
        self.assertEqual(r.data["totals"]["children"], 2)

    def test_response_has_whatsapp_and_email(self):
        self._att(1, "absent")
        r = self.client.get(self.url)
        self.assertTrue(r.data["whatsapp_url"].startswith("https://wa.me/?text="))
        self.assertIn(self.sa1.username, r.data["share_text"])
        self.assertTrue(r.data["email"]["subject"])
        self.assertIn("<ul", r.data["email"]["html"])


class WeeklyAttendanceTests(WeeklyBase):
    def test_nothing_marked(self):
        a = self._child()["attendance"]
        self.assertEqual(a["marked_days"], 0)
        self.assertIsNone(a["percent"])

    def test_counts_and_absent_dates(self):
        self._att(1, "present")
        self._att(2, "late")
        self._att(3, "absent")
        self._att(4, "leave")
        a = self._child()["attendance"]
        self.assertEqual((a["present"], a["late"], a["absent"], a["leave"]), (1, 1, 1, 1))
        self.assertEqual(a["marked_days"], 3)  # leave is not a marked school day
        self.assertEqual(Decimal(str(a["percent"])), Decimal("66.7"))
        self.assertEqual(a["absent_dates"], [(self.today - timedelta(days=3)).isoformat()])

    def test_worst_status_of_a_day_wins(self):
        self._att(1, "present")
        self._att(1, "absent", subject=self.math)
        a = self._child()["attendance"]
        self.assertEqual((a["present"], a["absent"]), (0, 1))

    def test_outside_the_7_day_window_is_ignored(self):
        self._att(parent_weekly.WINDOW_DAYS, "absent")
        self.assertEqual(self._child()["attendance"]["marked_days"], 0)

    def test_siblings_do_not_leak(self):
        self._att(1, "absent", enrollment=self.e_a2)
        self.assertEqual(self._child()["attendance"]["absent"], 0)


class WeeklyMarksTests(WeeklyBase):
    def test_no_results(self):
        m = self._child()["marks"]
        self.assertEqual((m["trend"], m["direction"]), ([], "none"))

    def test_draft_results_never_reach_a_parent(self):
        self._result(self._term("Unit 1", 60), "40", "100", status=ResultEntry.Status.DRAFT)
        self.assertEqual(self._child()["marks"]["trend"], [])

    def test_single_term_is_first(self):
        self._result(self._term("Unit 1", 60), "40", "50")
        m = self._child()["marks"]
        self.assertEqual(m["direction"], "first")
        self.assertEqual(len(m["trend"]), 1)

    def test_trend_up_down_flat(self):
        t1, t2 = self._term("Unit 1", 90), self._term("Mid", 45)
        self._result(t1, "50", "100")
        self._result(t2, "70", "100")
        m = self._child()["marks"]
        self.assertEqual([p["term"] for p in m["trend"]], ["Unit 1", "Mid"])  # oldest -> newest
        self.assertEqual(m["direction"], "up")
        self.assertEqual(Decimal(str(m["delta"])), Decimal("20.0"))

        t3 = self._term("Final", 5)
        self._result(t3, "30", "100")
        self.assertEqual(self._child()["marks"]["direction"], "down")

    def test_flat_band(self):
        self._result(self._term("A", 90), "60", "100")
        self._result(self._term("B", 45), "60.5", "100")
        self.assertEqual(self._child()["marks"]["direction"], "flat")

    def test_only_last_three_terms(self):
        for i, name in enumerate(["T1", "T2", "T3", "T4"]):
            self._result(self._term(name, 100 - i * 20), "50", "100")
        self.assertEqual([p["term"] for p in self._child()["marks"]["trend"]], ["T2", "T3", "T4"])

    def test_percent_is_total_over_total_across_subjects(self):
        t = self._term("Mid", 30)
        self._result(t, "20", "50", subject=self.math)
        self._result(t, "40", "50", subject=self.phy)
        self.assertEqual(Decimal(str(self._child()["marks"]["trend"][0]["percent"])), Decimal("60.0"))

    def test_new_results_this_week(self):
        t = self._term("Mid", 30)
        self._result(t, "20", "50", published_days_ago=2, subject=self.math)
        self._result(t, "20", "50", published_days_ago=20, subject=self.phy)
        self.assertEqual(self._child()["marks"]["new_results"], 1)


class WeeklyFeesTests(WeeklyBase):
    def test_nothing_due(self):
        f = self._child()["fees"]
        self.assertEqual((f["due"], f["open_invoices"]), ("0.00", 0))

    def test_balance_after_successful_payment(self):
        self._invoice("1000", paid="400")
        f = self._child()["fees"]
        self.assertEqual((f["due"], f["open_invoices"]), ("600.00", 1))

    def test_paid_invoice_not_counted(self):
        self._invoice("500", paid="500")
        self.assertEqual(self._child()["fees"]["open_invoices"], 0)

    def test_overdue_and_next_due_date(self):
        self._invoice("300", due_in=-3, title="Old")
        self._invoice("200", due_in=10, title="New")
        f = self._child()["fees"]
        self.assertEqual((f["due"], f["open_invoices"], f["overdue_invoices"]), ("500.00", 2, 1))
        self.assertEqual(f["next_due_date"], (self.today - timedelta(days=3)).isoformat())


class WeeklyTextTests(WeeklyBase):
    def _summary(self, lang="en"):
        return parent_weekly.build_weekly(self.parent, lang=lang)

    def test_english_text_lines(self):
        self._att(1, "present")
        self._att(2, "absent")
        self._invoice("750", due_in=-1)
        text = self._summary()["share_text"]
        self.assertIn("weekly report", text)
        self.assertIn("Attendance: 50% (1/2 days present)", text)
        self.assertIn("Absent on:", text)
        self.assertIn("Fees due: Rs. 750.00 (1 overdue)", text)

    def test_hindi_text(self):
        self._att(1, "present")
        text = self._summary("hi")["share_text"]
        self.assertIn("हाज़िरी", text)
        self.assertIn("फ़ीस", text)

    def test_unknown_language_falls_back_to_english(self):
        self.assertEqual(parent_weekly.normalize_lang("fr"), "en")
        self.assertEqual(parent_weekly.normalize_lang("hi-IN"), "hi")

    def test_email_escapes_html_in_names(self):
        self.sa1.first_name = "<b>Evil</b>"
        self.sa1.save()
        mail = parent_weekly.render_email(self._summary(), parent_name="P", campus_name="GV")
        self.assertNotIn("<b>Evil</b>", mail["html"])
        self.assertIn("&lt;b&gt;Evil&lt;/b&gt;", mail["html"])

    def test_whatsapp_url_is_encoded(self):
        url = parent_weekly.whatsapp_url("a b\n&")
        self.assertEqual(url, "https://wa.me/?text=a%20b%0A%26")
        self.assertEqual(parent_weekly.whatsapp_url(""), "")

    def test_push_copy_single_and_many(self):
        self._att(1, "present")
        title, body = parent_weekly.push_copy(self._summary())
        self.assertTrue(title)
        self.assertIn(self.sa1.username, body)
        CampusParentLink.objects.create(campus=self.campus, parent=self.parent, student=self.sa2)
        _, body2 = parent_weekly.push_copy(self._summary())
        self.assertIn("2 children", body2)

    def test_no_chat_or_remarks_in_payload(self):
        t = self._term("Mid", 30)
        r = self._result(t, "20", "50")
        r.remarks = "SECRET TEACHER REMARK"
        r.save()
        self.assertNotIn("SECRET", str(self.client.get(self.url).data))


class WeeklyPushTests(WeeklyBase):
    def setUp(self):
        super().setUp()
        self.notify = mock.patch.object(parent_weekly_push.bridge, "notify").start()
        self.email = mock.patch.object(parent_weekly_push, "_send_email", return_value=True).start()
        self.addCleanup(mock.patch.stopall)

    def test_nothing_to_report_sends_nothing_and_can_retry(self):
        res = parent_weekly_push.send_weekly_to_parent(self.parent)
        self.assertEqual((res["sent"], res["reason"]), (False, "nothing_to_report"))
        self.notify.assert_not_called()
        self._att(1, "present")  # marker was released -> a later run can still send
        self.assertTrue(parent_weekly_push.send_weekly_to_parent(self.parent)["sent"])

    def test_sends_bell_push_and_email_once_per_week(self):
        self._att(1, "absent")
        first = parent_weekly_push.send_weekly_to_parent(self.parent)
        self.assertEqual((first["sent"], first["push"], first["email"]), (True, True, True))
        kwargs = self.notify.call_args.kwargs
        self.assertEqual(kwargs["notif_type"], "campus_parent_weekly")
        self.assertEqual(kwargs["users"], [self.parent])
        self.assertEqual(kwargs["data"]["campus_id"], str(self.campus.pk))
        again = parent_weekly_push.send_weekly_to_parent(self.parent)
        self.assertEqual((again["sent"], again["reason"]), (False, "already_sent"))
        self.assertEqual(self.notify.call_count, 1)

    def test_email_failure_does_not_block_push(self):
        self._att(1, "present")
        self.email.side_effect = RuntimeError("smtp down")
        res = parent_weekly_push.send_weekly_to_parent(self.parent)
        self.assertEqual((res["sent"], res["push"], res["email"]), (True, True, False))

    def test_everything_failing_releases_marker(self):
        self._att(1, "present")
        self.notify.side_effect = RuntimeError("fcm down")
        self.email.return_value = False
        res = parent_weekly_push.send_weekly_to_parent(self.parent)
        self.assertEqual((res["sent"], res["reason"]), (False, "delivery_failed"))
        self.notify.side_effect = None
        self.assertTrue(parent_weekly_push.send_weekly_to_parent(self.parent)["sent"])

    def test_send_all_skips_shadow_parents(self):
        from django.contrib.auth import get_user_model

        shadow = get_user_model().objects.create_user(username="parent_shadow_1", password="x")
        CampusParentLink.objects.create(campus=self.campus, parent=shadow, student=self.sa2)
        self._att(1, "present")
        stats = parent_weekly_push.send_all()
        self.assertEqual(stats["parents"], 2)
        self.assertEqual(stats["sent"], 1)
        self.assertEqual(stats["skipped"], 1)

    def test_task_kill_switch(self):
        from .tasks import send_parent_weekly_summaries

        with self.settings(CAMPUS_PARENT_WEEKLY_ENABLED=False):
            self.assertEqual(send_parent_weekly_summaries(), {"disabled": True})
        self.notify.assert_not_called()
