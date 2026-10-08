"""Guard: every worked example's committed output matches a fresh `fim run`.

Each `doc/examples/<id>/` directory with a `config.yaml` commits the
output files of its own run (`dev/bin/regenerate-example-outputs`, design
doc `20261005-claude-opus-5-5-read-only-examples-and-classes-design.md`,
`selby/restricted`, sections 4.1 and 6): `manifest.json` and
`report.json` for a single run, or `manifest.json`, `summary.json`, and
each replicate's complete artifacts for a batch. JSONL artifacts are
losslessly archived; the app restores them on opening. The slow test
below reruns every example exactly as that script does and compares.

A run is a pure function of its configuration, so the comparison is
exact, except for the few manifest fields that record the moment or the
machine rather than the model (`VOLATILE_MANIFEST_KEYS`, and the
artifact digests listed in `_comparable_manifest`).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from fim.examples.artifacts import archive_target, output_files

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIRECTORY = ROOT / "doc" / "examples"
REGENERATE_SCRIPT = ROOT / "dev" / "bin" / "regenerate-example-outputs"

# The JSON receipts compared with a rerun; complete artifact presence is
# checked separately, with trajectory bytes covered by manifest digests.
TOP_LEVEL_OUTPUT_NAMES = ("manifest.json", "report.json", "summary.json")
REPLICATE_OUTPUT_NAMES = ("manifest.json", "report.json")

# Manifest fields that describe when and by which release a run was made,
# not what it computed.
VOLATILE_MANIFEST_KEYS = frozenset({"started_at", "ended_at", "software_version"})

# Every example with a configuration. All are slow: the quickest take
# seconds, Jost (2008) Part VI about a quarter of an hour, and Dear-Nolan
# low (30 loci, about 295,000 generations) about an hour.
EXAMPLE_IDS = tuple(
    sorted(path.parent.name for path in EXAMPLES_DIRECTORY.glob("*/config.yaml"))
)


def _comparable_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a manifest without the fields a rerun legitimately changes.

    Besides `VOLATILE_MANIFEST_KEYS`, two artifact digests are dropped: a
    batch manifest's digest of each replicate's manifest (which holds that
    replicate's own time stamps), and the scatter plot's (a PNG whose
    bytes depend on the plotting library's version and fonts). The
    trajectory, convergence, pairwise, and report digests stay, so the
    comparison still covers every generation of every run.

    Args:
        manifest: A parsed `manifest.json`.

    Returns:
        A copy without the volatile fields.
    """
    comparable = {
        key: value
        for key, value in manifest.items()
        if key not in VOLATILE_MANIFEST_KEYS
    }
    artifacts = comparable.get("artifacts")
    if isinstance(artifacts, dict):
        comparable["artifacts"] = {
            name: digest
            for name, digest in artifacts.items()
            if name != "scatter" and not name.startswith("replicate-")
        }
    return comparable


def _output_files(directory: Path) -> list[str]:
    """Return the committed-output paths present under a run directory.

    Args:
        directory: An example directory or a fresh run's output directory.

    Returns:
        Sorted paths relative to `directory`.
    """
    found = [name for name in TOP_LEVEL_OUTPUT_NAMES if (directory / name).is_file()]
    for replicate in sorted(directory.glob("replicate-*")):
        if replicate.is_dir():
            found += [
                f"{replicate.name}/{name}"
                for name in REPLICATE_OUTPUT_NAMES
                if (replicate / name).is_file()
            ]
    return sorted(found)


