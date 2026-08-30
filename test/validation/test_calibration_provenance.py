"""R18 remediation: the statistical calibration pass stays versioned and ungated."""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = PROJECT_ROOT / "build"
CI_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
CALIBRATION_SCRIPT = PROJECT_ROOT / "dev" / "bin" / "calibrate-statistical-bands"
EVIDENCE_DATA = (
    PROJECT_ROOT / "test" / "validation" / "statistical-calibration-evidence.json"
)
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
        "ruff check src test dev/lib \\\n"
        "        dev/bin/check-doc-links dev/bin/extract-release-notes \\\n"
        "        dev/bin/calibrate-statistical-bands dev/bin/generate-help-html\n"
    ) in build_script
    assert (
        "ruff format --check src test dev/lib \\\n"
        "        dev/bin/check-doc-links dev/bin/extract-release-notes \\\n"
        "        dev/bin/calibrate-statistical-bands dev/bin/generate-help-html\n"
    ) in build_script
    assert "run dev/bin/calibrate-statistical-bands" not in build_script

    assert "calibrate-statistical-bands" not in CI_WORKFLOW.read_text(encoding="utf-8")


def test_calibration_evidence_data_is_retained_and_versioned() -> None:
    """The characterization pass's generated data is retained and versioned.

    Regression guard for R18: the `_SIGMA_*` constants in
    `test_simulator_equilibrium.py` previously came from "an independent
    characterization pass" named only in a code comment -- no program,
    seeds, raw output, or environment fingerprint was ever retained. This
    checks the replacement generated evidence artifact carries that
    content, not just a placeholder file.
    """
    assert EVIDENCE_DATA.is_file()
    evidence = json.loads(EVIDENCE_DATA.read_text(encoding="utf-8"))
    assert evidence["schema_version"] == 1
    assert evidence["generator"]["script"] == "dev/bin/calibrate-statistical-bands"
    assert "python_version" in evidence["environment"]

    scenarios = evidence["scenarios"]
    for scenario_name in (
        "part_vi",
        "dear_nolan_low",
        "dear_nolan_high",
        "crow_aoki_torus",
    ):
        scenario = scenarios[scenario_name]
        assert scenario["replicates"] >= 2
        assert scenario["empirical_sigma_g"] > 0.0
        assert scenario["empirical_sigma_d"] > 0.0
        assert scenario["assertion_sigma_g"] > 0.0
        assert scenario["assertion_sigma_d"] > 0.0


def test_chao_shannon_equilibrium_evidence_is_retained_and_versioned() -> None:
    """The Chao-Shannon scenario's own (differently-shaped) evidence is retained.

    The `chao_shannon_equilibrium` counterpart to the test above: three
    named statistics (`total_entropy`/`subpopulation_entropy`/
    `shannon_differentiation`), not the two (`G_ST`/`D`) every other
    scenario shares -- see `dev/bin/calibrate-statistical-bands`'s own
    `_characterize_chao_shannon_equilibrium` docstring for why this one
    scenario's evidence uses its own schema instead of being folded into
    the loop above.
    """
    evidence = json.loads(EVIDENCE_DATA.read_text(encoding="utf-8"))
    scenario = evidence["scenarios"]["chao_shannon_equilibrium"]
    assert scenario["replicates"] >= 2
    for statistic_name in (
        "total_entropy",
        "subpopulation_entropy",
        "shannon_differentiation",
    ):
        statistic = scenario[statistic_name]
        assert statistic["empirical_sigma"] > 0.0
        assert statistic["recommended_sigma"] > 0.0
        assert len(statistic["values"]) == scenario["replicates"]


def test_user_facing_calibration_doc_is_present() -> None:
    """The user-facing calibration document is retained and script-linked."""
    assert EVIDENCE_DOC.is_file()
    doc = EVIDENCE_DOC.read_text(encoding="utf-8")

    assert "_SIGMA_" in doc
    assert "dev/bin/calibrate-statistical-bands" in doc
