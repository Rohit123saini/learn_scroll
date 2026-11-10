#!/usr/bin/env python3
"""X6 -- Python dependency vulnerability scan (wraps `pip-audit`).

    pip install pip-audit
    python deploy/tools/dependency_scan.py                         # scans requirements.txt
    python deploy/tools/dependency_scan.py -r requirements.txt --ignore-file deploy/tools/pip_audit_ignore.txt
    python deploy/tools/dependency_scan.py --from-json report.json # parse an existing `pip-audit --format json` file
    python deploy/tools/dependency_scan.py --fail-on none          # report only, never fail
    python deploy/tools/dependency_scan.py --save-json pip-audit.json   # also keep the raw pip-audit JSON (CI uploads it)

Why a wrapper and not bare `pip-audit`:
  * requirements.txt here holds LOWER BOUNDS (`Django>=5.0`), so the useful question is "does what pip resolves today
    have a known CVE?" - the wrapper runs pip-audit against the resolved set and turns the JSON into one short table.
  * an ignore list with an EXPIRY DATE: a vulnerability you consciously accept (no fix yet, not reachable) is listed
    as `GHSA-xxxx-xxxx-xxxx  2026-12-31  why`. After that date it fails the build again, so "temporary" cannot
    become "forever".
  * exit codes CI can use: 0 clean / only-ignored, 1 findings at or above --fail-on, 2 the scan itself could not run
    (pip-audit missing / network) - a scan that did not run is never reported as "clean".

Pure functions (`parse_pip_audit`, `parse_ignore_file`, `apply_ignores`, `exit_code`) are unit-tested without pip-audit
(deploy/tools/test_dependency_scan.py). Written by reading only - not run by the author.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class Vuln:
    package: str
    version: str
    vuln_id: str
    aliases: Tuple[str, ...] = ()
    fix_versions: Tuple[str, ...] = ()
    description: str = ""

    @property
    def ids(self) -> Tuple[str, ...]:
        return (self.vuln_id, *self.aliases)


@dataclass
class Ignore:
    vuln_id: str
    expires: Optional[dt.date]
    reason: str = ""

    def active(self, today: dt.date) -> bool:
        return self.expires is None or today <= self.expires


@dataclass
class Result:
    open: List[Vuln] = field(default_factory=list)
    ignored: List[Tuple[Vuln, Ignore]] = field(default_factory=list)
    expired: List[Tuple[Vuln, Ignore]] = field(default_factory=list)  # an ignore that ran out -> counts as open


def parse_pip_audit(text: str) -> List[Vuln]:
    """Vulns from `pip-audit --format json` (both the old list shape and the new {"dependencies": [...]} shape)."""
    data = json.loads(text)
    deps = data.get("dependencies", []) if isinstance(data, dict) else data
    out: List[Vuln] = []
    for dep in deps or []:
        for v in dep.get("vulns", []) or []:
            out.append(Vuln(
                package=str(dep.get("name", "?")),
                version=str(dep.get("version", "?")),
                vuln_id=str(v.get("id", "?")),
                aliases=tuple(str(a) for a in v.get("aliases", []) or []),
                fix_versions=tuple(str(x) for x in v.get("fix_versions", []) or []),
                description=str(v.get("description", "") or "")[:200],
            ))
    return out


def parse_ignore_file(text: str) -> List[Ignore]:
    """Lines: `<ID>  [YYYY-MM-DD]  [reason...]`. `#` comments and blank lines are skipped. No date = never expires
    (allowed, but the report flags it)."""
    out: List[Ignore] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 2)
        vid = parts[0]
        expires = None
        reason = ""
        rest = parts[1:]
        if rest:
            try:
                expires = dt.date.fromisoformat(rest[0])
                reason = rest[1] if len(rest) > 1 else ""
            except ValueError:
                reason = " ".join(rest)
        out.append(Ignore(vid, expires, reason.strip()))
    return out


def apply_ignores(vulns: Iterable[Vuln], ignores: Iterable[Ignore], today: Optional[dt.date] = None) -> Result:
    today = today or dt.date.today()
    by_id: Dict[str, Ignore] = {i.vuln_id.upper(): i for i in ignores}
    res = Result()
    for v in vulns:
        hit = next((by_id[i.upper()] for i in v.ids if i.upper() in by_id), None)
        if hit is None:
            res.open.append(v)
        elif hit.active(today):
            res.ignored.append((v, hit))
        else:
            res.expired.append((v, hit))
            res.open.append(v)
    return res


def exit_code(res: Result, fail_on: str = "any") -> int:
    """0 = ok. fail_on: 'any' (default) | 'fixable' (only vulns that have a fixed version) | 'none'."""
    if fail_on == "none":
        return 0
    if fail_on == "fixable":
        return 1 if any(v.fix_versions for v in res.open) else 0
    return 1 if res.open else 0


def render(res: Result) -> str:
    lines: List[str] = []
    if res.open:
        lines.append(f"{len(res.open)} open vulnerability(ies):")
        for v in sorted(res.open, key=lambda x: (x.package.lower(), x.vuln_id)):
            fix = ", ".join(v.fix_versions) or "no fix released"
            lines.append(f"  - {v.package} {v.version}  {v.vuln_id}  (fix: {fix})")
            if v.description:
                lines.append(f"      {v.description}")
    else:
        lines.append("no open vulnerabilities")
    if res.expired:
        lines.append("ignore entries that EXPIRED (re-decide or upgrade):")
        for v, ig in res.expired:
            lines.append(f"  - {v.vuln_id} ({v.package}) expired {ig.expires}  {ig.reason}")
    if res.ignored:
        lines.append(f"{len(res.ignored)} ignored on purpose:")
        for v, ig in res.ignored:
            until = f"until {ig.expires}" if ig.expires else "NO EXPIRY (add a date)"
            lines.append(f"  - {v.vuln_id} ({v.package}) {until}  {ig.reason}")
    return "\n".join(lines)


def run_pip_audit(requirements: str) -> str:
    exe = shutil.which("pip-audit")
    if not exe:
        raise RuntimeError("pip-audit is not installed (pip install pip-audit).")
    # exit code 1 = vulnerabilities found (normal); anything else with empty stdout = the scan itself failed.
    proc = subprocess.run(
        [exe, "-r", requirements, "--format", "json", "--progress-spinner", "off"],
        capture_output=True, text=True, check=False,
    )
    if not proc.stdout.strip():
        raise RuntimeError(f"pip-audit produced no output (exit {proc.returncode}): {proc.stderr.strip()[:300]}")
    return proc.stdout


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-r", "--requirements", default="requirements.txt")
    ap.add_argument("--ignore-file", default="deploy/tools/pip_audit_ignore.txt")
    ap.add_argument("--from-json", help="parse this pip-audit JSON file instead of running pip-audit")
    ap.add_argument("--fail-on", choices=["any", "fixable", "none"], default="any")
    ap.add_argument("--save-json", help="P-B7: write the raw pip-audit JSON to this file (CI artifact)")
    args = ap.parse_args(argv)

    try:
        if args.from_json:
            with open(args.from_json, encoding="utf-8") as fh:
                text = fh.read()
        else:
            text = run_pip_audit(args.requirements)
        vulns = parse_pip_audit(text)
        if args.save_json:
            with open(args.save_json, "w", encoding="utf-8") as out:
                out.write(text)
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"dependency_scan: the scan could not run: {exc}", file=sys.stderr)
        return 2

    try:
        with open(args.ignore_file, encoding="utf-8") as fh:
            ignores = parse_ignore_file(fh.read())
    except OSError:
        ignores = []

    res = apply_ignores(vulns, ignores)
    print(render(res))
    return exit_code(res, args.fail_on)


if __name__ == "__main__":
    sys.exit(main())
