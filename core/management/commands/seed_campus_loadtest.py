"""
core/management/commands/seed_campus_loadtest.py — school-scale (1000+ students) campus fixtures.

Creates, on a STAGING database only, ONE campus called "LOADTEST campus" with:
  * N students (default 1200) spread over S sections (default 24 -> ~50 per section), enrolled in the
    current session, each with 1 fee invoice (+ a success/pending payment mix), 20 days of attendance
    and marks for 2 subjects in one exam term
  * a parent per ~2 students (CampusParentLink), 1 admin, T teachers (approved subject assignments)
  * a fee concession with grants, so the finance lists have realistic child rows
  * a fixtures JSON with one access token per account (minted through login.token_issuance — same
    reason as seed_loadtest: don't hash/log in 1,000+ times from the injector)

    python manage.py seed_campus_loadtest --students 1200 --confirm-staging --out loadtest/campus_fixtures.json
    python manage.py seed_campus_loadtest --cleanup --confirm-staging

Everything it makes is prefixed `lt_` / "LOADTEST" so --cleanup can find it. Re-running tops up (idempotent).
Fixture accounts get an ADULT date_of_birth when the User model has that field: the platform's minor-safety
rules would otherwise change what the endpoints return and skew the measurement.
"""
import json
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

CAMPUS_NAME = "LOADTEST campus"
STU = "lt_stu_"
PAR = "lt_par_"
STAFF = "lt_cstaff_"
PASSWORD = "LoadTest!2026-not-a-secret"
ATTENDANCE_DAYS = 20


def _has_field(model, name):
    return any(f.name == name for f in model._meta.get_fields())


