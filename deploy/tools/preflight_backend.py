#!/usr/bin/env python3
"""P-A1b - static backend pre-flight (no Django, no DB, nothing is imported or executed; AST only).

    python3 deploy/tools/preflight_backend.py [--root .] [--json out.json]

Checks (each finding = file:line):
  IMP   `from X import name` / `import X as m; m.name` where X is a LOCAL module/package and `name` does not exist in it
        (catches broken package `__init__` re-exports after a module was split into a package, star-import gaps, renamed functions)
  PATCH `mock.patch("pkg.mod.attr")` strings whose target no longer exists (the usual casualty of a file split)
  URLS  `include("x.urls")` strings that do not resolve
  CFG   dotted paths in settings (INSTALLED_APPS, MIDDLEWARE, ...) and Celery beat `task` strings that do not resolve
  SER   ModelSerializer Meta.fields / read_only_fields / exclude names that the model, the serializer or a reverse relation does not provide
  MIG   model fields vs migrations (field missing / type, null, max_length, unique, blank changed) and migration graph problems
        (two leaf nodes, dependency on a file that does not exist)
It is a heuristic reader, not Django: every finding is a CANDIDATE to confirm with `manage.py makemigrations --check` / the test suite.
Exit 1 when anything is found, 0 when clean.
"""
import argparse
import ast
import json
import os
import re
import sys
from collections import defaultdict

SKIP_DIRS = {"__pycache__", ".git", "node_modules", "venv", ".venv", "staticfiles", "static"}


