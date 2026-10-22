"""Tests for `fim.engine.concurrency.default_replicate_concurrency`.

`generational-vector` has no fan-out mechanism (`VectorizedAdvancer.advance`
steps every active lane in one Python loop), so its default must stay the
fixed `batch_width` regardless of hardware; `lineal` and `generational` both
get genuine, hardware-scaled fan-out, so their default must track the
process-visible core count. Either way, the result never exceeds
`n_replicates`.
"""

from __future__ import annotations

import os

import pytest

from fim.engine.concurrency import default_replicate_concurrency


@pytest.mark.parametrize("engine_backend", ["lineal", "generational"])
def test_core_bound_backends_default_to_the_process_visible_core_count(
    monkeypatch: pytest.MonkeyPatch, engine_backend: str
) -> None:
    monkeypatch.delattr(os, "process_cpu_count", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 6)
    assert (
        default_replicate_concurrency(
            engine_backend,  # type: ignore[arg-type]
            n_replicates=200,
            batch_width=8,
        )
        == 6
    )


def test_generational_vector_defaults_to_batch_width_regardless_of_cores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(os, "process_cpu_count", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 64)
    assert (
        default_replicate_concurrency(
            "generational-vector", n_replicates=200, batch_width=8
        )
        == 8
    )


@pytest.mark.parametrize(
    "engine_backend", ["lineal", "generational", "generational-vector"]
)
def test_the_result_never_exceeds_n_replicates(
    monkeypatch: pytest.MonkeyPatch, engine_backend: str
) -> None:
    monkeypatch.delattr(os, "process_cpu_count", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 64)
    assert (
        default_replicate_concurrency(
            engine_backend,  # type: ignore[arg-type]
            n_replicates=3,
            batch_width=8,
        )
        == 3
    )


def test_prefers_process_cpu_count_over_the_machine_wide_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "process_cpu_count", lambda: 2, raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 64)
    assert (
        default_replicate_concurrency("generational", n_replicates=200, batch_width=8)
        == 2
    )


def test_falls_back_to_one_when_no_core_count_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(os, "process_cpu_count", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert default_replicate_concurrency("lineal", n_replicates=200, batch_width=8) == 1
