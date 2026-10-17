"""ZIP validation, safe extraction, source-root detection and temporary cleanup (offline)."""

import os
import stat
import zipfile
from pathlib import Path

import pytest

from codeatlas.sources.archive import (ArchiveLimits, _normalise, extract_zip, temp_workspace)
from codeatlas.sources.errors import InvalidSource, SourceError, SourceTooLarge

from .conftest import build_zip, fixture_zip

LIMITS = ArchiveLimits(max_archive_bytes=5_000_000, max_extracted_bytes=20_000_000,
                       max_entries=500, max_ratio=200.0, ratio_min_bytes=1_000_000,
                       max_file_bytes=300_000)


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_flat_archive_root(tmp_path):
    z = build_zip(tmp_path / "a.zip", {"main.py": "print(1)\n", "pkg/util.py": "x = 1\n",
                                        "README.md": "hi", ".gitignore": "*.log\n"})
    res = extract_zip(z, tmp_path / "out", LIMITS)
    assert res.root == tmp_path / "out" and res.wrapper is None
    # only what the analyzer reads is written
    assert _files(res.root) == {"main.py", "pkg/util.py", ".gitignore"}
    assert res.files_written == 3


def test_single_enclosing_directory_is_stripped(tmp_path):
    z = fixture_zip(tmp_path / "w.zip", wrapper="sample_repo-main")
    res = extract_zip(z, tmp_path / "out", LIMITS)
    assert res.wrapper == "sample_repo-main"
    assert res.root == tmp_path / "out" / "sample_repo-main"
    assert "shop/models.py" in _files(res.root)


def test_nested_wrappers_and_macos_metadata(tmp_path):
    z = build_zip(tmp_path / "n.zip", {"outer/inner/app.py": "x = 1\n",
                                        "__MACOSX/outer/._app.py": "junk",
                                        "outer/.DS_Store": "junk"})
    res = extract_zip(z, tmp_path / "out", LIMITS)
    assert res.wrapper == "outer/inner" and _files(res.root) == {"app.py"}


def test_fingerprint_ignores_wrapper_and_is_deterministic(tmp_path):
    a = extract_zip(fixture_zip(tmp_path / "a.zip"), tmp_path / "a", LIMITS)
    b = extract_zip(fixture_zip(tmp_path / "b.zip", wrapper="x-123"), tmp_path / "b", LIMITS)
    c = extract_zip(build_zip(tmp_path / "c.zip", {"m.py": "y = 2\n"}), tmp_path / "c", LIMITS)
    assert a.fingerprint == b.fingerprint != c.fingerprint


@pytest.mark.parametrize("name", ["../evil.py", "a/../../evil.py", "/etc/evil.py",
                                  "C:/evil.py", "C:\\evil.py", "a\\..\\..\\evil.py"])
def test_unsafe_paths_reject_the_archive(tmp_path, name):
    z = build_zip(tmp_path / "t.zip", {"ok.py": "x = 1\n", name: "boom = 1\n"})
    with pytest.raises(InvalidSource, match="Unsafe"):
        extract_zip(z, tmp_path / "out", LIMITS)
    assert not (tmp_path / "evil.py").exists() and not (tmp_path / "out").exists()


def test_nul_byte_name_rejected():
    with pytest.raises(InvalidSource):
        _normalise("a\x00.py")


def test_symlink_entries_are_never_created(tmp_path):
    z = tmp_path / "s.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("real.py", "x = 1\n")
        link = zipfile.ZipInfo("link.py")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "../../../../etc/passwd")
    res = extract_zip(z, tmp_path / "out", LIMITS)
    assert _files(res.root) == {"real.py"}
    assert not any(p.is_symlink() for p in (tmp_path / "out").rglob("*"))
    assert [(s.path, s.reason) for s in res.skipped] == [("link.py", "symlink")]


def test_encrypted_entries_rejected(tmp_path):
    # zipfile cannot write encrypted entries, so set the "encrypted" flag bit in the
    # local (offset 6) and central (offset 8) headers directly.
    z = build_zip(tmp_path / "e.zip", {"secret.py": "x = 1\n"})
    data = bytearray(z.read_bytes())
    for sig, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        i = data.find(sig)
        data[i + offset] |= 0x1
    z.write_bytes(bytes(data))
    with pytest.raises(InvalidSource, match="Encrypted"):
        extract_zip(z, tmp_path / "out", LIMITS)


def test_not_a_zip(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"this is not a zip archive at all")
    with pytest.raises(InvalidSource, match="Not a valid ZIP"):
        extract_zip(bad, tmp_path / "out", LIMITS)


