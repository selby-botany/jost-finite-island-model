"""Unit tests for literature-derived GUI visualization payloads."""

from __future__ import annotations

import pytest

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


def test_literature_visual_payload_carries_structure_and_spectrum() -> None:
    """Scalar runs expose stacked composition and a Wright beta overlay."""
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

    structure = payload["structureBars"]
    assert [entry["label"] for entry in structure["alleles"][:2]] == [
        "Allele 0",
        "Allele 1",
    ]
    for deme in structure["demes"]:
        assert sum(segment["value"] for segment in deme["segments"]) == pytest.approx(1)
    spectrum = payload["frequencySpectrum"]
    assert len(spectrum["bins"]) == 20
    assert sum(bin_["count"] for bin_ in spectrum["bins"]) == 8
    assert spectrum["betaOverlay"] is not None


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
