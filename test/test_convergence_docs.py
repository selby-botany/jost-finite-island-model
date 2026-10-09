"""Static checks that the documentation states the shipped convergence multiples."""

from __future__ import annotations

from pathlib import Path

import pytest

from fim.config.convergence import (
    CAP_RELAXATION_MULTIPLE,
    MINIMUM_MAX_GENERATIONS,
    MINIMUM_WINDOW,
    WINDOW_RELAXATION_MULTIPLE,
)

ROOT = Path(__file__).resolve().parents[1]
DOCS = ("doc/configuration.md", "doc/convergence.md")


@pytest.mark.parametrize("relative", DOCS)
def test_docs_state_the_shipped_window_and_cap_formulas(relative: str) -> None:
    """The formulas in the docs match `fim.convergence.defaults`."""
    text = (ROOT / relative).read_text(encoding="utf-8")

    window = f"max({MINIMUM_WINDOW}, ceil({WINDOW_RELAXATION_MULTIPLE:g} tau))"
    cap = f"max({MINIMUM_MAX_GENERATIONS}, ceil({CAP_RELAXATION_MULTIPLE:g} tau))"

    assert window in text
    assert cap in text
