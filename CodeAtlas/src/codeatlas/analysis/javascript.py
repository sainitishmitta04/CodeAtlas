"""Deterministic JavaScript / TypeScript extraction with tree-sitter.

Same contract as extractor.py: classes, functions and methods (1-based line ranges), import
and re-export statements, `require()` / dynamic `import()`, and call sites. No name
resolution happens here; resolver.py maps relative specifiers to project files.

Grammar per extension: .js/.jsx/.mjs/.cjs use the JavaScript grammar, .ts/.mts/.cts the
TypeScript grammar and .tsx the TSX grammar (JSX + type annotations).
"""

import os

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Parser

from codeatlas.analysis.syntax import (_LOCK, _NEWLINES, _SOURCE, PNode, _end_line, _field,
                                       _line, _materialize, _text)
from codeatlas.models import CallSite, FileAnalysis, ImportStmt, ScannedFile, Symbol

_GRAMMARS = {
    "javascript": Language(tree_sitter_javascript.language()),
    "typescript": Language(tree_sitter_typescript.language_typescript()),
    "tsx": Language(tree_sitter_typescript.language_tsx()),
}
_PARSERS = {name: Parser(lang) for name, lang in _GRAMMARS.items()}
_SUFFIX_GRAMMAR = {".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx"}

CLASS_TYPES = {"class_declaration", "abstract_class_declaration"}
FUNCTION_TYPES = {"function_declaration", "generator_function_declaration"}
FUNCTION_VALUE_TYPES = {"arrow_function", "function_expression", "function", "generator_function"}
IMPORT_TYPES = {"import_statement"}
DECLARATION_TYPES = {"lexical_declaration", "variable_declaration"}
CALL_TYPES = {"call_expression", "new_expression"}


def grammar_for(path: str) -> str:
    return _SUFFIX_GRAMMAR.get(os.path.splitext(path)[1].lower(), "javascript")


def extract_javascript(scanned: ScannedFile, source: bytes | None = None) -> FileAnalysis:
    if source is None:
        with open(scanned.abs_path, "rb") as fh:
            source = fh.read()
    with _LOCK:
        return _JsExtractor(scanned, source).run()


def _string_value(node: PNode | None) -> str | None:
    """The text of a string literal without its quotes."""
    if node is None:
        return None
    raw = _text(node)
    return raw[1:-1] if len(raw) >= 2 and raw[0] in "'\"`" else None


