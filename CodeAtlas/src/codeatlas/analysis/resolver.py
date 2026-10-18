"""Turn per-file extraction into project graph rows with explicit provenance.

Resolution rules (deterministic, conservative):

IMPORTS  File->File when the module maps to a scanned file (relative imports are
         resolved by path; absolute imports via a module index built from package
         structure). Otherwise File->Module with kind 'external' (third-party/stdlib,
         named by top-level package) or 'unresolved' (looks internal but no file found).
         One relationship per (file, target); every statement line and imported name
         is kept in `lines` / `names`, with `count` = number of statements.

CALLS    Function->Function, one relationship per (caller, callee). Each call site is
         kept: `lines[i]`, `confidences[i]`, `resolutions[i]` are parallel lists.
         confidence 'exact'      : local/nested def, same-file def, explicitly imported
                                   name, module attribute of an imported project module,
                                   self/cls method (incl. resolved base classes), or a
                                   class constructor (`__init__`).
         confidence 'name_match' : bare-name call whose only project-wide candidate is a
                                   single top-level function/class with that name.
         Ambiguous, external, builtin and unknown-receiver calls create no edge; they are
         counted and listed in Function.unresolved_calls.
"""

import builtins
import posixpath
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from codeatlas import ids
from codeatlas.models import (
    FileAnalysis, GraphRows, ImportStmt, NodeRow, RelRow, ResolutionStats, ScanResult, Symbol,
)

BUILTINS = set(dir(builtins))
JS_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
JS_ASSET_EXTENSIONS = {".css", ".scss", ".sass", ".less", ".svg", ".png", ".jpg", ".jpeg",
                       ".gif", ".webp", ".json", ".html", ".woff", ".woff2", ".ttf"}


def _family(language: str) -> str:
    """JavaScript and TypeScript share one namespace, so a .ts file can import a .js file."""
    return "python" if language == "python" else "javascript"


def _gkey(analysis: FileAnalysis, name: str) -> str:
    return f"{_family(analysis.language)}:{name}"


def _ctor(analysis: FileAnalysis) -> str:
    """Name of a class's constructor method in this language."""
    return "__init__" if analysis.language == "python" else "constructor"


def _package_name(spec: str) -> str:
    """'@scope/pkg/sub' -> '@scope/pkg'; 'pkg/sub' -> 'pkg'."""
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") and len(parts) > 1 else parts[0]
MAX_UNRESOLVED_LISTED = 30
MAX_INHERIT_DEPTH = 5


@dataclass
class _Binding:
    kind: str  # "module" (project file), "symbol" (name inside a project file), "external"
    path: str | None = None
    symbol: str | None = None


@dataclass
class _FileCtx:
    analysis: FileAnalysis
    module: str
    root: tuple[str, ...]  # source root directory parts
    bindings: dict[str, _Binding] = field(default_factory=dict)
    by_qualname: dict[str, Symbol] = field(default_factory=dict)


