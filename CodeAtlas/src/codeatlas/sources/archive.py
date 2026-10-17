"""Safe ZIP extraction into a temporary workspace.

Security model (every check happens before anything is written unless noted):
- the archive must be a readable ZIP; encrypted entries and unsupported compression are
  rejected;
- entry count, total declared uncompressed size and per-entry compression ratio are capped;
- entry names are normalised and rejected if absolute, drive-qualified, containing NUL or
  `..`, or duplicated; the resolved target must stay inside the extraction root;
- symlinks and other special entries are never created (skipped and reported);
- only files the analyzer reads (`.py`, `.gitignore`) are written, each streamed with a
  hard byte cap, so a header that understates its real size cannot exceed the limits.
"""

import hashlib
import os
import re
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from codeatlas.analysis.scanner import SUPPORTED_EXTENSIONS
from codeatlas.config import Settings
from codeatlas.models import SkippedFile
from codeatlas.sources.errors import InvalidSource, SourceTooLarge

EXTRACTED_NAMES = {".gitignore"}  # besides supported source extensions
IGNORED_PARTS = {"__MACOSX"}
IGNORED_NAMES = {".DS_Store", "Thumbs.db"}
MAX_WRAPPER_DEPTH = 3
CHUNK = 64 * 1024
_DRIVE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class ArchiveLimits:
    max_archive_bytes: int
    max_extracted_bytes: int
    max_entries: int
    max_ratio: float
    ratio_min_bytes: int
    max_file_bytes: int

    @classmethod
    def from_settings(cls, s: Settings) -> "ArchiveLimits":
        return cls(s.max_archive_bytes, s.max_extracted_bytes, s.max_archive_entries,
                   s.max_compression_ratio, s.compression_ratio_min_bytes, s.max_file_bytes)


@dataclass
class ExtractResult:
    root: Path  # detected source root (inside the extraction directory)
    files_written: int
    fingerprint: str  # sha256 over (relative path, content hash) of written files
    skipped: list[SkippedFile] = field(default_factory=list)
    wrapper: str | None = None  # stripped enclosing directory, e.g. "repo-main"


