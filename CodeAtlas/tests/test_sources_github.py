"""GitHub URL/ref validation and archive retrieval against a mocked transport (no network)."""

import json

import httpx
import pytest

from codeatlas.sources import github
from codeatlas.sources.errors import (DownloadFailed, InvalidSource, RateLimited, SourceNotFound,
                                      SourceTooLarge)


# ------------------------------------------------------------------ URL validation

@pytest.mark.parametrize("url,ref,expected", [
    ("https://github.com/psf/requests", None, ("psf", "requests", None)),
    ("https://github.com/psf/requests/", None, ("psf", "requests", None)),
    ("https://github.com/psf/requests.git", None, ("psf", "requests", None)),
    ("https://www.github.com/psf/requests", None, ("psf", "requests", None)),
    ("  https://github.com/psf/requests  ", None, ("psf", "requests", None)),
    ("https://github.com/psf/requests", "v2.32.0", ("psf", "requests", "v2.32.0")),
    ("https://github.com/psf/requests/tree/main", None, ("psf", "requests", "main")),
    ("https://github.com/o/r/tree/feature/x-1", None, ("o", "r", "feature/x-1")),
    ("https://github.com/o/r/tree/main", "main", ("o", "r", "main")),
    ("https://github.com/o/my.repo_name-1", None, ("o", "my.repo_name-1", None)),
    ("https://github.com/o/r", "0123456789abcdef0123456789abcdef01234567",
     ("o", "r", "0123456789abcdef0123456789abcdef01234567")),
])
def test_valid_urls(url, ref, expected):
    spec = github.parse_github_url(url, ref)
    assert (spec.owner, spec.repo, spec.ref) == expected


@pytest.mark.parametrize("url", [
    "", "github.com/o/r", "http://github.com/o/r", "ftp://github.com/o/r",
    "https://gitlab.com/o/r", "https://github.com.evil.io/o/r", "https://evil.io/github.com/o/r",
    "https://user@github.com/o/r", "https://user:pw@github.com/o/r", "https://github.com:443/o/r",
    "https://github.com:8080/o/r", "https://github.com./o/r", "https://api.github.com/repos/o/r",
    "https://gist.github.com/o/r", "https://github.com/o", "https://github.com/",
    "https://github.com/o/r?tab=readme", "https://github.com/o/r#readme",
    "https://github.com/o/r/blob/main/x.py", "https://github.com/o/r/issues/1",
    "https://github.com/o/r/tree", "https://github.com/settings/profile",
    "https://github.com/orgs/x", "https://github.com/-bad/r", "https://github.com/o/..",
    "https://github.com/o/r%2F..", "https://github.com/o/r r", "https://github.com/o\n/r", "https://github.com/o/r\x00",
    "https://github.com/o/" + "r" * 101, "https://github.com/o/r/tree/..%2f",
])
def test_invalid_urls(url):
    with pytest.raises(InvalidSource):
        github.parse_github_url(url)


@pytest.mark.parametrize("ref", ["..", "a..b", "-rf", "/main", "main/", "a//b", "x.lock", "a b",
                                 "a~1", "a^", "a:b", "a@{1}", "r" * 201, ".hidden"])
def test_invalid_refs(ref):
    with pytest.raises(InvalidSource):
        github.parse_github_url("https://github.com/o/r", ref)


def test_conflicting_refs():
    with pytest.raises(InvalidSource, match="also given"):
        github.parse_github_url("https://github.com/o/r/tree/dev", "main")


# ------------------------------------------------------------------ mocked GitHub

ZIP_BYTES = b"PK\x03\x04fake-zip-bytes"


def meta(**over):
    return {"full_name": "Owner/Repo", "private": False, "default_branch": "main", **over}


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), headers=github.HEADERS,
                        follow_redirects=False)


def happy_handler(seen: list):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        assert "authorization" not in req.headers
        if req.url.host == "api.github.com" and req.url.path.lower() == "/repos/owner/repo":
            return httpx.Response(200, json=meta())
        if req.url.host == "api.github.com" and req.url.path.startswith("/repos/Owner/Repo/zipball/"):
            ref = req.url.path.split("/zipball/", 1)[1]
            return httpx.Response(302, headers={
                "location": f"https://codeload.github.com/Owner/Repo/legacy.zip/{ref}"})
        if req.url.host == "codeload.github.com":
            return httpx.Response(200, content=ZIP_BYTES)
        return httpx.Response(404)
    return handler


def test_default_branch_resolved_from_metadata(tmp_path):
    seen = []
    with client_for(happy_handler(seen)) as c:
        repo = github.resolve_repo(c, github.parse_github_url("https://github.com/owner/repo"))
        n = github.download_archive(c, repo, tmp_path / "r.zip", 1_000_000)
    assert (repo.owner, repo.repo, repo.ref, repo.ref_is_default) == ("Owner", "Repo", "main", True)
    assert repo.identity == "github:owner/repo@main" and repo.label == "github.com/Owner/Repo@main"
    assert n == len(ZIP_BYTES) and (tmp_path / "r.zip").read_bytes() == ZIP_BYTES
    assert seen == ["https://api.github.com/repos/owner/repo",
                    "https://api.github.com/repos/Owner/Repo/zipball/main",
                    "https://codeload.github.com/Owner/Repo/legacy.zip/main"]


