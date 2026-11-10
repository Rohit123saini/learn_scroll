"""
core/drift_scan.py — static (AST / tokenize) scans used by `check_config_drift`.

Deliberately free of any Django import so it can run (and be unit-tested)
without settings, a database, or even Django installed — which is also what
lets a CI job run it in seconds.

Three scans:

  1. `scan_settings_source(path)`   — hazards visible in settings.py's SOURCE,
     independent of whatever environment the check happens to run in:
       * an UNCONDITIONAL module-level `ALLOWED_HOSTS = ['*']`
       * an unconditional `DEBUG = True`, or a DEBUG env default that is truthy
         (`os.getenv("DEBUG", "True")`) — i.e. insecure-by-default.
     The runtime values are checked separately by the management command;
     the source scan matters because a wildcard that only appears when no
     ALLOWED_HOSTS env var is set would otherwise pass on a CI box that
     happens to set one, and ship broken.

  2. `scan_stale_comments(root)`    — comments/docstrings that make a claim the
     code no longer supports:
       * "`X` is NOT confirmed to exist / doesn't exist yet / is missing …"
         where X now IS defined somewhere in the tree.
       * a comment that points at a `some/path.py` that no longer exists
         (unless the comment is clearly about its removal).
     A heuristic detector, tuned for FEW false positives: it only flags the
     "subject immediately before the negative phrase" pattern, ignores very
     short/generic names, and honours an inline `# drift: ignore` escape.

  3. fingerprints — every finding carries a stable fingerprint (no line
     numbers) so a baseline file can acknowledge pre-existing warnings
     without letting new ones in.
"""
from __future__ import annotations

import ast
import hashlib
import io
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional

IGNORE_MARKER = "drift: ignore"

# Directories never scanned (generated / vendored / history).
SKIP_DIRS = {"__pycache__", "migrations", "node_modules", ".git", "venv", ".venv", "env", "staticfiles", "static"}

TRUTHY_DEFAULTS = {"true", "1", "yes", "on", "y", "t"}


@dataclass
class Finding:
    kind: str                    # e.g. "stale_comment_symbol"
    severity: str                # "error" | "warning"
    message: str
    file: str = ""               # path relative to the scanned root
    line: int = 0
    scope: str = ""              # app label / "settings" / "project"
    key: str = field(default="")  # stable identity text for the fingerprint

    @property
    def fingerprint(self) -> str:
        raw = f"{self.kind}|{self.file}|{self.key or self.message}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------
# 1. settings.py source hazards
# --------------------------------------------------------------------------
def _is_wildcard_container(node: ast.AST) -> bool:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return any(isinstance(e, ast.Constant) and e.value == "*" for e in node.elts)
    return False


def _assign_targets(node: ast.stmt) -> list[str]:
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
        return [node.target.id]
    return []


def _getenv_default(node: ast.AST) -> Optional[str]:
    """For `os.getenv("X", "default")` / `os.environ.get(...)` (possibly wrapped
    in a comparison like `== "True"`), return the default string, else None."""
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call) or len(sub.args) < 2:
            continue
        fn = sub.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name in ("getenv", "get") and isinstance(sub.args[1], ast.Constant) and isinstance(sub.args[1].value, str):
            return sub.args[1].value
    return None


def scan_settings_source(path: Path) -> list[Finding]:
    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    rel = path.name
    findings: list[Finding] = []

    # The LAST unconditional (direct child of the module) assignment wins at
    # import time unless a conditional one overrides it, so track it per name.
    last_uncond: dict[str, ast.stmt] = {}
    for stmt in tree.body:
        for name in _assign_targets(stmt):
            last_uncond[name] = stmt

    hosts = last_uncond.get("ALLOWED_HOSTS")
    if hosts is not None and _is_wildcard_container(hosts.value):
        findings.append(Finding(
            kind="allowed_hosts_wildcard_source", severity="error", scope="settings",
            file=rel, line=hosts.lineno, key="ALLOWED_HOSTS=*",
            message=(
                f"{rel}:{hosts.lineno} assigns ALLOWED_HOSTS = ['*'] unconditionally — every deployment "
                f"(production included) accepts any Host header (host-header injection, cache/password-reset "
                f"link poisoning) and the env-driven allowlist is dead code. Read it from the ALLOWED_HOSTS "
                f"env var and only fall back to ['*'] inside `if DEBUG and not os.getenv('ALLOWED_HOSTS')`."
            ),
        ))

    debug = last_uncond.get("DEBUG")
    if debug is not None:
        value = debug.value
        if isinstance(value, ast.Constant) and value.value is True:
            findings.append(Finding(
                kind="debug_hardcoded_true", severity="error", scope="settings", file=rel,
                line=debug.lineno, key="DEBUG=True",
                message=f"{rel}:{debug.lineno} hard-codes DEBUG = True — tracebacks, settings and SQL leak on every error page.",
            ))
        else:
            default = _getenv_default(value)
            if default is not None and default.strip().lower() in TRUTHY_DEFAULTS:
                findings.append(Finding(
                    kind="debug_unsafe_default", severity="error", scope="settings", file=rel,
                    line=debug.lineno, key="DEBUG-default-truthy",
                    message=(
                        f"{rel}:{debug.lineno} DEBUG defaults to {default!r} when the env var is unset — a deploy "
                        f"that forgets DEBUG runs in debug mode. Default it to 'False'."
                    ),
                ))
    return findings


