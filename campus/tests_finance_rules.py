# campus/tests_finance_rules.py
"""Pure-Python tests (no Django, no DB): late fine, concessions, instalments, numbering,
reconciliation matcher + CSV/API parsers, export safety.

Run anywhere:   python -m unittest campus.tests_finance_rules -v      (from backend/backend)
"""
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal as D

from campus import finance_export as fx
from campus import finance_recon as rc
from campus import finance_rules as fr


class ConcessionTests(unittest.TestCase):
    def test_percent_and_flat(self):
        self.assertEqual(fr.concession_value(D("10000"), "percent", 10), D("1000.00"))
        self.assertEqual(fr.concession_value(D("10000"), "flat", 750), D("750.00"))

    def test_flat_never_exceeds_base(self):
        self.assertEqual(fr.concession_value(D("500"), "flat", 900), D("500.00"))

    def test_percent_over_100_is_clamped(self):
        self.assertEqual(fr.concession_value(D("500"), "percent", 150), D("500.00"))

    def test_stack_is_additive_and_capped(self):
        self.assertEqual(fr.total_concession(D("10000"), [("percent", 10), ("percent", 10)]), D("2000.00"))
        self.assertEqual(fr.total_concession(D("1000"), [("percent", 80), ("flat", 500)]), D("1000.00"))

    def test_bad_kind(self):
        with self.assertRaises(ValueError):
            fr.concession_value(D("100"), "bogus", 1)

    def test_amount_due_invariant(self):
        self.assertEqual(fr.amount_due(D("10000"), D("2000"), D("150")), D("8150.00"))
        self.assertEqual(fr.amount_due(D("1000"), D("5000"), D("0")), D("0.00"))  # never negative


class LateFineTests(unittest.TestCase):
    DUE = date(2026, 7, 15)

    def fine(self, kind, value, *, as_of, grace=0, cap=None, principal="10000", payments=()):
        return fr.late_fine(kind=kind, value=value, grace_days=grace, cap=cap, principal=D(principal),
                            due_date=self.DUE, payments=payments, as_of=as_of)

    def test_none_and_not_late_yet(self):
        self.assertEqual(self.fine("none", 50, as_of=date(2026, 8, 30)), D("0.00"))
        self.assertEqual(self.fine("flat", 100, as_of=self.DUE), D("0.00"))          # due day itself
        self.assertEqual(self.fine("flat", 100, as_of=self.DUE + timedelta(days=1)), D("100.00"))

    def test_grace_days(self):
        self.assertEqual(self.fine("per_day", 10, grace=5, as_of=self.DUE + timedelta(days=5)), D("0.00"))
        self.assertEqual(self.fine("per_day", 10, grace=5, as_of=self.DUE + timedelta(days=8)), D("30.00"))

    def test_per_day_grows_while_unpaid(self):
        self.assertEqual(self.fine("per_day", 10, as_of=self.DUE + timedelta(days=10)), D("100.00"))
        self.assertEqual(self.fine("per_day", 10, as_of=self.DUE + timedelta(days=20)), D("200.00"))

    def test_fine_freezes_when_principal_paid(self):
        paid_on = self.DUE + timedelta(days=10)
        pays = [(paid_on, D("10000"))]
        self.assertEqual(self.fine("per_day", 10, as_of=self.DUE + timedelta(days=90), payments=pays), D("100.00"))

    def test_paid_before_due_no_fine(self):
        pays = [(self.DUE - timedelta(days=3), D("10000"))]
        self.assertEqual(self.fine("per_day", 10, as_of=self.DUE + timedelta(days=90), payments=pays), D("0.00"))

    def test_partial_payment_keeps_fine_running(self):
        pays = [(self.DUE - timedelta(days=1), D("4000"))]
        self.assertEqual(self.fine("per_day", 10, as_of=self.DUE + timedelta(days=5), payments=pays), D("50.00"))

    def test_percent_is_on_unpaid_at_cutoff(self):
        pays = [(self.DUE - timedelta(days=1), D("4000"))]       # 6000 still unpaid at cutoff
        self.assertEqual(self.fine("percent", 2, as_of=self.DUE + timedelta(days=30), payments=pays), D("120.00"))
        self.assertEqual(self.fine("percent", 2, as_of=self.DUE + timedelta(days=60)), D("200.00"))

    def test_cap(self):
        self.assertEqual(self.fine("per_day", 50, cap=300, as_of=self.DUE + timedelta(days=100)), D("300.00"))

    def test_zero_principal_and_no_due_date(self):
        self.assertEqual(self.fine("flat", 100, principal="0", as_of=self.DUE + timedelta(days=9)), D("0.00"))
        self.assertEqual(fr.late_fine(kind="flat", value=100, principal=D("100"), due_date=None, as_of=date.today()), D("0.00"))

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            self.fine("compound", 1, as_of=self.DUE + timedelta(days=3))

    def test_deterministic(self):
        a = self.fine("per_day", 7, as_of=self.DUE + timedelta(days=12))
        b = self.fine("per_day", 7, as_of=self.DUE + timedelta(days=12))
        self.assertEqual(a, b)


