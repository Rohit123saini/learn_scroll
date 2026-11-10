"""T3.10 guards: the cleanups below must stay done."""
import os
import re
from pathlib import Path

from django.apps import apps
from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parent.parent


class DuplicateWsAuthTests(SimpleTestCase):
    """LearnScroll/ws_auth.py is the ONLY JWT websocket middleware (asgi.py uses it)."""

    def test_duplicate_copies_are_gone(self):
        for rel in ("tuitionclass/ws_auth.py", "message/Middleware.py"):
            self.assertFalse((ROOT / rel).exists(), f"{rel} is a dead duplicate of LearnScroll/ws_auth.py -- delete it")

    def test_nothing_imports_the_duplicates_and_asgi_uses_the_shared_one(self):
        bad = re.compile(r"(from|import)\s+(tuitionclass\.ws_auth|message\.Middleware)|from\s+\.(ws_auth|Middleware)\s+import")
        for p in ROOT.rglob("*.py"):
            if "__pycache__" in p.parts or p.name == "tests_housekeeping.py":
                continue
            self.assertIsNone(bad.search(p.read_text(encoding="utf-8", errors="ignore")), f"{p} imports a duplicate ws middleware")
        asgi = (ROOT / "LearnScroll" / "asgi.py").read_text(encoding="utf-8")
        self.assertIn("from LearnScroll.ws_auth import JWTAuthMiddleware", asgi)


class AssignmentsPackageTests(SimpleTestCase):
    """Package is spelled `assignments`; the app LABEL stays `assigments` so DB tables / migrations / FKs are untouched."""

    def test_package_and_label(self):
        cfg = apps.get_app_config("assigments")
        self.assertEqual(cfg.name, "assignments")
        self.assertFalse((ROOT / "assigments").exists(), "old misspelled package directory is back")
        self.assertIsNotNone(apps.get_model("assigments", "assigments"))

    def test_no_stray_duplicate_management_command_at_app_root(self):
        self.assertFalse((ROOT / "assignments" / "send_assignment_due_reminders.py").exists())
        self.assertTrue((ROOT / "assignments" / "management" / "commands" / "send_assignment_due_reminders.py").exists())
