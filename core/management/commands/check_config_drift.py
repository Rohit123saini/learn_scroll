"""
core/management/commands/check_config_drift.py

F-1 — automated "config-drift" check.

WHY THIS EXISTS:
    This codebase's own audit comments keep re-finding the *same* four
    bug shapes, over and over, in different apps:
        1. A ScopedRateThrottle / custom Throttle sets `throttle_scope`
           (or `scope`) but nobody added a matching key to
           REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] — first hit on that
           endpoint raises ImproperlyConfigured. (Happened 10+ times:
           session_join, coupon_validate, coin_withdrawal,
           chat_reaction, classroom_share, chunked_upload_*, ...)
        2. A `@shared_task` is written and clearly meant to run on a
           schedule (a sweep/reconcile/cleanup/expire job) but never
           gets a CELERY_BEAT_SCHEDULE entry — it just never fires.
           (refresh_stale_enrolled_counts, reconcile_stuck_coin_
           purchases, run_auto_renewals, expire_unclaimed_gifts,
           send_notification_digests all had this exact bug at some
           point per the comments in settings.py.)
        3. A model gets created but never registered in admin.py, so
           nobody can inspect/support it from /admin/.
        4. An APIView subclass gets written but never wired into
           urls.py, so it's dead code nobody can reach.

    Every one of these is a "the fix compiles fine, the bug only shows
    up the first time a real request/tick hits it" class of mistake —
    exactly what a human reviewer skims past and exactly what a cheap
    static check catches for free. This command is that check.

CURRENT SCOPE (deliberate, not a limitation of the approach):
    Only `user_profile` and `core` are checked right now
    (CONFIG_DRIFT_APPS below) — those are the two apps whose source was
    available when this was written. The command itself is fully
    generic (everything is driven off Django's app registry + AST/regex
    scans of whatever app is named), so adding the rest of the apps
    later is a one-line change to CONFIG_DRIFT_APPS — see settings.py.
    Nothing else about the command needs touching when that happens.

WIRING (one-time setup, settings.py):

    CONFIG_DRIFT_APPS = ["user_profile", "core"]  # grow this list later

    # Optional escape hatches — only add an entry here once you've
    # actually confirmed the flagged thing is fine on purpose, not
    # because the check is wrong:
    CONFIG_DRIFT_ADMIN_SKIP = set()        # {"app_label.ModelName", ...}
    CONFIG_DRIFT_ONDEMAND_TASKS = set()    # {"task_function_name", ...}
    CONFIG_DRIFT_URL_SKIP = set()          # {"app_label.ViewClassName", ...}

USAGE:
    python manage.py check_config_drift
    python manage.py check_config_drift --apps user_profile core liveclass
    python manage.py check_config_drift --strict   # nonzero exit if CI should fail

LIMITATIONS (read before trusting a clean run blindly):
    - Throttle-scope and APIView-vs-urls.py checks are regex/AST scans
      of source text, not a running interpreter — dynamically built
      scope strings (an f-string, a variable instead of a literal) won't
      be seen. Every real usage in this codebase so far has been a plain
      string literal, so this is a non-issue today; if that ever
      changes, this check needs to change with it.
    - The Celery check matches on task *function name* only (not full
      dotted path) because this codebase itself is inconsistent about
      whether CELERY_BEAT_SCHEDULE uses "app.tasks.func" or "app.func"
      (compare the user_profile entry to the liveclass/message entries
      in settings.py) — matching the last path component is what
      actually works against both conventions.
    - This is a drift *detector*, not a fixer. It never edits
      settings.py/admin.py/urls.py for you — it just tells you where to
      look, the same way the audit comments already scattered through
      this codebase do today, except automatically and on every CI run
      instead of once per manual pass.
"""
import ast
import re
from pathlib import Path

from django.apps import apps as django_apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

DEFAULT_CONFIG_DRIFT_APPS = ["user_profile", "core"]

# Regex fallback for `throttle_scope = "xxx"` wherever it appears (view
# class body, get_throttles() method body, etc.) — this is the common
# case and a plain attribute name, safe to regex directly.
_THROTTLE_SCOPE_ATTR_RE = re.compile(r"throttle_scope\s*=\s*['\"]([\w\-]+)['\"]")

# Custom `SomeThrottle(...RateThrottle...)` classes set `scope = "xxx"`
# instead of `throttle_scope = ...` (see message/throttles.py-style
# files) — only treat a bare `scope = "..."` as a throttle scope when it
# sits inside a class whose bases mention "Throttle", so we don't false-
# positive on an unrelated `scope` variable somewhere else in the app.
_THROTTLE_CLASS_RE = re.compile(
    r"class\s+\w+\([^)]*Throttle[^)]*\):(?P<body>.*?)(?=\nclass\s|\Z)",
    re.DOTALL,
)
_BARE_SCOPE_RE = re.compile(r"^\s*scope\s*=\s*['\"]([\w\-]+)['\"]", re.MULTILINE)