class _JsExtractor:
    def __init__(self, scanned: ScannedFile, source: bytes):
        self.scanned = scanned
        self.source = source
        self.grammar = grammar_for(scanned.path)
        self.symbols: list[Symbol] = []
        self.imports: list[ImportStmt] = []
        self.calls: list[CallSite] = []
        self.module_level_calls = 0
        self._seen_qualnames: set[str] = set()

    def run(self) -> FileAnalysis:
        _SOURCE[0] = self.source
        _NEWLINES[0] = [i for i, b in enumerate(self.source) if b == 10]
        tree = _PARSERS[self.grammar].parse(self.source)
        has_error = tree.root_node.has_error
        root = _materialize(tree)
        del tree
        self._visit(root, prefix="", caller=None, owner_class=None)
        return FileAnalysis(
            path=self.scanned.path,
            sha256=self.scanned.sha256,
            size_bytes=self.scanned.size_bytes,
            loc=self.source.count(b"\n") + (0 if self.source.endswith(b"\n") or not self.source else 1),
            docstring=None,
            has_syntax_errors=has_error,
            symbols=self.symbols,
            imports=self.imports,
            calls=self.calls,
            module_level_calls=self.module_level_calls,
            language="typescript" if self.grammar in ("typescript", "tsx") else "javascript",
        )

    # ------------------------------------------------------------------ walking
    def _visit(self, node: PNode, prefix: str, caller: str | None, owner_class: str | None):
        """prefix: qualname of the enclosing scope ("" at module level); caller: qualname of
        the enclosing function (None at module level); owner_class: qualname of the class
        whose body is being visited (methods are only recognised directly inside it)."""
        t = node.type
        if t in IMPORT_TYPES:
            self._import_statement(node)
            return
        if t == "export_statement":
            if _field(node, "source") is not None:  # re-export: export { a } from "./a"
                self._reexport(node)
                return
            for child in node.children:
                self._visit(child, prefix, caller, owner_class)
            return
        if t in CLASS_TYPES:
            self._class(node, prefix, caller)
            return
        if t == "method_definition" and owner_class is not None:
            self._function(node, prefix, owner_class=owner_class, name_node=_field(node, "name"))
            return
        if t in FUNCTION_TYPES and _field(node, "name") is not None:
            self._function(node, prefix, owner_class=None, name_node=_field(node, "name"))
            return
        if t in DECLARATION_TYPES:
            for decl in node.children:
                if decl.type == "variable_declarator":
                    self._declarator(decl, prefix, caller)
                else:
                    self._visit(decl, prefix, caller, owner_class)
            return
        if t in CALL_TYPES:
            self._call(node, caller)
        for child in node.children:
            self._visit(child, prefix, caller, owner_class)

    def _class(self, node: PNode, prefix: str, caller: str | None):
        name = _text(_field(node, "name"))
        if not name:
            for child in node.children:
                self._visit(child, prefix, caller, None)
            return
        qual = f"{prefix}{name}"
        bases = self._bases(node)
        self._add_symbol(Symbol(kind="class", name=name, qualname=qual, start_line=_line(node),
                                end_line=_end_line(node), bases=bases))
        body = _field(node, "body")
        if body is not None:
            self._visit(body, prefix=f"{qual}.", caller=None, owner_class=qual)

    def _function(self, node: PNode, prefix: str, owner_class: str | None, name_node: PNode | None):
        name = _text(name_node)
        if not name:
            return
        qual = f"{prefix}{name}"
        params_node = _field(node, "parameters")
        self._add_symbol(Symbol(
            kind="function", name=name, qualname=qual, start_line=_line(node),
            end_line=_end_line(node), parent_class=owner_class,
            is_async=any(c.type == "async" for c in node.children),
            params=self._params(params_node),
        ))
        for child in node.children:  # body, default values and nested definitions
            self._visit(child, prefix=f"{qual}.", caller=qual, owner_class=None)

    def _declarator(self, decl: PNode, prefix: str, caller: str | None):
        name_node, value = _field(decl, "name"), _field(decl, "value")
        name = _text(name_node) if name_node is not None and name_node.type == "identifier" else ""
        if value is not None and name and value.type in FUNCTION_VALUE_TYPES:
            qual = f"{prefix}{name}"
            self._add_symbol(Symbol(
                kind="function", name=name, qualname=qual, start_line=_line(decl),
                end_line=_end_line(value), is_async=any(c.type == "async" for c in value.children),
                params=self._params(_field(value, "parameters")),
            ))
            for child in value.children:
                self._visit(child, prefix=f"{qual}.", caller=qual, owner_class=None)
            return
        if value is not None and name and self._is_require(value):
            self._record_import(_line(decl), self._require_spec(value), [(name, None)], kind="from",
                                star_alias=name)
            return
        if value is not None:
            self._visit(value, prefix, caller, None)

    # ------------------------------------------------------------------ details
    def _add_symbol(self, sym: Symbol):
        if sym.qualname in self._seen_qualnames:  # keep first; qualnames must be unique per file
            return
        self._seen_qualnames.add(sym.qualname)
        self.symbols.append(sym)

    def _bases(self, node: PNode) -> list[str]:
        bases: list[str] = []
        heritage = next((c for c in node.children if c.type == "class_heritage"), None)
        if heritage is None:
            return bases
        for child in heritage.children:
            if child.type == "extends_clause":  # TypeScript
                value = _field(child, "value")
                if value is not None:
                    bases.append(_text(value))
            elif child.type in ("identifier", "member_expression", "type_identifier"):  # JavaScript
                bases.append(_text(child))
        return bases

    def _params(self, params: PNode | None) -> list[str]:
        if params is None:
            return []
        names: list[str] = []
        for p in params.children:
            if p.type == "identifier":
                names.append(_text(p))
            elif p.type in ("required_parameter", "optional_parameter"):
                pattern = _field(p, "pattern")
                names.append(_text(pattern) if pattern is not None and pattern.type == "identifier"
                             else "?")
            elif p.type == "assignment_pattern":
                left = _field(p, "left")
                names.append(_text(left) if left is not None and left.type == "identifier" else "?")
            elif p.type in ("rest_pattern", "rest_parameter"):
                names.append("..." + (_text(p).lstrip(".") or "?"))
        return [n for n in names if n]

    def _import_statement(self, node: PNode):
        spec = _string_value(_field(node, "source"))
        if spec is None:
            return
        names: list[tuple[str, str | None]] = []
        clause = next((c for c in node.children if c.type == "import_clause"), None)
        if clause is not None:
            for part in clause.children:
                if part.type == "identifier":  # import Default from "x"
                    names.append(("default", _text(part)))
                elif part.type == "namespace_import":  # import * as ns from "x"
                    ident = next((c for c in part.children if c.type == "identifier"), None)
                    names.append(("*", _text(ident)))
                elif part.type == "named_imports":  # import { a as b } from "x"
                    for spec_node in part.children:
                        if spec_node.type == "import_specifier":
                            alias = _field(spec_node, "alias")
                            names.append((_text(_field(spec_node, "name")),
                                          _text(alias) if alias is not None else None))
        self._record_import(_line(node), spec, names, kind="from")

    def _reexport(self, node: PNode):
        spec = _string_value(_field(node, "source"))
        if spec is None:
            return
        names: list[tuple[str, str | None]] = []
        clause = next((c for c in node.children if c.type == "export_clause"), None)
        if clause is not None:
            for spec_node in clause.children:
                if spec_node.type == "export_specifier":
                    alias = _field(spec_node, "alias")
                    names.append((_text(_field(spec_node, "name")),
                                  _text(alias) if alias is not None else None))
        else:
            names.append(("*", None))  # export * from "x"
        self._record_import(_line(node), spec, names, kind="from")

    def _record_import(self, line: int, spec: str, names, kind: str, star_alias: str | None = None):
        if star_alias is not None:
            names = [("*", star_alias)]
        self.imports.append(ImportStmt(line=line, kind=kind, module=spec, level=0, names=names))

    def _is_require(self, node: PNode) -> bool:
        if node.type != "call_expression":
            return False
        fn = _field(node, "function")
        return fn is not None and _text(fn) == "require" and self._require_spec(node) is not None

    def _require_spec(self, node: PNode) -> str | None:
        """Specifier of require("x") or import("x"); None for any other call."""
        fn = _field(node, "function")
        if fn is None or _text(fn) not in ("require", "import"):
            return None
        args = _field(node, "arguments")
        first = next((c for c in args.children if c.is_named), None) if args is not None else None
        return _string_value(first) if first is not None and first.type == "string" else None

    def _call(self, node: PNode, caller: str | None):
        spec = self._require_spec(node) if node.type == "call_expression" else None
        if spec is not None:  # require("x") / import("x") used inline, e.g. `require("x").y`
            self._record_import(_line(node), spec, [("*", None)], kind="import")
            return
        callee, receiver = self._callee(node)
        if callee is None:
            return
        if caller is None:
            self.module_level_calls += 1
            return
        self.calls.append(CallSite(caller=caller, line=_line(node), callee=callee, receiver=receiver))

    def _callee(self, node: PNode) -> tuple[str | None, str | None]:
        """(callee name, receiver text) for a call or `new`; receiver None for bare names."""
        fn = _field(node, "function") if node.type == "call_expression" else _field(node, "constructor")
        if fn is None:
            return None, None
        if fn.type in ("identifier", "type_identifier"):
            return _text(fn), None
        if fn.type == "member_expression":
            prop = _field(fn, "property")
            obj = _field(fn, "object")
            if prop is None or obj is None:
                return None, None
            return _text(prop), self._receiver(obj)
        return None, None

    def _receiver(self, obj: PNode) -> str:
        if obj.type == "this":
            return "this"
        if obj.type == "identifier":
            return _text(obj)
        if obj.type == "member_expression":
            inner_node = _field(obj, "object")
            inner = self._receiver(inner_node) if inner_node is not None else "<expr>"
            prop = _field(obj, "property")
            if inner != "<expr>" and prop is not None:
                return f"{inner}.{_text(prop)}"
        return "<expr>"

