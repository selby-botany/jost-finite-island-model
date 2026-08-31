"""Explicit, opt-in release-check logic shared by every front end.

This is the code behind "Check for updates": it asks GitHub whether a
newer version of `fim` has been released than the one currently
installed, and if so, reports the new version's own name and the web
page describing it — it never downloads or installs anything itself,
only checks and reports. It is also, deliberately, the *only* code
anywhere in this entire project that ever makes a network connection to
anywhere at all (see `SECURITY.md`'s own threat model) — every other
part of `fim` runs entirely offline, with no network access needed or
attempted. That single network call happens only when a person
explicitly asks for it (the command line's own `fim update --check`, or
the desktop app's own "Check for updates" menu item) — nothing here ever
runs automatically in the background, on a timer, or as a side effect of
an ordinary simulation run.

Extracted from `fim.cli` (`doc/fim-gui-design.md` §12) so `fim.gui`'s
"Check for updates" action performs exactly the same GitHub Releases
lookup and version comparison as `fim update --check`, rather than a
second implementation of the one network operation `SECURITY.md`'s
threat model permits.
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
    """Compare two three-part semantic versions.

    "Semantic versioning" is the widely used convention of naming a
    release `MAJOR.MINOR.PATCH` (for example `1.4.2`) so that comparing
    two version numbers to see which is newer is unambiguous and does
    not require knowing anything about what actually changed between
    them — this project's own releases follow that convention (see
    `version_parts`, below, for exactly what counts as valid). Follows
    the conventional three-way comparison result used throughout this
    project and its standard library: a negative number (always exactly
    ``-1`` here, since only one comparison is ever done) means `current`
    is older than `latest`, ``0`` means the two are the same release,
    and a positive number (``1``) means `current` is actually newer —
    which can genuinely happen for a development build running ahead of
    the latest *published* release, not just a symptom of something
    wrong.
    """
    current_parts = version_parts(current)
    latest_parts = version_parts(latest)
    return (current_parts > latest_parts) - (current_parts < latest_parts)


def fetch_latest_release() -> Mapping[str, Any]:
    """Fetch the latest GitHub release; this is the sole network path.

    Reads GitHub's own public "latest release" API for this project — no
    authentication, no data about the user or their machine sent beyond
    what any web request inherently reveals (the requesting program's
    own name and version, in the `User-Agent` header, which is
    standard, polite practice for identifying a program to a server, the
    same way a web browser identifies itself). `timeout=5` (seconds)
    means a genuinely unreachable network fails quickly and reports an
    error, rather than leaving the caller waiting indefinitely for a
    response that may never come.
    """
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
    """Validate the two release fields needed by an update check.

    Takes `fetcher` (defaulting to the real `fetch_latest_release` above)
    as an argument, rather than calling it directly, purely so a test
    can supply a fake one that returns a fixed, made-up response instead
    of making a real network call — the same "let the caller decide
    where the data comes from" pattern used throughout this project
    wherever something needs to be tested without touching the real
    network, filesystem, or clock.

    GitHub's own response is a large object with many fields; this
    function pulls out and checks only the two this project actually
    needs (the release's own version tag, and the web page describing
    it), raising a clear, specific error immediately if either is
    missing or is not the kind of value expected, rather than letting a
    caller further away discover the problem indirectly, later, as a
    confusing `TypeError` or `KeyError` somewhere else entirely.
    """
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
    """Parse a stable three-part semantic version.

    See `compare_versions`'s own docstring, above, for what "semantic
    version" means. A real release tag on GitHub is written with a
    leading "v" (`v1.4.2`); callers are expected to strip that off
    before calling this function, which only ever handles the bare
    `MAJOR.MINOR.PATCH` numbers themselves.
    """
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
