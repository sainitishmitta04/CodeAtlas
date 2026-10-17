"""One ingestion workflow for every source: local directory, public GitHub URL, ZIP upload.

    resolve target -> check Neo4j for an existing project (read only)
                   -> write guard -> (download / extract) -> analyze_repository
                   -> dry_run_report (no database access) or sync_project

The write guard: a non-dry-run request must carry `expected_project_id` equal to the target
the server computed (i.e. the target the user was shown), and `confirm_update=true` when
that project already exists. Otherwise ConfirmationRequired (HTTP 409) is raised *before*
anything is downloaded or written; it carries the target so the UI can show it.

Temporary checkouts live in `temp_workspace`, which is removed on success and on error.
"""

import re
from pathlib import Path

from codeatlas import ids
from codeatlas.analysis.pipeline import analyze_repository
from codeatlas.analysis.scanner import ScanError
from codeatlas.config import Settings
from codeatlas.graph import queries
from codeatlas.graph.db import GraphDBError
from codeatlas.graph.sync import dry_run_report, sync_project
from codeatlas.models import (AnalysisReport, AnalyzeRequest, GithubAnalyzeRequest, SkippedFile,
                              SourceTarget)
from codeatlas.sources import github
from codeatlas.sources.archive import ArchiveLimits, extract_zip, temp_workspace
from codeatlas.sources.errors import SourceError, SourceNotFound, TargetConflict


class ConfirmationRequired(SourceError):
    status = 409
    code = "confirmation_required"

    def __init__(self, message: str, target: SourceTarget):
        super().__init__(message)
        self.target = target


# ------------------------------------------------------------------ target checks


def check_target(target: SourceTarget, dry_run: bool, *, name_given: bool,
                 reuse_by_identity: bool) -> SourceTarget:
    """Fill in whether the target project exists. Read-only. If Neo4j is unreachable a dry
    run continues with exists=None; a write cannot."""
    try:
        if reuse_by_identity:
            same = queries.projects_by_identity(target.identity)
            if same and target.project_id not in same:
                target.notes.append(f"This exact source is already stored as project {same[0]}; "
                                    "that project will be updated instead of creating a duplicate.")
                target.project_id = same[0]
        existing = queries.project(target.project_id)
    except GraphDBError as exc:
        if not dry_run:
            raise
        target.exists = None
        target.notes.append(f"Neo4j could not be checked for an existing project ({exc}).")
        return target

    if existing is None:
        target.exists = False
        return target
    identity = existing.get("source_identity")
    legacy_local = identity is None and target.kind == "local"  # synced before source fields
    if existing.get("source") != "codeatlas" or not (identity == target.identity or legacy_local):
        raise TargetConflict(f"Project id {target.project_id} is already used by a different "
                             "source; it will not be overwritten. Choose another project name.")
    target.exists = True
    target.existing_name = existing.get("name")
    target.existing_status = existing.get("sync_status")
    target.existing_synced_at = str(existing["synced_at"]) if existing.get("synced_at") else None
    if not name_given and target.kind != "local" and target.existing_name:
        target.project_name = target.existing_name
    return target


def guard(target: SourceTarget, dry_run: bool, expected_project_id: str | None,
          confirm_update: bool) -> None:
    if dry_run:
        return
    if expected_project_id != target.project_id:
        raise ConfirmationRequired(
            f"Review the target project {target.project_id} and confirm the write.", target)
    if target.exists and not confirm_update:
        raise ConfirmationRequired(
            f"Project {target.project_id} already exists; confirm that it should be updated.",
            target)


def _execute(path: Path | str, target: SourceTarget, settings: Settings, dry_run: bool,
             extra_skipped: list[SkippedFile] | None = None) -> AnalysisReport:
    out = analyze_repository(str(path), settings, target.project_name, source=target)
    if extra_skipped:
        out.scan.skipped = [*extra_skipped, *out.scan.skipped]
    return dry_run_report(out) if dry_run else sync_project(out, settings)


# ------------------------------------------------------------------ sources


