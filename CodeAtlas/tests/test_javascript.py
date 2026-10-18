"""JavaScript / TypeScript support: extraction, import resolution and language isolation.

The polyglot fixture mixes .ts, .tsx, .js and .py files so the tests also prove that the
languages resolve their own imports and never link to each other by bare name.
"""

from pathlib import Path

import pytest

from codeatlas.analysis.languages import EXTENSION_LANGUAGES, language_for
from codeatlas.analysis.pipeline import analyze_repository
from codeatlas.analysis.scanner import scan_repository
from codeatlas.config import Settings
from codeatlas.models import ScannedFile

from .conftest import short

POLYGLOT = Path(__file__).parent / "fixtures" / "polyglot_repo"


@pytest.fixture(scope="module")
def polyglot():
    return analyze_repository(str(POLYGLOT), Settings(_env_file=None, neo4j_uri="", gemini_api_key=None))


def _by_path(out):
    return {a.path: a for a in out.analyses}


def _rels(out, variant):
    return {(short(r.source), short(r.target)): r.props for r in out.rows.rels[variant]}


def _extract(tmp_path, name, code):
    from codeatlas.analysis.javascript import extract_javascript
    import hashlib
    path = tmp_path / name
    path.write_text(code)
    data = code.encode()
    scanned = ScannedFile(path=name, abs_path=str(path), size_bytes=len(data),
                          sha256=hashlib.sha256(data).hexdigest())
    return extract_javascript(scanned)


# ---------------------------------------------------------------- language registry

@pytest.mark.parametrize("path, language", [
    ("a.py", "python"), ("a.js", "javascript"), ("a.jsx", "javascript"), ("a.mjs", "javascript"),
    ("a.ts", "typescript"), ("a.tsx", "typescript"), ("src/A.TSX", "typescript"),
])
def test_language_for_known_extensions(path, language):
    assert language_for(path) == language


def test_unknown_extensions_are_not_analyzed():
    assert language_for("README.md") is None
    assert language_for("pkg.d/notes") is None  # a dot in a directory name is not an extension
    assert ".css" not in EXTENSION_LANGUAGES


def test_scanner_skips_declaration_and_minified_bundles(tmp_path):
    for name in ["app.ts", "types.d.ts", "bundle.min.js", "lib.js", "view.tsx"]:
        (tmp_path / name).write_text("export const x = 1;\n")
    scanned = {f.path for f in scan_repository(str(tmp_path), 100, 10_000).files}
    assert scanned == {"app.ts", "lib.js", "view.tsx"}


# ---------------------------------------------------------------- extraction

def test_classes_methods_and_inheritance(tmp_path):
    a = _extract(tmp_path, "m.ts", """
export class Base { constructor(port) {} run() {} }
export class Child extends Base {
  async start() { this.run(); }
}
""")
    kinds = {(s.kind, s.qualname, s.parent_class, s.is_async) for s in a.symbols}
    assert ("class", "Base", None, False) in kinds
    assert ("function", "Base.run", "Base", False) in kinds
    assert ("function", "Child.start", "Child", True) in kinds
    child = next(s for s in a.symbols if s.qualname == "Child")
    assert child.bases == ["Base"]
    assert a.language == "typescript"


def test_functions_arrows_and_nested_definitions(tmp_path):
    a = _extract(tmp_path, "f.js", """
const double = (n) => n * 2;
function outer(x) {
  function inner(y) { return double(y); }
  return inner(x);
}
""")
    names = {s.qualname: s for s in a.symbols}
    assert names["double"].params == ["n"]
    assert "outer.inner" in names
    inner_calls = [c for c in a.calls if c.caller == "outer.inner"]
    assert [(c.callee, c.receiver) for c in inner_calls] == [("double", None)]
    assert a.language == "javascript"


def test_imports_of_every_shape(tmp_path):
    a = _extract(tmp_path, "i.ts", """
import React, { useState as st, memo } from "react";
import * as utils from "./utils";
import "./side-effect";
export { helper } from "./helper";
export * from "./all";
const fs = require("fs");
""")
    by_module = {i.module: i for i in a.imports}
    assert by_module["react"].names == [("default", "React"), ("useState", "st"), ("memo", None)]
    assert by_module["./utils"].names == [("*", "utils")]
    assert by_module["./side-effect"].names == []
    assert by_module["./helper"].names == [("helper", None)]
    assert by_module["./all"].names == [("*", None)]
    assert by_module["fs"].names == [("*", "fs")]


