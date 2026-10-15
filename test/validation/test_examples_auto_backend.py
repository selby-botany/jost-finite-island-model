"""Every worked example is named `auto`, and `lineal` reproduces its output.

Policy (project owner, 2026-10-14): each `doc/examples/<id>/config.yaml`
names `engine_backend: auto`, the choice the botanist is encouraged to use,
unless the point of the example is to show one engine. The committed output
was produced by the backend `auto` resolved to (the manifests record it).

`auto` never resolves to `lineal` (`fim.engine.backends.resolve_auto_engine_backend`),
but `generational` and `generational-vector` reproduce `lineal` bit for bit
for the same seed under either mutation model, adaptive batches included
(`GenerationalBackend`'s own docstring and `test/engine/test_vector_parity.py`).
The slow test below proves that on the shipped examples: it reruns each one
through the real `fim run` command with only `engine_backend` changed to
`lineal`, the reference implementation, and requires the same reports and the
same kept replicates as the committed output.

Fields excluded from "identical": only `run_id`, in each report. A run ID
is a digest of the configuration (`fim.model.params`), and the two
configurations legitimately differ in `engine_backend`. A batch `summary.json`
holds no run ID and is compared whole.

Archived evidence-window and Shannon-entropy (`E_ST`, `MI`) floats allow
numerical rounding (relative `1e-12`, absolute `1e-14`): FFT/BLAS reductions
and the system logarithm need not round identically across platforms, even
for identical histories. Structure, discrete values, and every other report
field remain exact. Same-host `auto` against `lineal` agreed exactly on
every example checked on Linux (2026-10-14).

One example is not rerun on `lineal` here: Dear-Nolan low runs about 2.6
million generations to convergence, which takes about an hour and three
quarters on `lineal` and about three and a half minutes on
`generational-vector`. Its identity was checked once, by hand (identical
report, 2026-10-14), and recorded in its README; the engine parity tests cover
the rest.

Every case runs a fixed seed from its configuration, so its outcome is a
pure function of the commit; a failure is investigated, never retried or
widened. Full lineal replays belong to the nightly/on-demand `slow` layer,
like the archive replays in `test_doc_examples.py`. Earlier scheduling
used timings measured for `auto`, not `lineal`: after the backend changed,
eleven cases still entered the per-push gate and its non-GUI layer took
25m43s in run 38650093650, leaving too little time for GUI tests and
packaging. Static policy guards and the focused engine parity tests remain
in the per-push gate.
"""

from __future__ import annotations

import json
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

# A run ID digests the configuration, which differs in `engine_backend`.
_IDENTITY_EXCLUDED_FIELDS = frozenset({"run_id"})


def _compare_identical(
    committed: Path, fresh: Path, *, archive: bool = False
) -> list[str]:
    """Require identical reports, and for a batch an identical summary.

    Args:
        committed: The committed example directory.
        fresh: The fresh run's output directory.
        archive: Allow window and entropy rounding in archived reports.

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


def _example_ids() -> list[str]:
    """Return every example with a runnable configuration, sorted.

    Returns:
        The names of the `doc/examples/*` directories with a `config.yaml`.
    """
    return sorted(path.parent.name for path in EXAMPLES_DIR.glob("*/config.yaml"))


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


# The examples whose point is one named engine; every other example is `auto`.
_ENGINE_EXAMPLES = {
    "a-large-d-batch-under-generational-vector": "generational-vector",
    "a-long-locus-batch-under-the-generational-engine": "generational",
}

# Not rerun on `lineal` here, with the reason (see the module docstring).
_LINEAL_RERUN_EXCLUDED = {
    "dear-nolan-low": "about 2.6 million generations: nearly two hours on lineal",
}


def test_every_example_names_auto_unless_it_shows_an_engine() -> None:
    """The policy: `auto` for every example, except the two engine examples."""
    named = {
        example: yaml.safe_load(
            (EXAMPLES_DIR / example / "config.yaml").read_text(encoding="utf-8")
        ).get("engine_backend")
        for example in _example_ids()
    }
    expected = {example: _ENGINE_EXAMPLES.get(example, "auto") for example in named}
    assert named == expected


def test_committed_outputs_ran_on_the_engine_the_configuration_resolves_to() -> None:
    """`auto` never lands on `lineal`; a named engine is the one that ran."""
    for example in _example_ids():
        configured = _ENGINE_EXAMPLES.get(example, "auto")
        resolved = _resolved_backend(EXAMPLES_DIR / example)
        if configured == "auto":
            assert resolved in {"generational", "generational-vector"}, example
        else:
            assert resolved == configured, example


@pytest.mark.slow
@pytest.mark.statistical
@pytest.mark.parametrize("example", _example_ids())
def test_lineal_reproduces_the_committed_output(example: str, tmp_path: Path) -> None:
    """Rerunning the example on `lineal` gives the committed reports exactly.

    The same configuration with only `engine_backend` changed to `lineal`
    (the reference implementation) must give the same reports, summary, and
    kept replicates, allowing only archive-platform rounding in
    `window_statistics`, `E_ST`, and `MI`.
    """
    if example in _LINEAL_RERUN_EXCLUDED:
        pytest.skip(_LINEAL_RERUN_EXCLUDED[example])
    committed = EXAMPLES_DIR / example
    fresh = _run_example(example, tmp_path, backend="lineal")
    problems = _compare_identical(committed, fresh, archive=True)
    assert not problems, (
        f"{example}: lineal differs from the committed "
        f"{_resolved_backend(committed)} output: " + "; ".join(problems)
    )
