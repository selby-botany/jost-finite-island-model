"""End-to-end tests for the deterministic library engine."""

from datetime import UTC, datetime

import pytest

from fim.engine import RunResult, fim, report_for_state
from fim.model.allele import MINTED_ID_START, AlleleId
from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.persistence.store import InMemoryTrajectoryStore


def _clock() -> datetime:
    """Return a fixed manifest timestamp."""
    return datetime(2026, 8, 14, 20, 0, tzinfo=UTC)


def _run(params: SimulationParams) -> RunResult:
    """Run one scalar configuration and narrow the output type."""
    result = fim(
        params.N,
        params.m,
        params.mu,
        params.d,
        params=params,
        clock=_clock,
    )
    assert isinstance(result, RunResult)
    return result


def test_seeded_run_is_bit_reproducible(tiny_params: SimulationParams) -> None:
    """Two runs persist exactly the same rows and final report."""
    first = _run(tiny_params)
    second = _run(tiny_params)

    assert list(first.store.read(first.run_id)) == list(
        second.store.read(second.run_id)
    )
    assert first.report == second.report
    assert first.final_state == second.final_state


def test_live_and_recomputed_reports_match(
    tiny_params: SimulationParams,
) -> None:
    """Statistics remain independent of the engine run loop."""
    result = _run(tiny_params)

    recomputed = report_for_state(
        result.final_state,
        tiny_params,
        run_id=result.run_id,
        converged=result.report["converged"],
        reason=result.report["reason"],
    )

    assert recomputed == result.report


def test_cap_is_a_valid_nonconverged_result(
    tiny_params: SimulationParams,
) -> None:
    """An intentionally impossible short window reports the hard cap."""
    params = SimulationParams.from_mapping(
        {
            **tiny_params.to_dict(),
            "convergence_window": 10,
            "convergence_tolerance": 0.0,
            "max_generations": 2,
        }
    )

    result = _run(params)

    assert not result.report["converged"]
    assert result.report["reason"] == "hit the cap"
    assert result.report["generation"] == 2


def test_replicates_are_independently_reproducible(
    tiny_params: SimulationParams,
) -> None:
    """Batching derives stable per-replicate seeds without changing scalar runs."""
    scalar = _run(tiny_params)
    batched_params = SimulationParams.from_mapping(
        {**tiny_params.to_dict(), "n_replicates": 2}
    )
    store = InMemoryTrajectoryStore()

    output = fim(
        batched_params.N,
        batched_params.m,
        batched_params.mu,
        batched_params.d,
        params=batched_params,
        store=store,
        clock=_clock,
    )

    assert isinstance(output, tuple)
    assert len(output) == 2
    assert output[0].final_state == scalar.final_state
    assert output[0].params.seed == tiny_params.seed
    assert output[1].params.seed == tiny_params.seed + 1


def test_public_signature_mismatches_are_reported(
    tiny_params: SimulationParams,
) -> None:
    """The legacy positional arguments must agree with the parameter bag."""
    cases = (
        (21, tiny_params.m, tiny_params.mu, tiny_params.d, "N"),
        (tiny_params.N, 0.2, tiny_params.mu, tiny_params.d, "m"),
        (tiny_params.N, tiny_params.m, 0.2, tiny_params.d, "mu"),
        (tiny_params.N, tiny_params.m, tiny_params.mu, 3, "d"),
    )
    for population_size, migration, mutation, demes, message in cases:
        with pytest.raises(ValueError, match=message):
            fim(
                population_size,
                migration,
                mutation,
                demes,
                params=tiny_params,
                clock=_clock,
            )


def test_batch_run_uses_explicit_run_id_suffixes(
    tiny_params: SimulationParams,
) -> None:
    """Caller-provided batch IDs receive deterministic one-based suffixes."""
    params = SimulationParams.from_mapping({**tiny_params.to_dict(), "n_replicates": 2})
    output = fim(
        params.N,
        params.m,
        params.mu,
        params.d,
        params=params,
        run_id="batch",
        clock=_clock,
    )
    assert isinstance(output, tuple)
    assert [result.run_id for result in output] == ["batch-r001", "batch-r002"]


def test_naive_manifest_clock_is_rejected(tiny_params: SimulationParams) -> None:
    """Manifest timestamps require an explicit timezone."""
    with pytest.raises(ValueError, match="timezone-aware"):
        fim(
            tiny_params.N,
            tiny_params.m,
            tiny_params.mu,
            tiny_params.d,
            params=tiny_params,
            clock=lambda: _clock().replace(tzinfo=None),
        )