# --------------------------------------------------------------------------
# 2. stale comments
# --------------------------------------------------------------------------
# Only STRONG "this does not exist" disclaimers. Looser phrases ("is missing",
# "does not have") overwhelmingly describe runtime values/arguments, not code,
# and produced mostly false positives when calibrated on this codebase.
_NEG = (
    r"(?:is|are|was|were)?\s*(?:still\s+)?"
    r"(?:"
    r"NOT\s+(?:yet\s+)?(?:confirmed\s+to\s+exist|confirmed\s+to\s+be\s+(?:defined|present))"
    r"|(?:does|do|did)\s*(?:n't|not)\s+exist\s+yet"
    r"|doesn'?t\s+exist\s+yet"
    r"|not\s+yet\s+(?:added|implemented|written|created|defined)"
    r")"
)
_NEG_RE = re.compile(_NEG, re.IGNORECASE)
# `Foo`, `core.models.Notification.NotifType.X`, `some_func()`
_TICKED_RE = re.compile(r"`([A-Za-z_][\w.]*)(?:\(\))?`")
_PATH_RE = re.compile(r"(?<![\w/.])([A-Za-z_][\w\-]*(?:/[\w\-\.]+)+\.py)\b")
_REMOVAL_WORDS = re.compile(
    r"\b(delet\w*|remov\w*|no longer|renam\w*|replac\w*|moved|was\s+(?:at|in)|used\s+to|old\s+(?:copy|file|location)|legacy|deprecat\w*|dead\s+code|duplicate)\b",
    re.IGNORECASE,
)
_MIN_SYMBOL_LEN = 8  # shorter names (`name`, `user`, `save`) are too generic to trust

_GENERIC_NAMES = {
    "settings", "request", "response", "serializer", "queryset", "context", "message",
    "notification", "default", "created", "updated", "deleted", "service", "services",
}


def _looks_like_code_name(name: str) -> bool:
    """snake_case / CamelCase / UPPER_CASE — not a plain lowercase English word."""
    return "_" in name or any(c.isupper() for c in name)


def iter_py_files(root: Path) -> Iterator[Path]:
    for p in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        yield p


def build_symbol_index(root: Path, exclude: Optional[Iterable[Path]] = None) -> set[str]:
    """Every class / function / module- or class-level assigned name defined
    anywhere under `root` (enum members like NotifType.FOO are class-level
    assignments, so they are included)."""
    skip = {Path(e).resolve() for e in (exclude or [])}
    names: set[str] = set()
    for path in iter_py_files(root):
        if path.resolve() in skip:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
    return names


@dataclass
class _Block:
    line: int
    text: str  # comment lines joined with spaces, '#' stripped


def _comment_blocks(source: str) -> list[_Block]:
    """Consecutive full-line / trailing comments merged into blocks."""
    blocks: list[_Block] = []
    last_line = -2
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return blocks
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        text = tok.string.lstrip("#").strip()
        line = tok.start[0]
        if blocks and line == last_line + 1:
            blocks[-1].text += " " + text
        else:
            blocks.append(_Block(line=line, text=text))
        last_line = line
    return blocks


def _docstring_blocks(tree: ast.AST) -> list[_Block]:
    out: list[_Block] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.append(_Block(line=body[0].lineno, text=" ".join(body[0].value.value.split())))
    return out


def _subject_before(text: str, match_start: int) -> Optional[str]:
    """The last backticked identifier within ~140 chars before the negative
    phrase — "`X` is NOT confirmed to exist" -> X (last dotted component)."""
    window = text[max(0, match_start - 140):match_start]
    found = _TICKED_RE.findall(window)
    if not found:
        return None
    return found[-1].split(".")[-1]


