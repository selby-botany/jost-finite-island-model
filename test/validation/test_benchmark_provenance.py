"""The engine-backend benchmark record stays provenanced and out of the gate.

The performance-baseline remediation this file answers asks for two
separate things, and these tests hold both in place: deterministic
structural checks belong in continuous integration, while
timing/RSS comparison stays a controlled maintainer benchmark. A
wall-clock threshold in the gate would make pass/fail a function of
runner load rather than of the commit -- what the project's own test
determinism rule forbids -- so nothing here times anything. Instead
they check that the benchmark *record* keeps the properties that make a
recorded number meaningful at all, and that the benchmark *tooling*
never leaks into the gate.
"""

from __future__ import annotations

import re
from pathlib import Path

from fim.model.params import (
    DEFAULT_AUTO_VECTOR_MAX_CAPACITY,
    DEFAULT_AUTO_VECTOR_MIN_D,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = PROJECT_ROOT / "build"
CI_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
BENCHMARK_DOC = PROJECT_ROOT / "doc" / "fim-engine-backend-benchmarks.md"

# Every script whose output is a timing or peak-RSS measurement. Named
# individually rather than globbed from `dev/bin/`, so a newly added
# benchmark script is a deliberate addition to this list (and so to the
# gate exclusion below) rather than silently inheriting the exemption.
BENCHMARK_SCRIPTS = (
    "benchmark-engines",
    "benchmark-queue",
    "generate-heatmap-queue",
    "render-heatmap",
    "calibrate-auto-threshold",
)

# A table's own `#### Setup` list must answer all three of these before
# its numbers mean anything: which code produced them, on what hardware,
# and when. The document's own opening paragraph states this contract
# ("each table stamped with the one thing worth dating: the exact commit
# and hardware that produced it"); these are that sentence, enforced.
REQUIRED_PROVENANCE_FIELDS = ("- Commit:", "- System:", "- Run Date:")


def _benchmark_document() -> str:
    """Return the benchmark document's full text."""
    assert BENCHMARK_DOC.is_file()
    return BENCHMARK_DOC.read_text(encoding="utf-8")


def _table_sections() -> dict[str, str]:
    """Return each `### B.x` section's own body, keyed by its heading text.

    Splits on the `### ` level specifically: `#### Setup`/`#### Results`
    are a table's own subsections and must stay inside its body, while
    `## Reading the tables`/`## Known gaps` are not tables at all and
    are excluded by the `B.` prefix check.
    """
    document = _benchmark_document()
    sections: dict[str, str] = {}
    for match in re.finditer(
        r"^### (B\.\S+.*?)$(.*?)(?=^### |^## |\Z)",
        document,
        flags=re.MULTILINE | re.DOTALL,
    ):
        sections[match.group(1).strip()] = match.group(2)
    return sections


def test_benchmark_tooling_is_never_executed_by_the_deterministic_gate() -> None:
    """No benchmark script is invoked by `build` or by `ci.yml`.

    The same invariant `test_calibration_provenance.py` asserts for
    `calibrate-statistical-bands`, for the timing/RSS tooling instead: a
    benchmark's result is a property of the machine and its momentary
    load, not of the commit, so running one inside the gate would make
    the gate non-deterministic by construction. This checks the two
    files that would actually carry such a regression, rather than
    trusting the exclusion to stay true by nobody thinking of it.

    Deliberately an absence check, not a lint-line check: these scripts
    are not currently part of `build`'s own `ruff` argument list either
    (unlike `calibrate-statistical-bands`, which is), so their names are
    expected nowhere in `build` at all. Were they added to the lint
    list later -- a reasonable change, and independent of this one --
    only the `run ...` half of this assertion would still apply.
    """
    build_script = BUILD_SCRIPT.read_text(encoding="utf-8")
    ci_workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    for script in BENCHMARK_SCRIPTS:
        assert (PROJECT_ROOT / "dev" / "bin" / script).is_file(), script
        assert f"run dev/bin/{script}" not in build_script, script
        assert f"dev/bin/{script}" not in ci_workflow, script


def test_every_benchmark_table_records_its_commit_and_hardware() -> None:
    """Each `B.x` section states the commit, machine, and date behind its numbers.

    Without this, a table added in a hurry reads exactly like a fully
    provenanced one -- a number with no commit or machine beside it
    looks like current behavior on the reader's own hardware, and is
    not. Asserted per section rather than by counting occurrences
    document-wide, so a section that omits one field cannot be covered
    for by another section that happens to state it twice.
    """
    sections = _table_sections()
    assert len(sections) >= 6, sorted(sections)

    for heading, body in sections.items():
        for field in REQUIRED_PROVENANCE_FIELDS:
            assert field in body, (heading, field)


def test_auto_vector_defaults_match_the_recorded_benchmark_conclusion() -> None:
    """The shipped `"auto"` cutover is the one the recorded sweep concluded.

    `auto_vector_min_d`/`auto_vector_max_capacity` are the only two
    constants in the package whose values are justified purely by a
    benchmark result rather than by anything checkable from the code
    itself. The remediation this file answers asks that they be revised
    "only from a recorded benchmark result, not from an unqualified
    machine-specific observation" -- which is enforceable exactly here:
    changing either default without updating the document that justifies
    it fails, so the edit has to name its own evidence.
    """
    document = _benchmark_document()

    assert f"auto_vector_min_d={DEFAULT_AUTO_VECTOR_MIN_D}" in document
    assert f"auto_vector_max_capacity={DEFAULT_AUTO_VECTOR_MAX_CAPACITY}" in document


def test_benchmark_document_records_its_unmeasured_axes() -> None:
    """The replicate-concurrency gap is named in the record, not silently absent.

    `benchmark-engines` sweeps `d`, `N`, `mu`, `m`, locus length, and
    `n_replicates`; it has no `max_concurrent_replicates` axis at all,
    and no table records either replicate axis. A reader comparing this
    document against the remediation's own list of axes to sweep
    ("demes, loci, capacity, and replicate concurrency separately")
    would otherwise have to infer the omission from what is not there.
    """
    document = _benchmark_document()

    assert "## Known gaps: axes not yet measured" in document
    gaps = document.split("## Known gaps: axes not yet measured", 1)[1]
    assert "max_concurrent_replicates" in gaps
    assert "n_replicates" in gaps
