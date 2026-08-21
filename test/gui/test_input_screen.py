"""Headless functional tests for Screen 1's six tabs.

`test_input_screen_run_button_disabled_until_valid` (design §6.4's own
named test) is now meaningful end to end: every tab exists, so a fresh,
prefilled screen actually validates successfully and enables "Run
simulation." An invalid field's own tab gets a small error dot and the
always-visible banner names both the tab and the message (design §4.0
#2's "no click-through hunting").
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


def test_notebook_has_all_six_tabs_in_order(app: Application) -> None:
    """Every tab §4.1 names appears, in `configuration.md`'s own section order."""
    screen = InputScreen(app)

    tab_labels = [
        screen._notebook.tab(tab_id, "text")  # type: ignore[no-untyped-call]
        for tab_id in screen._notebook.tabs()  # type: ignore[no-untyped-call]
    ]

    assert tab_labels == [
        "Population",
        "Migration",
        "Mutation",
        "Initial conditions",
        "Convergence",
        "Batch",
    ]


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


def test_input_screen_run_button_disabled_until_valid(app: Application) -> None:
    """Design §6.4's own named test: valid by default, invalid once broken.

    A fresh, prefilled screen validates successfully (every tab's
    required fields now exist) and enables "Run simulation"; an
    invalid edit disables it again.
    """
    screen = InputScreen(app)
    assert "disabled" not in screen._run_button.state()
    assert screen._valid_params is not None

    screen._vars["d"].set("not a number")
    app.update_idletasks()

    assert "disabled" in screen._run_button.state()
    assert screen._valid_params is None


def test_on_run_only_ever_fires_with_validated_params(app: Application) -> None:
    """ "Run simulation" calls `on_run` with the validated params, never otherwise."""
    received: list[object] = []
    screen = InputScreen(app, on_run=received.append)

    screen._vars["d"].set("not a number")
    app.update_idletasks()
    screen._on_run_clicked()
    assert received == []

    screen._vars["d"].set("5")
    app.update_idletasks()
    screen._on_run_clicked()

    assert len(received) == 1
    assert received[0] is screen._valid_params


def test_batch_extra_fields_shown_only_once_n_replicates_exceeds_one(
    app: Application,
) -> None:
    """`replicate_tolerance`/`minimum`/`confidence` appear once `n_replicates > 1`."""
    screen = InputScreen(app)
    assert len(screen._batch_extra_rows.grid_info()) == 0

    screen._vars["n_replicates"].set("20")
    app.update_idletasks()

    assert len(screen._batch_extra_rows.grid_info()) > 0

    screen._vars["n_replicates"].set("1")
    app.update_idletasks()

    assert len(screen._batch_extra_rows.grid_info()) == 0


def test_convergence_combinator_shown_only_once_two_statistics_are_checked(
    app: Application,
) -> None:
    """`convergence_combinator` appears only once two or more statistics are checked."""
    screen = InputScreen(app)
    assert len(screen._combinator_row.grid_info()) == 0

    screen._cs_vars["G_ST"].set("true")
    app.update_idletasks()

    assert len(screen._combinator_row.grid_info()) > 0

    screen._cs_vars["G_ST"].set("false")
    app.update_idletasks()

    assert len(screen._combinator_row.grid_info()) == 0


def test_mu_mode_switch_shows_only_the_selected_sub_row(app: Application) -> None:
    """Selecting "mu_b" hides the `mu` row and reveals the `mu_b` row."""
    screen = InputScreen(app)
    assert len(screen._mu_row.grid_info()) > 0
    assert len(screen._mu_b_row.grid_info()) == 0

    screen._vars["mu_mode"].set("mu_b")
    app.update_idletasks()

    assert len(screen._mu_row.grid_info()) == 0
    assert len(screen._mu_b_row.grid_info()) > 0


def test_loading_a_matrix_m_shows_the_read_only_badge(app: Application) -> None:
    """`set_values` with a "loaded" `m_mode` shows the badge row, not either sub-row."""
    screen = InputScreen(app)
    values = dict(screen.get_values())
    values.update(
        {
            "m_mode": "loaded",
            "m_loaded_summary": "3x3 migration matrix (loaded from file)",
        }
    )

    screen.set_values(values)

    assert len(screen._m_scalar_row.grid_info()) == 0
    assert len(screen._m_topology_row.grid_info()) == 0
    assert len(screen._m_loaded_row.grid_info()) > 0