def test_g_st_convergence_handles_shared_fixation() -> None:
    """Undefined G_ST at total fixation is treated as zero for convergence."""
    params = SimulationParams(
        N=10,
        m=0.0,
        mu=0.0,
        d=2,
        seed=7,
        loci=(LocusSpec(1, 100),),
        convergence_statistic="G_ST",
        convergence_window=2,
        convergence_tolerance=0.0,
        max_generations=2,
        initial_frequencies=(
            ({AlleleId(0): 1.0},),
            ({AlleleId(0): 1.0},),
        ),
    )
    result = fim(
        params.N,
        params.m,
        params.mu,
        params.d,
        params=params,
        clock=_clock,
    )
    assert isinstance(result, RunResult)
    assert result.report["converged"]
    assert result.report["G_ST"] is None


def test_mutation_ids_follow_high_explicit_initial_id() -> None:
    """Mutations cannot collide with labels supplied through explicit p_0."""
    params = SimulationParams(
        N=1,
        m=0.0,
        mu=1.0,
        d=2,
        seed=7,
        loci=(LocusSpec(1, 100),),
        initial_allele_count=1,
        convergence_window=2,
        max_generations=1,
        initial_frequencies=(
            ({AlleleId(MINTED_ID_START): 1.0},),
            ({AlleleId(MINTED_ID_START): 1.0},),
        ),
    )

    result = _run(params)

    final_ids = {
        int(allele_id)
        for deme in result.final_state.frequencies
        for locus in deme
        for allele_id in locus
    }
    assert final_ids == {MINTED_ID_START + 1, MINTED_ID_START + 2}


def test_unequal_deme_sizes_run_is_reproducible_and_bounds_support() -> None:
    """A full run with per-deme N stays reproducible and honors each N_i."""
    sizes = (6, 30)
    params = SimulationParams(
        N=sizes,
        m=0.2,
        mu=0.05,
        d=2,
        seed=20260817,
        loci=(LocusSpec(1, 100),),
        convergence_window=4,
        convergence_tolerance=1.0,
        max_generations=8,
    )

    first = _run(params)
    second = _run(params)

    first_rows = list(first.store.read(first.run_id))
    assert first_rows == list(second.store.read(second.run_id))
    assert first.report == second.report

    support: dict[tuple[int, int], set[int]] = {}
    for row in first_rows:
        key = (int(row["generation"]), int(row["deme"]))
        support.setdefault(key, set()).add(int(row["allele_id"]))
    for (_generation, deme), alleles in support.items():
        assert len(alleles) <= sizes[deme - 1]


def test_report_size_weighting_reflects_actual_per_deme_sizes() -> None:
    """Engine-level reports thread each deme's own N through, not an equal split."""
    loci = (LocusSpec(1, 100),)
    state = ModelState(
        loci=loci,
        frequencies=(
            ({AlleleId(0): 1.0},),
            ({AlleleId(0): 0.2, AlleleId(1): 0.8},),
        ),
    )
    sized_params = SimulationParams(
        N=(10, 10_000),
        m=0.1,
        mu=0.0,
        d=2,
        seed=7,
        loci=loci,
        deme_weighting="size",
    )
    equal_params = SimulationParams(
        N=(10, 10_000),
        m=0.1,
        mu=0.0,
        d=2,
        seed=7,
        loci=loci,
        deme_weighting="equal",
    )

    sized_report = report_for_state(
        state,
        sized_params,
        run_id="run-a",
        converged=False,
        reason="test",
    )
    equal_report = report_for_state(
        state,
        equal_params,
        run_id="run-a",
        converged=False,
        reason="test",
    )

    # The 10,000-copy deme dominates the size-weighted pool, pulling E_ST
    # toward that deme's own diversity rather than the 50/50 equal split.
    assert sized_report["E_ST"] == pytest.approx(0.20326126045322912)
    assert equal_report["E_ST"] == pytest.approx(0.6099865470109876)


def test_report_for_state_supports_multiple_loci_and_equal_weighting() -> None:
    """Independent per-locus reports are averaged under equal deme weighting."""
    loci = (LocusSpec(1, 100), LocusSpec(2, 100))
    state = ModelState(
        loci=loci,
        frequencies=(
            (
                {AlleleId(0): 1.0},
                {AlleleId(0): 0.5, AlleleId(1): 0.5},
            ),
            (
                {AlleleId(1): 1.0},
                {AlleleId(0): 0.5, AlleleId(1): 0.5},
            ),
        ),
    )
    params = SimulationParams(
        N=10,
        m=0.1,
        mu=0.0,
        d=2,
        seed=7,
        loci=loci,
        deme_weighting="equal",
    )
    report = report_for_state(
        state,
        params,
        run_id="run-a",
        converged=False,
        reason="test",
    )
    assert report["run_id"] == "run-a"
    assert report["G_ST"] is not None
