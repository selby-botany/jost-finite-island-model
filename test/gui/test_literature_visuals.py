"""Unit tests for literature-derived GUI visualization payloads."""

from __future__ import annotations

import pytest

from fim.gui.literature_visuals import (
    literature_visual_payload,
    pooled_allele_composition_payload,
    pooled_frequency_spectrum_payload,
    pooled_isolation_by_distance_payload,
    pooled_literature_visual_payload,
)
from fim.model.allele import AlleleId
from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams
from fim.model.state import ModelState


def _gradient_state() -> ModelState:
    """Return a four-deme state with positive pairwise identity at every distance."""
    return ModelState(
        loci=(LocusSpec(1, 200),),
        frequencies=(
            ({AlleleId(0): 0.8, AlleleId(1): 0.2},),
            ({AlleleId(0): 0.6, AlleleId(1): 0.4},),
            ({AlleleId(0): 0.4, AlleleId(1): 0.6},),
            ({AlleleId(0): 0.2, AlleleId(1): 0.8},),
        ),
    )


def test_literature_visual_payload_carries_a_wright_beta_overlay() -> None:
    """A scalar run's frequency spectrum carries a Wright beta overlay."""
    params = SimulationParams(
        N=50,
        m=0.05,
        mu=0.01,
        d=4,
        seed=7,
        loci=(LocusSpec(1, 200),),
        convergence_window=4,
        convergence_tolerance=1.0,
        max_generations=10,
        n_replicates=1,
        replicate_tolerance=None,
    )

    payload = literature_visual_payload(_gradient_state(), params)

    spectrum = payload["frequencySpectrum"]
    assert len(spectrum["bins"]) == 20
    assert sum(bin_["count"] for bin_ in spectrum["bins"]) == 8
    assert spectrum["betaOverlay"] is not None
    # The overlay is a *comparable* expectation, drawn on the same axes
    # as the bars: its own expected counts therefore sum to the same
    # sample count the bars do. Regression guard for a real defect --
    # the overlay used to approximate each bin as one midpoint density
    # times the bin width, which loses nearly all the mass of the
    # sharply-peaked mixture components a realistic mutation rate
    # produces, so the drawn curve collapsed onto the axis.
    overlay_total = sum(point["expectedCount"] for point in spectrum["betaOverlay"])
    assert overlay_total == pytest.approx(8.0)


def test_literature_visual_payload_remaps_allele_composition_legend_labels() -> None:
    """The allele-composition legend shows a dense 1-based order, not raw ids.

    Regression test for a real, reported confusion: the barplot (for one
    interval this session called "STRUCTURE-style allele composition"
    and removed outright over a misreading of the request to drop that
    name) used to label each legend entry with the underlying minted-
    and-retired internal allele id directly (`f"Allele {allele_id}"`),
    which is sparse — a reader had no way to tell whether a low id was
    simply not common enough to make the legend's own top-`max_alleles`
    cut, or never existed at all ("where did all the missing ones go?").
    `_gradient_state`'s own two alleles are `AlleleId(0)`/`AlleleId(1)`
    — already dense — so this test uses a state with a gap (allele id
    `5`, no `2`/`3`/`4`) to actually distinguish "dense display order"
    from "raw id" behavior.
    """
    params = SimulationParams(
        N=50,
        m=0.05,
        mu=0.01,
        d=2,
        seed=7,
        loci=(LocusSpec(1, 200),),
        convergence_window=4,
        convergence_tolerance=1.0,
        max_generations=10,
        n_replicates=1,
        replicate_tolerance=None,
    )
    state = ModelState(
        loci=(LocusSpec(1, 200),),
        frequencies=(
            ({AlleleId(0): 0.5, AlleleId(5): 0.5},),
            ({AlleleId(0): 0.3, AlleleId(5): 0.7},),
        ),
    )

    payload = literature_visual_payload(state, params)

    composition = payload["alleleComposition"]
    assert composition["title"] == "Allele composition by deme"
    labels = [allele["label"] for allele in composition["alleles"]]
    assert labels == ["Allele 1", "Allele 2", "Other alleles"]
    # Sorted by descending average frequency, tie-broken by ascending
    # allele id (`allele_composition_payload`'s own sort key) -- allele
    # `5` averages 0.6 across the two demes above, allele `0` averages
    # 0.4, so `5` (the higher-frequency allele) gets the dense "Allele
    # 1" label despite its own raw id being larger.
    keys = [allele["key"] for allele in composition["alleles"]]
    assert keys == ["5", "0", "other"]


