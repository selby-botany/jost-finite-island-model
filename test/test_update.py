"""Unit tests for the shared, explicit, opt-in release-check logic."""

from __future__ import annotations

import json
from email.message import Message
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from fim import update


class _ReleaseResponse:
    """Minimal context-managed response for the urllib release client."""

    def __init__(self, payload: object) -> None:
        self._payload = payload

    def __enter__(self) -> _ReleaseResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, *_args: object) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


@pytest.mark.parametrize(
    "error",
    [
        # `hdrs` is typed as `email.message.Message`, not a bare dict — an
        # empty `Message()` is what a header-less real response carries.
        HTTPError("https://example.invalid", 500, "bad", Message(), None),
        URLError("offline"),
        # Regression case for FIM-04: `except (HTTPError, URLError)` used
        # to let a raw `TimeoutError` (a request exceeding `timeout=5`)
        # escape this function uncaught, breaking its own documented
        # `RuntimeError` contract — `TimeoutError` is neither `HTTPError`
        # nor `URLError`, but is an `OSError` subclass, same as both of
        # those.
        TimeoutError("timed out"),
    ],
)
def test_fetch_latest_release_wraps_network_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    """Network failures become the documented runtime error contract."""

    def fail(*args: object, **kwargs: object) -> Any:
        raise error

    monkeypatch.setattr(update, "urlopen", fail)
    with pytest.raises(RuntimeError, match="update check failed"):
        update.fetch_latest_release()


class _MalformedJsonResponse:
    """A response whose body is not valid JSON at all."""

    def __enter__(self) -> _MalformedJsonResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, *_args: object) -> bytes:
        return b"not json at all {"


def test_fetch_latest_release_wraps_malformed_json_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response body that is not valid JSON becomes the documented contract.

    Regression test for FIM-04: `except (HTTPError, URLError)` used to
    let a `json.JSONDecodeError` escape this function uncaught for a
    genuinely malformed response body, rather than this function's own
    documented `RuntimeError` contract.
    """
    monkeypatch.setattr(
        update, "urlopen", lambda _request, **_kwargs: _MalformedJsonResponse()
    )
    with pytest.raises(RuntimeError, match="update check failed"):
        update.fetch_latest_release()


def test_fetch_latest_release_rejects_non_object_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful HTTP response still requires an object payload."""
    monkeypatch.setattr(
        update,
        "urlopen",
        lambda _request, **_kwargs: _ReleaseResponse(["not", "an", "object"]),
    )
    with pytest.raises(RuntimeError, match="non-object"):
        update.fetch_latest_release()


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "tag_name"),
        ({"tag_name": "v1.0.0"}, "html_url"),
        ({"tag_name": "v1", "html_url": "x"}, "not semantic"),
        ({"tag_name": "v1.0.x", "html_url": "x"}, "not semantic"),
        ({"tag_name": "v-1.0.0", "html_url": "x"}, "not semantic"),
    ],
)
def test_latest_release_validates_release_fields(
    payload: dict[str, object],
    message: str,
) -> None:
    """Update checks reject incomplete and malformed release metadata."""
    with pytest.raises(RuntimeError, match=message):
        update.latest_release(lambda: payload)


@pytest.mark.parametrize(
    ("current", "latest", "expected"),
    [("1.0.0", "1.0.0", 0), ("1.0.0", "1.0.1", -1), ("1.0.1", "1.0.0", 1)],
)
def test_version_comparison_is_stable(
    current: str,
    latest: str,
    expected: int,
) -> None:
    """Semantic version comparison has deterministic, symmetric output."""
    assert update.compare_versions(current, latest) == expected


@pytest.mark.parametrize("value", ["1.0", "1.0.0.0", "1.a.0", "-1.0.0"])
def test_version_parser_rejects_non_semantic_values(value: str) -> None:
    """Release version parsing requires exactly three non-negative integers."""
    with pytest.raises(RuntimeError, match="not semantic"):
        update.version_parts(value)
