"""Deterministic Python extraction with tree-sitter.

Produces classes, functions/methods (with 1-based line ranges), import statements
and call sites. No name resolution happens here; see resolver.py.
"""

import tree_sitter_python
from tree_sitter import Language, Parser

from codeatlas.analysis.syntax import (_children, _end_line, _field, _fields, _LOCK, _materialize,
                                       _NEWLINES, _named, _SOURCE, _text, _line, PNode)
from codeatlas.models import CallSite, FileAnalysis, ImportStmt, ScannedFile, Symbol

PY_LANGUAGE = Language(tree_sitter_python.language())
_parser = Parser(PY_LANGUAGE)

DOCSTRING_MAX = 300


def _docstring(block: PNode | None) -> str | None:
    if block is None:
        return None
    for child in _named(block):
        if child.type == "comment":
            continue
        if child.type == "expression_statement" and len(_named(child)) == 1:
            s = _named(child)[0]
            if s.type == "string":
                content = "".join(_text(c) for c in _named(s) if c.type == "string_content")
                content = " ".join(content.split())
                return content[:DOCSTRING_MAX] or None
        return None
    return None


def _params(params: PNode | None) -> list[str]:
    out: list[str] = []
    if params is None:
        return out
    for child in _named(params):
        if child.type == "identifier":
            name = _text(child)
        elif child.type in ("typed_parameter", "default_parameter", "typed_default_parameter"):
            ident = _field(child, "name") or next(
                (c for c in _named(child) if c.type == "identifier"), None)
            name = _text(ident)
        elif child.type == "list_splat_pattern":
            name = "*" + _text(_named(child)[0]) if len(_named(child)) else "*"
        elif child.type == "dictionary_splat_pattern":
            name = "**" + _text(_named(child)[0]) if len(_named(child)) else "**"
        else:
            continue
        if name and name not in ("self", "cls"):
            out.append(name)
    return out


def _dotted(node: PNode | None) -> str | None:
    """Text of an identifier/attribute chain like `a.b.c`, else None (dynamic expression)."""
    if node is None:
        return None
    if node.type == "identifier":
        return _text(node)
    if node.type == "attribute":
        obj = _dotted(_field(node, "object"))
        attr = _field(node, "attribute")
        return f"{obj}.{_text(attr)}" if obj and attr is not None else None
    return None


