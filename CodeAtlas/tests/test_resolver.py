"""Golden tests for the fixture graph: relationships, provenance and evidence aggregation."""

from .conftest import short


def _rels(out, variant):
    return {(short(r.source), short(r.target)): r.props for r in out.rows.rels[variant]}


def test_imports_resolved_external_and_aggregated(fixture_output):
    files = _rels(fixture_output, "IMPORTS_FILE")
    assert set(files) == {
        ("file:shop/cli.py", "file:shop/models.py"),     # from . import models (submodule)
        ("file:shop/cli.py", "file:shop/pricing.py"),    # import shop.pricing as pricing
        ("file:shop/cli.py", "file:shop/utils.py"),      # from shop import utils
        ("file:shop/pricing.py", "file:shop/models.py"),  # from .models import Item, DiscountedItem
        ("file:shop/pricing.py", "file:shop/utils.py"),  # from shop.utils import fmt
    }
    assert files[("file:shop/pricing.py", "file:shop/models.py")]["names"] == ["DiscountedItem", "Item"]
    mods = _rels(fixture_output, "IMPORTS_MODULE")
    # repeated imports: one relationship, every statement line kept
    os_edge = mods[("file:shop/utils.py", "module:os")]
    assert os_edge == {"lines": [1, 2, 3], "names": ["os", "path"], "count": 3, "resolution": "external"}
    assert ("file:shop/cli.py", "module:json") in mods


def test_calls_keep_every_call_site(fixture_output):
    calls = _rels(fixture_output, "CALLS")
    fmt = calls[("function:shop/cli.py:main", "function:shop/utils.py:fmt")]
    assert fmt["lines"] == [9, 10] and fmt["count"] == 2
    assert fmt["confidences"] == ["exact", "exact"]
    assert fmt["resolutions"] == ["module_attr", "module_attr"]


def test_call_resolution_kinds(fixture_output):
    calls = {k: v["resolutions"][0] for k, v in _rels(fixture_output, "CALLS").items()}
    expected = {
        ("function:shop/pricing.py:total", "function:shop/pricing.py:_round"): "same_file",
        ("function:shop/pricing.py:total", "function:shop/utils.py:fmt"): "import",
        ("function:shop/pricing.py:make_items", "function:shop/pricing.py:make_items.build"): "local",
        ("function:shop/pricing.py:make_items.build", "function:shop/models.py:Item.__init__"): "constructor",
        ("function:shop/models.py:Item.price", "function:shop/models.py:Item.base"): "self",
        # inherited method via resolved INHERITS
        ("function:shop/models.py:DiscountedItem.price", "function:shop/models.py:Item.base"): "self",
        ("function:shop/cli.py:main", "function:reports.py:run_report"): "global_name",
    }
    for key, how in expected.items():
        assert calls.get(key) == how, key
    # constructor of a subclass without __init__ resolves to the inherited __init__
    assert ("function:shop/pricing.py:make_items", "function:shop/models.py:Item.__init__") in calls


def test_name_match_is_labelled_and_ambiguity_creates_no_edge(fixture_output):
    calls = _rels(fixture_output, "CALLS")
    assert calls[("function:shop/cli.py:main", "function:reports.py:run_report")]["confidence"] == "name_match"
    assert not any(t.endswith(":process") for _, t in calls)  # dup_a/dup_b ambiguity
    fns = {short(r.id): r.props for r in fixture_output.rows.nodes["Function"]}
    assert fns["function:shop/cli.py:main"]["unresolved_calls"] == ["process"]
    assert fns["function:shop/cli.py:main"]["external_calls"] == ["json.dumps"]
    assert fns["function:shop/pricing.py:total"]["unresolved_calls"] == ["item.price"]
    st = fixture_output.stats
    assert (st.calls_ambiguous, st.calls_name_match) == (1, 1)


def test_inheritance(fixture_output):
    assert _rels(fixture_output, "INHERITS") == {
        ("class:shop/models.py:DiscountedItem", "class:shop/models.py:Item"): {"confidence": "exact"}}