class Index:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.files = {}      # dotted module -> path
        self.packages = set()
        self.tops = set()
        for d, dirs, fs in os.walk(self.root):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            rel = os.path.relpath(d, self.root)
            parts = [] if rel == "." else rel.split(os.sep)
            if parts and not os.path.exists(os.path.join(d, "__init__.py")):
                dirs[:] = []     # not a package: do not descend (e.g. docs, deploy/ without __init__)
                continue
            for f in fs:
                if not f.endswith(".py"):
                    continue
                if f == "__init__.py":
                    mod = ".".join(parts)
                    if mod:
                        self.files[mod] = os.path.join(d, f)
                        self.packages.add(mod)
                else:
                    self.files[".".join(parts + [f[:-3]])] = os.path.join(d, f)
        for m in self.files:
            self.tops.add(m.split(".")[0])
        self.ast_cache = {}
        self.ns_cache = {}

    def parse(self, path):
        if path not in self.ast_cache:
            try:
                src = open(path, encoding="utf-8", errors="replace").read()
                self.ast_cache[path] = ast.parse(src, path)
            except SyntaxError:
                self.ast_cache[path] = None
        return self.ast_cache[path]

    def is_local(self, dotted):
        return dotted.split(".")[0] in self.tops

    def resolve_rel(self, mod, level, name):
        """module (dotted) of file `mod` -> absolute dotted for a relative import."""
        if level == 0:
            return name
        base = mod.split(".")
        if mod not in self.packages:
            base = base[:-1]
        base = base[: len(base) - (level - 1)] if level > 1 else base
        return ".".join(base + ([name] if name else []))

    def namespace(self, mod, _stack=()):
        """-> (names:set, open:bool). open = names we cannot enumerate (external star import, __getattr__, globals())."""
        if mod in self.ns_cache:
            return self.ns_cache[mod]
        if mod in _stack:
            return set(), True
        path = self.files.get(mod)
        tree = self.parse(path) if path else None
        if tree is None:
            self.ns_cache[mod] = (set(), True)
            return self.ns_cache[mod]
        names, is_open = set(), False
        all_list = None

        def add_target(t):
            if isinstance(t, ast.Name):
                names.add(t.id)
            elif isinstance(t, (ast.Tuple, ast.List)):
                for e in t.elts:
                    add_target(e)

        def visit(body):
            nonlocal is_open, all_list
            for n in body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(n.name)
                    if n.name == "__getattr__":
                        is_open = True
                elif isinstance(n, ast.Assign):
                    for t in n.targets:
                        add_target(t)
                        if isinstance(t, ast.Name) and t.id == "__all__":
                            try:
                                all_list = list(ast.literal_eval(n.value))
                            except Exception:
                                pass
                elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
                    add_target(n.target)
                elif isinstance(n, ast.Import):
                    for a in n.names:
                        names.add((a.asname or a.name.split(".")[0]))
                elif isinstance(n, ast.ImportFrom):
                    for a in n.names:
                        if a.name == "*":
                            tgt = self.resolve_rel(mod, n.level, n.module or "")
                            if tgt in self.files:
                                sub, sub_open = self.namespace(tgt, _stack + (mod,))
                                names.update(x for x in sub if not x.startswith("_"))
                                is_open = is_open or sub_open
                            else:
                                is_open = True
                        else:
                            names.add(a.asname or a.name)
                elif isinstance(n, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                    for attr in ("body", "orelse", "finalbody"):
                        visit(getattr(n, attr, []) or [])
                    for h in getattr(n, "handlers", []) or []:
                        visit(h.body)
                elif isinstance(n, ast.Expr) and isinstance(n.value, ast.Call):
                    f = n.value.func
                    if isinstance(f, ast.Attribute) and f.attr == "update" and isinstance(f.value, ast.Call) and getattr(f.value.func, "id", "") == "globals":
                        is_open = True

        visit(tree.body)
        src_has_globals = "globals()[" in open(path, encoding="utf-8", errors="replace").read()
        if src_has_globals:
            is_open = True
        if all_list is not None:
            names |= set(all_list)
        self.ns_cache[mod] = (names, is_open)
        return self.ns_cache[mod]

    def has_attr(self, mod, name):
        names, is_open = self.namespace(mod)
        if name in names or is_open:
            return True
        return (mod + "." + name) in self.files


def line(path, node, root):
    return f"{os.path.relpath(path, root)}:{getattr(node, 'lineno', 0)}"


def check_imports(ix, out):
    for mod, path in sorted(ix.files.items()):
        tree = ix.parse(path)
        if tree is None:
            out.append(("SYNTAX", os.path.relpath(path, ix.root) + ":1", "file does not parse"))
            continue
        alias_mod = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                tgt = ix.resolve_rel(mod, n.level, n.module or "")
                if not tgt or not ix.is_local(tgt):
                    continue
                if tgt not in ix.files:
                    out.append(("IMP", line(path, n, ix.root), f"module '{tgt}' does not exist"))
                    continue
                for a in n.names:
                    if a.name == "*":
                        continue
                    if not ix.has_attr(tgt, a.name):
                        out.append(("IMP", line(path, n, ix.root), f"'{a.name}' is not defined/re-exported by '{tgt}'"))
                    if (tgt + "." + a.name) in ix.files:
                        alias_mod[a.asname or a.name] = tgt + "." + a.name
            elif isinstance(n, ast.Import):
                for a in n.names:
                    if ix.is_local(a.name):
                        if a.name not in ix.files:
                            out.append(("IMP", line(path, n, ix.root), f"module '{a.name}' does not exist"))
                        elif a.asname:
                            alias_mod[a.asname] = a.name
        if alias_mod:
            for n in ast.walk(tree):
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in alias_mod and isinstance(n.ctx, ast.Load):
                    target = alias_mod[n.value.id]
                    if not ix.has_attr(target, n.attr):
                        out.append(("IMP", line(path, n, ix.root), f"'{n.value.id}.{n.attr}': '{target}' has no '{n.attr}'"))


PATCH_RE = re.compile(r"""patch(?:\.dict)?\(\s*['"]([A-Za-z_][\w\.]*)['"]""")


def resolve_dotted(ix, dotted):
    """-> (ok, why). Longest module prefix, then the attribute chain (class members checked one level)."""
    parts = dotted.split(".")
    for i in range(len(parts), 0, -1):
        m = ".".join(parts[:i])
        if m in ix.files:
            rest = parts[i:]
            if not rest:
                return True, ""
            if not ix.has_attr(m, rest[0]):
                return False, f"'{m}' has no '{rest[0]}'"
            if len(rest) > 1:
                cls = find_class(ix, m, rest[0])
                if cls is not None:
                    members = class_members(cls)
                    if rest[1] not in members and not class_open(cls):
                        return False, f"class '{rest[0]}' in '{m}' has no member '{rest[1]}'"
            return True, ""
    return False, f"no local module for '{dotted}'"


def find_class(ix, mod, name):
    path = ix.files.get(mod)
    tree = ix.parse(path) if path else None
    if tree is None:
        return None
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == name:
            return n
    return None


def class_members(cls):
    s = set()
    for n in cls.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            s.add(n.name)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    s.add(t.id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            s.add(n.target.id)
    return s


def class_open(cls):
    return bool(cls.bases)    # inherited members are not enumerated here


def check_patch_and_cfg(ix, out):
    for mod, path in sorted(ix.files.items()):
        base = os.path.basename(path)
        src = open(path, encoding="utf-8", errors="replace").read()
        if base.startswith("tests") or "/tests/" in path or base.startswith("test_"):
            for m in PATCH_RE.finditer(src):
                d = m.group(1)
                if not ix.is_local(d):
                    continue
                ok, why = resolve_dotted(ix, d)
                if not ok:
                    ln = src[: m.start()].count("\n") + 1
                    out.append(("PATCH", f"{os.path.relpath(path, ix.root)}:{ln}", f"patch target '{d}': {why}"))
        if base.startswith("settings") or base in ("celery.py", "ops_settings.py", "beat_guard.py") or base.startswith("urls"):
            tree = ix.parse(path)
            if tree is None:
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and re.fullmatch(r"[A-Za-z_][\w]*(\.[A-Za-z_]\w*)+", n.value):
                    d = n.value
                    if not ix.is_local(d):
                        continue
                    if d.split(".")[0] not in ix.tops:
                        continue
                    ok, why = resolve_dotted(ix, d)
                    if not ok and ix.has_attr(d.rsplit(".", 1)[0], "__name__") is not None:
                        # urls.py include("app.urls") and settings paths
                        out.append(("CFG" if not base.startswith("urls") else "URLS", line(path, n, ix.root), f"'{d}': {why}"))


# ---------------------------------------------------------------- models / serializers / migrations
FIELD_SUFFIX = ("Field", "ForeignKey", "OneToOneField", "ManyToManyField", "GenericForeignKey", "ImageField", "FileField")


def call_name(c):
    f = c.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def kw(c, name, default=None):
    for k in c.keywords:
        if k.arg == name:
            try:
                return ast.literal_eval(k.value)
            except Exception:
                return "<expr>"
    return default


def has_kw(c, name):
    return any(k.arg == name for k in c.keywords)


def field_sig(c):
    t = call_name(c)
    return {
        "type": t,
        "null": kw(c, "null", False),
        "blank": kw(c, "blank", False),
        "unique": kw(c, "unique", False),
        "max_length": kw(c, "max_length") if has_kw(c, "max_length") else (c.args[0].value if t == "CharField" and False else None),
    }


def collect_models(ix):
    """-> {(app, Model): {'fields': {name: sig}, 'abstract': bool, 'managed': bool, 'bases': [names], 'attrs': set, 'related': set, 'path':, 'line':}}"""
    classes = {}
    for mod, path in ix.files.items():
        parts = mod.split(".")
        if "migrations" in parts or parts[-1].startswith("tests") or parts[-1].startswith("test_") or "tests" in parts:
            continue
        tree = ix.parse(path)
        if tree is None:
            continue
        app = parts[0]
        for n in ast.walk(tree):
            if isinstance(n, ast.ClassDef):
                classes.setdefault(n.name, []).append((app, mod, path, n))
    models = {}

    def is_model(cls, seen=()):
        for b in cls.bases:
            nm = b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "")
            if nm == "Model" and isinstance(b, ast.Attribute):
                return True
            if nm in seen:
                continue
            for (_, _, _, c2) in classes.get(nm, []):
                if is_model(c2, seen + (nm,)):
                    return True
        return False

    for name, lst in classes.items():
        for (app, mod, path, cls) in lst:
            if not is_model(cls):
                continue
            abstract, managed, app_label = False, True, app
            for n in cls.body:
                if isinstance(n, ast.ClassDef) and n.name == "Meta":
                    for m in n.body:
                        if isinstance(m, ast.Assign) and isinstance(m.targets[0], ast.Name):
                            tn = m.targets[0].id
                            try:
                                v = ast.literal_eval(m.value)
                            except Exception:
                                continue
                            if tn == "abstract":
                                abstract = bool(v)
                            elif tn == "managed":
                                managed = bool(v)
                            elif tn == "app_label":
                                app_label = v
                    if any(isinstance(b, ast.Name) and b.id == "Meta" for b in n.bases) is False:
                        pass
            fields = {}
            related = set()
            for n in cls.body:
                if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
                    cn = call_name(n.value)
                    if cn.endswith(FIELD_SUFFIX) and cn != "GenericForeignKey":
                        for t in n.targets:
                            if isinstance(t, ast.Name):
                                fields[t.id] = field_sig(n.value)
                                rn = kw(n.value, "related_name")
                                if isinstance(rn, str):
                                    related.add(rn.rstrip("+"))
            models[(app_label, name, mod)] = {
                "fields": fields, "abstract": abstract, "managed": managed, "path": path, "line": cls.lineno,
                "bases": [b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "") for b in cls.bases],
                "attrs": class_members(cls), "related": related, "node": cls,
            }
    return models