class _Extractor:
    def __init__(self, scanned: ScannedFile, source: bytes):
        self.scanned = scanned
        self.source = source
        self.symbols: list[Symbol] = []
        self.imports: list[ImportStmt] = []
        self.calls: list[CallSite] = []
        self.module_level_calls = 0
        self._seen_qualnames: set[str] = set()

    def run(self) -> FileAnalysis:
        _SOURCE[0] = self.source
        _NEWLINES[0] = [i for i, b in enumerate(self.source) if b == 10]
        tree = _parser.parse(self.source)
        has_error = tree.root_node.has_error
        root = _materialize(tree)
        del tree
        self._walk_block(root, scope=None, class_scope=None)
        module_doc = _docstring(root)
        return FileAnalysis(
            path=self.scanned.path,
            sha256=self.scanned.sha256,
            size_bytes=self.scanned.size_bytes,
            loc=self.source.count(b"\n") + (0 if self.source.endswith(b"\n") or not self.source else 1),
            docstring=module_doc,
            has_syntax_errors=has_error,
            symbols=self.symbols,
            imports=self.imports,
            calls=self.calls,
            module_level_calls=self.module_level_calls,
        )

    # scope = qualname of the enclosing function (calls are attributed to it)
    # class_scope = qualname of the directly enclosing class (for methods)
    def _walk_block(self, node: PNode, scope: str | None, class_scope: str | None,
                    prefix: str = "") -> None:
        for child in _children(node):
            self._visit(child, scope, class_scope, prefix)

    def _visit(self, node: PNode, scope: str | None, class_scope: str | None, prefix: str) -> None:
        t = node.type
        if t == "decorated_definition":
            decorators = [_text(d)[1:].strip() for d in _children(node) if d.type == "decorator"]
            for d in _children(node):  # decorator expressions may contain calls
                if d.type == "decorator":
                    self._visit(d, scope, class_scope, prefix)
            definition = _field(node, "definition")
            if definition is not None:
                self._definition(definition, scope, class_scope, prefix, decorators, outer=node)
            return
        if t in ("function_definition", "class_definition"):
            self._definition(node, scope, class_scope, prefix, [], outer=node)
            return
        if t == "import_statement":
            self._import(node)
            return
        if t == "import_from_statement":
            self._import_from(node)
            return
        if t == "call":
            self._call(node, scope)
        for child in _children(node):
            self._visit(child, scope, class_scope, prefix)

    def _unique_qualname(self, qualname: str, line: int) -> str:
        if qualname in self._seen_qualnames:  # redefinition (e.g. @x.setter, conditional defs)
            qualname = f"{qualname}@{line}"
        self._seen_qualnames.add(qualname)
        return qualname

    def _definition(self, node: PNode, scope: str | None, class_scope: str | None, prefix: str,
                    decorators: list[str], outer: PNode) -> None:
        name = _text(_field(node, "name"))
        body = _field(node, "body")
        qualname = self._unique_qualname(f"{prefix}{name}", _line(outer))
        if node.type == "class_definition":
            supers = _field(node, "superclasses")
            bases = []
            if supers is not None:
                for arg in _named(supers):
                    if arg.type == "keyword_argument":  # metaclass=...
                        continue
                    bases.append(_dotted(arg) or _text(arg))
                    if arg.type == "call":
                        self._visit(arg, scope, class_scope, prefix)
            self.symbols.append(Symbol(
                kind="class", name=name, qualname=qualname, start_line=_line(outer),
                end_line=_end_line(outer), decorators=decorators, bases=bases,
                docstring=_docstring(body)))
            if body is not None:
                # class bodies are not call scopes: code there runs at import time
                self._walk_block(body, scope=scope, class_scope=qualname, prefix=f"{qualname}.")
            return

        is_async = any(c.type == "async" for c in _children(node))
        self.symbols.append(Symbol(
            kind="function", name=name, qualname=qualname, start_line=_line(outer),
            end_line=_end_line(outer), parent_class=class_scope, is_async=is_async,
            params=_params(_field(node, "parameters")), decorators=decorators,
            docstring=_docstring(body)))
        for field in ("parameters", "return_type"):  # default values may contain calls
            sub = _field(node, field)
            if sub is not None:
                self._visit(sub, scope, class_scope, prefix)
        if body is not None:
            self._walk_block(body, scope=qualname, class_scope=None, prefix=f"{qualname}.")

    def _import(self, node: PNode) -> None:
        names: list[tuple[str, str | None]] = []
        for child in _named(node):
            if child.type == "dotted_name":
                names.append((_text(child), None))
            elif child.type == "aliased_import":
                names.append((_text(_field(child, "name")),
                              _text(_field(child, "alias")) or None))
        for module, alias in names:
            self.imports.append(ImportStmt(line=_line(node), kind="import", module=module,
                                           names=[(module, alias)]))

    def _import_from(self, node: PNode) -> None:
        module_node = _field(node, "module_name")
        level, module = 0, ""
        if module_node is not None and module_node.type == "relative_import":
            for c in _children(module_node):
                if c.type == "import_prefix":
                    level = _text(c).count(".")
                elif c.type == "dotted_name":
                    module = _text(c)
        else:
            module = _text(module_node)
        names: list[tuple[str, str | None]] = []
        for child in _fields(node, "name"):
            if child.type == "dotted_name":
                names.append((_text(child), None))
            elif child.type == "aliased_import":
                names.append((_text(_field(child, "name")),
                              _text(_field(child, "alias")) or None))
        if any(c.type == "wildcard_import" for c in _children(node)):
            names.append(("*", None))
        self.imports.append(ImportStmt(line=_line(node), kind="from", module=module,
                                       level=level, names=names))

    def _call(self, node: PNode, scope: str | None) -> None:
        if scope is None:
            self.module_level_calls += 1
            return
        fn = _field(node, "function")
        if fn is None:
            return
        if fn.type == "identifier":
            self.calls.append(CallSite(caller=scope, line=_line(node), callee=_text(fn)))
        elif fn.type == "attribute":
            attr = _field(fn, "attribute")
            receiver = _dotted(_field(fn, "object")) or "<expr>"
            self.calls.append(CallSite(caller=scope, line=_line(node), callee=_text(attr),
                                       receiver=receiver))
        else:
            self.calls.append(CallSite(caller=scope, line=_line(node),
                                       callee=_text(fn)[:60], receiver="<expr>"))


def extract_python(scanned: ScannedFile, source: bytes | None = None) -> FileAnalysis:
    if source is None:
        with open(scanned.abs_path, "rb") as fh:
            source = fh.read()
    with _LOCK:
        return _Extractor(scanned, source).run()
