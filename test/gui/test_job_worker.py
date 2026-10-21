"""Integration tests for GUI Run objects executed in child processes."""

from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from fim.gui import job_worker
from fim.persistence.run_lifecycle import RunState
from fim.persistence.run_objects import create_run_object, read_run_object
from fim.persistence.tlog import iter_frames
from fim.run_service import CheckpointCadence, execute_run_object

_CONFIG: dict[str, Any] = {
    "N": 20,
    "ploidy": "haploid",
    "d": 2,
    "m": 0.1,
    "mu": 0.05,
    "seed": 20261011,
    "loci": [{"locus_id": 1, "length": 200}],
    "precision": 0.0,
    "max_generations": 30,
    "n_replicates": 1,
    "stop_batch_early": False,
    "engine_backend": "lineal",
}

_CADENCE = CheckpointCadence(generations=10, seconds=1e9, control_generations=5)


def _create_run_object(results: Path) -> str:
    """Create a planned object with a small, seeded configuration."""
    config_text = yaml.safe_dump(_CONFIG, sort_keys=False)
    return create_run_object(config_text, results=results).object_id


def _worker_command(object_id: str, results: Path) -> list[str]:
    """Build the source-install worker command with an explicit results root."""
    return [
        sys.executable,
        "-m",
        "fim.launcher",
        "--gui-job",
        object_id,
        "--results-directory",
        str(results),
    ]


def _worker_environment() -> dict[str, str]:
    """Expose this checkout's source tree to the child process."""
    environment = os.environ.copy()
    source = str(Path(__file__).resolve().parents[2] / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source if not existing else os.pathsep.join((source, existing))
    )
    return environment


def test_job_worker_uses_the_requested_priority_before_engine_imports() -> None:
    """The worker lowers priority before it imports modules that load NumPy."""
    source = inspect.getsource(job_worker.main)

    assert source.index("_lower_process_priority()") < source.index(
        "from fim.gui.object_runner"
    )
    module = ast.parse(inspect.getsource(job_worker))
    imports = [
        alias.name
        for node in ast.walk(module)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    ]
    assert "numpy" not in imports


def test_child_worker_pauses_on_stdin_eof_then_resumes_to_same_results(
    tmp_path: Path,
) -> None:
    """A closed lifeline pauses safely; resuming matches an uninterrupted run."""
    reference_id = _create_run_object(tmp_path)
    paused_id = _create_run_object(tmp_path)
    reference = execute_run_object(
        reference_id,
        results=tmp_path,
        cadence=_CADENCE,
    )
    reference_directory = reference.directory

    paused_process = subprocess.Popen(
        _worker_command(paused_id, tmp_path),
        cwd=Path(__file__).resolve().parents[2],
        env=_worker_environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert paused_process.stdin is not None
    paused_process.stdin.close()
    pause_status = paused_process.wait(timeout=60)
    pause_stdout = paused_process.stdout.read() if paused_process.stdout else b""
    pause_stderr = paused_process.stderr.read() if paused_process.stderr else b""

    paused = read_run_object(paused_id, results=tmp_path)
    assert pause_status == 0, (pause_stdout, pause_stderr)
    assert paused.record.state is RunState.PAUSED
    assert paused.record.committed_generation is not None

    resumed_process = subprocess.Popen(
        _worker_command(paused_id, tmp_path),
        cwd=Path(__file__).resolve().parents[2],
        env=_worker_environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert resumed_process.stdin is not None
    resume_status = resumed_process.wait(timeout=120)
    resumed_process.stdin.close()
    resume_stdout = resumed_process.stdout.read() if resumed_process.stdout else b""
    resume_stderr = resumed_process.stderr.read() if resumed_process.stderr else b""

    completed = read_run_object(paused_id, results=tmp_path)
    assert resume_status == 0, (resume_stdout, resume_stderr)
    assert completed.record.state is RunState.COMPLETED
    reference_log = reference_directory / "trajectory.tlog"
    resumed_log = completed.directory / "trajectory.tlog"
    reference_frames = list(iter_frames(reference_log))
    resumed_frames = list(iter_frames(resumed_log))
    assert [frame.generation for frame in resumed_frames] == [
        frame.generation for frame in reference_frames
    ]
    # Checkpoint boundaries can change binary block encoding, not the data.
    for reference_frame, resumed_frame in zip(
        reference_frames, resumed_frames, strict=True
    ):
        np.testing.assert_array_equal(reference_frame.counts, resumed_frame.counts)
        np.testing.assert_array_equal(
            reference_frame.allele_ids, resumed_frame.allele_ids
        )
        np.testing.assert_array_equal(
            reference_frame.frequencies, resumed_frame.frequencies
        )
    assert (completed.directory / "report.json").read_bytes() == (
        reference_directory / "report.json"
    ).read_bytes()
