"""Every worked example agrees with its committed output under `auto`.

Read-only examples design (2026-10-05, `selby/restricted`), section 7:
each `doc/examples/<id>/config.yaml` is run again through the real
`fim run` command with only `engine_backend` changed to `auto`, and the
result is compared with the output committed beside the configuration
(produced by `dev/bin/regenerate-example-outputs` on the backend the
configuration names, `lineal` unless it names another).

Which comparison applies depends on the backend `auto` resolves to, read
from the fresh run's own manifest, not predicted here:

- **Same random stream: identical reports.** `auto` never resolves to
  `lineal` (`fim.engine._resolve_auto_engine_backend`), but both
  `generational` with its default sequential advancer and
  `generational-vector` reproduce `lineal` bit for bit for the same seed,
  under either mutation model (`GenerationalBackend`'s own docstring and
  `fim.model.vector_block`, checked by the golden-parity engine tests and
  `test/engine/test_vector_parity.py`). That holds for an adaptive batch
  (`stop_batch_early` on) too: every backend judges the adaptive stop
  on replicates in replicate order (`fim.engine.run_batch`'s own
  docstring), so all keep the same replicates. So a committed `lineal`
  or `generational` output and a fresh `generational` or
  `generational-vector` run must agree exactly, and so must two local
  `generational-vector` runs. A committed *vector* output is the one
  exception: it may have been archived by an older vector engine whose
  random stream differed from `lineal`'s (it was statistically, not
  bit-for-bit, equal to `lineal` before the compiled kernel replaced its
  BLAS migration), or on another platform. Vector cases therefore also
  run the configured backend locally for exact identity, and use the
  statistical rule against the archived output until it is regenerated.
  Exact local identity is stronger than the design's statistical rule and
  implies it, so it is the rule used wherever it holds: a difference is a
  defect, never noise.
- **Different random stream, single run: window means.** Applies only
  when a committed archive is on a different stream from the fresh run
  (above). For every watched statistic (the configuration's
  `convergence_statistic`), the two `report.json` `window_statistics`
  means must agree within `3 * sqrt(se_L**2 + se_auto**2)`.
- **Different random stream, batch: interval half-widths.** For every
  watched statistic, the two `summary.json` across-replicate means must
  agree within the sum of the two confidence-interval half-widths.

Archived evidence-window floats allow numerical rounding (relative `1e-12`,
absolute `1e-14`): FFT and BLAS reductions need not round identically across
platforms, even for identical histories. Structure, discrete values, other
report fields, and same-host configured/auto comparisons remain exact.
The local reference explicitly selects the backend `auto` resolved to,
not a potentially much slower archived backend. Cross-backend bit identity
is independently covered by the golden-parity engine and vector tests.

Fields excluded from "identical": only `run_id`, in each report. A run ID
is a digest of the configuration (`fim.model.params`), and the two
configurations legitimately differ in `engine_backend` (`auto` against
the committed name). A batch `summary.json` holds no run ID and is
compared whole, as is the list of kept replicate directories.

Missing data, in the statistical rules:

- A watched statistic with no window statistics (or a null standard
  error) in *both* reports means the run was too short to estimate its
  own noise, as in a one-generation run. The only evidence left is the
  final value, so the two final values must agree to rounding
  (`abs=1e-12`): such a run is meaningful to compare only when it is
  deterministic, and a stochastic one fails here loudly rather than
  passing on no evidence.
- Window statistics present in only one report is a structural
  disagreement between the runs and fails.
- A batch watched statistic without a mean and half-width in either
  `summary.json` fails: there is nothing to compare.

Every case runs a fixed seed from its configuration, so its outcome is a
pure function of the commit; a failure is investigated, never retried or
widened. Each case's measured wall time is recorded in
`_RUNTIME_SECONDS`, and cases over about a minute are also marked `slow`.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from example_support import archived_report_equal

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = ROOT / "doc" / "examples"

# Measured wall time of each case on the development machine (2026-10-08,
# Apple Silicon, shared: three cases at once, load average varying from
# about 10 to 60), in whole seconds. Recorded so a reader can budget a run;
# over `_SLOW_SECONDS` also marks `slow`.
_RUNTIME_SECONDS: dict[str, int] = {
    "a-large-d-batch-under-generational-vector": 92,
    "a-long-locus-batch-under-the-generational-engine": 60,
    "an-adaptive-replicate-batch-with-a-confidence-interval": 395,
    "dear-nolan-high": 312,
    "dear-nolan-low": 2271,
    "equilibrium-split-founding": 12,
    "finite-length-alleles-the-k-allele-model": 10,
    "golden-part-vi": 221,
    "kimura-weiss-isolation-by-distance": 26,
    "literature-distance-statistics-from-an-explicit-founder-split": 4,
    "per-base-mutation-rate-across-unequal-locus-lengths": 2,
    "several-convergence-statistics": 9,
    "stepping-stone-spatial-migration": 25,
    "stochastic-migrant-counts": 20,
    "unequal-island-sizes-with-a-migration-hub": 21,
    "within-run-sigma-band": 104,
    "wright-takahata-finite-deme-correction": 35,
}
_SLOW_SECONDS = 60

# Backends whose runs draw the same random stream for the same seed, so
# their outputs are identical (see the module docstring). Backend V joined
# `lineal`'s stream when its kernel became bit-identical to Backends L and
# G; an older archived vector output is handled separately in the test.
_STREAM: dict[str, str] = {
    "lineal": "lineal",
    "generational": "lineal",
    "generational-vector": "lineal",
}

# A run ID digests the configuration, which differs in `engine_backend`.
_IDENTITY_EXCLUDED_FIELDS = frozenset({"run_id"})


def _case(example: str) -> Any:
    """Return one example's parameter, marked `slow` when it is long.

    Args:
        example: The example directory name.

    Returns:
        A `pytest.param` for `example`.
    """
    slow = _RUNTIME_SECONDS.get(example, 0) > _SLOW_SECONDS
    return pytest.param(example, marks=[pytest.mark.slow] if slow else [])


def _compare_batch_statistically(
    committed: Path, fresh: Path, watched: list[str]
) -> list[str]:
    """Compare two batches' across-replicate means of watched statistics.

    Args:
        committed: The committed example directory.
        fresh: The fresh run's output directory.
        watched: The watched statistic names.

    Returns:
        One message per disagreement; empty when they agree.
    """
    summary_l = _load(committed / "summary.json")
    summary_a = _load(fresh / "summary.json")
    problems: list[str] = []
    for name in watched:
        interval_l = _interval(summary_l, name)
        interval_a = _interval(summary_a, name)
        if interval_l is None or interval_a is None:
            problems.append(f"{name}: no mean and half-width to compare")
            continue
        difference = abs(interval_l[0] - interval_a[0])
        bound = interval_l[1] + interval_a[1]
        if difference > bound:
            problems.append(
                f"{name}: means {interval_l[0]:.6g} and {interval_a[0]:.6g} "
                f"differ by {difference:.3g} > half-width sum {bound:.3g}"
            )
    return problems


def _compare_identical(
    committed: Path, fresh: Path, *, archive: bool = False
) -> list[str]:
    """Require identical reports, and for a batch an identical summary.

    Args:
        committed: The committed example directory.
        fresh: The fresh run's output directory.
        archive: Allow platform rounding in archived evidence-window floats.

    Returns:
        One message per differing file; empty when all agree.
    """
    problems: list[str] = []
    if (committed / "summary.json").is_file():
        # A batch: the same summary and the same kept replicates.
        if _load(committed / "summary.json") != _load(fresh / "summary.json"):
            problems.append("summary.json differs")
        names_l = sorted(p.name for p in committed.glob("replicate-*"))
        names_a = sorted(p.name for p in fresh.glob("replicate-*"))
        if names_l != names_a:
            problems.append(f"replicates {names_l} != {names_a}")
        reports = [f"{name}/report.json" for name in names_l if name in names_a]
    else:
        reports = ["report.json"]
    for report in reports:
        expected = _without_excluded(_load(committed / report))
        actual = _without_excluded(_load(fresh / report))
        equal = (
            archived_report_equal(expected, actual) if archive else expected == actual
        )
        if not equal:
            keys = sorted(
                key
                for key in expected.keys() | actual.keys()
                if expected.get(key) != actual.get(key)
            )
            problems.append(f"{report} differs in {keys}")
    return problems


def _compare_scalar_statistically(
    committed: Path, fresh: Path, watched: list[str]
) -> list[str]:
    """Compare two single runs' window means of watched statistics.

    Args:
        committed: The committed example directory.
        fresh: The fresh run's output directory.
        watched: The watched statistic names.

    Returns:
        One message per disagreement; empty when they agree.
    """
    report_l = _load(committed / "report.json")
    report_a = _load(fresh / "report.json")
    problems: list[str] = []
    for name in watched:
        window_l = _window(report_l, name)
        window_a = _window(report_a, name)

        # Too short a run for a noise estimate: only exact agreement of
        # the final values is evidence (see the module docstring).
        if window_l is None and window_a is None:
            if report_l.get(name) != pytest.approx(report_a.get(name), abs=1e-12):
                problems.append(
                    f"{name}: no window statistics, and final values "
                    f"{report_l.get(name)!r} != {report_a.get(name)!r}"
                )
            continue
        if window_l is None or window_a is None:
            problems.append(f"{name}: window statistics in only one report")
            continue

        # Both windows present: the 3-sigma rule on the window means.
        difference = abs(window_l[0] - window_a[0])
        bound = 3 * math.sqrt(window_l[1] ** 2 + window_a[1] ** 2)
        if difference > bound:
            problems.append(
                f"{name}: window means {window_l[0]:.6g} and {window_a[0]:.6g} "
                f"differ by {difference:.3g} > 3 * combined se {bound:.3g}"
            )
    return problems


def _example_ids() -> list[str]:
    """Return every example with a runnable configuration, sorted.

    Returns:
        The names of the `doc/examples/*` directories with a `config.yaml`.
    """
    return sorted(path.parent.name for path in EXAMPLES_DIR.glob("*/config.yaml"))


def _interval(summary: dict[str, Any], name: str) -> tuple[float, float] | None:
    """Return a statistic's across-replicate mean and half-width, if both exist.

    Args:
        summary: A decoded batch `summary.json`.
        name: The statistic name.

    Returns:
        `(mean, half_width)`, or `None` when either is missing.
    """
    entry = summary.get(name) or {}
    mean, half_width = entry.get("mean"), entry.get("half_width")
    if mean is None or half_width is None:
        return None
    return float(mean), float(half_width)


def _load(path: Path) -> dict[str, Any]:
    """Return a JSON object read from `path`.

    Args:
        path: A JSON file holding an object.

    Returns:
        The decoded object.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path} is not a JSON object"
    return data


