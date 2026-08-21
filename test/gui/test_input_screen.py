"""Headless functional tests for Screen 1's Population and Migration tabs.

Full validation-wiring tests (`Run simulation` enabling/disabling,
inline vs. banner error placement across every tab) belong once every
tab exists — this commit's own surface is the tab structure itself,
the two tabs' plain fields, and the `m` selector's mode switch.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from fim.gui.app import Application
from fim.gui.screens.input_screen import InputScreen

pytestmark = pytest.mark.gui


@pytest.fixture
def app() -> Iterator[Application]:
    """Build and tear down one real `Application` root per test."""
    application = Application()
    try:
        yield application
    finally:
        application.destroy()


def test_notebook_has_population_and_migration_tabs_in_order(app: Application) -> None:
    """The two tabs this commit builds appear, in the design's own order."""
    screen = InputScreen(app)

    tab_labels = [
        screen._notebook.tab(tab_id, "text")  # type: ignore[no-untyped-call]
        for tab_id in screen._notebook.tabs()  # type: ignore[no-untyped-call]
    ]

    assert tab_labels == ["Population", "Migration"]


def test_prefill_matches_the_starter_config(app: Application) -> None:
    """A fresh screen starts from `config_form.starter_form_values()`."""
    screen = InputScreen(app)

    values = screen.get_values()

    assert values["N"] == "450"
    assert values["d"] == "20"
    assert values["seed"] == "20260814"
    assert values["deme_weighting"] == "size"
    assert values["max_generations"] == "10000"
    assert values["migrant_sampling"] == "continuous"
    assert values["m_mode"] == "scalar"
    assert values["m_rate"] == "0.001"


def test_set_values_replaces_population_and_migration_fields(app: Application) -> None:
    """`set_values` writes through to every built widget, round-tripping."""
    screen = InputScreen(app)
    new_values = dict(screen.get_values())
    new_values.update(
        {
            "N": "200,300",
            "d": "2",
            "seed": "99",
            "deme_weighting": "equal",
            "max_generations": "500",
            "migrant_sampling": "stochastic",
            "m_mode": "topology",
            "m_topology": "linear",
            "m_topology_rate": "0.2",
        }
    )

    screen.set_values(new_values)

    assert screen.get_values()["N"] == "200,300"
    assert screen.get_values()["deme_weighting"] == "equal"
    assert screen.get_values()["migrant_sampling"] == "stochastic"
    assert screen.get_values()["m_mode"] == "topology"
    assert screen.get_values()["m_topology"] == "linear"
    assert screen.get_values()["m_topology_rate"] == "0.2"


def test_m_mode_switch_shows_only_the_selected_sub_row(app: Application) -> None:
    """Selecting "topology" hides the scalar row and reveals the topology row.

    `grid_info()` (empty after `grid_remove()`, populated after `grid()`)
    is used rather than `winfo_ismapped()`: the latter also depends on
    whether the Migration tab is the Notebook's currently *selected*
    tab, which is a separate concern this test does not need to drive.
    `len(...)`, not bare truthiness, sidesteps mypy's own
    always-truthy-TypedDict special case for `grid_info()`'s stub
    return type — accurate for a real grid-managed widget, but not for
    one `grid_remove()` has just emptied.
    """
    screen = InputScreen(app)
    assert len(screen._m_scalar_row.grid_info()) > 0
    assert len(screen._m_topology_row.grid_info()) == 0

    screen._vars["m_mode"].set("topology")
    screen.update_idletasks()

    assert len(screen._m_scalar_row.grid_info()) == 0
    assert len(screen._m_topology_row.grid_info()) > 0

    screen._vars["m_mode"].set("scalar")
    screen.update_idletasks()

    assert len(screen._m_scalar_row.grid_info()) > 0
    assert len(screen._m_topology_row.grid_info()) == 0


def test_reset_to_defaults_restores_the_starter_config(app: Application) -> None:
    """ "Reset to defaults" discards edits and restores the starter values."""
    screen = InputScreen(app)
    screen.set_values({**screen.get_values(), "d": "99"})
    assert screen.get_values()["d"] == "99"

    screen._on_reset_to_defaults()

    assert screen.get_values()["d"] == "20"
