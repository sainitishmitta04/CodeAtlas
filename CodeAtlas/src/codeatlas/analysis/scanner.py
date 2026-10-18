"""Walk a repository and select supported source files (Python only in this MVP)."""

import hashlib
import os
from pathlib import Path

import pathspec

from codeatlas.models import ScannedFile, ScanResult, SkippedFile

from codeatlas.analysis.languages import EXTENSION_LANGUAGES as SUPPORTED_EXTENSIONS  # noqa: E402

IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", ".env", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox", ".idea", ".vscode",
    "dist", "build", "out", "target", "site-packages", ".eggs", "coverage", "htmlcov",
    ".next", ".ua", ".understand-anything",
}
IGNORED_SUFFIXES = (".min.py", "_pb2.py", "_pb2_grpc.py",  # generated code
                    ".min.js", ".min.mjs", ".d.ts")  # bundles and type declarations


class ScanError(ValueError):
    pass


def _gitignore_spec(root: Path) -> pathspec.PathSpec | None:
    gi = root / ".gitignore"
    if not gi.is_file():
        return None
    return pathspec.PathSpec.from_lines("gitignore", gi.read_text(errors="replace").splitlines())


def scan_repository(root: str | Path, max_files: int, max_file_bytes: int) -> ScanResult:
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise ScanError(f"Not a directory: {root}")

    spec = _gitignore_spec(root_path)
    files: list[ScannedFile] = []
    skipped: list[SkippedFile] = []

    for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
        rel_dir = Path(dirpath).relative_to(root_path).as_posix()
        kept = []
        for d in sorted(dirnames):
            rel = d if rel_dir == "." else f"{rel_dir}/{d}"
            if d in IGNORED_DIRS or d.endswith(".egg-info"):
                continue
            if spec and spec.match_file(rel + "/"):
                continue
            kept.append(d)
        dirnames[:] = kept  # prune in place

        for name in sorted(filenames):
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            ext = os.path.splitext(name)[1]
            if ext not in SUPPORTED_EXTENSIONS or name.endswith(IGNORED_SUFFIXES):
                continue
            if spec and spec.match_file(rel):
                continue
            abs_path = Path(dirpath) / name
            if abs_path.is_symlink():
                skipped.append(SkippedFile(path=rel, reason="symlink"))
                continue
            size = abs_path.stat().st_size
            if size > max_file_bytes:
                skipped.append(SkippedFile(path=rel, reason=f"larger than {max_file_bytes} bytes"))
                continue
            data = abs_path.read_bytes()
            files.append(
                ScannedFile(path=rel, abs_path=str(abs_path), size_bytes=size,
                            sha256=hashlib.sha256(data).hexdigest())
            )
            if len(files) > max_files:
                raise ScanError(
                    f"More than {max_files} Python files found; analyze a subdirectory "
                    f"or raise MAX_FILES (AuraDB Free has limited capacity)."
                )

    return ScanResult(root=str(root_path), files=files, skipped=skipped)
