"""Tests for `fim.persistence.scalar_snapshot`: exact population and RNG export."""

from __future__ import annotations

import io
import json
from typing import Any

import numpy as np
import pytest

from fim.execution import ScalarSession
from fim.model.allele import MINTED_ID_START, AlleleRegistry, FiniteAlleleRegistry
from fim.model.operators import step
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.persistence.scalar_snapshot import (
    FINITE_COUNTS,
    FINITE_MINTED,
    POPULATION_COUNTS,
    POPULATION_FREQUENCIES,
    POPULATION_IDS,
    SnapshotError,
    export_population,
    export_registries,
    export_rng,
    import_population,
    import_registries,
    import_rng,
)

GENERATIONS_BEFORE = 7
GENERATIONS_AFTER = 9


def _params(**changes: Any) -> SimulationParams:
    """Return a small single-replicate configuration, with `changes` applied."""
    config: dict[str, Any] = {
        "N": 20,
        "ploidy": "haploid",
        "d": 3,
        "m": 0.1,
        "mu": 0.2,
        "seed": 20261011,
        "loci": [{"locus_id": 1, "length": 200}, {"locus_id": 4, "length": 50}],
        "precision": 0.0,
        "max_generations": 100,
        "n_replicates": 1,
        "stop_batch_early": False,
        "engine_backend": "lineal",
    }
    config.update(changes)
    return SimulationParams.from_mapping(config)


FINITE = {
    "mutation_model": "finite_alleles",
    "loci": [{"locus_id": 1, "length": 1}, {"locus_id": 2, "length": 3}],
}


def _through_files(
    metadata: dict[str, Any], arrays: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Round-trip metadata through JSON and arrays through a pickle-free npz."""
    buffer = io.BytesIO()
    np.savez(buffer, **arrays)
    buffer.seek(0)
    with np.load(buffer, allow_pickle=False) as archive:
        loaded = {name: archive[name] for name in archive.files}
    return json.loads(json.dumps(metadata)), loaded


def _session_at(params: SimulationParams, generations: int) -> ScalarSession:
    """Return a session advanced `generations` generations."""
    session = ScalarSession.create(params)
    session.advance(generations)
    return session


def _continue(
    state: ModelState,
    params: SimulationParams,
    registry: AlleleRegistry,
    finite: FiniteAlleleRegistry | None,
    rng: np.random.Generator,
) -> ModelState:
    """Step `GENERATIONS_AFTER` generations from the given working state."""
    for _ in range(GENERATIONS_AFTER):
        state = step(state, params, registry, rng, finite_alleles=finite)
    return state


def _restore_lane(
    session: ScalarSession,
) -> tuple[ModelState, AlleleRegistry, FiniteAlleleRegistry | None, Any]:
    """Export a session's working state through files and import it again."""
    lane = session.lane
    population = _through_files(*export_population(lane.state))
    registries = _through_files(*export_registries(lane.registry, lane.finite_alleles))
    rng_metadata = json.loads(json.dumps(export_rng(lane.rng)))
    state = import_population(*population)
    registry, finite = import_registries(*registries, state)
    return state, registry, finite, import_rng(rng_metadata)


@pytest.mark.parametrize("changes", [{}, FINITE], ids=["infinite", "finite"])
def test_restored_working_state_continues_exactly(changes: dict[str, Any]) -> None:
    """Continuing from a restored snapshot matches continuing the original."""
    params = _params(**changes)
    with _session_at(params, GENERATIONS_BEFORE) as session:
        state, registry, finite, rng = _restore_lane(session)
        lane = session.lane

        assert state == lane.state
        restored = _continue(state, params, registry, finite, rng)
        original = _continue(
            lane.state, params, lane.registry, lane.finite_alleles, lane.rng
        )

    assert restored == original
    assert registry.next_value == lane.registry.next_value


def test_population_keeps_every_map_in_insertion_order() -> None:
    """Allele order inside each map survives, since draws follow it."""
    with _session_at(_params(), GENERATIONS_BEFORE) as session:
        original = session.lane.state
        restored = import_population(*_through_files(*export_population(original)))

    for restored_deme, original_deme in zip(
        restored.frequencies, original.frequencies, strict=True
    ):
        for restored_map, original_map in zip(
            restored_deme, original_deme, strict=True
        ):
            assert list(restored_map.items()) == list(original_map.items())
    assert restored.generation == GENERATIONS_BEFORE


def test_minted_then_lost_finite_states_stay_minted() -> None:
    """A state minted and then lost still counts as minted after a restore."""
    with _session_at(_params(**FINITE), 40) as session:
        finite = session.lane.finite_alleles
        assert finite is not None
        _, _, restored, _ = _restore_lane(session)
        state = session.lane.state

    assert restored is not None
    lost_somewhere = False
    for locus_index, locus in enumerate(state.loci):
        original_states = finite.space_for(locus.locus_id).minted_states
        assert restored.space_for(locus.locus_id).minted_states == original_states
        present = {int(a) for deme in state.frequencies for a in deme[locus_index]}
        lost_somewhere |= bool({int(s) for s in original_states} - present)
    assert lost_somewhere


def test_rng_round_trip_reproduces_the_next_draws() -> None:
    """The restored generator draws exactly what the original would."""
    rng = np.random.Generator(np.random.PCG64(20261011))
    rng.integers(0, 10, size=3, dtype=np.uint32)
    rng.random(5)
    metadata = json.loads(json.dumps(export_rng(rng)))
    restored = import_rng(metadata)

    assert restored.bit_generator.state == rng.bit_generator.state
    assert np.array_equal(restored.random(20), rng.random(20))
    assert np.array_equal(
        restored.integers(0, 1 << 30, size=7, dtype=np.uint32),
        rng.integers(0, 1 << 30, size=7, dtype=np.uint32),
    )


def test_rng_words_are_decimal_strings() -> None:
    """Words above 2**53 survive canonical JSON as strings."""
    metadata = export_rng(np.random.Generator(np.random.PCG64(1)))

    assert isinstance(metadata["state"], str)
    assert isinstance(metadata["inc"], str)


def test_a_non_pcg64_generator_cannot_be_exported() -> None:
    """Only the generator the engine uses is supported."""
    with pytest.raises(SnapshotError, match="PCG64"):
        export_rng(np.random.Generator(np.random.MT19937(1)))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"bit_generator": "MT19937"}, "PCG64"),
        ({"state": 5}, "decimal string"),
        ({"inc": "-1"}, "decimal string"),
        ({"state": str(1 << 128)}, "128 bits"),
        ({"has_uint32": 2}, "out of range"),
    ],
)
def test_malformed_random_state_is_refused(
    change: dict[str, Any], message: str
) -> None:
    """Every random-state field is validated before use."""
    metadata = {**export_rng(np.random.Generator(np.random.PCG64(1))), **change}

    with pytest.raises(SnapshotError, match=message):
        import_rng(metadata)


