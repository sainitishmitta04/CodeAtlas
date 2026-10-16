"""Stable, project-scoped identifiers. Every node id is prefixed with its project id."""

import hashlib
import os
import re


def project_id_for(root: str, name: str | None = None) -> str:
    real = os.path.realpath(root)
    base = name or os.path.basename(real.rstrip("/")) or "project"
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-") or "project"
    return f"{slug}-{hashlib.sha1(real.encode()).hexdigest()[:8]}"


def file_id(pid: str, path: str) -> str:
    return f"{pid}:file:{path}"


def class_id(pid: str, path: str, qualname: str) -> str:
    return f"{pid}:class:{path}:{qualname}"


def function_id(pid: str, path: str, qualname: str) -> str:
    return f"{pid}:function:{path}:{qualname}"


def module_id(pid: str, name: str) -> str:
    return f"{pid}:module:{name}"
