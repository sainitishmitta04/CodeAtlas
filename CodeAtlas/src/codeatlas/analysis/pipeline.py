"""Scan -> extract -> resolve, without touching the database."""

import os
from dataclasses import dataclass

from codeatlas import ids
from codeatlas.analysis.languages import extract_file
from codeatlas.analysis.resolver import build_graph
from codeatlas.analysis.scanner import scan_repository
from codeatlas.config import Settings
from codeatlas.models import FileAnalysis, GraphRows, ResolutionStats, ScanResult, SourceTarget


@dataclass
class AnalysisOutput:
    project_id: str
    project_name: str
    scan: ScanResult
    analyses: list[FileAnalysis]
    rows: GraphRows
    stats: ResolutionStats
    source: SourceTarget | None = None

    @property
    def root_label(self) -> str:
        """What is persisted as Project.root_path: the real path for local directories, a
        readable source label (never the temporary extraction path) for GitHub/ZIP."""
        return self.source.label if self.source else self.scan.root


def analyze_repository(path: str, settings: Settings, name: str | None = None,
                       source: SourceTarget | None = None) -> AnalysisOutput:
    scan = scan_repository(path, settings.max_files, settings.max_file_bytes)
    if source is None:  # local directory: original naming and id formula
        project_name = name or os.path.basename(scan.root.rstrip("/"))
        pid = ids.project_id_for(scan.root, name)
    else:
        project_name, pid = source.project_name, source.project_id
    analyses = [extract_file(f) for f in scan.files]
    rows, stats = build_graph(scan, analyses, pid, project_name)
    out = AnalysisOutput(pid, project_name, scan, analyses, rows, stats, source)
    if source is not None:
        rows.project.props.update(root_path=source.label, **source_props(source))
    return out


def source_props(source: SourceTarget) -> dict:
    """Project properties describing where the graph came from."""
    return {"source_kind": source.kind, "source_identity": source.identity,
            "source_label": source.label, "source_ref": source.ref}