def test_call_receivers_are_recorded(tmp_path):
    a = _extract(tmp_path, "c.js", """
function run() {
  this.a();
  svc.repo.save(1);
  new Thing();
  getThing().ping();
}
""")
    calls = [(c.callee, c.receiver) for c in a.calls]
    assert ("a", "this") in calls
    assert ("save", "svc.repo") in calls
    assert ("Thing", None) in calls
    assert ("ping", "<expr>") in calls


def test_syntax_errors_are_flagged_not_fatal(tmp_path):
    a = _extract(tmp_path, "broken.ts", "export function ok() { return 1 }\nclass { oops(\n")
    assert a.has_syntax_errors
    assert any(s.qualname == "ok" for s in a.symbols)


def test_tsx_grammar_parses_jsx_and_types(tmp_path):
    a = _extract(tmp_path, "view.tsx", """
type Props = { name: string };
export function Greeting({ name }: Props) {
  return <h1>{name.toUpperCase()}</h1>;
}
""")
    assert not a.has_syntax_errors
    assert any(s.qualname == "Greeting" for s in a.symbols)


# ---------------------------------------------------------------- resolution (fixture)

def test_relative_imports_resolve_to_project_files(polyglot):
    files = _rels(polyglot, "IMPORTS_FILE")
    assert files[("file:components/App.tsx", "file:server/app.ts")]["resolution"] == "resolved"
    assert ("file:server/app.ts", "file:services/orders.ts") in files
    assert ("file:services/orders.ts", "file:utils/helpers.js") in files
    # namespace import (`import * as Orders`) is a file edge too
    assert ("file:components/App.tsx", "file:services/orders.ts") in files


def test_stylesheet_imports_are_ignored_not_unresolved(polyglot):
    modules = _rels(polyglot, "IMPORTS_MODULE")
    assert not any("theme.css" in dst for _, dst in modules)
    assert polyglot.stats.imports_unresolved == 0


def test_packages_become_external_modules(polyglot):
    modules = _rels(polyglot, "IMPORTS_MODULE")
    assert ("file:components/App.tsx", "module:react") in modules
    assert ("file:server/app.ts", "module:express") in modules
    assert ("file:utils/helpers.js", "module:path") in modules
    assert modules[("file:utils/helpers.js", "module:path")]["resolution"] == "external"


def test_calls_resolve_through_imports_this_and_same_file(polyglot):
    calls = {(short(r.source), short(r.target)): r.props for r in polyglot.rows.rels["CALLS"]}
    assert ("function:server/app.ts:Server.start", "function:server/app.ts:BaseServer.register") in calls
    assert ("function:services/orders.ts:OrderService.place",
            "function:utils/helpers.js:clean") in calls
    assert ("function:services/orders.ts:OrderService.place",
            "function:services/orders.ts:formatPrice") in calls
    assert ("function:components/App.tsx:App", "function:server/app.ts:boot") in calls


def test_inherited_constructor_is_resolved(polyglot):
    # `new Server(8080)` has no own constructor; the base class constructor is the target
    calls = {(short(r.source), short(r.target)) for r in polyglot.rows.rels["CALLS"]}
    assert ("function:server/app.ts:boot", "function:server/app.ts:BaseServer.constructor") in calls


def test_python_and_javascript_never_link_by_bare_name(polyglot):
    calls = {(short(r.source), short(r.target)) for r in polyglot.rows.rels["CALLS"]}
    # reports.py has its own clean(); it must link to itself and never to helpers.js
    assert ("function:reports.py:run", "function:reports.py:clean") in calls
    assert not any(src.startswith("function:reports.py") and "helpers.js" in dst for src, dst in calls)


def test_file_nodes_record_their_language(polyglot):
    langs = {short(n.id): n.props["language"] for n in polyglot.rows.nodes["File"]}
    assert langs["file:reports.py"] == "python"
    assert langs["file:utils/helpers.js"] == "javascript"
    assert langs["file:server/app.ts"] == "typescript"
    assert polyglot.rows.project.props["languages"] == ["javascript", "python", "typescript"]