def test_p0_summary_field_reflects_set_values(app: Application) -> None:
    """The read-only `p_0` summary label follows `set_values`, like any other field."""
    screen = InputScreen(app)
    assert screen.get_values()["p0_summary"] == ""

    screen.set_values(
        {**screen.get_values(), "p0_summary": "initial frequencies loaded"}
    )

    assert screen.get_values()["p0_summary"] == "initial frequencies loaded"


def test_get_values_includes_every_convergence_statistic_checkbox(
    app: Application,
) -> None:
    """`get_values` reports every `cs_<NAME>` checkbox key, checked or not."""
    screen = InputScreen(app)

    values = screen.get_values()

    assert values["cs_D"] == "true"
    for name in ("G_ST", "E_ST", "K_ST", "H_S", "H_T"):
        assert values[f"cs_{name}"] == "false"


def test_invalid_field_flags_its_tab_with_an_error_dot(app: Application) -> None:
    """An invalid plain field marks its own tab's label, not any other tab's."""
    screen = InputScreen(app)

    screen._vars["d"].set("not a number")
    app.update_idletasks()

    tab_labels = [
        screen._notebook.tab(tab_id, "text")  # type: ignore[no-untyped-call]
        for tab_id in screen._notebook.tabs()  # type: ignore[no-untyped-call]
    ]
    assert tab_labels[0] == "Population \N{WARNING SIGN}"
    assert tab_labels[1:] == [
        "Migration",
        "Mutation",
        "Initial conditions",
        "Convergence",
        "Batch",
    ]


def test_fixing_the_invalid_field_clears_its_tabs_error_dot(app: Application) -> None:
    """Once the field validates again, its tab's error dot disappears."""
    screen = InputScreen(app)
    screen._vars["d"].set("not a number")
    app.update_idletasks()
    assert (
        screen._notebook.tab(  # type: ignore[no-untyped-call]
            screen._tab_frames["population"], "text"
        )
        == "Population \N{WARNING SIGN}"
    )

    screen._vars["d"].set("20")
    app.update_idletasks()

    assert (
        screen._notebook.tab(  # type: ignore[no-untyped-call]
            screen._tab_frames["population"], "text"
        )
        == "Population"
    )


def test_invalid_composite_field_flags_its_own_tab_not_migration(
    app: Application,
) -> None:
    """A bad `mu` flags Mutation, not Migration — a regression guard for the
    bare-`m`-is-a-prefix-of-`mu` routing bug."""
    screen = InputScreen(app)

    screen._vars["mu_value"].set("not a number")
    app.update_idletasks()

    assert (
        screen._notebook.tab(  # type: ignore[no-untyped-call]
            screen._tab_frames["mutation"], "text"
        )
        == "Mutation \N{WARNING SIGN}"
    )
    assert (
        screen._notebook.tab(  # type: ignore[no-untyped-call]
            screen._tab_frames["migration"], "text"
        )
        == "Migration"
    )


def test_banner_names_the_offending_tab_and_the_message(app: Application) -> None:
    """The always-visible banner names both the tab and the validation message.

    §4.0 #2's "no click-through hunting": the banner is readable
    regardless of which tab happens to be selected.
    """
    screen = InputScreen(app)

    screen._vars["d"].set("not a number")
    app.update_idletasks()

    assert screen._banner["text"] == "Population: d must be an integer"


def test_banner_names_convergence_for_a_below_minimum_window(app: Application) -> None:
    """A semantic (not just a parse) failure is routed and prefixed too."""
    screen = InputScreen(app)

    screen._vars["convergence_window"].set("1")
    app.update_idletasks()

    assert screen._banner["text"] == (
        "Convergence: convergence_window must be at least 2"
    )
    assert (
        screen._notebook.tab(  # type: ignore[no-untyped-call]
            screen._tab_frames["convergence"], "text"
        )
        == "Convergence \N{WARNING SIGN}"
    )
