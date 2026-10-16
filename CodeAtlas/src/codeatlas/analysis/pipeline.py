"""Scan -> extract -> resolve, without touching the database."""

import os
from dataclasses import dataclass

from codeatlas import ids
from codeatlas.analysis.extractor import extract_python
from codeatlas.analysis.resolver import build_graph
from codeatlas.analysis.scanner import scan_repository
from codeatlas.config import Settings
from codeatlas.models import FileAnalysis, GraphRows, ResolutionStats, ScanResult


@dataclass
class AnalysisOutput:
    project_id: str
    project_name: str
    scan: ScanResult
    analyses: list[FileAnalysis]
    rows: GraphRows
    stats: ResolutionStats


def analyze_repository(path: str, settings: Settings, name: str | None = None) -> AnalysisOutput:
    scan = scan_repository(path, settings.max_files, settings.max_file_bytes)
    project_name = name or os.path.basename(scan.root.rstrip("/"))
    pid = ids.project_id_for(scan.root, name)
    analyses = [extract_python(f) for f in scan.files]
    rows, stats = build_graph(scan, analyses, pid, project_name)
    return AnalysisOutput(pid, project_name, scan, analyses, rows, stats)
