"""Stable, project-scoped identifiers. Every node id is prefixed with its project id."""

import hashlib
import os
import re


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "project"


def project_id_for(root: str, name: str | None = None) -> str:
    """Local directories: slug + hash of the real path (unchanged since the first release)."""
    real = os.path.realpath(root)
    base = name or os.path.basename(real.rstrip("/")) or "project"
    return f"{slugify(base)}-{hashlib.sha1(real.encode()).hexdigest()[:8]}"


def project_id_for_identity(identity: str, slug_base: str) -> str:
    """Remote/uploaded sources: slug + hash of a deterministic source identity, never of the
    (random) temporary directory the source was extracted into."""
    return f"{slugify(slug_base)}-{hashlib.sha1(identity.encode()).hexdigest()[:8]}"


def file_id(pid: str, path: str) -> str:
    return f"{pid}:file:{path}"


def class_id(pid: str, path: str, qualname: str) -> str:
    return f"{pid}:class:{path}:{qualname}"


def function_id(pid: str, path: str, qualname: str) -> str:
    return f"{pid}:function:{path}:{qualname}"


def module_id(pid: str, name: str) -> str:
    return f"{pid}:module:{name}"