def scan_stale_comments(root: Path, symbols: Optional[set[str]] = None) -> list[Finding]:
    root = Path(root)
    findings: list[Finding] = []
    seen: set[tuple] = set()
    py_files = list(iter_py_files(root))
    # A symbol only mentioned by the very comment that disclaims it must not
    # count as "defined" — definitions come from AST, never from comments, so
    # the index is naturally clean of that.
    symbols = symbols if symbols is not None else build_symbol_index(root)

    for path in py_files:
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            tree = None

        blocks = _comment_blocks(source) + (_docstring_blocks(tree) if tree else [])
        for block in blocks:
            if IGNORE_MARKER in block.text.lower():
                continue
            # --- (a) "X is NOT confirmed to exist" but X exists --------------
            for m in _NEG_RE.finditer(block.text):
                subject = _subject_before(block.text, m.start())
                if not subject or len(subject) < _MIN_SYMBOL_LEN or subject.lower() in _GENERIC_NAMES:
                    continue
                if not _looks_like_code_name(subject):
                    continue
                if subject in symbols:
                    k = ("sym", rel, subject)
                    if k in seen:
                        continue
                    seen.add(k)
                    findings.append(Finding(
                        kind="stale_comment_symbol", severity="warning", scope=rel.split("/")[0],
                        file=rel, line=block.line, key=f"symbol:{subject}",
                        message=(
                            f"{rel}:{block.line} says `{subject}` doesn't exist / isn't confirmed, but it is now "
                            f"defined in the tree — update or delete the comment (or add '# {IGNORE_MARKER}')."
                        ),
                    ))
            # --- (b) comment points at a .py file that is gone ----------------
            if _REMOVAL_WORDS.search(block.text):
                continue
            for pm in _PATH_RE.finditer(block.text):
                ref = pm.group(1)
                if any(ch in ref for ch in "*<>{}") or re.search(r"0[xX]{2,}|\.\.\.|/sub/|app/", ref):
                    continue
                if (root / ref).exists() or (root.parent / ref).exists():
                    continue
                # `app/views.py` that became an `app/views/` package (T3.2 split) is
                # not stale — the module still exists, just as a package.
                if ref.endswith(".py") and (root / ref[:-3] / "__init__.py").exists():
                    continue
                # allow "app/sub/file.py" written relative to an app dir
                if any((app / ref).exists() for app in root.iterdir() if app.is_dir()):
                    continue
                k = ("path", rel, ref)
                if k in seen:
                    continue
                seen.add(k)
                findings.append(Finding(
                    kind="stale_comment_path", severity="warning", scope=rel.split("/")[0],
                    file=rel, line=block.line, key=f"path:{ref}",
                    message=f"{rel}:{block.line} refers to `{ref}`, which does not exist in the tree.",
                ))
    findings.sort(key=lambda f: (f.file, f.line, f.kind))
    return findings


# --------------------------------------------------------------------------
# baseline helpers (acknowledge existing warnings, block new ones)
# --------------------------------------------------------------------------
def apply_baseline(findings: list[Finding], baseline: Iterable[str]) -> tuple[list[Finding], list[Finding]]:
    """-> (new_findings, baselined_findings). Errors are NEVER baselined."""
    known = set(baseline)
    new, old = [], []
    for f in findings:
        (old if (f.severity == "warning" and f.fingerprint in known) else new).append(f)
    return new, old


# --------------------------------------------------------------------------
# CLI — a Django-FREE gate:  python core/drift_scan.py
# Runs the two static scans without importing settings (settings.py itself
# raises ImproperlyConfigured on a bad ALLOWED_HOSTS, so the full management
# command can't even start in exactly the situation we most want to flag).
# Exit 1 on any non-baselined finding that reaches --fail-on.
# --------------------------------------------------------------------------
def main(argv=None) -> int:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="Static config-drift scan (settings.py hazards + stale comments).")
    ap.add_argument("--root", default=".", help="backend root to scan (default: cwd)")
    ap.add_argument("--settings", default="LearnScroll/settings.py", help="settings file, relative to --root")
    ap.add_argument("--baseline", default=None, help="baseline JSON (default: <root>/config_drift_baseline.json if present)")
    ap.add_argument("--fail-on", choices=["error", "warning"], default="warning")
    ap.add_argument("--skip-stale-comments", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    findings = scan_settings_source(root / args.settings)
    if not args.skip_stale_comments:
        findings += scan_stale_comments(root)

    baseline_path = Path(args.baseline) if args.baseline else root / "config_drift_baseline.json"
    baselined: list[Finding] = []
    if baseline_path.exists():
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
        known = data.get("fingerprints", []) if isinstance(data, dict) else list(data)
        findings, baselined = apply_baseline(findings, known)

    for f in findings:
        print(f"[{f.severity.upper()}][{f.kind}] {f.message}")
    errors = sum(f.severity == "error" for f in findings)
    warnings = len(findings) - errors
    print(f"static drift scan: {errors} error(s), {warnings} warning(s), {len(baselined)} baselined.")
    failing = [f for f in findings if f.severity == "error" or args.fail_on == "warning"]
    return 1 if failing else 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main())
