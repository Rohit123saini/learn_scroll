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

SCOPE:
    The four per-app checks above run for every app in
    settings.CONFIG_DRIFT_APPS (`--apps` overrides). The command is fully
    generic (Django app registry + AST/regex scans), so covering another
    app is a one-word change to that list — see settings.py.

T3.5 / R10 — PROJECT-LEVEL CHECKS (run once, not per app):
    5. ALLOWED_HOSTS wildcard — runtime value AND settings.py source. A bare
       "*" (or ".") accepts any Host header: host-header injection, cache /
       password-reset-link poisoning. ERROR when DEBUG is off (or
       --production); WARNING in local dev. The SOURCE scan additionally
       catches an unconditional `ALLOWED_HOSTS = ['*']` that a CI box with
       the env var set would never trip at runtime.
    6. DEBUG — runtime DEBUG=True (ERROR under --production / DJANGO_ENV=
       production, otherwise WARNING), a hard-coded `DEBUG = True`, or a
       truthy env default like os.getenv("DEBUG", "True") (insecure by
       default).
    7. Stale comments — "`X` is NOT confirmed to exist / doesn't exist yet"
       where X is defined now, and comments pointing at a .py file that is
       gone (see core/drift_scan.py). WARNINGs (heuristic). Silence one with
       an inline `# drift: ignore`; acknowledge a backlog with --baseline.

SEVERITY / EXIT CODE (the CI gate):
    Findings are "error" or "warning". Without --strict the command only
    reports. With --strict it exits non-zero when anything NOT in the
    baseline reaches --fail-on (default: warning == fail on any, matching
    the original --strict behaviour; use --fail-on error to gate only on
    errors). Baselines only ever absorb WARNINGs — errors can't be
    baselined, they must be fixed.

WIRING (settings.py):

    CONFIG_DRIFT_APPS = ["user_profile", "core", ...]
    # Optional escape hatches — only add an entry once you've confirmed the
    # flagged thing is fine on purpose, not because the check is wrong:
    CONFIG_DRIFT_ADMIN_SKIP = set()        # {"app_label.ModelName", ...}
    CONFIG_DRIFT_ONDEMAND_TASKS = set()    # {"task_function_name", ...}
    CONFIG_DRIFT_URL_SKIP = set()          # {"app_label.ViewClassName", ...}

USAGE:
    python manage.py check_config_drift
    python manage.py check_config_drift --apps user_profile core tuitionclass
    python manage.py check_config_drift --strict --production          # CI gate
    python manage.py check_config_drift --format json                  # machine-readable
    python manage.py check_config_drift --write-baseline config_drift_baseline.json
    python manage.py check_config_drift --strict --baseline config_drift_baseline.json
    scripts/ci_config_drift.sh                                         # what CI runs

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
      (compare the user_profile entry to the tuitionclass/message entries
      in settings.py) — matching the last path component is what
      actually works against both conventions.
    - This is a drift *detector*, not a fixer. It never edits
      settings.py/admin.py/urls.py for you — it just tells you where to
      look, the same way the audit comments already scattered through
      this codebase do today, except automatically and on every CI run
      instead of once per manual pass.