def test_truncated_zip(tmp_path):
    good = build_zip(tmp_path / "g.zip", {"a.py": os.urandom(4000).hex()})
    cut = tmp_path / "cut.zip"
    cut.write_bytes(good.read_bytes()[:-60])  # central directory is cut off
    with pytest.raises(InvalidSource):
        extract_zip(cut, tmp_path / "out", LIMITS)


def test_no_python_files(tmp_path):
    z = build_zip(tmp_path / "docs.zip", {"README.md": "x", "src/app.js": "let a"})
    with pytest.raises(InvalidSource, match="no supported source files"):
        extract_zip(z, tmp_path / "out", LIMITS)


def test_oversized_python_files_are_skipped_not_extracted(tmp_path):
    limits = ArchiveLimits(**{**LIMITS.__dict__, "max_file_bytes": 100})
    z = build_zip(tmp_path / "big.zip", {"small.py": "x = 1\n", "big.py": "y = 2\n" * 100})
    res = extract_zip(z, tmp_path / "out", limits)
    assert _files(res.root) == {"small.py"}
    assert any(s.path == "big.py" and "larger than" in s.reason for s in res.skipped)


def test_too_many_entries(tmp_path):
    limits = ArchiveLimits(**{**LIMITS.__dict__, "max_entries": 5})
    z = build_zip(tmp_path / "many.zip", {f"m{i}.py": "x = 1\n" for i in range(6)})
    with pytest.raises(SourceTooLarge, match="entries"):
        extract_zip(z, tmp_path / "out", limits)


def test_archive_file_size_limit(tmp_path):
    limits = ArchiveLimits(**{**LIMITS.__dict__, "max_archive_bytes": 100})
    z = build_zip(tmp_path / "a.zip", {"a.py": "x = 1\n" * 100}, compression=zipfile.ZIP_STORED)
    with pytest.raises(SourceTooLarge):
        extract_zip(z, tmp_path / "out", limits)


def test_total_uncompressed_limit(tmp_path):
    limits = ArchiveLimits(**{**LIMITS.__dict__, "max_extracted_bytes": 1000})
    z = build_zip(tmp_path / "t.zip", {"a.py": "x = 1\n" * 150, "b.py": "y = 2\n" * 150})
    with pytest.raises(SourceTooLarge, match="expands to"):
        extract_zip(z, tmp_path / "out", limits)


def test_compression_ratio_bomb(tmp_path):
    z = build_zip(tmp_path / "bomb.zip", {"ok.py": "x = 1\n", "bomb.txt": b"\0" * 3_000_000})
    with pytest.raises(SourceTooLarge, match="compression ratio"):
        extract_zip(z, tmp_path / "out", LIMITS)


def test_understated_size_header_cannot_overflow(tmp_path):
    """Patch the declared uncompressed size of an entry to be smaller than its content."""
    z = build_zip(tmp_path / "lie.zip", {"lie.py": "x = 1\n" * 2000},
                  compression=zipfile.ZIP_STORED)
    data = bytearray(z.read_bytes())
    for sig, offset in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
        i = data.find(sig)
        data[i + offset:i + offset + 4] = (10).to_bytes(4, "little")
    z.write_bytes(bytes(data))
    with pytest.raises(SourceError):
        extract_zip(z, tmp_path / "out", LIMITS)
    out = tmp_path / "out" / "lie.py"
    assert not out.exists() or out.stat().st_size <= 10


def test_duplicate_entries_rejected(tmp_path):
    z = tmp_path / "dup.zip"
    with pytest.warns(UserWarning), zipfile.ZipFile(z, "w") as zf:
        zf.writestr("a.py", "x = 1\n")
        zf.writestr("a.py", "x = 2\n")
    with pytest.raises(InvalidSource, match="Duplicate"):
        extract_zip(z, tmp_path / "out", LIMITS)


def test_file_directory_conflict(tmp_path):
    z = build_zip(tmp_path / "c.zip", {"a.py": "x = 1\n", "a.py/b.py": "y = 1\n"})
    with pytest.raises(InvalidSource, match="structure"):
        extract_zip(z, tmp_path / "out", LIMITS)


def test_temp_workspace_removed_on_success_and_error(tmp_path, offline_settings):
    settings = offline_settings.model_copy(update={"codeatlas_tmp_dir": str(tmp_path / "tmp")})
    with temp_workspace(settings) as work:
        (work / "f.py").write_text("x = 1\n")
        kept = work
    assert not kept.exists()
    with pytest.raises(RuntimeError), temp_workspace(settings) as work:
        failed = work
        raise RuntimeError("boom")
    assert not failed.exists()
    assert list((tmp_path / "tmp").iterdir()) == []
