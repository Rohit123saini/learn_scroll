#!/usr/bin/env python3
"""
Static first-party import checker (no Django needed): every `import x.y` / `from x.y import z` that targets a
package in this repo must resolve to a real module, and (best effort) `z` must be defined at module level.
Run after renames / deletions:   python deploy/tools/check_imports.py     (exit 1 on any broken import)
"""
import ast
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FIRST_PARTY = {d for d in os.listdir(ROOT) if os.path.isfile(os.path.join(ROOT, d, "__init__.py"))}
SKIP_DIRS = {"__pycache__", "migrations", "node_modules", ".git"}


def module_file(dotted):
    parts = dotted.split(".")
    base = os.path.join(ROOT, *parts)
    if os.path.isfile(base + ".py"):
        return base + ".py"
    if os.path.isfile(os.path.join(base, "__init__.py")):
        return os.path.join(base, "__init__.py")
    return None


_names_cache = {}


def top_level_names(path):
    """(names, opaque) -- opaque=True when the module uses star-imports / __getattr__ so names can't be judged."""
    if path in _names_cache:
        return _names_cache[path]
    names, opaque = set(), False
    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
    except SyntaxError:
        _names_cache[path] = (names, True)
        return _names_cache[path]
    for node in ast.walk(tree):  # walk: names defined under `if`/`try` at module level count too
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            if node.name == "__getattr__":
                opaque = True
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "*":
                    opaque = True
                names.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
    _names_cache[path] = (names, opaque)
    return _names_cache[path]


def resolve_relative(pkg_of_file, level, module):
    parts = pkg_of_file.split(".") if pkg_of_file else []
    parts = parts[: len(parts) - (level - 1)] if level > 1 else parts
    return ".".join(parts + ([module] if module else []))


def check_file(path, problems):
    rel = os.path.relpath(path, ROOT)
    pkg = os.path.dirname(rel).replace(os.sep, ".")
    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
    except SyntaxError as exc:
        problems.append(f"{rel}: SYNTAX ERROR {exc}")
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in FIRST_PARTY and not module_file(a.name):
                    problems.append(f"{rel}:{node.lineno}: import {a.name} -> module not found")
        elif isinstance(node, ast.ImportFrom):
            target = resolve_relative(pkg, node.level, node.module) if node.level else (node.module or "")
            if not target or target.split(".")[0] not in FIRST_PARTY:
                continue
            f = module_file(target)
            if not f:
                problems.append(f"{rel}:{node.lineno}: from {'.' * node.level}{node.module or ''} import ... -> module not found")
                continue
            names, opaque = top_level_names(f)
            for a in node.names:
                if a.name == "*" or opaque or a.name in names:
                    continue
                if module_file(f"{target}.{a.name}"):  # `from pkg import submodule`
                    continue
                problems.append(f"{rel}:{node.lineno}: '{a.name}' is not defined in {target}")


def main():
    problems = []
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if f.endswith(".py"):
                check_file(os.path.join(dp, f), problems)
    for p in sorted(problems):
        print(p)
    print(f"{len(problems)} problem(s) in {len(FIRST_PARTY)} first-party packages")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
