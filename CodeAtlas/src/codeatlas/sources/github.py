"""Public GitHub repositories: strict URL/ref validation, metadata lookup and archive download.

Retrieval uses two documented GitHub REST endpoints and never the `git` executable:
1. GET https://api.github.com/repos/{owner}/{repo}
   -> canonical owner/name, `private`, `default_branch` (validated before use);
2. GET https://api.github.com/repos/{owner}/{repo}/zipball/{ref}
   -> 302 redirect to codeload.github.com, which streams the ZIP.

No token is requested, read or sent; `trust_env=False` keeps httpx from picking up
`.netrc` credentials or proxy settings from the environment. Redirects are followed
manually and only to approved HTTPS GitHub hosts. Every input is validated before any
request is made.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import httpx

from codeatlas.config import Settings
from codeatlas.sources.errors import (DownloadFailed, InvalidSource, RateLimited,
                                      SourceNotFound, SourceTooLarge)

API_BASE = "https://api.github.com"
URL_HOSTS = {"github.com", "www.github.com"}
REDIRECT_HOSTS = {"api.github.com", "codeload.github.com", "github.com"}
MAX_REDIRECTS = 3
MAX_METADATA_BYTES = 1_000_000
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
           "User-Agent": "CodeAtlas"}

_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_REF = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
RESERVED_OWNERS = {
    "about", "account", "apps", "codespaces", "collections", "customer-stories", "enterprise",
    "explore", "features", "issues", "join", "login", "logout", "marketplace", "new",
    "notifications", "organizations", "orgs", "pricing", "pulls", "readme", "search",
    "security", "settings", "site", "sponsors", "topics", "trending",
}


@dataclass(frozen=True)
class RepoSpec:
    """A validated public repository reference parsed from user input (no network)."""
    owner: str
    repo: str
    ref: str | None = None


@dataclass(frozen=True)
class ResolvedRepo:
    """Repository after the metadata lookup: canonical names and the ref to analyze."""
    owner: str
    repo: str
    ref: str
    default_branch: str

    @property
    def ref_is_default(self) -> bool:
        return self.ref == self.default_branch

    @property
    def identity(self) -> str:
        # Canonical (renames resolved by GitHub), case-insensitive owner/repo, explicit ref:
        # the default branch and its explicit name give the same identity.
        return f"github:{self.owner.lower()}/{self.repo.lower()}@{self.ref}"

    @property
    def label(self) -> str:
        return f"github.com/{self.owner}/{self.repo}@{self.ref}"


def validate_ref(ref: str) -> str:
    ref = ref.strip()
    if (not _REF.match(ref) or ".." in ref or "//" in ref or ref.startswith(("/", "-", "."))
            or ref.endswith(("/", ".", ".lock"))):
        raise InvalidSource(f"Invalid branch, tag or commit: {ref!r}")
    return ref


def parse_github_url(url: str, ref: str | None = None) -> RepoSpec:
    """Accept only https://github.com/{owner}/{repo}[.git][/][/tree/{ref}]."""
    url = (url or "").strip()
    if not url or len(url) > 500 or any(c.isspace() or ord(c) < 32 for c in url):
        raise InvalidSource("Enter a GitHub repository URL like https://github.com/owner/repo")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise InvalidSource(f"Malformed URL: {exc}") from exc
    if parts.scheme != "https":
        raise InvalidSource("Only https:// GitHub URLs are supported.")
    host = (parts.hostname or "").lower()
    if parts.username is not None or parts.password is not None or port is not None \
            or parts.netloc.lower() != host or host not in URL_HOSTS:
        raise InvalidSource("Only public repositories on https://github.com are supported.")
    if parts.query or parts.fragment or "%" in parts.path:
        raise InvalidSource("Remove query strings, fragments and encoded characters from the URL.")

    segs = parts.path.split("/")[1:]
    if segs and segs[-1] == "":
        segs = segs[:-1]
    if len(segs) < 2:
        raise InvalidSource("The URL must point to a repository: https://github.com/owner/repo")
    owner, repo, rest = segs[0], segs[1], segs[2:]
    url_ref = None
    if rest:
        if rest[0] != "tree" or len(rest) < 2:
            raise InvalidSource("Use the repository URL (optionally …/tree/<branch>), not a "
                                "file, issue or other page.")
        url_ref = "/".join(rest[1:])
    elif repo.endswith(".git"):
        repo = repo[:-4]
    if not _OWNER.match(owner) or owner.lower() in RESERVED_OWNERS:
        raise InvalidSource(f"Invalid GitHub owner: {owner!r}")
    if not _REPO.match(repo) or repo in (".", "..") or repo.endswith(".git"):
        raise InvalidSource(f"Invalid GitHub repository name: {repo!r}")

    ref = (ref or "").strip() or None
    if url_ref and ref and url_ref != ref:
        raise InvalidSource(f"The URL names ref {url_ref!r} but {ref!r} was also given.")
    chosen = ref or url_ref
    return RepoSpec(owner, repo, validate_ref(chosen) if chosen else None)


# ------------------------------------------------------------------ network


