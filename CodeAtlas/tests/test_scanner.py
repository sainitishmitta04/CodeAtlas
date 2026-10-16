import pytest

from codeatlas.analysis.scanner import ScanError, scan_repository

from .conftest import FIXTURE


def test_fixture_scan_ignores_vendor_dirs():
    res = scan_repository(FIXTURE, max_files=100, max_file_bytes=300_000)
    paths = [f.path for f in res.files]
    assert paths == ["dup_a.py", "dup_b.py", "reports.py", "shop/__init__.py", "shop/cli.py",
                     "shop/models.py", "shop/pricing.py", "shop/utils.py"]
    assert all(len(f.sha256) == 64 for f in res.files)


def test_size_limit_and_gitignore(tmp_path):
    (tmp_path / "small.py").write_text("x = 1\n")
    (tmp_path / "big.py").write_text("#" * 2000)
    (tmp_path / "gen").mkdir()
    (tmp_path / "gen" / "out.py").write_text("y = 2\n")
    (tmp_path / "msg_pb2.py").write_text("z = 3\n")
    (tmp_path / ".gitignore").write_text("gen/\n")
    res = scan_repository(tmp_path, max_files=100, max_file_bytes=1000)
    assert [f.path for f in res.files] == ["small.py"]
    assert [(s.path, "larger" in s.reason) for s in res.skipped] == [("big.py", True)]


def test_too_many_files_is_refused(tmp_path):
    for i in range(4):
        (tmp_path / f"m{i}.py").write_text("")
    with pytest.raises(ScanError):
        scan_repository(tmp_path, max_files=3, max_file_bytes=1000)
