"""Tests for `pairwise.json`: every deme pair's Nei identities."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from fim.model.allele import AlleleId
from fim.model.locus import LocusSpec
from fim.model.state import ModelState
from fim.persistence.pairwise import (
    pair_value,
    pairwise_payload,
    read_pairwise,
    upper_triangle_index,
    write_pairwise,
)
from fim.statistics import nei_pair_identity, pairwise_f_st

_LOCI = (LocusSpec(1, 100), LocusSpec(2, 100))
_FREQUENCIES = (
    ({AlleleId(0): 1.0}, {AlleleId(0): 0.5, AlleleId(1): 0.5}),
    ({AlleleId(0): 0.2, AlleleId(2): 0.8}, {AlleleId(1): 1.0}),
    ({AlleleId(0): 0.5, AlleleId(2): 0.5}, {AlleleId(0): 0.3, AlleleId(1): 0.7}),
    ({AlleleId(3): 1.0}, {AlleleId(1): 0.4, AlleleId(4): 0.6}),
)


def _state() -> ModelState:
    """Four demes, two loci, one deme sharing nothing at locus 1."""
    return ModelState(loci=_LOCI, frequencies=_FREQUENCIES, generation=17)


def test_upper_triangle_index_is_row_major() -> None:
    """(0,1), (0,2), (0,3), (1,2), (1,3), (2,3) map to 0..5, either order."""
    pairs = list(itertools.combinations(range(4), 2))
    assert [upper_triangle_index(4, a, b) for a, b in pairs] == list(range(6))
    assert upper_triangle_index(4, 3, 1) == upper_triangle_index(4, 1, 3)
    with pytest.raises(ValueError):
        upper_triangle_index(4, 2, 2)
    with pytest.raises(ValueError):
        upper_triangle_index(4, 0, 4)


def test_payload_holds_every_pair_matching_the_pair_function(tmp_path: Path) -> None:
    """Written, read back, and equal to `nei_pair_identity` for every pair."""
    path = tmp_path / "pairwise.json"
    write_pairwise(path, pairwise_payload(_state(), max_demes=1024))
    payload = read_pairwise(path)
    assert payload["mode"] == "full"
    assert payload["generation"] == 17
    assert payload["deme_count"] == 4
    for first, second in itertools.combinations(range(4), 2):
        for key, denominator, locus_rule in (
            ("NEI_I_PAIR_GEO", "geometric", "pooled"),
            ("NEI_I_PAIR_ARITH_LOCUS_MEAN", "arithmetic", "locus_mean"),
        ):
            expected = nei_pair_identity(
                [dict(_FREQUENCIES[first][locus]) for locus in range(2)],
                [dict(_FREQUENCIES[second][locus]) for locus in range(2)],
                denominator=denominator,  # type: ignore[arg-type]
                locus_rule=locus_rule,  # type: ignore[arg-type]
            )
            assert pair_value(payload, key, first, second) == pytest.approx(
                expected, abs=1e-12
            )
    assert pair_value(payload, "NEI_I_PAIR_GEO", 2, 2) == 1.0


def test_no_shared_allele_is_stored_as_a_finite_zero(tmp_path: Path) -> None:
    """Strict JSON: an infinite distance is stored as identity 0."""
    path = tmp_path / "pairwise.json"
    write_pairwise(path, pairwise_payload(_state(), max_demes=1024))
    payload = read_pairwise(path)
    # Demes 0 and 3 share nothing at locus 1, so the locus mean is 0.
    assert pair_value(payload, "NEI_I_PAIR_GEO_LOCUS_MEAN", 0, 3) == 0.0


def test_above_the_limit_the_matrices_are_skipped() -> None:
    """A deme count over the limit records the limit, not the matrices."""
    payload = pairwise_payload(_state(), max_demes=3)
    assert payload["mode"] == "skipped"
    assert payload["max_demes"] == 3
    assert "matrices" not in payload
    assert pair_value(payload, "NEI_I_PAIR_GEO", 0, 1) is None


def test_written_bytes_are_deterministic(tmp_path: Path) -> None:
    """Writing the same state twice gives identical bytes."""
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    write_pairwise(first, pairwise_payload(_state(), max_demes=1024))
    write_pairwise(second, pairwise_payload(_state(), max_demes=1024))
    assert first.read_bytes() == second.read_bytes()
    assert first.read_text(encoding="utf-8").endswith("\n")


def test_unknown_schema_version_is_rejected(tmp_path: Path) -> None:
    """A future format is not misread."""
    path = tmp_path / "pairwise.json"
    path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        read_pairwise(path)


def test_pairwise_f_st_matrix_matches_the_pair_function(tmp_path: Path) -> None:
    """The saved pairwise F_ST equals `pairwise_f_st` at one locus, pooled at two."""
    loci = (LocusSpec(1, 100),)
    frequencies = (
        ({AlleleId(0): 0.5, AlleleId(1): 0.5},),
        ({AlleleId(1): 0.2, AlleleId(2): 0.8},),
        ({AlleleId(0): 0.1, AlleleId(2): 0.9},),
    )
    state = ModelState(loci=loci, frequencies=frequencies)
    payload = pairwise_payload(state, max_demes=1024)
    for first, second in itertools.combinations(range(3), 2):
        assert pair_value(payload, "F_ST_PAIR", first, second) == pytest.approx(
            pairwise_f_st(dict(frequencies[first][0]), dict(frequencies[second][0]))
        )
    assert pair_value(payload, "F_ST_PAIR", 1, 1) == 0.0


def test_pairwise_f_st_is_null_where_undefined(tmp_path: Path) -> None:
    """Two demes fixed for the same allele: F_ST undefined, saved as null."""
    loci = (LocusSpec(1, 100),)
    state = ModelState(
        loci=loci,
        frequencies=(({AlleleId(0): 1.0},), ({AlleleId(0): 1.0},)),
    )
    path = tmp_path / "pairwise.json"
    write_pairwise(path, pairwise_payload(state, max_demes=1024))
    payload = read_pairwise(path)
    assert payload["matrices"]["F_ST_PAIR"] == [None]
    assert pair_value(payload, "F_ST_PAIR", 0, 1) is None