class Command(BaseCommand):
    help = "Create (or --cleanup) a 1000+ student campus for load testing the campus module."

    def add_arguments(self, parser):
        parser.add_argument("--students", type=int, default=1200)
        parser.add_argument("--sections", type=int, default=24)
        parser.add_argument("--teachers", type=int, default=8)
        parser.add_argument("--out", default="loadtest/campus_fixtures.json")
        parser.add_argument("--cleanup", action="store_true")
        parser.add_argument("--confirm-staging", action="store_true")
        parser.add_argument("--i-know-this-is-production", action="store_true")

    def handle(self, *args, **o):
        if not o["confirm_staging"]:
            raise CommandError("Refusing to run without --confirm-staging (this writes tens of thousands of rows).")
        env = (os.environ.get("DJANGO_ENV") or os.environ.get("ENVIRONMENT") or "").lower()
        if env == "production" and not o["i_know_this_is_production"]:
            raise CommandError("Process is flagged production. Refusing.")
        if o["cleanup"]:
            return self._cleanup()
        if not (1 <= o["students"] <= 20000 and 1 <= o["sections"] <= 400 and 1 <= o["teachers"] <= 100):
            raise CommandError("--students 1..20000, --sections 1..400, --teachers 1..100")
        self._seed(o["students"], o["sections"], o["teachers"], Path(o["out"]))

    # ------------------------------------------------------------------
    def _cleanup(self):
        from campus.models import Campus

        User = get_user_model()
        with transaction.atomic():
            Campus.objects.filter(name=CAMPUS_NAME).delete()
            n = 0
            for p in (STU, PAR, STAFF):
                d, _ = User.objects.filter(username__startswith=p).delete()
                n += d
        self.stdout.write(self.style.SUCCESS(f"Cleanup done ({n} rows incl. cascades from users)."))

    # ------------------------------------------------------------------
    def _users(self, prefix, n, hashed, width=5):
        User = get_user_model()
        have = {u.username: u for u in User.objects.filter(username__startswith=prefix)}
        make = []
        for i in range(1, n + 1):
            name = f"{prefix}{i:0{width}d}"
            if name not in have:
                extra = {"date_of_birth": date(1990, 1, 1)} if _has_field(User, "date_of_birth") else {}
                make.append(User(username=name, email=f"{name}@loadtest.invalid", password=hashed, **extra))
        User.objects.bulk_create(make, batch_size=500)
        by = {u.username: u for u in User.objects.filter(username__startswith=prefix)}
        return [by[f"{prefix}{i:0{width}d}"] for i in range(1, n + 1)]

    def _seed(self, n_students, n_sections, n_teachers, out_path):
        from campus.models import (
            AcademicSession, Attendance, Campus, CampusParentLink, Department, ExamTerm, FeeConcession,
            FeeConcessionGrant, FeeInvoice, FeePayment, FeeStructure, ResultEntry, SchoolClass, Section,
            StaffProfile, StudentEnrollment, Subject, SubjectTeacherassigments,
        )
        from login.token_issuance import issue_tokens_for_user

        hashed = make_password(PASSWORD)  # hash once
        students = self._users(STU, n_students, hashed)
        parents = self._users(PAR, (n_students + 1) // 2, hashed)
        staff_users = self._users(STAFF, 1 + n_teachers, hashed, width=3)
        admin, teachers = staff_users[0], staff_users[1:]
        self.stdout.write(f"{len(students)} students, {len(parents)} parents, {len(staff_users)} staff users ready.")

        today = timezone.localdate()
        with transaction.atomic():
            campus = Campus.objects.filter(name=CAMPUS_NAME).first() or Campus.objects.create(
                name=CAMPUS_NAME, created_by=admin)
            if hasattr(Campus, "VerificationStatus") and _has_field(Campus, "verification_status"):
                Campus.objects.filter(pk=campus.pk).update(verification_status=Campus.VerificationStatus.APPROVED)
            session = AcademicSession.objects.filter(campus=campus, is_current=True).first() or \
                AcademicSession.objects.create(campus=campus, name="LT 2026-27", start_date=date(2026, 6, 1),
                                               end_date=date(2027, 4, 30), is_current=True)
            StaffProfile.objects.get_or_create(campus=campus, user=admin, defaults={"role": StaffProfile.Role.ADMIN})
            t_profiles = [
                StaffProfile.objects.get_or_create(
                    campus=campus, user=t, defaults={"role": StaffProfile.Role.SUBJECT_TEACHER})[0]
                for t in teachers
            ]
            dept = Department.objects.filter(campus=campus).first() or Department.objects.create(campus=campus, name="LT")
            subjects = [Subject.objects.get_or_create(campus=campus, name=n)[0] for n in ("LT Maths", "LT Science")]
            term = ExamTerm.objects.filter(session=session).first() or ExamTerm.objects.create(
                session=session, name="LT Mid-Term", start_date=today - timedelta(days=30), end_date=today - timedelta(days=20))
            cls = SchoolClass.objects.filter(campus=campus, session=session).first() or SchoolClass.objects.create(
                campus=campus, session=session, name="LT 10", department=dept)
            sections = list(Section.objects.filter(school_class=cls).order_by("name"))
            for i in range(len(sections), n_sections):
                sections.append(Section.objects.create(school_class=cls, name=f"LT-{i + 1:03d}", capacity=max(60, n_students // n_sections + 10)))
            for i, sec in enumerate(sections):
                tp = t_profiles[i % len(t_profiles)]
                for sub in subjects:
                    SubjectTeacherassigments.objects.get_or_create(
                        section=sec, subject=sub, staff=tp, defaults={"status": SubjectTeacherassigments.Status.APPROVED})

            # ---- enrollments --------------------------------------------------
            have = {e.student_id: e for e in StudentEnrollment.objects.filter(session=session, student__in=students)}
            new = []
            for i, u in enumerate(students):
                if u.pk not in have:
                    new.append(StudentEnrollment(student=u, section=sections[i % len(sections)], session=session,
                                                 roll_number=str(i // len(sections) + 1)))
            StudentEnrollment.objects.bulk_create(new, batch_size=500)
            enrollments = {e.student_id: e for e in StudentEnrollment.objects.filter(session=session, student__in=students)}
            new_ids = {x.student_id for x in new}
            fresh = [enrollments[u.pk] for u in students if u.pk in new_ids]

            # ---- parents ------------------------------------------------------
            have_links = set(CampusParentLink.objects.filter(campus=campus).values_list("student_id", flat=True))
            CampusParentLink.objects.bulk_create([
                CampusParentLink(campus=campus, student=u, parent=parents[i // 2])
                for i, u in enumerate(students) if u.pk not in have_links
            ], batch_size=500)

            # ---- fees ---------------------------------------------------------
            fs = FeeStructure.objects.filter(campus=campus, session=session).first() or FeeStructure.objects.create(
                campus=campus, session=session, title="LT Term fee", amount=Decimal("12000"), due_date=today + timedelta(days=15))
            invoiced = set(FeeInvoice.objects.filter(fee_structure=fs).values_list("enrollment_id", flat=True))
            invs = [FeeInvoice(enrollment=e, fee_structure=fs, campus=campus, amount_due=Decimal("12000"),
                               base_amount=Decimal("12000")) for e in enrollments.values() if e.pk not in invoiced]
            FeeInvoice.objects.bulk_create(invs, batch_size=500)
            if invs:
                pays = []
                for k, inv in enumerate(invs):
                    pays.append(FeePayment(invoice=inv, amount=Decimal("5000"), payer_role="admin", payment_mode="cash",
                                           status=FeePayment.Status.SUCCESS, paid_at=timezone.now()))
                    if k % 3 == 0:
                        pays.append(FeePayment(invoice=inv, amount=Decimal("2000"), payer_role="admin", payment_mode="cash",
                                               status=FeePayment.Status.PENDING))
                FeePayment.objects.bulk_create(pays, batch_size=500)
            conc = FeeConcession.objects.filter(campus=campus).first() or FeeConcession.objects.create(
                campus=campus, name="LT Sibling 10%", kind="percent", value=Decimal("10"))
            granted = set(FeeConcessionGrant.objects.filter(concession=conc).values_list("enrollment_id", flat=True))
            FeeConcessionGrant.objects.bulk_create([
                FeeConcessionGrant(concession=conc, enrollment=e, granted_by=admin)
                for i, e in enumerate(enrollments.values()) if i % 10 == 0 and e.pk not in granted
            ], batch_size=500)

            # ---- attendance + marks (only for newly created enrollments => idempotent top-up) ----
            att = []
            for e in fresh:
                for d in range(1, ATTENDANCE_DAYS + 1):
                    att.append(Attendance(enrollment=e, date=today - timedelta(days=d),
                                          status="absent" if (d + int(e.roll_number or 0)) % 17 == 0 else "present",
                                          marked_by=teachers[0]))
            Attendance.objects.bulk_create(att, batch_size=1000)
            ResultEntry.objects.bulk_create([
                ResultEntry(enrollment=e, subject=sub, exam_term=term, marks_obtained=Decimal("72"), max_marks=Decimal("100"),
                            entered_by=t_profiles[0], status=ResultEntry.Status.PUBLISHED, published_at=timezone.now())
                for e in fresh for sub in subjects
            ], batch_size=1000)

        def tok(u, **extra):
            t = issue_tokens_for_user(u)
            return {"username": u.username, "user_id": str(u.pk), "access": t["access"], "refresh": t["refresh"], **extra}

        out_path.parent.mkdir(parents=True, exist_ok=True)
        link_by_student = {l.student_id: l.parent_id for l in CampusParentLink.objects.filter(campus=campus)}
        out_path.write_text(json.dumps({
            "generated_at": timezone.now().isoformat(),
            "campus_id": str(campus.pk),
            "session_id": str(session.pk),
            "exam_term_id": str(term.pk),
            "section_ids": [str(s.pk) for s in sections],
            "subject_ids": [str(s.pk) for s in subjects],
            "admin": tok(admin),
            "teachers": [tok(t, section_id=str(sections[i % len(sections)].pk)) for i, t in enumerate(teachers)],
            "students": [tok(u, enrollment_id=str(enrollments[u.pk].pk), section_id=str(enrollments[u.pk].section_id)) for u in students],
            "parents": [tok(p) for p in parents],
        }, indent=1), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(
            f"Wrote {out_path}: campus={campus.pk}, {len(sections)} sections, {len(students)} students. KEEP THIS FILE PRIVATE."))
