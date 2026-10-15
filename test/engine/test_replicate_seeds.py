"""Replicate seeds come from `SeedSequence`, so batches never share a replicate."""

from __future__ import annotations

from dataclasses import replace

from vector_support import loci, make_params

import fim
from fim.engine.identity import params_for_replicate, replicate_seed


def test_replicate_seeds_are_deterministic_and_pinned() -> None:
    """The same seed and index give the same value, on every machine."""
    assert replicate_seed(100, 0) == 6961924204601216437
    assert replicate_seed(100, 1) == 6754569615716118803
    assert replicate_seed(100, 2) == replicate_seed(100, 2)


def test_nearby_batches_share_no_replicate() -> None:
    """The earlier `seed + i` rule repeated seed 102 in batches 100 and 102."""
    batch_100 = {replicate_seed(100, i) for i in range(50)}
    batch_101 = {replicate_seed(101, i) for i in range(50)}
    batch_102 = {replicate_seed(102, i) for i in range(50)}
    assert not batch_100 & batch_101
    assert not batch_100 & batch_102
    assert len(batch_100) == 50


def test_a_replicate_seed_does_not_depend_on_the_batch_size() -> None:
    """Replicate 3 is the same whether the batch has 5 replicates or 50."""
    small = replace(make_params(10, loci=loci(1)), n_replicates=5)
    large = replace(small, n_replicates=50)
    assert params_for_replicate(small, 3).seed == params_for_replicate(large, 3).seed


def test_a_batch_replicate_is_the_standalone_run_with_its_seed() -> None:
    """Replicate `i` reproduces a single run configured with its derived seed."""
    spec = fim.RunSpec(
        model=fim.Model(
            demes=3,
            population=fim.Population(individuals=40, ploidy=1),
            migration=0.1,
            mutation=fim.InfiniteAlleles(rate=0.01),
        ),
        seed=7,
        stopping=fim.Stopping(burn_in=5, max_generations=40),
        replication=fim.Replication(n=3, stop_early=False),
    )
    batch = fim.simulate(spec)
    for replicate in batch.replicates:
        single = fim.simulate(
            fim.RunSpec(
                model=spec.model,
                seed=replicate.seed,
                stopping=spec.stopping,
                replication=fim.Replication(n=1),
            )
        )
        assert single.only().report["D"] == replicate.report["D"]
        assert single.only().report["H_S"] == replicate.report["H_S"]
    assert [r.seed for r in batch.replicates] == [
        replicate_seed(7, i) for i in range(3)
    ]