def _resolved_backend(directory: Path) -> str:
    """Return the backend a run actually ran on, as its manifests record.

    Args:
        directory: A committed example directory or fresh output directory.

    Returns:
        The resolved `engine_backend`, the same for every replicate.
    """
    manifests = [directory / "manifest.json"]
    if (directory / "summary.json").is_file():
        # A batch manifest records only the configured name; each
        # replicate's own manifest records the backend it resolved to.
        manifests = sorted(directory.glob("replicate-*/manifest.json"))
    backends = {_load(path)["engine_backend"] for path in manifests}
    assert len(backends) == 1, f"{directory}: replicates disagree: {backends}"
    return str(backends.pop())


def _run_example(example: str, tmp_path: Path, *, backend: str = "auto") -> Path:
    """Run one example's configuration with the requested backend.

    Args:
        example: The example directory name.
        tmp_path: A private temporary directory for this run.
        backend: Backend override; defaults to `auto`.

    Returns:
        The fresh run's output directory.
    """
    # Copy the configuration, changing only the backend.
    configuration = yaml.safe_load(
        (EXAMPLES_DIR / example / "config.yaml").read_text(encoding="utf-8")
    )
    assert isinstance(configuration, dict)
    configuration["engine_backend"] = backend
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(configuration, sort_keys=False), "utf-8")

    # Run it exactly as `dev/bin/regenerate-example-outputs` ran the
    # committed one, with the Study index kept out of the real `results/`.
    output = tmp_path / "output"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "fim.launcher",
            "run",
            str(config),
            "--output",
            str(output),
            "--quiet",
        ],
        check=True,
        cwd=ROOT,
        env={**os.environ, "FIM_RESULTS_DIRECTORY": str(tmp_path / "results")},
    )
    return output


