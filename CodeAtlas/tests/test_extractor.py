from codeatlas.analysis.extractor import extract_python
from codeatlas.models import ScannedFile


def _extract(src: str):
    sf = ScannedFile(path="m.py", abs_path="m.py", size_bytes=len(src), sha256="0" * 64)
    return extract_python(sf, src.encode())


def test_symbols_lines_and_methods():
    a = _extract(
        '"""Module doc."""\n'
        "class A(Base, pkg.Mixin):\n"
        '    """Class doc."""\n'
        "    def m(self, x, *args, y=1, **kw):\n"
        "        return helper(x)\n"
        "\n"
        "@decorator\n"
        "async def top():\n"
        "    def inner():\n"
        "        pass\n"
        "    return inner()\n")
    syms = {s.qualname: s for s in a.symbols}
    assert a.docstring == "Module doc."
    assert set(syms) == {"A", "A.m", "top", "top.inner"}
    assert (syms["A"].start_line, syms["A"].end_line, syms["A"].bases) == (2, 5, ["Base", "pkg.Mixin"])
    assert syms["A"].docstring == "Class doc."
    assert syms["A.m"].parent_class == "A" and syms["A.m"].params == ["x", "*args", "y", "**kw"]
    assert syms["top"].is_async and syms["top"].decorators == ["decorator"]
    assert syms["top"].start_line == 7  # decorated range starts at the decorator
    assert syms["top.inner"].parent_class is None
    assert [(c.caller, c.callee, c.receiver, c.line) for c in a.calls] == [
        ("A.m", "helper", None, 5), ("top", "inner", None, 11)]


def test_imports_relative_aliases_and_wildcard():
    a = _extract("import os, a.b as ab\nfrom .. import x\nfrom .pkg.mod import y as z, w\n"
                 "from lib import *\n")
    got = [(i.kind, i.module, i.level, i.names, i.line) for i in a.imports]
    assert got == [
        ("import", "os", 0, [("os", None)], 1),
        ("import", "a.b", 0, [("a.b", "ab")], 1),
        ("from", "", 2, [("x", None)], 2),
        ("from", "pkg.mod", 1, [("y", "z"), ("w", None)], 3),
        ("from", "lib", 0, [("*", None)], 4),
    ]


def test_redefinitions_get_unique_qualnames_and_module_calls_are_counted():
    a = _extract("def f():\n    pass\n\ndef f():\n    pass\n\nprint(f())\n")
    assert [s.qualname for s in a.symbols] == ["f", "f@4"]
    assert a.module_level_calls == 2 and a.calls == []


def test_syntax_errors_are_flagged_not_fatal():
    a = _extract("def ok():\n    pass\n\ndef broken(:\n")
    assert a.has_syntax_errors
    assert "ok" in [s.qualname for s in a.symbols]
