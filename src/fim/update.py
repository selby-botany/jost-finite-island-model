"""Explicit, opt-in release-check logic shared by every front end.

Extracted from `fim.cli` (design doc `20260819-claude-sonnet-5-graphical-
interface.md` §3.9) so `fim.gui`'s "Check for updates" action performs
exactly the same GitHub Releases lookup and version comparison as
`fim update --check`, rather than a second implementation of the one
network operation SECURITY.md's threat model permits. This module never
runs on its own; every function here is reached only by an explicit,
user-initiated caller.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, TypeAlias
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fim import __version__

RELEASES_API = (
    "https://api.github.com/repos/selby-botany/jost-finite-island-model/releases/latest"
)
SEMANTIC_VERSION_PARTS = 3

ReleaseFetcher: TypeAlias = Callable[[], Mapping[str, Any]]


def compare_versions(current: str, latest: str) -> int:
    """Compare two three-part semantic versions."""
    current_parts = version_parts(current)
    latest_parts = version_parts(latest)
    return (current_parts > latest_parts) - (current_parts < latest_parts)


def fetch_latest_release() -> Mapping[str, Any]:
    """Fetch the latest GitHub release; this is the sole network path."""
    request = Request(
        RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"fim/{__version__}",
        },
    )
    try:
        with urlopen(request, timeout=5) as response:
            payload = json.load(response)
    except (HTTPError, URLError) as error:
        raise RuntimeError(f"update check failed: {error}") from error
    if not isinstance(payload, Mapping):
        raise RuntimeError("update check returned a non-object response")
    return payload


def latest_release(
    fetcher: ReleaseFetcher = fetch_latest_release,
) -> tuple[str, str]:
    """Validate the two release fields needed by an update check."""
    payload = fetcher()
    tag = payload.get("tag_name")
    release_url = payload.get("html_url")
    if not isinstance(tag, str) or not tag:
        raise RuntimeError("latest release response is missing tag_name")
    if not isinstance(release_url, str) or not release_url:
        raise RuntimeError("latest release response is missing html_url")
    version_parts(tag.removeprefix("v"))
    return tag, release_url


def version_parts(value: str) -> tuple[int, int, int]:
    """Parse a stable three-part semantic version."""
    parts = value.split(".")
    if len(parts) != SEMANTIC_VERSION_PARTS:
        raise RuntimeError(f"release version is not semantic: {value}")
    try:
        parsed = tuple(int(part) for part in parts)
    except ValueError as error:
        raise RuntimeError(f"release version is not semantic: {value}") from error
    if any(part < 0 for part in parsed):
        raise RuntimeError(f"release version is not semantic: {value}")
    return parsed  # type: ignore[return-value]