def inherited_fields(models, key, seen=()):
    info = models[key]
    out = dict(info["fields"])
    attrs = set(info["attrs"])
    for b in info["bases"]:
        for k2, i2 in models.items():
            if k2[1] == b and k2 not in seen:
                f2, a2 = inherited_fields(models, k2, seen + (key,))
                for n, s in f2.items():
                    out.setdefault(n, s)
                attrs |= a2
    return out, attrs


def parse_migrations(ix):
    """-> {app: {'state': {Model(lower): {field: sig}}, 'leaves': [...], 'problems': [...]}}"""
    res = {}
    mig_mods = defaultdict(list)
    for mod in ix.files:
        p = mod.split(".")
        if len(p) == 3 and p[1] == "migrations" and p[2] != "__init__":
            mig_mods[p[0]].append(mod)
    for app, mods in mig_mods.items():
        mods.sort()
        names = {m.split(".")[2] for m in mods}
        state = {}
        deps_of = {}
        problems = []
        for m in mods:
            path = ix.files[m]
            tree = ix.parse(path)
            if tree is None:
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.ClassDef) and n.name == "Migration":
                    for b in n.body:
                        if isinstance(b, ast.Assign) and isinstance(b.targets[0], ast.Name):
                            tn = b.targets[0].id
                            if tn == "dependencies" and isinstance(b.value, (ast.List, ast.Tuple)):
                                for e in b.value.elts:
                                    try:
                                        v = ast.literal_eval(e)
                                    except Exception:
                                        continue
                                    if isinstance(v, tuple) and len(v) == 2:
                                        if v[0] == app:
                                            deps_of.setdefault(m.split(".")[2], set()).add(v[1])
                                            if v[1] not in names and v[1] != "__first__":
                                                problems.append((os.path.relpath(path, ix.root) + f":{e.lineno}", f"depends on missing migration {app}.{v[1]}"))
                                        elif v[0] != "auth" and not str(v[0]).startswith("__") and v[0] in ix.tops:
                                            other = {mm.split(".")[2] for mm in ix.files if mm.startswith(v[0] + ".migrations.")}
                                            if v[1] not in other and v[1] != "__first__":
                                                problems.append((os.path.relpath(path, ix.root) + f":{e.lineno}", f"depends on missing migration {v[0]}.{v[1]}"))
                            elif tn == "operations" and isinstance(b.value, (ast.List, ast.Tuple)):
                                for op in b.value.elts:
                                    apply_op(state, op)
            deps_of.setdefault(m.split(".")[2], set())
        depended = set().union(*deps_of.values()) if deps_of else set()
        leaves = sorted(n for n in names if n not in depended)
        res[app] = {"state": state, "leaves": leaves, "problems": problems}
    return res


