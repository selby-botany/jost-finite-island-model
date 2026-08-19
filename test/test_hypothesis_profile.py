"""Regression guard for the test suite's own Hypothesis configuration."""

from __future__ import annotations

from hypothesis import settings


def test_hypothesis_profile_stays_derandomized() -> None:
    """The active Hypothesis profile never draws from real system entropy.

    Regression test for R21: `test/conftest.py` registers and loads a
    "deterministic" profile with `derandomize=True` so every
    property-based test's examples are a pure function of the test's own
    seed, not of wall-clock-seeded randomness. A silent edit to that
    profile — or a competing `load_profile` call sequenced after it by a
    future test module — would reintroduce exactly the failure mode this
    project's own "a test is a pure function of its commit" rule treats
    as a defect, not weather: a property-based test that passes or fails
    depending on when it happened to run.

    Checking `settings.default` (the profile actually active for
    whatever test runs after this one), not
    `settings.get_profile("deterministic")` (which only proves a
    correctly configured profile exists somewhere, whether or not it is
    the one in effect), is deliberate.
    """
    active_profile = settings.default
    assert active_profile is not None, "no Hypothesis profile has been loaded"
    assert active_profile.derandomize is True