def test_default_branch_and_its_explicit_name_share_an_identity():
    with client_for(happy_handler([])) as c:
        implicit = github.resolve_repo(c, github.parse_github_url("https://github.com/owner/repo"))
        explicit = github.resolve_repo(c, github.parse_github_url(
            "https://github.com/OWNER/REPO.git", "main"))
        tag = github.resolve_repo(c, github.parse_github_url("https://github.com/owner/repo", "v1"))
    assert implicit.identity == explicit.identity
    assert tag.identity == "github:owner/repo@v1" and not tag.ref_is_default


def test_ref_with_slash_is_path_encoded(tmp_path):
    seen = []
    with client_for(happy_handler(seen)) as c:
        repo = github.resolve_repo(c, github.parse_github_url(
            "https://github.com/owner/repo/tree/feature/x"))
        github.download_archive(c, repo, tmp_path / "r.zip", 1_000_000)
    assert seen[1].endswith("/zipball/feature/x")


@pytest.mark.parametrize("response,error", [
    (httpx.Response(404), SourceNotFound),
    (httpx.Response(451), SourceNotFound),
    (httpx.Response(403), SourceNotFound),
    (httpx.Response(403, headers={"x-ratelimit-remaining": "0"}), RateLimited),
    (httpx.Response(429), RateLimited),
    (httpx.Response(500), DownloadFailed),
    (httpx.Response(200, content=b"<html>not json"), DownloadFailed),
    (httpx.Response(200, json=["a", "list"]), DownloadFailed),
    (httpx.Response(200, json=meta(private=True)), SourceNotFound),
    (httpx.Response(200, json=meta(private=None)), SourceNotFound),
    (httpx.Response(200, json=meta(default_branch=None)), DownloadFailed),
    (httpx.Response(200, json=meta(default_branch="../../evil")), DownloadFailed),
    (httpx.Response(200, json=meta(full_name="a/b/c")), DownloadFailed),
    (httpx.Response(200, content=b"x" * (github.MAX_METADATA_BYTES + 1)), DownloadFailed),
])
def test_metadata_errors(response, error):
    with client_for(lambda req: response) as c, pytest.raises(error):
        github.resolve_repo(c, github.RepoSpec("owner", "repo"))


@pytest.mark.parametrize("location", [
    "https://evil.example/archive.zip", "http://codeload.github.com/x.zip",
    "https://codeload.github.com.evil.io/x.zip", "https://user@codeload.github.com/x.zip",
    "https://codeload.github.com:8443/x.zip",
])
def test_redirects_only_to_approved_https_hosts(tmp_path, location):
    def handler(req):
        if req.url.host == "api.github.com":
            return httpx.Response(302, headers={"location": location})
        raise AssertionError(f"followed a redirect to {req.url}")
    repo = github.ResolvedRepo("o", "r", "main", "main")
    with client_for(handler) as c, pytest.raises(DownloadFailed, match="unexpected location"):
        github.download_archive(c, repo, tmp_path / "r.zip", 1_000_000)


def test_redirect_loop_is_bounded(tmp_path):
    def handler(req):
        return httpx.Response(302, headers={"location": "https://api.github.com/loop"})
    with client_for(handler) as c, pytest.raises(DownloadFailed, match="Too many redirects"):
        github.download_archive(c, github.ResolvedRepo("o", "r", "main", "main"),
                                tmp_path / "r.zip", 1_000_000)


def test_missing_ref_reports_not_found(tmp_path):
    with client_for(lambda req: httpx.Response(404)) as c, \
            pytest.raises(SourceNotFound, match="nope"):
        github.download_archive(c, github.ResolvedRepo("o", "r", "nope", "main"),
                                tmp_path / "r.zip", 1_000_000)


def test_timeouts_and_connection_errors(tmp_path):
    def timeout(req):
        raise httpx.ConnectTimeout("slow", request=req)

    def refused(req):
        raise httpx.ConnectError("refused", request=req)
    for handler, match in ((timeout, "in time"), (refused, "Could not reach")):
        with client_for(handler) as c, pytest.raises(DownloadFailed, match=match):
            github.resolve_repo(c, github.RepoSpec("o", "r"))


def test_download_size_cap_declared_and_streamed(tmp_path):
    repo = github.ResolvedRepo("o", "r", "main", "main")
    declared = lambda req: httpx.Response(200, content=b"x" * 10,  # noqa: E731
                                          headers={"content-length": "999999"})
    with client_for(declared) as c, pytest.raises(SourceTooLarge):
        github.download_archive(c, repo, tmp_path / "a.zip", 1000)

    def streamed(req):  # no content-length; body bigger than the cap
        return httpx.Response(200, content=iter([b"x" * 600, b"x" * 600]))
    with client_for(streamed) as c, pytest.raises(SourceTooLarge):
        github.download_archive(c, repo, tmp_path / "b.zip", 1000)


def test_empty_archive(tmp_path):
    with client_for(lambda req: httpx.Response(200, content=b"")) as c, \
            pytest.raises(DownloadFailed, match="empty"):
        github.download_archive(c, github.ResolvedRepo("o", "r", "main", "main"),
                                tmp_path / "a.zip", 1000)


def test_client_sends_no_credentials_and_ignores_environment(offline_settings, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "should-not-be-used")
    with github.make_client(offline_settings) as c:
        assert c.trust_env is False and c.follow_redirects is False
        assert "authorization" not in {k.lower() for k in c.headers}
    assert json.dumps(github.HEADERS).lower().count("token") == 0