def local_target(path: str, name: str | None) -> SourceTarget:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise ScanError(f"Not a directory: {path}")
    root_s = str(root)
    return SourceTarget(  # same naming and id formula as analyze_repository(path, name)
        kind="local", label=root_s, identity=f"local:{root_s}",
        project_id=ids.project_id_for(root_s, name),
        project_name=name or root.name)


def analyze_local(req: AnalyzeRequest, settings: Settings) -> AnalysisReport:
    target = check_target(local_target(req.path, req.name), req.dry_run,
                          name_given=bool(req.name), reuse_by_identity=False)
    guard(target, req.dry_run, req.expected_project_id, req.confirm_update)
    return _execute(target.label, target, settings, req.dry_run)


def analyze_github(req: GithubAnalyzeRequest, settings: Settings) -> AnalysisReport:
    spec = github.parse_github_url(req.url, req.ref)  # validated before any request
    with github.make_client(settings) as client:
        repo = github.resolve_repo(client, spec)
        target = SourceTarget(
            kind="github", label=repo.label, identity=repo.identity,
            project_id=ids.project_id_for_identity(repo.identity, repo.repo),
            project_name=req.name or repo.repo, ref=repo.ref, ref_is_default=repo.ref_is_default)
        target = check_target(target, req.dry_run, name_given=bool(req.name),
                              reuse_by_identity=True)
        guard(target, req.dry_run, req.expected_project_id, req.confirm_update)
        with temp_workspace(settings) as work:
            archive = work / "repo.zip"
            github.download_archive(client, repo, archive, settings.max_archive_bytes)
            res = extract_zip(archive, work / "src", ArchiveLimits.from_settings(settings))
            archive.unlink(missing_ok=True)
            target.fingerprint = res.fingerprint
            return _execute(res.root, target, settings, req.dry_run, res.skipped)


def safe_filename(filename: str | None) -> str:
    base = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .")[:120]
    return base or "upload.zip"


def analyze_zip_file(zip_path: Path, work: Path, settings: Settings, *, filename: str | None,
                     name: str | None, target_project_id: str | None, dry_run: bool,
                     expected_project_id: str | None, confirm_update: bool) -> AnalysisReport:
    """`zip_path` lives inside `work`, a temp_workspace owned (and removed) by the caller."""
    fname = safe_filename(filename)
    res = extract_zip(zip_path, work / "src", ArchiveLimits.from_settings(settings))
    label = f"zip:{fname} (sha256 {res.fingerprint[:12]})"

    if target_project_id:  # explicit update of a project previously created from a ZIP
        existing = queries.project(target_project_id)
        if existing is None or existing.get("source") != "codeatlas":
            raise SourceNotFound(f"Project {target_project_id} does not exist.")
        identity = existing.get("source_identity")
        if existing.get("source_kind") != "zip" or not identity:
            raise TargetConflict("Only projects created from a ZIP upload can be updated with a "
                                 "ZIP; choose 'New project' instead.")
        target = SourceTarget(kind="zip", label=label, identity=identity,
                              project_id=target_project_id,
                              project_name=name or existing.get("name") or target_project_id,
                              fingerprint=res.fingerprint)
        target.notes.append("Updating an existing ZIP project: files no longer present in this "
                            "upload are removed from its graph.")
        target = check_target(target, dry_run, name_given=bool(name), reuse_by_identity=False)
    else:  # new project, identified by the content of the extracted source files
        identity = f"zip:sha256:{res.fingerprint}"
        stem = Path(fname).stem or "upload"
        target = SourceTarget(kind="zip", label=label, identity=identity,
                              project_id=ids.project_id_for_identity(identity, name or stem),
                              project_name=name or stem, fingerprint=res.fingerprint)
        target = check_target(target, dry_run, name_given=bool(name), reuse_by_identity=True)
    if res.wrapper:
        target.notes.append(f"Source root detected inside enclosing folder '{res.wrapper}/'.")
    guard(target, dry_run, expected_project_id, confirm_update)
    return _execute(res.root, target, settings, dry_run, res.skipped)