def make_client(settings: Settings) -> httpx.Client:
    headers = dict(HEADERS)
    if settings.github_token and settings.github_token.get_secret_value():
        headers["Authorization"] = f"Bearer {settings.github_token.get_secret_value()}"
    return httpx.Client(
        headers=headers, follow_redirects=False, trust_env=False,
        timeout=httpx.Timeout(settings.github_timeout_s, connect=settings.github_connect_timeout_s))


def _build(client: httpx.Client, url: str) -> httpx.Request:
    """The request for one hop. Authorization is attached for the REST API only, never for the
    archive host, so the token cannot leak through a redirect."""
    request = client.build_request("GET", url)
    if (urlsplit(url).hostname or "").lower() != "api.github.com":
        request.headers.pop("Authorization", None)
    return request


def _send(client: httpx.Client, url: str) -> httpx.Response:
    """GET with manual, host-restricted redirects. Returns an open streaming response."""
    for _ in range(MAX_REDIRECTS + 1):
        try:
            resp = client.send(_build(client, url), stream=True)
        except httpx.TimeoutException as exc:
            raise DownloadFailed("GitHub did not respond in time; try again later.") from exc
        except httpx.HTTPError as exc:
            raise DownloadFailed(f"Could not reach GitHub ({type(exc).__name__}).") from exc
        if not resp.is_redirect:
            return resp
        location = resp.headers.get("location", "")
        resp.close()
        nxt = urljoin(url, location)
        target = urlsplit(nxt)
        if target.scheme != "https" or (target.hostname or "").lower() not in REDIRECT_HOSTS \
                or target.username or target.port:
            raise DownloadFailed("GitHub redirected to an unexpected location; refusing to follow.")
        url = nxt
    raise DownloadFailed("Too many redirects from GitHub.")


def _raise_for_status(resp: httpx.Response, not_found: str) -> None:
    if resp.status_code < 400:
        return
    resp.close()
    if resp.status_code in (403, 429) and (resp.headers.get("x-ratelimit-remaining") == "0"
                                          or "retry-after" in resp.headers
                                          or resp.status_code == 429):
        raise RateLimited("GitHub's rate limit was reached. Wait and try again, or upload the "
                          "repository as a ZIP. (The server admin can set GITHUB_TOKEN to raise the "
                          "limit.)")
    if resp.status_code in (404, 451):
        raise SourceNotFound(not_found)
    if resp.status_code == 403:
        raise SourceNotFound("GitHub refused access; only public repositories are supported.")
    raise DownloadFailed(f"GitHub returned HTTP {resp.status_code}.")


def resolve_repo(client: httpx.Client, spec: RepoSpec) -> ResolvedRepo:
    resp = _send(client, f"{API_BASE}/repos/{spec.owner}/{spec.repo}")
    _raise_for_status(resp, f"Repository {spec.owner}/{spec.repo} was not found or is not public.")
    try:
        body = b""
        for chunk in resp.iter_bytes():
            body += chunk
            if len(body) > MAX_METADATA_BYTES:
                raise DownloadFailed("Unexpectedly large metadata response from GitHub.")
    except httpx.HTTPError as exc:
        raise DownloadFailed(f"Reading GitHub metadata failed ({type(exc).__name__}).") from exc
    finally:
        resp.close()
    try:
        meta = json.loads(body)
    except ValueError as exc:
        raise DownloadFailed("GitHub returned invalid repository metadata.") from exc
    if not isinstance(meta, dict):
        raise DownloadFailed("GitHub returned invalid repository metadata.")
    if meta.get("private") is not False:
        raise SourceNotFound("Only public repositories are supported.")
    full_name, default_branch = meta.get("full_name"), meta.get("default_branch")
    if not isinstance(full_name, str) or full_name.count("/") != 1 \
            or not isinstance(default_branch, str):
        raise DownloadFailed("GitHub returned incomplete repository metadata.")
    owner, repo = full_name.split("/")
    if not _OWNER.match(owner) or not _REPO.match(repo):
        raise DownloadFailed("GitHub returned an unexpected repository name.")
    try:
        default_branch = validate_ref(default_branch)
    except InvalidSource as exc:
        raise DownloadFailed("GitHub returned an unsupported default branch name.") from exc
    return ResolvedRepo(owner, repo, spec.ref or default_branch, default_branch)


def download_archive(client: httpx.Client, repo: ResolvedRepo, dest: Path,
                     max_bytes: int) -> int:
    url = f"{API_BASE}/repos/{repo.owner}/{repo.repo}/zipball/{quote(repo.ref, safe='/')}"
    resp = _send(client, url)
    _raise_for_status(resp, f"Branch, tag or commit {repo.ref!r} was not found in "
                            f"{repo.owner}/{repo.repo}.")
    total = 0
    try:
        declared = resp.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise SourceTooLarge(f"Repository archive is larger than {max_bytes:,} bytes.")
        with open(dest, "wb") as fh:
            for chunk in resp.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise SourceTooLarge(f"Repository archive is larger than {max_bytes:,} bytes.")
                fh.write(chunk)
    except httpx.TimeoutException as exc:
        raise DownloadFailed("Downloading the repository archive timed out.") from exc
    except httpx.HTTPError as exc:
        raise DownloadFailed(f"Downloading the repository archive failed "
                             f"({type(exc).__name__}).") from exc
    finally:
        resp.close()
    if total == 0:
        raise DownloadFailed("GitHub returned an empty archive.")
    return total