def _read_json(path: Path) -> Any:
    """Parse one JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def test_dear_nolan_high_configuration_matches_its_derivation() -> None:
    """`dear-nolan-high/config.yaml` is exactly what `reproduce.py` derives.

    The example's `p_0` is the validation suite's near-equilibrium start
    (`_dn2_equilibrium_start`), written into the file by the script; a
    change to that derivation fails here until the file is rewritten.
    """
    script = EXAMPLES_DIRECTORY / "dear-nolan-high" / "reproduce.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("example", EXAMPLE_IDS)
def test_every_example_commits_its_output_files(example: str) -> None:
    """Each example directory holds a complete set of committed outputs.

    Every manifest-referenced artifact is present, with JSONL data
    losslessly archived and each part bounded to the Git-safe size limit.
    """
    directory = EXAMPLES_DIRECTORY / example
    files = _output_files(directory)

    assert "manifest.json" in files
    if "summary.json" in files:
        replicates = [path for path in files if path.startswith("replicate-")]
        assert replicates
        assert len(replicates) % len(REPLICATE_OUTPUT_NAMES) == 0
    else:
        assert files == ["manifest.json", "report.json"]
    assert not list(directory.rglob("trajectory.jsonl"))
    assert not list(directory.rglob("convergence.jsonl"))
    for manifest_path in directory.rglob("manifest.json"):
        manifest = _read_json(manifest_path)
        names = {path.name for path in output_files(manifest_path.parent, batch=False)}
        for key in manifest["artifacts"]:
            if key.startswith("replicate-"):
                continue
            target = (
                f"{key}.png"
                if key == "scatter"
                else f"{key}.jsonl"
                if key
                in {
                    "trajectory",
                    "equilibrium_trajectory",
                    "convergence",
                    "sigma_band_trajectory",
                }
                else f"{key}.json"
            )
            if target.endswith(".jsonl"):
                parts = sorted(name for name in names if archive_target(name) == target)
                assert parts, f"{example}: missing {target}"
                assert parts == [
                    f"{target}.gz.part-{index:04d}"
                    for index in range(1, len(parts) + 1)
                ]
                assert all(
                    (manifest_path.parent / part).stat().st_size <= 50 * 1024 * 1024
                    for part in parts
                )
            else:
                assert (manifest_path.parent / target).is_file(), (
                    f"{example}: missing {target}"
                )


def test_regeneration_uses_only_the_configuration(tmp_path: Path) -> None:
    """The regeneration command overrides nothing: out-of-the-box defaults.

    Every example is run as `fim run CONFIG --output DIR --quiet`, so each
    setting its configuration leaves out takes `fim run`'s own default,
    and the committed outputs are what a user gets from the same file.
    """
    completed = subprocess.run(
        [sys.executable, str(REGENERATE_SCRIPT), "--dry-run"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    commands = [line.split() for line in completed.stdout.splitlines()]

    assert len(commands) == len(EXAMPLE_IDS)
    for command, example in zip(commands, EXAMPLE_IDS, strict=True):
        config = str(EXAMPLES_DIRECTORY / example / "config.yaml")
        assert command[1:5] == ["-m", "fim.launcher", "run", config]
        assert command[5] == "--output"
        assert command[7:] == ["--quiet"]


@pytest.mark.slow
@pytest.mark.parametrize("example", EXAMPLE_IDS)
def test_example_outputs_match_a_fresh_run(example: str, tmp_path: Path) -> None:
    """The committed output files are the example's exact `fim run` output.

    Adding a statistic or changing the engine changes the output; this
    fails until `dev/bin/regenerate-example-outputs` is rerun, so the
    shipped results cannot silently drift from the code.
    """
    directory = EXAMPLES_DIRECTORY / example
    output = tmp_path / example
    subprocess.run(
        [
            sys.executable,
            "-m",
            "fim.launcher",
            "run",
            str(directory / "config.yaml"),
            "--output",
            str(output),
            "--quiet",
        ],
        check=True,
        cwd=ROOT,
        # The command records each run in a Study index under the results
        # directory; keep that out of the developer's real `results/`.
        env={**os.environ, "FIM_RESULTS_DIRECTORY": str(tmp_path / "results")},
    )

    committed_files = _output_files(directory)
    assert _output_files(output) == committed_files
    for relative in committed_files:
        fresh = _read_json(output / relative)
        committed = _read_json(directory / relative)
        if Path(relative).name == "manifest.json":
            fresh = _comparable_manifest(fresh)
            committed = _comparable_manifest(committed)
        assert fresh == committed, relative