class PlanTests(unittest.TestCase):
    def test_split_is_exact(self):
        parts = fr.split_amount(D("10000.00"), 3)
        self.assertEqual(sum(parts), D("10000.00"))
        self.assertEqual(parts, [D("3333.34"), D("3333.33"), D("3333.33")])

    def test_split_single(self):
        self.assertEqual(fr.split_amount(D("999.99"), 1), [D("999.99")])

    def test_split_validation(self):
        with self.assertRaises(ValueError):
            fr.split_amount(D("0"), 3)
        with self.assertRaises(ValueError):
            fr.split_amount(D("100"), 0)

    def test_add_months_clamps(self):
        self.assertEqual(fr.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(fr.add_months(date(2028, 1, 31), 1), date(2028, 2, 29))
        self.assertEqual(fr.add_months(date(2026, 11, 15), 3), date(2027, 2, 15))

    def test_installment_dates(self):
        self.assertEqual(fr.installment_dates(date(2026, 4, 10), 4, 3),
                         [date(2026, 4, 10), date(2026, 7, 10), date(2026, 10, 10), date(2027, 1, 10)])

    def test_titles(self):
        self.assertEqual(fr.installment_titles("Tuition", 1), ["Tuition"])
        self.assertEqual(fr.installment_titles("Tuition", 2)[1], "Tuition — Instalment 2/2")

    def test_invoice_number(self):
        self.assertEqual(fr.session_label(date(2026, 4, 1), date(2027, 3, 31)), "2026-27")
        self.assertEqual(fr.session_label(date(2026, 1, 1), date(2026, 12, 31)), "2026")
        self.assertEqual(fr.format_invoice_no("2026-27", 123), "INV/2026-27/000123")

    def test_parse_money(self):
        self.assertEqual(fr.parse_money("1500.5"), D("1500.50"))
        for bad in ("-1", "0", "abc", "1e12"):
            with self.assertRaises(ValueError):
                fr.parse_money(bad)
        self.assertEqual(fr.parse_money("0", allow_zero=True), D("0.00"))


def ours(pid, order, amount, status="success", gw_pay="", when=date(2026, 8, 5), created=None):
    return rc.OurPayment(payment_id=pid, order_id=order, gateway_payment_id=gw_pay, amount=D(str(amount)),
                         status=status, when=when, created_at=created)


def gw(pid, order, amount, status="captured", refunded="0", kind="", fee=None, tax=None, when=date(2026, 8, 5)):
    return rc.GatewayPayment(payment_id=pid, order_id=order, amount=D(str(amount)), status=status,
                             amount_refunded=D(str(refunded)), kind=kind,
                             fee=D(str(fee)) if fee is not None else None,
                             tax=D(str(tax)) if tax is not None else None, when=when)


def cats(findings):
    return sorted(f.category for f in findings)


class ReconcileTests(unittest.TestCase):
    def test_clean_match(self):
        f = rc.reconcile([ours("p1", "order_1", 5000, gw_pay="pay_1")], [gw("pay_1", "order_1", 5000)])
        self.assertEqual(cats(f), [rc.MATCHED])

    def test_match_by_order_when_payment_id_not_stored(self):
        f = rc.reconcile([ours("p1", "order_1", 5000)], [gw("pay_1", "order_1", 5000)])
        self.assertEqual(cats(f), [rc.MATCHED])

    def test_pending_but_captured_is_settlable(self):
        f = rc.reconcile([ours("p1", "order_1", 5000, status="pending")], [gw("pay_1", "order_1", 5000)])
        self.assertEqual(cats(f), [rc.PENDING_BUT_CAPTURED])
        self.assertEqual(f[0].suggested_action, "settle")

    def test_pending_with_different_amount_is_not_settlable(self):
        f = rc.reconcile([ours("p1", "order_1", 5000, status="pending")], [gw("pay_1", "order_1", 4000)])
        self.assertEqual(cats(f), [rc.AMOUNT_MISMATCH])
        self.assertEqual(f[0].suggested_action, "review")

    def test_gateway_only(self):
        f = rc.reconcile([], [gw("pay_9", "order_9", 700, kind="campus_fee")])
        self.assertEqual(cats(f), [rc.GATEWAY_ONLY])
        self.assertIn("no record", f[0].detail)

    def test_ignored_orders_are_skipped(self):
        f = rc.reconcile([], [gw("pay_9", "order_coin", 700)], ignore_order_ids=["order_coin"])
        self.assertEqual(f, [])

    def test_non_captured_gateway_only_rows_ignored(self):
        f = rc.reconcile([], [gw("pay_9", "order_9", 700, status="failed"), gw("pay_8", "o8", 5, status="created")])
        self.assertEqual(f, [])

    def test_missing_at_gateway_respects_window(self):
        o_in = ours("p1", "order_1", 100, when=date(2026, 8, 5))
        o_out = ours("p2", "order_2", 100, when=date(2026, 6, 1))
        f = rc.reconcile([o_in, o_out], [], window=(date(2026, 8, 1), date(2026, 8, 31)))
        self.assertEqual([x.payment_id for x in f], ["p1"])
        self.assertEqual(f[0].category, rc.MISSING_AT_GATEWAY)

    def test_amount_mismatch(self):
        f = rc.reconcile([ours("p1", "order_1", 5000)], [gw("pay_1", "order_1", 4500)])
        self.assertEqual(cats(f), [rc.AMOUNT_MISMATCH])

    def test_refunded_at_gateway(self):
        f = rc.reconcile([ours("p1", "order_1", 5000)], [gw("pay_1", "order_1", 5000, status="refunded", refunded=5000)])
        self.assertEqual(cats(f), [rc.REFUNDED_AT_GATEWAY])
        self.assertEqual(f[0].suggested_action, "mark_refunded")

    def test_refund_by_amount_even_if_status_says_captured(self):
        f = rc.reconcile([ours("p1", "order_1", 5000)], [gw("pay_1", "order_1", 5000, refunded=5000)])
        self.assertEqual(cats(f), [rc.REFUNDED_AT_GATEWAY])

    def test_partial_refund(self):
        f = rc.reconcile([ours("p1", "order_1", 5000)], [gw("pay_1", "order_1", 5000, refunded=1000)])
        self.assertEqual(cats(f), [rc.PARTIAL_REFUND])

    def test_refund_not_at_gateway(self):
        f = rc.reconcile([ours("p1", "order_1", 5000, status="refunded")], [gw("pay_1", "order_1", 5000)])
        self.assertEqual(cats(f), [rc.REFUND_NOT_AT_GATEWAY])

    def test_refunded_both_sides_is_matched(self):
        f = rc.reconcile([ours("p1", "order_1", 5000, status="refunded")],
                         [gw("pay_1", "order_1", 5000, status="refunded", refunded=5000)])
        self.assertEqual(cats(f), [rc.MATCHED])

    def test_failed_but_success(self):
        f = rc.reconcile([ours("p1", "order_1", 5000)], [gw("pay_1", "order_1", 5000, status="failed")])
        self.assertEqual(cats(f), [rc.FAILED_BUT_SUCCESS])

    def test_authorized_not_captured_but_we_say_success(self):
        f = rc.reconcile([ours("p1", "order_1", 5000)], [gw("pay_1", "order_1", 5000, status="authorized")])
        self.assertEqual(cats(f), [rc.MISSING_AT_GATEWAY])

    def test_duplicate_capture_on_one_order(self):
        f = rc.reconcile([ours("p1", "order_1", 5000, gw_pay="pay_1")],
                         [gw("pay_1", "order_1", 5000), gw("pay_2", "order_1", 5000)])
        self.assertIn(rc.DUPLICATE_CAPTURE, cats(f))
        self.assertEqual(cats(f).count(rc.DUPLICATE_CAPTURE), 1)

    def test_stale_pending(self):
        now = datetime(2026, 8, 10, 12, 0)
        old = ours("p1", "order_1", 5000, status="pending", created=now - timedelta(hours=30))
        fresh = ours("p2", "order_2", 5000, status="pending", created=now - timedelta(hours=2))
        f = rc.reconcile([old, fresh], [], now=now)
        self.assertEqual([(x.payment_id, x.category) for x in f], [("p1", rc.STALE_PENDING)])

    def test_every_category_has_meta(self):
        for c in rc.ALL_CATEGORIES:
            sev, action = rc.CATEGORY_META[c]
            self.assertIn(sev, ("ok", "info", "warn", "error"))
            self.assertIn(action, ("none", "settle", "mark_refunded", "review", "recompute"))

    def test_summary_money(self):
        g = [gw("a", "o1", 1000, fee=20, tax=3.6), gw("b", "o2", 500, status="refunded", refunded=500, fee=10, tax=1.8)]
        f = rc.reconcile([], g)
        s = rc.summarize(f, g)
        self.assertEqual(s["gross_captured"], "1500.00")
        self.assertEqual(s["refunded"], "500.00")
        self.assertEqual(s["gateway_fee"], "30.00")
        self.assertEqual(s["net_expected_settlement"], str(D("1500") - D("500") - D("30") - D("5.40")))
        self.assertEqual(s["open_issues"], 2)


CSV_OK = (
    "id,entity,amount,currency,status,order_id,method,amount_refunded,refund_status,fee,tax,created_at\n"
    "pay_A1,payment,5000.00,INR,captured,order_A1,upi,0.00,,118.00,18.00,2026-08-05 10:11:12\n"
    "pay_B2,payment,2500.00,INR,captured,order_B2,card,2500.00,full,59.00,9.00,05/08/2026 11:00:00\n"
    "pay_C3,payment,100.00,INR,failed,order_C3,upi,0,,,,2026-08-06\n"
)


class CsvParseTests(unittest.TestCase):
    def test_parses_dashboard_export(self):
        rows, errors = rc.parse_razorpay_csv(CSV_OK)
        self.assertEqual(errors, [])
        self.assertEqual([r.payment_id for r in rows], ["pay_A1", "pay_B2", "pay_C3"])
        a, b, c = rows
        self.assertEqual((a.amount, a.status, a.method, a.fee), (D("5000.00"), "captured", "upi", D("118.00")))
        self.assertEqual(a.when, date(2026, 8, 5))
        self.assertEqual(b.status, "refunded")          # refund_status=full promotes captured -> refunded
        self.assertEqual(b.when, date(2026, 8, 5))
        self.assertEqual(c.status, "failed")

    def test_paise_unit(self):
        rows, errors = rc.parse_razorpay_csv("id,amount,status\npay_1,500000,captured\n", amount_unit="paise")
        self.assertEqual((errors, rows[0].amount), ([], D("5000.00")))

    def test_semicolon_delimiter_and_bom(self):
        rows, errors = rc.parse_razorpay_csv("\ufeffid;amount;status;order id\npay_1;10;captured;order_1\n")
        self.assertEqual((errors, rows[0].order_id), ([], "order_1"))

    def test_missing_columns_is_one_clear_error(self):
        rows, errors = rc.parse_razorpay_csv("foo,bar\n1,2\n")
        self.assertEqual(rows, [])
        self.assertIn("Missing column", errors[0][1])

    def test_bad_rows_reported_with_line_numbers(self):
        text = "id,amount,status\npay_1,abc,captured\n,5,captured\npay_3,7,captured\npay_3,7,captured\n"
        rows, errors = rc.parse_razorpay_csv(text)
        self.assertEqual([r.payment_id for r in rows], ["pay_3"])
        self.assertEqual([n for n, _ in errors], [2, 3, 5])

    def test_empty_file(self):
        self.assertEqual(rc.parse_razorpay_csv("")[1][0][1], "The file is empty.")

    def test_bad_unit(self):
        with self.assertRaises(ValueError):
            rc.parse_razorpay_csv("id,amount,status\n", amount_unit="dollars")

    def test_api_items(self):
        items = [{"id": "pay_1", "order_id": "order_1", "amount": 500000, "amount_refunded": 0, "status": "captured",
                  "method": "upi", "fee": 11800, "tax": 1800, "created_at": 1785924672,
                  "notes": {"kind": "campus_fee"}}]
        g = rc.from_api_items(items)[0]
        self.assertEqual((g.amount, g.fee, g.tax, g.kind), (D("5000.00"), D("118.00"), D("18.00"), "campus_fee"))

    def test_parse_date_variants(self):
        self.assertEqual(rc.parse_date("2026-08-05T10:00:00Z"), date(2026, 8, 5))
        self.assertEqual(rc.parse_date("05 Aug 2026"), date(2026, 8, 5))
        self.assertIsNone(rc.parse_date("garbage"))


class ExportTests(unittest.TestCase):
    def test_formula_injection_guard(self):
        for bad in ("=1+1", "+91", "-cmd", "@SUM(A1)", "\tx"):
            self.assertTrue(fx.safe_text(bad).startswith("'"))
        self.assertEqual(fx.safe_text("Ravi"), "Ravi")

    def test_numbers_not_mangled(self):
        self.assertEqual(fx.cell(D("-500")), "-500.00")
        self.assertEqual(fx.cell(-5), -5)
        self.assertEqual(fx.cell(None), "")
        self.assertEqual(fx.cell(date(2026, 8, 5)), "2026-08-05")
        self.assertEqual(fx.cell(True), "yes")

    def test_csv_bom_and_escaping(self):
        data = fx.csv_bytes(["Name", "Note"], [["Ravi, K", '=HYPERLINK("x")'], ["राधा", "ok"]])
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        text = data.decode("utf-8-sig")
        self.assertIn('"Ravi, K"', text)
        self.assertIn("'=HYPERLINK", text)
        self.assertIn("राधा", text)

    def test_row_cap(self):
        with self.assertRaises(fx.TooManyRows):
            fx.csv_bytes(["a"], ([i] for i in range(fx.MAX_ROWS + 1)))

    def test_xlsx_roundtrip(self):
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            self.skipTest("openpyxl not installed")
        import io
        from openpyxl import load_workbook
        blob = fx.xlsx_bytes("Dues", ["Name", "Due"], [["=evil()", D("10.5")]])
        ws = load_workbook(io.BytesIO(blob)).active
        self.assertEqual(ws["A2"].value, "'=evil()")
        self.assertEqual(ws["B2"].value, "10.50")


if __name__ == "__main__":
    unittest.main()