class Command(BaseCommand):
    help = (
        "Detect config drift: throttle scopes missing a rate, periodic-"
        "looking Celery tasks missing a beat schedule entry, unregistered "
        "admin models, and APIViews never wired into urls.py. Scope is "
        "settings.CONFIG_DRIFT_APPS (default: user_profile, core)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apps",
            nargs="+",
            default=None,
            help="App labels to check. Defaults to settings.CONFIG_DRIFT_APPS, "
            f"or {DEFAULT_CONFIG_DRIFT_APPS} if that setting isn't defined.",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit with a nonzero status if any drift is found (for CI).",
        )

    def handle(self, *args, **options):
        target_apps = options["apps"] or getattr(
            settings, "CONFIG_DRIFT_APPS", DEFAULT_CONFIG_DRIFT_APPS
        )

        issues = []
        for app_label in target_apps:
            try:
                app_config = django_apps.get_app_config(app_label)
            except LookupError as exc:
                raise CommandError(f"Unknown app label: {app_label!r}") from exc

            app_path = Path(app_config.path)
            issues += self._check_throttle_scopes(app_label, app_path)
            issues += self._check_celery_beat(app_label, app_path)
            issues += self._check_admin_registration(app_label)
            issues += self._check_urls_wiring(app_label, app_path)

        self._report(target_apps, issues)

        if options["strict"] and issues:
            raise CommandError(f"{len(issues)} config-drift issue(s) found.")

    # ------------------------------------------------------------------
    # Check 1 — throttle scope -> DEFAULT_THROTTLE_RATES
    # ------------------------------------------------------------------
    def _check_throttle_scopes(self, app_label, app_path):
        configured_rates = set(
            settings.REST_FRAMEWORK.get("DEFAULT_THROTTLE_RATES", {})
        )
        found_scopes = {}  # scope -> relative file path (first seen)

        for py_file in app_path.rglob("*.py"):
            text = self._read(py_file)
            if text is None:
                continue

            for match in _THROTTLE_SCOPE_ATTR_RE.finditer(text):
                found_scopes.setdefault(match.group(1), py_file.relative_to(app_path))

            for class_match in _THROTTLE_CLASS_RE.finditer(text):
                scope_match = _BARE_SCOPE_RE.search(class_match.group("body"))
                if scope_match:
                    found_scopes.setdefault(scope_match.group(1), py_file.relative_to(app_path))

        issues = []
        for scope, rel_path in sorted(found_scopes.items()):
            if scope not in configured_rates:
                issues.append(
                    (
                        "throttle_rate_missing",
                        app_label,
                        f"throttle_scope={scope!r} (in {app_label}/{rel_path}) has no "
                        f"entry in REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'] — the first "
                        f"request hitting it will raise ImproperlyConfigured.",
                    )
                )
        return issues

    # ------------------------------------------------------------------
    # Check 2 — @shared_task -> CELERY_BEAT_SCHEDULE
    # ------------------------------------------------------------------
    def _check_celery_beat(self, app_label, app_path):
        tasks_file = app_path / "tasks.py"
        text = self._read(tasks_file)
        if text is None:
            return []

        task_names = self._find_shared_task_names(text)
        if not task_names:
            return []

        beat_schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {}) or {}
        scheduled_last_components = {
            entry["task"].split(".")[-1]
            for entry in beat_schedule.values()
            if "task" in entry
        }
        ondemand_whitelist = set(getattr(settings, "CONFIG_DRIFT_ONDEMAND_TASKS", set()))

        issues = []
        for task_name in sorted(task_names):
            if task_name in scheduled_last_components:
                continue
            if task_name in ondemand_whitelist or f"{app_label}.{task_name}" in ondemand_whitelist:
                continue
            issues.append(
                (
                    "task_not_scheduled",
                    app_label,
                    f"'{app_label}.tasks.{task_name}' is a @shared_task but has no "
                    f"CELERY_BEAT_SCHEDULE entry and isn't in CONFIG_DRIFT_ONDEMAND_TASKS. "
                    f"If it's only ever called via .delay()/.apply_async() from request "
                    f"handling (genuinely on-demand), add {task_name!r} to "
                    f"CONFIG_DRIFT_ONDEMAND_TASKS to silence this. Otherwise it needs a "
                    f"beat entry or it will simply never run on its own.",
                )
            )
        return issues

    def _find_shared_task_names(self, text):
        """AST-parse tasks.py and return the names of every function
        decorated with @shared_task (bare or called with arguments,
        e.g. @shared_task(bind=True))."""
        names = set()
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return names

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                dec_name = decorator
                if isinstance(dec_name, ast.Call):
                    dec_name = dec_name.func
                if isinstance(dec_name, ast.Name) and dec_name.id == "shared_task":
                    names.add(node.name)
                elif isinstance(dec_name, ast.Attribute) and dec_name.attr == "task":
                    # covers @app.task style decorators too
                    names.add(node.name)
        return names

    # ------------------------------------------------------------------
    # Check 3 — model -> admin.site registration
    # ------------------------------------------------------------------
    def _check_admin_registration(self, app_label):
        from django.contrib import admin

        skip = set(getattr(settings, "CONFIG_DRIFT_ADMIN_SKIP", set()))
        registered = set(admin.site._registry.keys())

        issues = []
        for model in django_apps.get_app_config(app_label).get_models():
            if model in registered:
                continue
            if f"{app_label}.{model.__name__}" in skip:
                continue
            issues.append(
                (
                    "model_not_registered",
                    app_label,
                    f"{app_label}.{model.__name__} has no admin.py registration. "
                    f"Add an @admin.register(...) for it, or add "
                    f"'{app_label}.{model.__name__}' to CONFIG_DRIFT_ADMIN_SKIP if that's "
                    f"deliberate (e.g. a pure through-table).",
                )
            )
        return issues

    # ------------------------------------------------------------------
    # Check 4 — APIView subclass -> referenced somewhere in urls.py
    # ------------------------------------------------------------------
    def _check_urls_wiring(self, app_label, app_path):
        views_file = app_path / "views.py"
        views_text = self._read(views_file)
        if views_text is None:
            return []

        urls_text = self._read(app_path / "urls.py") or ""
        skip = set(getattr(settings, "CONFIG_DRIFT_URL_SKIP", set()))

        api_view_classes = self._find_api_view_classes(views_text)

        issues = []
        for class_name in sorted(api_view_classes):
            if class_name in urls_text:
                continue
            if f"{app_label}.{class_name}" in skip:
                continue
            issues.append(
                (
                    "view_not_wired",
                    app_label,
                    f"{app_label}.views.{class_name} looks like an APIView but is never "
                    f"mentioned in {app_label}/urls.py — likely dead code, or wired via "
                    f"another app's urls.py (add '{app_label}.{class_name}' to "
                    f"CONFIG_DRIFT_URL_SKIP if so).",
                )
            )
        return issues

    def _find_api_view_classes(self, text):
        """Return class names that subclass APIView/GenericAPIView (or an
        already-flagged base from this same file) but are NOT ViewSets —
        ViewSets are normally wired through a router.register(...) call
        rather than a literal .as_view() reference, so they're out of
        scope for this specific check."""
        names = set()
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return names

        api_view_bases = {"APIView", "GenericAPIView"}
        known_api_views = set()

        class_defs = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        # Multiple passes so a class inheriting from another view class
        # defined earlier in the same file is also picked up.
        changed = True
        while changed:
            changed = False
            for node in class_defs:
                if node.name in known_api_views or node.name.endswith("ViewSet"):
                    continue
                base_names = {
                    b.id if isinstance(b, ast.Name) else getattr(b, "attr", "")
                    for b in node.bases
                }
                if base_names & (api_view_bases | known_api_views):
                    known_api_views.add(node.name)
                    changed = True

        names |= known_api_views
        return names

    @staticmethod
    def _read(path):
        try:
            return path.read_text(encoding="utf-8")
        except (FileNotFoundError, UnicodeDecodeError):
            return None

    # ------------------------------------------------------------------
    def _report(self, target_apps, issues):
        self.stdout.write(f"Checked apps: {', '.join(target_apps)}\n")

        if not issues:
            self.stdout.write(self.style.SUCCESS("No config drift found."))
            return

        by_kind = {}
        for kind, app_label, message in issues:
            by_kind.setdefault(kind, []).append((app_label, message))

        headings = {
            "throttle_rate_missing": "Throttle scopes with no DEFAULT_THROTTLE_RATES entry",
            "task_not_scheduled": "Tasks with no CELERY_BEAT_SCHEDULE entry",
            "model_not_registered": "Models not registered in admin.py",
            "view_not_wired": "APIViews not referenced in urls.py",
        }

        for kind, heading in headings.items():
            entries = by_kind.get(kind)
            if not entries:
                continue
            self.stdout.write(self.style.WARNING(f"\n{heading}:"))
            for app_label, message in entries:
                self.stdout.write(f"  [{app_label}] {message}")

        self.stdout.write(self.style.ERROR(f"\n{len(issues)} issue(s) found."))