"""Static checks that the documentation states the shipped convergence multiples."""

from __future__ import annotations

from pathlib import Path

import pytest

from fim.config.convergence import (
    BURN_IN_MINIMUM_RELAXATION_TIMES,
    CAP_RELAXATION_MULTIPLE,
    MINIMUM_MAX_GENERATIONS,
)

ROOT = Path(__file__).resolve().parents[1]
DOCS = ("doc/configuration.md", "doc/convergence.md")


@pytest.mark.parametrize("relative", DOCS)
def test_docs_state_the_shipped_burn_in_and_cap_formulas(relative: str) -> None:
    """The formulas in the docs match `fim.convergence.defaults`."""
    text = (ROOT / relative).read_text(encoding="utf-8")

    burn_in = f"max({BURN_IN_MINIMUM_RELAXATION_TIMES:g}, ln(2 / precision))"
    cap = (
        f"max({MINIMUM_MAX_GENERATIONS}, burn_in + "
        f"ceil({CAP_RELAXATION_MULTIPLE:g} tau))"
    )

    assert burn_in in text
    assert cap in text