def _population() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return an exported population to corrupt."""
    with _session_at(_params(), 3) as session:
        return export_population(session.lane.state)


@pytest.mark.parametrize(
    ("corrupt", "message"),
    [
        (lambda _m, a: a.pop(POPULATION_IDS), "missing"),
        (
            lambda _m, a: a.__setitem__(
                POPULATION_IDS, a[POPULATION_IDS].astype(np.int32)
            ),
            "int64",
        ),
        (lambda m, _a: m.__setitem__("deme_count", 2), "entries"),
        (
            lambda _m, a: a.__setitem__(POPULATION_IDS, a[POPULATION_IDS][:-1]),
            "disagree",
        ),
        (
            lambda _m, a: a.__setitem__(
                POPULATION_IDS, np.full_like(a[POPULATION_IDS], 7)
            ),
            "repeats",
        ),
        (
            lambda _m, a: a.__setitem__(
                POPULATION_FREQUENCIES, a[POPULATION_FREQUENCIES] * 2
            ),
            "not a valid state",
        ),
        (
            lambda _m, a: a.__setitem__(
                POPULATION_COUNTS, np.zeros_like(a[POPULATION_COUNTS])
            ),
            "at least one allele",
        ),
        (lambda m, _a: m.__setitem__("loci", []), "non-empty"),
        (lambda m, _a: m.__setitem__("generation", -1), "generation"),
    ],
)
def test_malformed_population_is_refused(corrupt: Any, message: str) -> None:
    """Population data is validated before any state is built."""
    metadata, arrays = _population()
    corrupt(metadata, arrays)

    with pytest.raises(SnapshotError, match=message):
        import_population(metadata, arrays)


def test_a_registry_counter_at_or_below_a_minted_identity_is_refused() -> None:
    """Restoring must never mint an identity already in use."""
    with _session_at(_params(), GENERATIONS_BEFORE) as session:
        state = session.lane.state
        metadata, arrays = export_registries(session.lane.registry, None)
    highest = max(int(a) for deme in state.frequencies for locus in deme for a in locus)
    assert highest >= MINTED_ID_START

    with pytest.raises(SnapshotError, match="registry counter"):
        import_registries({**metadata, "next_allele_id": highest}, arrays, state)


@pytest.mark.parametrize(
    ("corrupt", "message"),
    [
        (lambda m, _a: m["finite"][0].__setitem__("capacity", "5"), "capacity"),
        (lambda m, _a: m["finite"][0].__setitem__("capacity", 4), "decimal"),
        (
            lambda _m, a: a.__setitem__(FINITE_MINTED, a[FINITE_MINTED] + 100),
            "capacity",
        ),
        (
            lambda _m, a: a.__setitem__(FINITE_COUNTS, a[FINITE_COUNTS] + 1),
            "disagree",
        ),
        (lambda m, _a: m.__setitem__("finite", m["finite"][:1]), "loci"),
    ],
)
def test_malformed_finite_registry_is_refused(corrupt: Any, message: str) -> None:
    """Finite spaces are checked against the locus lengths and the population."""
    with _session_at(_params(**FINITE), 5) as session:
        state = session.lane.state
        metadata, arrays = export_registries(
            session.lane.registry, session.lane.finite_alleles
        )
    corrupt(metadata, arrays)

    with pytest.raises(SnapshotError, match=message):
        import_registries(metadata, arrays, state)


def test_a_present_allele_missing_from_the_minted_states_is_refused() -> None:
    """Every allele in the population must already be minted."""
    with _session_at(_params(**FINITE), 5) as session:
        state = session.lane.state
        metadata, arrays = export_registries(
            session.lane.registry, session.lane.finite_alleles
        )
    present = int(next(iter(state.frequencies[0][0])))
    counts = arrays[FINITE_COUNTS].copy()
    minted = list(arrays[FINITE_MINTED])
    first = minted[: int(counts[0])]
    minted = [value for value in first if value != present] + minted[int(counts[0]) :]
    counts[0] -= 1
    arrays = {**arrays, FINITE_COUNTS: counts, FINITE_MINTED: np.asarray(minted)}

    with pytest.raises(SnapshotError, match="never minted"):
        import_registries(metadata, arrays, state)