def _watched(directory: Path) -> list[str]:
    """Return the committed run's watched statistic names.

    Args:
        directory: The committed example directory.

    Returns:
        The configuration's `convergence_statistic`, as a list.
    """
    manifest = _load(directory / "manifest.json")
    watched = manifest["parameters"]["convergence_statistic"]
    return [watched] if isinstance(watched, str) else list(watched)


def _window(report: dict[str, Any], name: str) -> tuple[float, float] | None:
    """Return a statistic's window mean and standard error, if both exist.

    Args:
        report: A decoded `report.json`.
        name: The statistic name.

    Returns:
        `(mean, standard_error)`, or `None` when either is missing.
    """
    entry = (report.get("window_statistics") or {}).get(name) or {}
    mean, error = entry.get("mean"), entry.get("standard_error")
    if mean is None or error is None:
        return None
    return float(mean), float(error)


def _without_excluded(report: dict[str, Any]) -> dict[str, Any]:
    """Return `report` without the fields excluded from identity.

    Args:
        report: A decoded `report.json`.

    Returns:
        A copy without `_IDENTITY_EXCLUDED_FIELDS`.
    """
    return {k: v for k, v in report.items() if k not in _IDENTITY_EXCLUDED_FIELDS}


def _write(path: Path, data: dict[str, Any]) -> None:
    """Write `data` as JSON to `path`, creating its directory.

    Args:
        path: The file to write.
        data: The object to encode.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_batch_rule_uses_the_sum_of_half_widths(tmp_path: Path) -> None:
    """A batch disagreement is a mean difference beyond both half-widths.

    No example's `auto` run lands on a different random stream from a
    committed non-vector archive as a batch, so the rule is checked here on
    synthetic summaries.
    """
    committed, fresh = tmp_path / "l", tmp_path / "a"
    _write(committed / "summary.json", {"D": {"mean": 0.30, "half_width": 0.02}})
    _write(fresh / "summary.json", {"D": {"mean": 0.34, "half_width": 0.03}})
    assert _compare_batch_statistically(committed, fresh, ["D"]) == []
    _write(fresh / "summary.json", {"D": {"mean": 0.36, "half_width": 0.03}})
    assert _compare_batch_statistically(committed, fresh, ["D"])
    assert _compare_batch_statistically(committed, fresh, ["G_ST"])


def test_identity_rule_ignores_only_the_run_id(tmp_path: Path) -> None:
    """Identical reports may differ in `run_id` and in nothing else."""
    committed, fresh = tmp_path / "l", tmp_path / "a"
    _write(committed / "report.json", {"run_id": "run-1", "D": 0.5})
    _write(fresh / "report.json", {"run_id": "run-2", "D": 0.5})
    assert _compare_identical(committed, fresh) == []
    _write(fresh / "report.json", {"run_id": "run-2", "D": 0.5000001})
    assert _compare_identical(committed, fresh) == ["report.json differs in ['D']"]


def test_identity_rule_requires_the_same_kept_replicates(tmp_path: Path) -> None:
    """A batch, adaptive or not, must keep the same replicates to agree.

    An adaptive batch on the same random stream keeps the same replicates
    on every backend, so a different kept set is a failure even when the
    summaries happen to match.
    """
    committed, fresh = tmp_path / "l", tmp_path / "a"
    for directory in (committed, fresh):
        _write(directory / "summary.json", {"D": {"mean": 0.3, "half_width": 0.02}})
        _write(directory / "replicate-001" / "report.json", {"run_id": "1", "D": 0.3})
    assert _compare_identical(committed, fresh) == []
    _write(fresh / "replicate-002" / "report.json", {"run_id": "2", "D": 0.3})
    assert _compare_identical(committed, fresh) == [
        "replicates ['replicate-001'] != ['replicate-001', 'replicate-002']"
    ]


@pytest.mark.parametrize("local_difference", [False, True])
def test_rounded_archive_still_requires_exact_local_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local_difference: bool
) -> None:
    """Archive rounding cannot hide even one ULP of local backend drift."""
    example = "rounded-fixture"
    examples = tmp_path / "examples"
    committed, fresh, reference = (
        examples / example,
        tmp_path / "fresh",
        tmp_path / "reference",
    )
    for directory, mean, backend in (
        (committed, 0.3, "lineal"),
        (fresh, math.nextafter(0.3, math.inf), "generational"),
        (
            reference,
            0.3 if local_difference else math.nextafter(0.3, math.inf),
            "generational",
        ),
    ):
        _write(directory / "manifest.json", {"engine_backend": backend})
        _write(
            directory / "report.json",
            {"D": 0.3, "window_statistics": {"D": {"mean": mean}}},
        )

    requested: list[str] = []

    def run_example(example: str, tmp_path: Path, *, backend: str = "auto") -> Path:
        """Return controlled archive, auto, and configured outputs."""
        requested.append(backend)
        return fresh if backend == "auto" else reference

    module = sys.modules[__name__]
    monkeypatch.setattr(module, "EXAMPLES_DIR", examples)
    monkeypatch.setattr(module, "_run_example", run_example)
    if local_difference:
        with pytest.raises(AssertionError, match="local configured backend"):
            test_example_on_auto_agrees_with_its_committed_output(example, tmp_path)
    else:
        test_example_on_auto_agrees_with_its_committed_output(example, tmp_path)
    assert requested == ["auto", "generational"]


def test_scalar_rule_uses_three_combined_standard_errors(tmp_path: Path) -> None:
    """A single-run disagreement is beyond `3 * sqrt(se_L**2 + se_auto**2)`.

    Combined standard error 0.05 here, so the bound is 0.15. With no
    window statistics in either report, final values must agree exactly.
    """
    committed, fresh = tmp_path / "l", tmp_path / "a"

    def report(mean: float, error: float) -> dict[str, Any]:
        """Return a report with one D window."""
        window = {"D": {"mean": mean, "standard_error": error}}
        return {"D": mean, "window_statistics": window}

    _write(committed / "report.json", report(0.50, 0.03))
    _write(fresh / "report.json", report(0.64, 0.04))
    assert _compare_scalar_statistically(committed, fresh, ["D"]) == []
    _write(fresh / "report.json", report(0.66, 0.04))
    assert _compare_scalar_statistically(committed, fresh, ["D"])

    # One report without the window is a structural disagreement.
    _write(fresh / "report.json", {"D": 0.5, "window_statistics": {}})
    assert _compare_scalar_statistically(committed, fresh, ["D"])

    # Neither has one: only the final values count.
    _write(committed / "report.json", {"D": 1.0, "window_statistics": {}})
    _write(fresh / "report.json", {"D": 1.0, "window_statistics": {}})
    assert _compare_scalar_statistically(committed, fresh, ["D"]) == []
    _write(fresh / "report.json", {"D": 0.9, "window_statistics": {}})
    assert _compare_scalar_statistically(committed, fresh, ["D"])


def test_every_backend_shares_the_lineal_random_stream() -> None:
    """All three engines draw `lineal`'s stream, so identity is the rule.

    Backend V's kernel reproduces the dictionary-based operators bit for
    bit, so a committed `lineal` output against a fresh vector run is an
    identity comparison. Were V mapped to a stream of its own, the
    statistical rule would silently replace the stronger one.
    """
    assert set(_STREAM.values()) == {"lineal"}


def test_every_example_has_a_recorded_runtime() -> None:
    """`_RUNTIME_SECONDS` names exactly the examples with a configuration.

    A new example must have its `auto` case timed, so its `slow` mark is
    decided from a measurement rather than left to default; a removed one
    must not leave a stale entry.
    """
    assert sorted(_RUNTIME_SECONDS) == _example_ids()


@pytest.mark.parametrize("local_difference", [False, True])
def test_vector_archive_uses_intervals_but_local_reference_requires_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local_difference: bool
) -> None:
    """Platform rounding is allowed only for the archive, never local parity."""
    example = "vector-fixture"
    examples = tmp_path / "examples"
    committed = examples / example
    fresh = tmp_path / "fresh"
    reference = tmp_path / "reference"
    for directory, mean in ((committed, 0.3), (fresh, 0.31), (reference, 0.31)):
        _write(
            directory / "manifest.json",
            {"parameters": {"convergence_statistic": "D", "stop_batch_early": False}},
        )
        _write(
            directory / "summary.json",
            {"D": {"mean": mean, "half_width": 0.02}},
        )
        _write(
            directory / "replicate-001" / "manifest.json",
            {"engine_backend": "generational-vector"},
        )
        _write(directory / "replicate-001" / "report.json", {"D": mean})
    if local_difference:
        _write(reference / "replicate-001" / "report.json", {"D": 0.32})

    def run_example(example: str, tmp_path: Path, *, backend: str = "auto") -> Path:
        """Return controlled fresh and configured outputs without a simulation."""
        return fresh if backend == "auto" else reference

    module = sys.modules[__name__]
    monkeypatch.setattr(module, "EXAMPLES_DIR", examples)
    monkeypatch.setattr(module, "_run_example", run_example)
    if local_difference:
        with pytest.raises(AssertionError, match="local configured backend"):
            test_example_on_auto_agrees_with_its_committed_output(example, tmp_path)
    else:
        test_example_on_auto_agrees_with_its_committed_output(example, tmp_path)


@pytest.mark.statistical
@pytest.mark.parametrize("example", [_case(example) for example in _example_ids()])
def test_example_on_auto_agrees_with_its_committed_output(
    example: str, tmp_path: Path
) -> None:
    """The example's `auto` run agrees with its committed output.

    The comparison follows from the backend `auto` resolved to (the fresh
    manifest) against the backend the committed output ran on: identical
    reports on the same portable random stream (adaptive batches
    included), otherwise the window-mean or half-width rule. Vector
    archives use the statistical rule plus exact comparison with a
    same-host configured run.
    """
    committed = EXAMPLES_DIR / example
    fresh = _run_example(example, tmp_path)
    backend_l = _resolved_backend(committed)
    backend_a = _resolved_backend(fresh)
    same_stream = _STREAM[backend_l] == _STREAM[backend_a]
    batch = (committed / "summary.json").is_file()

    local_problems: list[str] = []
    if backend_l == backend_a == "generational-vector":
        reference = _run_example(example, tmp_path / "configured", backend=backend_l)
        local_problems = _compare_identical(reference, fresh)
        # Same-host identity is mandatory; an archived vector output may
        # predate the exact kernel (a different stream) or come from
        # another platform, so it satisfies the statistical contract used
        # for different streams until it is regenerated.
        same_stream = False

    if same_stream:
        problems = _compare_identical(committed, fresh, archive=True)
        # When the archive differs only by platform rounding, still prove
        # that auto and its explicitly resolved backend agree locally.
        # Replaying the archive's lineal backend here could turn a fast
        # vector case into a long simulation; cross-backend parity has its
        # own engine tests and is not needed to check dispatch.
        if not problems and _compare_identical(committed, fresh):
            reference = _run_example(
                example, tmp_path / "configured", backend=backend_a
            )
            local_problems = _compare_identical(reference, fresh)
    elif batch:
        problems = _compare_batch_statistically(committed, fresh, _watched(committed))
    else:
        problems = _compare_scalar_statistically(committed, fresh, _watched(committed))
    problems += [f"local configured backend: {problem}" for problem in local_problems]
    assert not problems, (
        f"{example}: auto resolved to {backend_a}, committed ran on {backend_l}: "
        + "; ".join(problems)
    )