@contextmanager
def temp_workspace(settings: Settings) -> Iterator[Path]:
    """A private temporary directory that is always removed, on success or error."""
    parent = settings.codeatlas_tmp_dir or None
    if parent:
        os.makedirs(parent, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="codeatlas-", dir=parent))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _normalise(name: str) -> PurePosixPath | None:
    """Safe relative path for an entry, None for ignorable entries; raises if unsafe."""
    raw = name.replace("\\", "/")
    if "\x00" in raw:
        raise InvalidSource(f"Unsafe path in archive (NUL byte): {name!r}")
    if raw.startswith("/") or _DRIVE.match(raw):
        raise InvalidSource(f"Unsafe absolute path in archive: {name!r}")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise InvalidSource(f"Unsafe path traversal in archive: {name!r}")
    if not parts or parts[0] in IGNORED_PARTS or parts[-1] in IGNORED_NAMES:
        return None
    return PurePosixPath(*parts)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _is_special(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    fmt = stat.S_IFMT(mode)
    return fmt not in (0, stat.S_IFREG, stat.S_IFDIR)


def _wanted(rel: PurePosixPath) -> bool:
    return rel.suffix in SUPPORTED_EXTENSIONS or rel.name in EXTRACTED_NAMES


def _detect_wrapper(paths: list[tuple[PurePosixPath, bool]]) -> PurePosixPath:
    """Strip enclosing directories while the archive holds exactly one top-level directory
    and no top-level files (GitHub's `repo-<ref>/`, or a zipped folder)."""
    prefix = PurePosixPath()
    for _ in range(MAX_WRAPPER_DEPTH):
        depth = len(prefix.parts)
        tops, has_top_file = set(), False
        for p, is_dir in paths:
            if p.parts[:depth] != prefix.parts or len(p.parts) <= depth:
                continue
            rest = p.parts[depth:]
            if len(rest) == 1 and not is_dir:
                has_top_file = True
            tops.add(rest[0])
        if has_top_file or len(tops) != 1:
            break
        prefix = prefix / next(iter(tops))
    return prefix


def extract_zip(zip_path: Path, dest: Path, limits: ArchiveLimits) -> ExtractResult:
    if zip_path.stat().st_size > limits.max_archive_bytes:
        raise SourceTooLarge(f"Archive is larger than {limits.max_archive_bytes:,} bytes.")
    if not zipfile.is_zipfile(zip_path):
        raise InvalidSource("Not a valid ZIP archive.")
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise InvalidSource(f"Not a valid ZIP archive: {exc}") from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > limits.max_entries:
            raise SourceTooLarge(f"Archive has {len(infos):,} entries; the limit is "
                                 f"{limits.max_entries:,}.")
        entries: list[tuple[PurePosixPath, zipfile.ZipInfo]] = []
        seen: set[str] = set()
        skipped: list[SkippedFile] = []
        declared_total = 0
        for info in infos:
            rel = _normalise(info.filename)
            if rel is None:
                continue
            key = rel.as_posix() + ("/" if info.is_dir() else "")
            if key in seen:
                raise InvalidSource(f"Duplicate entry in archive: {rel.as_posix()}")
            seen.add(key)
            if info.flag_bits & 0x1:
                raise InvalidSource("Encrypted ZIP archives are not supported.")
            if _is_symlink(info) or _is_special(info):
                skipped.append(SkippedFile(path=rel.as_posix(),
                                           reason="symlink" if _is_symlink(info) else "special file"))
                continue
            if not info.is_dir():
                declared_total += info.file_size
                if info.file_size > limits.ratio_min_bytes and (
                        info.compress_size == 0
                        or info.file_size / info.compress_size > limits.max_ratio):
                    raise SourceTooLarge(f"Entry {rel.as_posix()!r} has a suspicious compression "
                                         f"ratio (possible archive bomb).")
            entries.append((rel, info))
        if declared_total > limits.max_extracted_bytes:
            raise SourceTooLarge(f"Archive expands to {declared_total:,} bytes; the limit is "
                                 f"{limits.max_extracted_bytes:,}.")

        wrapper = _detect_wrapper([(rel, info.is_dir()) for rel, info in entries])
        dest.mkdir(parents=True, exist_ok=True)
        dest_real = dest.resolve()
        root = dest / wrapper
        written_total, files_written, digests = 0, 0, []
        for rel, info in entries:
            if info.is_dir() or not _wanted(rel):
                continue
            if info.file_size > limits.max_file_bytes:
                skipped.append(SkippedFile(path=_rel_to(rel, wrapper),
                                           reason=f"larger than {limits.max_file_bytes} bytes"))
                continue
            target = dest / rel
            if not target.resolve().is_relative_to(dest_real):
                raise InvalidSource(f"Unsafe path in archive: {rel.as_posix()!r}")
            written, digest = _write_member(zf, info, target, limits,
                                            limits.max_extracted_bytes - written_total)
            written_total += written
            files_written += 1
            digests.append(f"{_rel_to(rel, wrapper)}\0{digest}")

    if not any(d.split("\0", 1)[0].endswith(tuple(SUPPORTED_EXTENSIONS)) for d in digests):
        raise InvalidSource("The archive contains no supported source files (.py) within the "
                            "size limits.")
    fingerprint = hashlib.sha256("\n".join(sorted(digests)).encode()).hexdigest()
    return ExtractResult(root=root, files_written=files_written, fingerprint=fingerprint,
                         skipped=skipped, wrapper=wrapper.as_posix() if wrapper.parts else None)


def _rel_to(rel: PurePosixPath, wrapper: PurePosixPath) -> str:
    return PurePosixPath(*rel.parts[len(wrapper.parts):]).as_posix()


def _write_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path,
                  limits: ArchiveLimits, budget: int) -> tuple[int, str]:
    """Stream one entry to disk with hard caps; never follows or creates symlinks."""
    cap = min(info.file_size, limits.max_file_bytes, budget)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                     0o600)
    except (FileExistsError, NotADirectoryError) as exc:
        raise InvalidSource(f"Unsupported archive structure near {info.filename!r}.") from exc
    h, total = hashlib.sha256(), 0
    try:
        with os.fdopen(fd, "wb") as out, zf.open(info) as src:
            while chunk := src.read(CHUNK):
                total += len(chunk)
                if total > cap:
                    raise SourceTooLarge(f"Entry {info.filename!r} expands beyond its allowed "
                                         "size (possible archive bomb).")
                h.update(chunk)
                out.write(chunk)
    except NotImplementedError as exc:
        raise InvalidSource(f"Unsupported compression method in {info.filename!r}.") from exc
    except (zipfile.BadZipFile, EOFError, zipfile.LargeZipFile) as exc:
        raise InvalidSource(f"Corrupt archive entry {info.filename!r}: {exc}") from exc
    return total, h.hexdigest()
