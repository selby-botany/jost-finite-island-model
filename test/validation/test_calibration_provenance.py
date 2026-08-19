"""R18 remediation: the statistical calibration pass stays versioned and ungated."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = PROJECT_ROOT / "build"
CI_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
CALIBRATION_SCRIPT = PROJECT_ROOT / "dev" / "bin" / "calibrate-statistical-bands"
EVIDENCE_DOC = PROJECT_ROOT / "doc" / "statistical-calibration-evidence.md"


def test_calibration_script_is_not_wired_into_the_deterministic_gate() -> None:
    """`calibrate-statistical-bands` is never invoked by `build` or `ci.yml`.

    Regression guard for R18 (`doc/dev/20260818-claude-opus-5-project-
    review-rollup.md`, not committed -- gitignored review material): "keep
    characterization out of the deterministic PR gate." A characterization
    pass is itself stochastic by design (that is the thing it measures),
    so wiring it into `build --ci` or a CI step would make the gate a
    function of the run, not only the commit -- exactly what this
    project's test-determinism rule (CLAUDE.md: "a test is a pure function
    of its commit") forbids. This asserts the invariant directly against
    the files that would carry a regression, rather than trusting it to
    stay true by omission.
    """
    assert CALIBRATION_SCRIPT.is_file()
    build_script = BUILD_SCRIPT.read_text(encoding="utf-8")

    # `build`'s lint stage does statically check this script (ruff check /
    # ruff format --check, matching every other dev/bin Python script --
    # see build's own lint invocation), so its filename legitimately
    # appears there; that reference is the *only* one allowed anywhere in
    # `build` -- never a `run dev/bin/calibrate-statistical-bands`
    # execution line.
    assert (
        "ruff check src test \\\n"
        "        dev/bin/check-doc-links dev/bin/extract-release-notes \\\n"
        "        dev/bin/calibrate-statistical-bands\n"
    ) in build_script
    assert (
        "ruff format --check src test \\\n"
        "        dev/bin/check-doc-links dev/bin/extract-release-notes \\\n"
        "        dev/bin/calibrate-statistical-bands\n"
    ) in build_script
    assert "run dev/bin/calibrate-statistical-bands" not in build_script

    assert "calibrate-statistical-bands" not in CI_WORKFLOW.read_text(encoding="utf-8")


def test_calibration_evidence_is_retained_and_versioned() -> None:
    """The characterization pass's raw output is a real, structured document.

    Regression guard for R18: the `_SIGMA_*` constants in
    `test_simulator_equilibrium.py` previously came from "an independent
    characterization pass" named only in a code comment -- no program,
    seeds, raw output, or environment fingerprint was ever retained. This
    checks the replacement evidence document actually carries that
    content, not just a placeholder file.
    """
    assert EVIDENCE_DOC.is_file()
    evidence = EVIDENCE_DOC.read_text(encoding="utf-8")

    for scenario in ("part_vi", "dear_nolan_low", "dear_nolan_high"):
        assert f"### {scenario}" in evidence
    for required_field in (
        "Characterization seed",
        "Replicates",
        "Empirical `sigma_G`",
        "Empirical `sigma_D`",
    ):
        assert required_field in evidence

    assert "## Environment" in evidence
    assert "python_version" in evidence
    assert "## Analytic bound" in evidence
    assert "## Metadata" in evidence
    assert "generator-model-token" in evidence