def apply_op(state, op):
    if not isinstance(op, ast.Call):
        return
    t = call_name(op)
    if t == "CreateModel":
        name = kw(op, "name") if has_kw(op, "name") else (op.args[0].value if op.args and isinstance(op.args[0], ast.Constant) else None)
        fields = {}
        fnode = next((k.value for k in op.keywords if k.arg == "fields"), op.args[1] if len(op.args) > 1 else None)
        if isinstance(fnode, (ast.List, ast.Tuple)):
            for e in fnode.elts:
                if isinstance(e, ast.Tuple) and len(e.elts) == 2 and isinstance(e.elts[0], ast.Constant) and isinstance(e.elts[1], ast.Call):
                    fields[e.elts[0].value] = field_sig(e.elts[1])
        opts = {}
        onode = next((k.value for k in op.keywords if k.arg == "options"), None)
        if isinstance(onode, ast.Dict):
            for k, v in zip(onode.keys, onode.values):
                try:
                    opts[ast.literal_eval(k)] = ast.literal_eval(v)
                except Exception:
                    pass
        if isinstance(name, str):
            state[name.lower()] = {"fields": fields, "opts": opts}
    elif t == "DeleteModel":
        n = kw(op, "name")
        state.pop(str(n).lower(), None)
    elif t == "RenameModel":
        o, nn = kw(op, "old_name"), kw(op, "new_name")
        if str(o).lower() in state:
            state[str(nn).lower()] = state.pop(str(o).lower())
    elif t in ("AddField", "AlterField"):
        mn, fn = kw(op, "model_name"), kw(op, "name")
        fld = next((k.value for k in op.keywords if k.arg == "field"), None)
        if isinstance(fld, ast.Call) and mn and fn:
            state.setdefault(str(mn).lower(), {"fields": {}, "opts": {}})["fields"][fn] = field_sig(fld)
    elif t == "RemoveField":
        mn, fn = kw(op, "model_name"), kw(op, "name")
        state.get(str(mn).lower(), {"fields": {}})["fields"].pop(fn, None)
    elif t == "RenameField":
        mn, o, nn = kw(op, "model_name"), kw(op, "old_name"), kw(op, "new_name")
        f = state.get(str(mn).lower(), {"fields": {}})["fields"]
        if o in f:
            f[nn] = f.pop(o)


