"""Unit tests for literature-derived GUI visualization payloads."""

from __future__ import annotations

from fim.gui.literature_visuals import literature_visual_payload
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