"""
import ast
import importlib.util
import json
import os
import re
from pathlib import Path

from django.apps import apps as django_apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.drift_scan import (
    Finding,
    apply_baseline,
    scan_settings_source,
    scan_stale_comments,
)

DEFAULT_CONFIG_DRIFT_APPS = ["user_profile", "core"]  # fallback only; settings.CONFIG_DRIFT_APPS wins

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


_PRODUCTION_ENV_NAMES = ("DJANGO_ENV", "ENVIRONMENT", "SENTRY_ENVIRONMENT")


class Command(BaseCommand):
    help = (
        "Detect config drift: throttle scopes missing a rate, periodic-looking "
        "Celery tasks missing a beat schedule entry, unregistered admin models, "
        "APIViews never wired into urls.py (per app, settings.CONFIG_DRIFT_APPS) "
        "PLUS project-level checks: ALLOWED_HOSTS wildcard, DEBUG, stale comments."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apps", nargs="+", default=None,
            help="App labels to check. Defaults to settings.CONFIG_DRIFT_APPS, "
            f"or {DEFAULT_CONFIG_DRIFT_APPS} if that setting isn't defined.",
        )
        parser.add_argument(
            "--strict", action="store_true",
            help="Exit non-zero if any (non-baselined) finding reaches --fail-on (for CI).",
        )
        parser.add_argument(
            "--fail-on", choices=["error", "warning"], default="warning",
            help="With --strict: lowest severity that fails the run (default: warning = any finding).",
        )
        parser.add_argument(
            "--production", action="store_true",
            help="Treat this run as validating a production config: DEBUG=True and a wildcard "
            "ALLOWED_HOSTS become errors. Also implied by DJANGO_ENV/ENVIRONMENT/SENTRY_ENVIRONMENT=production.",
        )
        parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
        parser.add_argument("--baseline", default=None, help="JSON file of acknowledged warning fingerprints.")
        parser.add_argument(
            "--write-baseline", default=None, metavar="PATH",
            help="Write the current WARNING fingerprints to PATH and exit 0 (errors are never baselined).",
        )
        parser.add_argument("--skip-security", action="store_true", help="Skip the ALLOWED_HOSTS / DEBUG checks.")
        parser.add_argument("--skip-stale-comments", action="store_true", help="Skip the stale-comment scan.")

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        target_apps = options["apps"] or getattr(settings, "CONFIG_DRIFT_APPS", DEFAULT_CONFIG_DRIFT_APPS)
        production = options["production"] or self._env_says_production()

        findings = []
        for app_label in target_apps:
            try:
                app_config = django_apps.get_app_config(app_label)
            except LookupError as exc:
                raise CommandError(f"Unknown app label: {app_label!r}") from exc

            app_path = Path(app_config.path)
            raw = []
            raw += self._check_throttle_scopes(app_label, app_path)
            raw += self._check_celery_beat(app_label, app_path)
            raw += self._check_admin_registration(app_label)
            raw += self._check_urls_wiring(app_label, app_path)
            # legacy (kind, app_label, message) tuples -> Finding(error)
            findings += [Finding(kind=k, severity="error", message=m, scope=a, key=m) for k, a, m in raw]

        if not options["skip_security"]:
            findings += self._check_allowed_hosts(production)
            findings += self._check_debug(production)
            findings += self._check_settings_source()
        if not options["skip_stale_comments"]:
            findings += self._check_stale_comments()

        if options["write_baseline"]:
            notes = {f.fingerprint: f"{f.kind}: {f.file or f.scope} — {f.key}"
                     for f in findings if f.severity == "warning"}
            warnings_only = sorted(notes)
            Path(options["write_baseline"]).write_text(
                json.dumps({"version": 1, "fingerprints": warnings_only,
                            "notes": {k: notes[k] for k in warnings_only}}, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            self.stdout.write(self.style.SUCCESS(
                f"Wrote {len(warnings_only)} warning fingerprint(s) to {options['write_baseline']}."
            ))
            errors = [f for f in findings if f.severity == "error"]
            if errors:
                self.stdout.write(self.style.ERROR(f"{len(errors)} error(s) remain and are NOT baselined — fix them."))
            return

        baselined = []
        baseline_path = options["baseline"] or self._default_baseline()
        if baseline_path:
            known = self._load_baseline(baseline_path)
            findings, baselined = apply_baseline(findings, known)

        if options["format"] == "json":
            self.stdout.write(json.dumps(self._as_json(target_apps, production, findings, baselined), indent=2))
        else:
            self._report(target_apps, findings, baselined, production)

        if options["strict"]:
            failing = [f for f in findings if f.severity == "error" or options["fail_on"] == "warning"]
            if failing:
                raise CommandError(f"{len(failing)} config-drift issue(s) found.")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _env_says_production():
        return any(os.environ.get(n, "").strip().lower() in ("production", "prod") for n in _PRODUCTION_ENV_NAMES)

    @staticmethod
    def _default_baseline():
        configured = getattr(settings, "CONFIG_DRIFT_BASELINE", None)
        if configured and Path(configured).exists():
            return str(configured)
        candidate = Path(settings.BASE_DIR) / "config_drift_baseline.json"
        return str(candidate) if candidate.exists() else None

    @staticmethod
    def _load_baseline(path):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CommandError(f"Cannot read baseline {path!r}: {exc}") from exc
        return data.get("fingerprints", []) if isinstance(data, dict) else list(data)

    @staticmethod
    def _as_json(target_apps, production, findings, baselined):
        def row(f):
            return {"kind": f.kind, "severity": f.severity, "scope": f.scope, "file": f.file,
                    "line": f.line, "message": f.message, "fingerprint": f.fingerprint}
        return {
            "apps": list(target_apps), "production": production,
            "errors": sum(f.severity == "error" for f in findings),
            "warnings": sum(f.severity == "warning" for f in findings),
            "baselined": len(baselined),
            "findings": [row(f) for f in findings],
        }

    # ------------------------------------------------------------------
    # Check 5 / 6 / 7 — project-level (T3.5, R10)
    # ------------------------------------------------------------------
    def _check_allowed_hosts(self, production):
        hosts = list(getattr(settings, "ALLOWED_HOSTS", []) or [])
        debug = bool(getattr(settings, "DEBUG", False))
        strict_env = production or not debug
        out = []
        if any(h in ("*", ".") for h in hosts):
            out.append(Finding(
                kind="allowed_hosts_wildcard", severity="error" if strict_env else "warning", scope="settings",
                key="runtime:ALLOWED_HOSTS=*",
                message=(
                    "ALLOWED_HOSTS contains a wildcard ('*') — any Host header is accepted "
                    "(host-header injection, cache and password-reset-link poisoning; Channels' "
                    "AllowedHostsOriginValidator is also defeated). List the real hostnames in the "
                    "ALLOWED_HOSTS env var."
                    + ("" if strict_env else " (Tolerated here only because DEBUG=True.)")
                ),
            ))
        elif not hosts and strict_env:
            out.append(Finding(
                kind="allowed_hosts_empty", severity="warning", scope="settings", key="runtime:ALLOWED_HOSTS=[]",
                message="ALLOWED_HOSTS is empty with DEBUG off — Django will answer every request with 400. "
                        "Set the ALLOWED_HOSTS env var for this environment.",
            ))
        return out

    def _check_debug(self, production):
        if not getattr(settings, "DEBUG", False):
            return []
        return [Finding(
            kind="debug_enabled", severity="error" if production else "warning", scope="settings",
            key="runtime:DEBUG=True",
            message="DEBUG is True — error pages leak source, settings and SQL, and static/CORS "
                    "conveniences switch on. Must be False in production"
                    + ("." if production else " (reported as a warning because this isn't a --production run)."),
        )]

    def _check_settings_source(self):
        module = getattr(settings, "SETTINGS_MODULE", None)
        try:
            spec = importlib.util.find_spec(module) if module else None
        except (ImportError, ValueError):
            spec = None
        if spec is None or not spec.origin:
            return []
        return scan_settings_source(Path(spec.origin))

    def _check_stale_comments(self):
        root = Path(getattr(settings, "CONFIG_DRIFT_STALE_COMMENT_ROOT", None) or settings.BASE_DIR)
        return scan_stale_comments(root)

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
    def _view_sources(self, app_path):
        """Text of every module that can hold views: `views.py` and/or a `views/`
        package (T3.2 split message/ and tuitionclass/ into packages — a plain
        `views.py` read would silently skip them)."""
        texts = []
        single = self._read(app_path / "views.py")
        if single is not None:
            texts.append(single)
        pkg = app_path / "views"
        if pkg.is_dir():
            for module in sorted(pkg.glob("*.py")):
                text = self._read(module)
                if text is not None:
                    texts.append(text)
        return texts

    def _check_urls_wiring(self, app_label, app_path):
        view_texts = self._view_sources(app_path)
        if not view_texts:
            return []

        urls_text = self._read(app_path / "urls.py") or ""
        skip = set(getattr(settings, "CONFIG_DRIFT_URL_SKIP", set()))

        api_view_classes = set()
        for text in view_texts:
            api_view_classes |= self._find_api_view_classes(text)

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
    def _report(self, target_apps, findings, baselined, production):
        self.stdout.write(f"Checked apps: {', '.join(target_apps)} (+ project-level checks)"
                          f"{' [production]' if production else ''}\n")
        if baselined:
            self.stdout.write(f"{len(baselined)} known warning(s) acknowledged by the baseline.")

        if not findings:
            self.stdout.write(self.style.SUCCESS("No config drift found."))
            return

        headings = {
            "allowed_hosts_wildcard": "ALLOWED_HOSTS wildcard (runtime)",
            "allowed_hosts_wildcard_source": "ALLOWED_HOSTS wildcard (settings.py source)",
            "allowed_hosts_empty": "ALLOWED_HOSTS empty",
            "debug_enabled": "DEBUG enabled",
            "debug_hardcoded_true": "DEBUG hard-coded True",
            "debug_unsafe_default": "DEBUG insecure default",
            "throttle_rate_missing": "Throttle scopes with no DEFAULT_THROTTLE_RATES entry",
            "task_not_scheduled": "Tasks with no CELERY_BEAT_SCHEDULE entry",
            "model_not_registered": "Models not registered in admin.py",
            "view_not_wired": "APIViews not referenced in urls.py",
            "stale_comment_symbol": "Stale comments (claim something is missing, but it exists now)",
            "stale_comment_path": "Stale comments (point at a file that no longer exists)",
        }
        by_kind = {}
        for f in findings:
            by_kind.setdefault(f.kind, []).append(f)

        for kind in list(headings) + [k for k in by_kind if k not in headings]:
            entries = by_kind.get(kind)
            if not entries:
                continue
            style = self.style.ERROR if any(e.severity == "error" for e in entries) else self.style.WARNING
            self.stdout.write(style(f"\n{headings.get(kind, kind)}:"))
            for f in entries:
                self.stdout.write(f"  [{f.severity.upper()}][{f.scope}] {f.message}")

        errors = sum(f.severity == "error" for f in findings)
        warnings = len(findings) - errors
        self.stdout.write(self.style.ERROR(f"\n{errors} error(s), {warnings} warning(s)."))