class _Resolver:
    def __init__(self, scan: ScanResult, analyses: list[FileAnalysis], pid: str, name: str):
        self.pid, self.name, self.scan = pid, name, scan
        self.paths = {a.path for a in analyses}
        self.ctx: dict[str, _FileCtx] = {}
        self.module_index: dict[str, list[str]] = defaultdict(list)
        self.stats = ResolutionStats()
        for a in analyses:
            module, root = self._module_name(a.path)
            ctx = _FileCtx(a, module, root, by_qualname={s.qualname: s for s in a.symbols})
            self.ctx[a.path] = ctx
            if a.language == "python":  # JS/TS imports are resolved by path, not dotted name
                if module:
                    self.module_index[module].append(a.path)
                full = a.path[:-3].replace("/", ".")
                full = full[: -len(".__init__")] if full.endswith(".__init__") else full
                if full and full != module:
                    self.module_index[full].append(a.path)

        # symbol tables
        self.top_funcs: dict[str, dict[str, str]] = defaultdict(dict)
        self.top_classes: dict[str, dict[str, str]] = defaultdict(dict)
        self.methods: dict[str, dict[str, str]] = defaultdict(dict)  # class id -> name -> fn id
        self.global_funcs: dict[str, list[str]] = defaultdict(list)
        self.global_classes: dict[str, list[str]] = defaultdict(list)
        self.class_paths: dict[str, tuple[str, Symbol]] = {}
        for path, c in self.ctx.items():
            for s in c.analysis.symbols:
                if s.kind == "class":
                    cid = ids.class_id(pid, path, s.qualname)
                    self.class_paths[cid] = (path, s)
                    if s.qualname == s.name:
                        self.top_classes[path][s.name] = cid
                        self.global_classes[_gkey(c.analysis, s.name)].append(cid)
                else:
                    fid = ids.function_id(pid, path, s.qualname)
                    if s.parent_class:
                        self.methods[ids.class_id(pid, path, s.parent_class)][s.name] = fid
                    elif s.qualname == s.name:
                        self.top_funcs[path][s.name] = fid
                        self.global_funcs[_gkey(c.analysis, s.name)].append(fid)
        self.inherits: dict[str, list[str]] = defaultdict(list)  # class id -> base class ids

    # ------------------------------------------------------------ modules
    def _module_name(self, path: str) -> tuple[str, tuple[str, ...]]:
        parts = path.split("/")
        dirs, stem = parts[:-1], parts[-1][:-3]
        i = len(dirs)
        while i > 0 and "/".join(dirs[:i]) + "/__init__.py" in self.paths:
            i -= 1
        pkg = dirs[i:]
        mod = pkg if stem == "__init__" else pkg + [stem]
        return ".".join(mod), tuple(dirs[:i])

    def _resolve_abs(self, name: str, importer: str) -> str | None:
        cands = self.module_index.get(name, [])
        if len(cands) == 1:
            return cands[0]
        if len(cands) > 1:
            same_root = [p for p in cands if self.ctx[p].root == self.ctx[importer].root]
            if len(same_root) == 1:
                return same_root[0]
        return None

    def _path_module(self, dir_parts: list[str], dotted: str) -> str | None:
        base = "/".join(dir_parts + (dotted.split(".") if dotted else []))
        cands = [f"{base}.py"] if dotted else []
        cands.append(f"{base}/__init__.py" if base else "__init__.py")
        return next((cand for cand in cands if cand in self.paths), None)

    def _is_internal_top(self, name: str) -> bool:
        top = name.split(".")[0]
        return any(m == top or m.startswith(top + ".") for m in self.module_index)

    # ------------------------------------------------------------ imports
    def resolve_imports(self) -> tuple[dict[tuple[str, str], dict], dict[str, str]]:
        """Return {(source file id, target id): agg} and {module id: kind}."""
        edges: dict[tuple[str, str], dict] = {}
        modules: dict[str, str] = {}

        def add(src: str, target_id: str, line: int, names: list[str], resolution: str):
            agg = edges.setdefault((src, target_id), {"lines": [], "names": set(),
                                                      "count": 0, "resolution": resolution})
            agg["lines"].append(line)
            agg["names"].update(names)
            agg["count"] += 1

        for path, c in self.ctx.items():
            src = ids.file_id(self.pid, path)
            dir_parts = path.split("/")[:-1]
            if c.analysis.language != "python":
                for imp in c.analysis.imports:
                    self._js_import(c, src, imp, add, modules)
                continue
            for imp in c.analysis.imports:
                if imp.kind == "import":
                    for mod, alias in imp.names:
                        target = self._resolve_abs(mod, path)
                        if target:
                            c.bindings[alias or mod] = _Binding("module", target)
                            if not alias:  # `import a.b` also binds `a`
                                top = self._resolve_abs(mod.split(".")[0], path)
                                if top:
                                    c.bindings.setdefault(mod.split(".")[0], _Binding("module", top))
                            if target != path:
                                add(src, ids.file_id(self.pid, target), imp.line, [mod], "resolved")
                                self.stats.imports_resolved += 1
                        else:
                            self._external(c, src, mod, imp.line, [mod], alias or mod.split(".")[0],
                                           add, modules)
                    continue

                # from-import
                if imp.level:
                    up = imp.level - 1
                    if up > len(dir_parts):
                        self._unresolved(src, "." * imp.level + imp.module, imp.line,
                                         [n for n, _ in imp.names], add, modules)
                        continue
                    base_dir = dir_parts[: len(dir_parts) - up]
                    module_path = self._path_module(base_dir, imp.module)

                    def sub_path(n: str, base_dir=base_dir, imp=imp):
                        return self._path_module(base_dir, f"{imp.module}.{n}" if imp.module else n)
                else:
                    module_path = self._resolve_abs(imp.module, path)

                    def sub_path(n: str, imp=imp, path=path):
                        return self._resolve_abs(f"{imp.module}.{n}", path)

                leftover: list[str] = []
                for n, alias in imp.names:
                    sp = sub_path(n) if n != "*" else None
                    if sp:  # `from pkg import submodule`
                        c.bindings[alias or n] = _Binding("module", sp)
                        if sp != path:
                            add(src, ids.file_id(self.pid, sp), imp.line, [n], "resolved")
                            self.stats.imports_resolved += 1
                    elif module_path:
                        c.bindings[alias or n] = _Binding("symbol", module_path, n)
                        leftover.append(n)
                    else:
                        c.bindings[alias or n] = _Binding("external")
                        leftover.append(n)
                if not leftover:
                    continue
                if module_path:
                    if module_path != path:
                        add(src, ids.file_id(self.pid, module_path), imp.line, leftover, "resolved")
                        self.stats.imports_resolved += 1
                elif imp.level:
                    self._unresolved(src, "." * imp.level + imp.module, imp.line, leftover, add, modules)
                else:
                    self._external(c, src, imp.module, imp.line, leftover, None, add, modules)
        return edges, modules

    def _js_import(self, c: _FileCtx, src: str, imp: ImportStmt, add, modules):
        """import/export-from/require() of a JavaScript or TypeScript file."""
        names = [n for n, _ in imp.names]
        if not imp.module.startswith("."):  # a package, or a Node built-in
            pkg = _package_name(imp.module)
            mid = ids.module_id(self.pid, pkg)
            modules[mid] = "external"
            add(src, mid, imp.line, names, "external")
            self.stats.imports_external += 1
            for name, alias in imp.names:
                if alias or name != "*":
                    c.bindings[alias or name] = _Binding("external")
            return
        here = posixpath.dirname(c.analysis.path)
        target = self._js_file(posixpath.normpath(posixpath.join(here, imp.module)))
        if target is None and posixpath.splitext(imp.module)[1].lower() in JS_ASSET_EXTENSIONS:
            return  # stylesheets, images and data are not code: no node, no warning
        if target is None:  # relative path that matches no scanned file
            self._unresolved(src, imp.module, imp.line, names, add, modules)
            return
        if target != c.analysis.path:
            add(src, ids.file_id(self.pid, target), imp.line, names, "resolved")
            self.stats.imports_resolved += 1
        for name, alias in imp.names:
            local = alias or name
            if local == "*":  # bare `require("x")` / `import "x"`: nothing to bind
                continue
            if name == "*" or imp.kind == "import":  # namespace import or require()
                c.bindings[local] = _Binding("module", target)
            else:
                c.bindings[local] = _Binding("symbol", target, name)

    def _js_file(self, base: str) -> str | None:
        """Project file for a module path with or without an extension, as Node and
        TypeScript resolve them: exact, + extension, /index + extension, and TypeScript's
        `./x.js` -> `./x.ts` convention."""
        if base in self.paths:
            return base
        for ext in JS_EXTENSIONS:
            if base + ext in self.paths:
                return base + ext
        for ext in JS_EXTENSIONS:
            if f"{base}/index{ext}" in self.paths:
                return f"{base}/index{ext}"
        stem, dot, ext = base.rpartition(".")
        if dot and f".{ext}" in (".js", ".jsx", ".mjs", ".cjs"):
            for alt in JS_EXTENSIONS:
                if stem + alt in self.paths:
                    return stem + alt
        return None

    def _external(self, c, src, mod, line, names, bind_as, add, modules):
        if self._is_internal_top(mod):
            self._unresolved(src, mod, line, names, add, modules)
        else:
            top = mod.split(".")[0]
            mid = ids.module_id(self.pid, top)
            modules[mid] = "external"
            add(src, mid, line, names, "external")
            self.stats.imports_external += 1
        if bind_as:
            c.bindings[bind_as] = _Binding("external")

    def _unresolved(self, src, mod, line, names, add, modules):
        mid = ids.module_id(self.pid, mod)
        modules[mid] = "unresolved"
        add(src, mid, line, names, "unresolved")
        self.stats.imports_unresolved += 1

    # ------------------------------------------------------------ inheritance
    def _lookup_class(self, c: _FileCtx, expr: str) -> tuple[str | None, str]:
        """Resolve a class reference -> (class id, confidence) or (None, reason)."""
        path = c.analysis.path
        if "." not in expr:
            if expr in self.top_classes[path]:
                return self.top_classes[path][expr], "exact"
            b = c.bindings.get(expr)
            if b and b.kind == "symbol" and b.symbol in self.top_classes.get(b.path, {}):
                return self.top_classes[b.path][b.symbol], "exact"
            if (b and b.kind == "external") or (not b and expr in BUILTINS):
                return None, "external"
            if len(self.global_classes.get(_gkey(c.analysis, expr), [])) == 1 and not b:
                return self.global_classes[_gkey(c.analysis, expr)][0], "name_match"
            return None, "unresolved"
        recv, _, attr = expr.rpartition(".")
        b = c.bindings.get(recv)
        if b and b.kind == "module" and attr in self.top_classes.get(b.path, {}):
            return self.top_classes[b.path][attr], "exact"
        return None, "external" if b and b.kind == "external" else "unresolved"

    def resolve_inherits(self) -> dict[tuple[str, str], dict]:
        edges = {}
        for path, c in self.ctx.items():
            for s in c.analysis.symbols:
                if s.kind != "class":
                    continue
                cid = ids.class_id(self.pid, path, s.qualname)
                for base in s.bases:
                    target, conf = self._lookup_class(c, base)
                    if target and target != cid:
                        edges[(cid, target)] = {"confidence": conf}
                        self.inherits[cid].append(target)
                        self.stats.inherits_resolved += 1
                    elif conf != "external":
                        self.stats.inherits_unresolved += 1
        return edges

    def _method_in_hierarchy(self, class_id: str, name: str) -> str | None:
        seen, frontier = set(), [class_id]
        for _ in range(MAX_INHERIT_DEPTH + 1):
            nxt = []
            for cid in frontier:
                if cid in seen:
                    continue
                seen.add(cid)
                if name in self.methods.get(cid, {}):
                    return self.methods[cid][name]
                nxt.extend(self.inherits.get(cid, []))
            frontier = nxt
        return None

    # ------------------------------------------------------------ calls
    def _resolve_call(self, c: _FileCtx, caller: Symbol, callee: str,
                      receiver: str | None) -> tuple[str | None, str, str]:
        """-> (target fn id | None, confidence | category, resolution)."""
        path = c.analysis.path
        if receiver is None:
            # nested definitions visible from enclosing function scopes
            parts = caller.qualname.split("@")[0].split(".")
            for i in range(len(parts), 0, -1):
                scope = ".".join(parts[:i])
                scope_sym = c.by_qualname.get(scope)
                if scope_sym is None or scope_sym.kind != "function":
                    continue
                inner = c.by_qualname.get(f"{scope}.{callee}")
                if inner is not None and inner.kind == "function":
                    return ids.function_id(self.pid, path, inner.qualname), "exact", "local"
            if callee in self.top_funcs[path]:
                return self.top_funcs[path][callee], "exact", "same_file"
            if callee in self.top_classes[path]:
                init = self._method_in_hierarchy(self.top_classes[path][callee], _ctor(c.analysis))
                return (init, "exact", "constructor") if init else (None, "unresolved", "")
            b = c.bindings.get(callee)
            if b is not None:
                if b.kind == "symbol":
                    if b.symbol in self.top_funcs.get(b.path, {}):
                        return self.top_funcs[b.path][b.symbol], "exact", "import"
                    if b.symbol in self.top_classes.get(b.path, {}):
                        init = self._method_in_hierarchy(self.top_classes[b.path][b.symbol], _ctor(c.analysis))
                        if init:
                            return init, "exact", "constructor"
                    return None, "unresolved", ""
                return None, "external" if b.kind == "external" else "unresolved", ""
            if callee in BUILTINS:
                return None, "builtin", ""
            funcs = self.global_funcs.get(_gkey(c.analysis, callee), [])
            classes = self.global_classes.get(_gkey(c.analysis, callee), [])
            if len(funcs) + len(classes) > 1:
                return None, "ambiguous", ""
            if len(funcs) == 1:
                return funcs[0], "name_match", "global_name"
            if len(classes) == 1:
                init = self._method_in_hierarchy(classes[0], _ctor(c.analysis))
                if init:
                    return init, "name_match", "global_name"
            return None, "unresolved", ""

        if receiver in ("self", "cls", "this") and caller.parent_class:
            cid = ids.class_id(self.pid, path, caller.parent_class)
            target = self._method_in_hierarchy(cid, callee)
            return (target, "exact", "self") if target else (None, "unresolved", "")
        if receiver == "<expr>":
            return None, "unresolved", ""
        b = c.bindings.get(receiver)
        if b is not None and b.kind == "module":
            if callee in self.top_funcs.get(b.path, {}):
                return self.top_funcs[b.path][callee], "exact", "module_attr"
            if callee in self.top_classes.get(b.path, {}):
                init = self._method_in_hierarchy(self.top_classes[b.path][callee], _ctor(c.analysis))
                if init:
                    return init, "exact", "constructor"
            return None, "unresolved", ""
        if b is not None and b.kind == "external":
            return None, "external", ""
        if receiver.split(".")[0] in c.bindings and c.bindings[receiver.split(".")[0]].kind == "external":
            return None, "external", ""
        cls_id, conf = self._lookup_class(c, receiver)
        if cls_id and conf == "exact":
            target = self._method_in_hierarchy(cls_id, callee)
            if target:
                return target, "exact", "class_attr"
        return None, "unresolved", ""

    def resolve_calls(self) -> tuple[dict[tuple[str, str], dict], dict[str, dict]]:
        edges: dict[tuple[str, str], dict] = {}
        per_fn: dict[str, dict] = defaultdict(
            lambda: {"sites": 0, "unresolved": set(), "external": set()})
        for path, c in self.ctx.items():
            for call in c.analysis.calls:
                caller = c.by_qualname.get(call.caller)
                if caller is None:
                    continue
                caller_id = ids.function_id(self.pid, path, caller.qualname)
                per_fn[caller_id]["sites"] += 1
                target, conf, how = self._resolve_call(c, caller, call.callee, call.receiver)
                if target is None:
                    setattr(self.stats, f"calls_{conf}", getattr(self.stats, f"calls_{conf}") + 1)
                    expr = f"{call.receiver}.{call.callee}" if call.receiver else call.callee
                    if conf == "external":
                        per_fn[caller_id]["external"].add(expr)
                    elif conf != "builtin":
                        per_fn[caller_id]["unresolved"].add(expr)
                    continue
                setattr(self.stats, f"calls_{conf}", getattr(self.stats, f"calls_{conf}") + 1)
                agg = edges.setdefault((caller_id, target), {"sites": []})
                agg["sites"].append((call.line, conf, how))
        return edges, per_fn