def check_migrations(ix, models, out):
    mig = parse_migrations(ix)
    for app, info in mig.items():
        if len(info["leaves"]) > 1:
            out.append(("MIG", f"{app}/migrations", f"{len(info['leaves'])} leaf migrations ({', '.join(info['leaves'])}): needs a merge migration"))
        for loc, why in info["problems"]:
            out.append(("MIG", loc, why))
    for (app, name, mod), m in sorted(models.items()):
        if m["abstract"] or not m["managed"] or app not in mig:
            continue
        fields, _ = inherited_fields(models, (app, name, mod))
        st = mig[app]["state"].get(name.lower())
        loc = os.path.relpath(m["path"], ix.root) + f":{m['line']}"
        if st is None:
            if fields:
                out.append(("MIG", loc, f"model {app}.{name} has no CreateModel in {app}/migrations"))
            continue
        mf = st["fields"]
        for f, sig in fields.items():
            if f not in mf:
                out.append(("MIG", loc, f"{app}.{name}.{f} is in the model but in no migration (AddField missing)"))
                continue
            o = mf[f]
            for key in ("type", "null", "blank", "unique"):
                a, b = sig[key], o[key]
                if "<expr>" in (a, b):
                    continue
                if a != b:
                    out.append(("MIG", loc, f"{app}.{name}.{f}: {key} is {a!r} in the model but {b!r} after migrations"))
            if sig["max_length"] is not None and o["max_length"] is not None and "<expr>" not in (sig["max_length"], o["max_length"]) and sig["max_length"] != o["max_length"]:
                out.append(("MIG", loc, f"{app}.{name}.{f}: max_length {sig['max_length']} in the model, {o['max_length']} in migrations"))
        for f in mf:
            if f not in fields and f != "id":
                # may come from a base class we could not resolve: only report when all bases are known models
                known = all(any(k2[1] == b for k2 in models) or b in ("Model", "object") for b in m["bases"])
                if known:
                    out.append(("MIG", loc, f"{app}.{name}.{f} exists in migrations but not in the model (RemoveField missing)"))