def test_literature_visual_payload_groups_identity_by_stepping_stone_distance() -> None:
    """A topology-expanded migration matrix still yields IBD distance classes."""
    params = SimulationParams.from_mapping(
        {
            "N": 50,
            "d": 4,
            "m": {"topology": "ring", "rate": 0.05},
            "mu": 0.01,
            "seed": 7,
            "loci": [{"locus_id": 1, "length": 200}],
            "convergence_window": 4,
            "convergence_tolerance": 1.0,
            "max_generations": 10,
            "n_replicates": 1,
            "replicate_tolerance": None,
        }
    )

    payload = literature_visual_payload(_gradient_state(), params)

    ibd = payload["isolationByDistance"]
    assert ibd is not None
    assert [point["distance"] for point in ibd["points"]] == [1, 2]
    assert [point["pairCount"] for point in ibd["points"]] == [4, 2]
    assert ibd["fit"] is not None


def _base_params(*, d: int = 2) -> SimulationParams:
    """A minimal, valid `SimulationParams` for the pooling tests below."""
    return SimulationParams(
        N=50,
        m=0.05,
        mu=0.01,
        d=d,
        seed=7,
        loci=(LocusSpec(1, 200),),
        convergence_window=4,
        convergence_tolerance=1.0,
        max_generations=10,
        n_replicates=1,
        replicate_tolerance=None,
    )


def test_pooled_literature_visual_payload_matches_the_single_state_wrapper() -> None:
    """Pooling exactly one state reproduces `literature_visual_payload`'s own output.

    A real, reported gap: a completed batch's own run view used to
    carry no supplemental-visuals payload at all (`fim.gui.app.
    _batch_done_payload` never computed one), unlike a completed scalar
    run — this and the two tests below prove the pooled entry point is
    the identical underlying computation, not a second, independently
    maintained implementation that could drift from the single-state
    one over time.
    """
    params = _base_params()
    state = _gradient_state()

    assert pooled_literature_visual_payload((state,), params) == (
        literature_visual_payload(state, params)
    )


def test_pooled_allele_composition_payload_averages_frequencies_across_states() -> None:
    """Pooling two states averages their own per-deme frequencies, not just the first.

    `state_a`'s only deme is 100% allele 0; `state_b`'s own single deme
    (same shape) is 100% allele 1 — a pooled call must show each at 50%,
    proving the frequencies genuinely combine rather than the pooled
    entry point silently reading only its first argument.
    """
    loci = (LocusSpec(1, 200),)
    state_a = ModelState(loci=loci, frequencies=(({AlleleId(0): 1.0},),))
    state_b = ModelState(loci=loci, frequencies=(({AlleleId(1): 1.0},),))

    single = pooled_allele_composition_payload((state_a,))
    pooled = pooled_allele_composition_payload((state_a, state_b))

    assert single["demes"][0]["segments"][0]["value"] == 1.0
    assert (
        single["note"]
        == "Allele frequencies are averaged across loci within each deme."
    )

    pooled_values = {
        segment["key"]: segment["value"] for segment in pooled["demes"][0]["segments"]
    }
    assert pooled_values["0"] == 0.5
    assert pooled_values["1"] == 0.5
    assert pooled["note"] == (
        "Allele frequencies are averaged across loci and 2 replicates within each deme."
    )


def test_pooled_frequency_spectrum_payload_concatenates_every_states_frequencies() -> (
    None
):
    """Pooling two states' own spectra concatenates their frequency samples.

    Each of `_gradient_state`'s own four demes contributes two nonzero
    frequencies (one locus, two alleles), so one state contributes 8
    samples total (matching `test_literature_visual_payload_carries_a_
    wright_beta_overlay`'s own count above) and pooling two identical
    copies of it must contribute 16 — proving the histogram counts
    accumulate across states rather than only reflecting the last one.
    """
    params = _base_params(d=4)
    state = _gradient_state()

    single = pooled_frequency_spectrum_payload((state,), params)
    pooled = pooled_frequency_spectrum_payload((state, state), params)

    single_total = sum(bin_["count"] for bin_ in single["bins"])
    pooled_total = sum(bin_["count"] for bin_ in pooled["bins"])
    assert single_total == 8
    assert pooled_total == 16


def test_pooled_isolation_by_distance_payload_counts_pairs_across_states() -> None:
    """`pairCount` totals (deme pair, state) samples, not just deme pairs.

    Pooling two identical copies of `_gradient_state` must double every
    distance class's own `pairCount` relative to pooling just one copy,
    since each deme pair now contributes one identity sample per state.
    """
    params = SimulationParams.from_mapping(
        {
            "N": 50,
            "d": 4,
            "m": {"topology": "ring", "rate": 0.05},
            "mu": 0.01,
            "seed": 7,
            "loci": [{"locus_id": 1, "length": 200}],
            "convergence_window": 4,
            "convergence_tolerance": 1.0,
            "max_generations": 10,
            "n_replicates": 1,
            "replicate_tolerance": None,
        }
    )
    state = _gradient_state()

    single = pooled_isolation_by_distance_payload((state,), params)
    pooled = pooled_isolation_by_distance_payload((state, state), params)

    assert single is not None
    assert pooled is not None
    assert [point["pairCount"] for point in single["points"]] == [4, 2]
    assert [point["pairCount"] for point in pooled["points"]] == [8, 4]