def _sym_props(path: str, s: Symbol) -> dict:
    base = {
        "name": s.name, "qualname": s.qualname, "file_path": path,
        "start_line": s.start_line, "end_line": s.end_line,
        "docstring": s.docstring, "decorators": s.decorators,
    }
    if s.kind == "class":
        base["bases"] = s.bases
    else:
        base.update(is_method=s.parent_class is not None, is_async=s.is_async, params=s.params)
    return base


def build_graph(scan: ScanResult, analyses: list[FileAnalysis], pid: str,
                project_name: str) -> tuple[GraphRows, ResolutionStats]:
    r = _Resolver(scan, analyses, pid, project_name)
    import_edges, modules = r.resolve_imports()
    inherit_edges = r.resolve_inherits()
    call_edges, per_fn = r.resolve_calls()

    files, classes, functions = [], [], []
    contains, defines_class, defines_fn, has_method = [], [], [], []
    for a in sorted(analyses, key=lambda a: a.path):
        c = r.ctx[a.path]
        fid = ids.file_id(pid, a.path)
        files.append(NodeRow(id=fid, props={
            "path": a.path, "name": posixpath.basename(a.path), "module": c.module,
            "language": a.language, "sha256": a.sha256, "size_bytes": a.size_bytes, "loc": a.loc,
            "docstring": a.docstring, "has_syntax_errors": a.has_syntax_errors,
            "symbol_count": len(a.symbols), "import_count": len(a.imports),
        }))
        contains.append(RelRow(source=pid, target=fid))
        for s in a.symbols:
            if s.kind == "class":
                sid = ids.class_id(pid, a.path, s.qualname)
                classes.append(NodeRow(id=sid, props=_sym_props(a.path, s)))
                defines_class.append(RelRow(source=fid, target=sid))
            else:
                sid = ids.function_id(pid, a.path, s.qualname)
                info = per_fn.get(sid, {"sites": 0, "unresolved": set(), "external": set()})
                props = _sym_props(a.path, s)
                props.update(call_sites=info["sites"],
                             unresolved_calls=sorted(info["unresolved"])[:MAX_UNRESOLVED_LISTED],
                             external_calls=sorted(info["external"])[:MAX_UNRESOLVED_LISTED])
                functions.append(NodeRow(id=sid, props=props))
                defines_fn.append(RelRow(source=fid, target=sid))
                if s.parent_class:
                    has_method.append(RelRow(source=ids.class_id(pid, a.path, s.parent_class),
                                             target=sid))

    module_rows = [NodeRow(id=mid, props={"name": mid.split(":module:", 1)[1], "kind": kind})
                   for mid, kind in sorted(modules.items())]

    imports_file, imports_module = [], []
    for (src, tgt), agg in sorted(import_edges.items()):
        row = RelRow(source=src, target=tgt, props={
            "lines": sorted(agg["lines"]), "names": sorted(agg["names"]),
            "count": agg["count"], "resolution": agg["resolution"]})
        (imports_module if ":module:" in tgt else imports_file).append(row)

    calls = []
    for (src, tgt), agg in sorted(call_edges.items()):
        sites = sorted(agg["sites"])
        confs = [s[1] for s in sites]
        calls.append(RelRow(source=src, target=tgt, props={
            "lines": [s[0] for s in sites], "confidences": confs,
            "resolutions": [s[2] for s in sites], "count": len(sites),
            "confidence": "exact" if "exact" in confs else "name_match"}))

    inherits = [RelRow(source=s, target=t, props=p) for (s, t), p in sorted(inherit_edges.items())]

    project = NodeRow(id=pid, props={
        "name": project_name, "root_path": scan.root, "languages": sorted({a.language for a in analyses}),
        "file_count": len(files), "analyzed_at": datetime.now(timezone.utc).isoformat(),
    })
    rows = GraphRows(
        project=project,
        nodes={"File": files, "Module": module_rows, "Class": classes, "Function": functions},
        rels={"CONTAINS": contains, "DEFINES_CLASS": defines_class, "DEFINES_FUNCTION": defines_fn,
              "HAS_METHOD": has_method, "IMPORTS_FILE": imports_file,
              "IMPORTS_MODULE": imports_module, "INHERITS": inherits, "CALLS": calls},
    )
    return rows, r.stats