def check_serializers(ix, models, out):
    by_name = defaultdict(list)
    for key in models:
        by_name[key[1]].append(key)
    related_all = set()
    for m in models.values():
        related_all |= m["related"]
    ser_classes = {}
    for mod, path in ix.files.items():
        p = mod.split(".")
        if "migrations" in p or p[-1].startswith("tests") or "tests" in p:
            continue
        tree = ix.parse(path)
        if tree is None:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.ClassDef):
                ser_classes.setdefault(n.name, []).append((path, n))
    for sname, lst in ser_classes.items():
        for path, cls in lst:
            meta = next((n for n in cls.body if isinstance(n, ast.ClassDef) and n.name == "Meta"), None)
            if meta is None:
                continue
            model_name, fields, ro, excl = None, None, None, None
            for m in meta.body:
                if isinstance(m, ast.Assign) and isinstance(m.targets[0], ast.Name):
                    t = m.targets[0].id
                    if t == "model" and isinstance(m.value, ast.Name):
                        model_name = m.value.id
                    elif t == "model" and isinstance(m.value, ast.Attribute):
                        model_name = m.value.attr
                    elif t in ("fields", "read_only_fields", "exclude"):
                        try:
                            v = ast.literal_eval(m.value)
                        except Exception:
                            continue
                        if isinstance(v, (list, tuple)) and all(isinstance(x, str) for x in v):
                            if t == "fields":
                                fields = list(v)
                            elif t == "read_only_fields":
                                ro = list(v)
                            else:
                                excl = list(v)
            if not model_name or model_name not in by_name:
                continue
            declared = class_members(cls)
            # local base serializers add declared fields
            for b in cls.bases:
                bn = b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "")
                for _, bc in ser_classes.get(bn, []):
                    declared |= class_members(bc)
            ok_names = None
            for key in by_name[model_name]:
                f, attrs = inherited_fields(models, key)
                names = set(f) | attrs | {"id", "pk"} | {x + "_id" for x in f}
                ok_names = names if ok_names is None else ok_names | names
            for label, lst2 in (("fields", fields), ("read_only_fields", ro), ("exclude", excl)):
                for nme in lst2 or []:
                    if nme == "__all__" or nme in declared or (ok_names and nme in ok_names) or nme in related_all or nme.endswith("_set"):
                        continue
                    out.append(("SER", f"{os.path.relpath(path, ix.root)}:{cls.lineno}", f"{sname}.Meta.{label}: '{nme}' is not a field/property of {model_name}, not declared on the serializer and not a known reverse relation"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--json")
    ap.add_argument("--only", help="comma list of IMP,PATCH,CFG,URLS,SER,MIG")
    a = ap.parse_args(argv)
    ix = Index(a.root)
    out = []
    check_imports(ix, out)
    check_patch_and_cfg(ix, out)
    models = collect_models(ix)
    check_migrations(ix, models, out)
    check_serializers(ix, models, out)
    if a.only:
        keep = set(a.only.split(","))
        out = [o for o in out if o[0] in keep]
    out = sorted(set(out))
    for k, loc, msg in out:
        print(f"{k:6} {loc}  {msg}")
    print(f"preflight_backend: {len(ix.files)} modules, {len(models)} models, {len(out)} finding(s)", file=sys.stderr)
    if a.json:
        json.dump([{"kind": k, "where": l, "msg": m} for k, l, m in out], open(a.json, "w"), indent=1)
    return 1 if out else 0


if __name__ == "__main__":
    sys.exit(main())
