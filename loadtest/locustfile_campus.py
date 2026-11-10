"""
loadtest/locustfile_campus.py — campus module at school scale (1000+ students).

Complements locustfile.py (feed / chat / live join). This one hits the REST list/detail endpoints that
used to do N+1 queries (fee invoices, enrollments, parent links, assignments, concessions) plus the
daily teacher/student/parent screens, with realistic role mix:

    student 70 %  ·  parent 20 %  ·  teacher 8 %  ·  admin 2 %

    python manage.py seed_campus_loadtest --students 1200 --confirm-staging --out loadtest/campus_fixtures.json
    locust -f loadtest/locustfile_campus.py --headless --host https://staging.example.com \\
        -u 1000 -r 20 -t 10m --csv loadtest/out/campus1k --html loadtest/out/campus1k.html
    # smoke first:  -u 10 -r 2 -t 1m

Exit code is NON-ZERO when a threshold below is breached (release gate). 429 = injector mis-configured
(several VUs on one account) — the fixtures give every simulated user its OWN token.
Staging only: Postgres, DEBUG=False, real Redis (see README.md).
"""
import json
import os
import random
import threading
from pathlib import Path

from locust import HttpUser, between, events, task

FIXTURES = Path(os.environ.get("CAMPUS_FIXTURES", Path(__file__).with_name("campus_fixtures.json")))
ADMIN_PAGE_SIZE = int(os.environ.get("ADMIN_PAGE_SIZE", 50))

# Release gate — keys are Locust request names. p95 in ms.
THRESHOLDS = {
    "GET /campus/fee-invoices/ [admin page]": {"p95_ms": 800, "fail_ratio": 0.01},
    "GET /campus/enrollments/ [admin page]": {"p95_ms": 800, "fail_ratio": 0.01},
    "GET /campus/fee-concessions/": {"p95_ms": 600, "fail_ratio": 0.01},
    "GET /campus/parent-links/": {"p95_ms": 600, "fail_ratio": 0.01},
    "GET /campus/assigmentss/": {"p95_ms": 700, "fail_ratio": 0.01},
    "GET /campus/assigmentss/ [paged]": {"p95_ms": 700, "fail_ratio": 0.01},
    "GET /campus/fee-invoices/ [student]": {"p95_ms": 500, "fail_ratio": 0.01},
    "GET /campus/results/ [student]": {"p95_ms": 500, "fail_ratio": 0.01},
    "GET /campus/attendance/ [student]": {"p95_ms": 500, "fail_ratio": 0.01},
    "GET /campus/attendance/day/": {"p95_ms": 600, "fail_ratio": 0.01},
    "GET /campus/results/sheet/": {"p95_ms": 900, "fail_ratio": 0.01},
}

_fx, _lock, _idx = None, threading.Lock(), {"student": 0, "parent": 0, "teacher": 0}


def fx():
    global _fx
    if _fx is None:
        if not FIXTURES.exists():
            raise SystemExit(f"{FIXTURES} not found — run `manage.py seed_campus_loadtest --confirm-staging` first.")
        _fx = json.loads(FIXTURES.read_text(encoding="utf-8"))
    return _fx


def pick(kind):
    rows = fx()[kind + "s"]
    with _lock:
        a = rows[_idx[kind] % len(rows)]
        _idx[kind] += 1
    return a


def _auth(user, acct):
    user.client.headers["Authorization"] = f"Bearer {acct['access']}"


def _get(user, url, name, **params):
    with user.client.get(url, params=params or None, name=name, catch_response=True) as r:
        if r.status_code != 200:
            r.failure(f"HTTP {r.status_code}")
            return None
        try:
            return r.json()
        except ValueError:
            r.failure("non-JSON body")
            return None


class StudentUser(HttpUser):
    weight = 70
    wait_time = between(2, 6)

    def on_start(self):
        self.acct = pick("student")
        _auth(self, self.acct)
        self.enr = self.acct["enrollment_id"]
        self.sec = self.acct["section_id"]

    @task(4)
    def my_fees(self):
        _get(self, "/campus/fee-invoices/", "GET /campus/fee-invoices/ [student]")

    @task(3)
    def my_results(self):
        _get(self, "/campus/results/", "GET /campus/results/ [student]", enrollment=self.enr)

    @task(3)
    def my_attendance(self):
        _get(self, "/campus/attendance/", "GET /campus/attendance/ [student]", enrollment=self.enr)

    @task(2)
    def assignments(self):
        _get(self, "/campus/assigmentss/", "GET /campus/assigmentss/", section=self.sec)  # legacy bare list


class ParentUser(HttpUser):
    weight = 20
    wait_time = between(3, 8)

    def on_start(self):
        _auth(self, pick("parent"))

    @task(3)
    def children(self):
        _get(self, "/campus/parent-links/", "GET /campus/parent-links/")

    @task(2)
    def fees(self):
        _get(self, "/campus/fee-invoices/", "GET /campus/fee-invoices/ [student]")


class TeacherUser(HttpUser):
    weight = 8
    wait_time = between(3, 8)

    def on_start(self):
        self.acct = pick("teacher")
        _auth(self, self.acct)
        self.sec = self.acct["section_id"]
        self.subject = random.choice(fx()["subject_ids"])

    @task(3)
    def attendance_day(self):
        from datetime import date, timedelta

        d = (date.today() - timedelta(days=random.randint(1, 15))).isoformat()
        _get(self, "/campus/attendance/day/", "GET /campus/attendance/day/", section=self.sec, date=d)

    @task(2)
    def marks_sheet(self):
        _get(self, "/campus/results/sheet/", "GET /campus/results/sheet/",
             section=self.sec, subject=self.subject, exam_term=fx()["exam_term_id"])

    @task(1)
    def assignments_paged(self):
        _get(self, "/campus/assigmentss/", "GET /campus/assigmentss/ [paged]", section=self.sec, page=1, page_size=25)


class AdminUser(HttpUser):
    weight = 2
    wait_time = between(4, 10)

    def on_start(self):
        _auth(self, fx()["admin"])
        self.campus = fx()["campus_id"]

    def _walk(self, path, name, pages=3, **params):
        url, q = path, {"campus": self.campus, "page_size": ADMIN_PAGE_SIZE, **params}
        for _ in range(pages):
            body = _get(self, url, name, **q)
            if not body or not body.get("next"):
                return
            q["page"] = int(q.get("page", 1)) + 1

    @task(4)
    def invoices(self):
        self._walk("/campus/fee-invoices/", "GET /campus/fee-invoices/ [admin page]")

    @task(3)
    def enrollments(self):
        self._walk("/campus/enrollments/", "GET /campus/enrollments/ [admin page]")

    @task(1)
    def concessions(self):
        _get(self, "/campus/fee-concessions/", "GET /campus/fee-concessions/", campus=self.campus)


@events.quitting.add_listener
def _enforce(environment, **_):
    bad = []
    for name, lim in THRESHOLDS.items():
        e = environment.stats.entries.get((name, "GET"))
        if not e or not e.num_requests:
            continue
        if e.get_response_time_percentile(0.95) > lim["p95_ms"]:
            bad.append(f"{name}: p95 {e.get_response_time_percentile(0.95):.0f} ms > {lim['p95_ms']}")
        if e.num_failures / e.num_requests > lim["fail_ratio"]:
            bad.append(f"{name}: {e.num_failures}/{e.num_requests} failed")
    if bad:
        print("\nTHRESHOLDS BREACHED:\n  " + "\n  ".join(bad))
        environment.process_exit_code = 1
