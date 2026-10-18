"""Which extractor handles which file extension.

To add a language: write an extractor that returns FileAnalysis, register its extensions
here, and teach resolver.py how its imports map to project files.
"""

from codeatlas.analysis.extractor import extract_python
from codeatlas.analysis.javascript import extract_javascript
from codeatlas.models import FileAnalysis, ScannedFile

EXTENSION_LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "typescript",
}


def language_for(path: str) -> str | None:
    dot = path.rfind(".")
    return EXTENSION_LANGUAGES.get(path[dot:].lower()) if dot > path.rfind("/") else None


def extract_file(scanned: ScannedFile) -> FileAnalysis:
    language = language_for(scanned.path)
    if language == "python":
        return extract_python(scanned)
    return extract_javascript(scanned)
