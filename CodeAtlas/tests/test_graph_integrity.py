"""Offline integrity and idempotency checks on the rows that sync would write."""

from codeatlas import ids
from codeatlas.analysis.pipeline import analyze_repository
from codeatlas.graph.sync import REL_VARIANTS

from .conftest import FIXTURE


def _all_ids(out):
    return {out.rows.project.id: "Project"} | {
        r.id: label for label, rows in out.rows.nodes.items() for r in rows}


def test_every_relationship_endpoint_exists_with_expected_label(fixture_output):
    labels = _all_ids(fixture_output)
    for variant, rows in fixture_output.rows.rels.items():
        src_label, _, dst_label = REL_VARIANTS[variant]
        for r in rows:
            assert labels.get(r.source) == src_label, (variant, r.source)
            assert labels.get(r.target) == dst_label, (variant, r.target)


def test_containment_cardinality(fixture_output):
    rows = fixture_output.rows
    contained = [r.target for r in rows.rels["CONTAINS"]]
    assert sorted(contained) == sorted(r.id for r in rows.nodes["File"])
    defined = [r.target for r in rows.rels["DEFINES_CLASS"] + rows.rels["DEFINES_FUNCTION"]]
    symbols = [r.id for r in rows.nodes["Class"] + rows.nodes["Function"]]
    assert sorted(defined) == sorted(symbols)  # exactly one DEFINES per symbol
    for r in rows.rels["HAS_METHOD"]:
        fn = next(n for n in rows.nodes["Function"] if n.id == r.target)
        assert fn.props["is_method"] is True


def test_ids_unique_and_project_scoped(fixture_output):
    pid = fixture_output.project_id
    all_ids = [r.id for rows in fixture_output.rows.nodes.values() for r in rows]
    assert len(all_ids) == len(set(all_ids))
    assert all(i.startswith(pid + ":") for i in all_ids)
    keys = [(v, r.source, r.target) for v, rows in fixture_output.rows.rels.items() for r in rows]
    assert len(keys) == len(set(keys))  # one relationship per (type, source, target)


def test_rows_are_deterministic_across_runs(offline_settings):
    a = analyze_repository(str(FIXTURE), offline_settings)
    b = analyze_repository(str(FIXTURE), offline_settings)
    strip = lambda o: o.rows.model_dump(exclude={"project": {"props": {"analyzed_at"}}})  # noqa: E731
    assert strip(a) == strip(b)


def test_project_ids_differ_by_root(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    pa, pb = ids.project_id_for(str(tmp_path / "a"), "same"), ids.project_id_for(str(tmp_path / "b"), "same")
    assert pa != pb and pa.startswith("same-")
    assert ids.project_id_for(str(tmp_path / "a"), "same") == pa


def test_props_have_no_nested_maps(fixture_output):
    """Neo4j properties must be primitives or homogeneous lists of primitives."""
    for rows in fixture_output.rows.nodes.values():
        for r in rows:
            for k, v in r.props.items():
                assert not isinstance(v, dict), k
                if isinstance(v, list):
                    assert len({type(x) for x in v}) <= 1, k
