"""Headless functional tests for Settings' "Statistics shown" chooser.

`test/gui/test_app_api.py` proves the bridge methods as plain Python
calls; these prove the page builds the chooser from the statistic catalog,
applies a choice to the statistics panel at once, and hides the Nei
family by default (statistics catalog design, section 6.5).
"""

from __future__ import annotations

import queue
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview

from .conftest import poll_page

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"

_ROW_STATE = (
    "({"
    "d: document.getElementById('stat-D').hidden, "
    "neiAll: document.getElementById('stat-NEI_D_ALL_GEO').hidden, "
    "neiPair: document.getElementById('stat-NEI_D_PAIR_ARITH').hidden, "
    "neiLocusMean: document.getElementById('stat-NEI_D_PAIR_ARITH_LOCUS_MEAN').hidden, "
    "pairHeading: document.getElementById('pair-statistics-heading').hidden, "
    "shown: window.fim.getShownStatistics()"
    "})"
)


def test_the_nei_family_starts_hidden_and_the_long_standing_statistics_shown(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """A fresh install: `D` shown, every Nei row and the pair heading hidden."""
    settled = drive(window, ready=_INPUT_SCREEN_READY, trigger="null", read=_ROW_STATE)

    assert settled["d"] is False
    assert settled["neiAll"] is True
    assert settled["neiPair"] is True
    assert settled["pairHeading"] is True
    assert "D" in settled["shown"]
    assert not any(key.startswith("NEI_") for key in settled["shown"])


def test_the_chooser_lists_every_catalog_statistic_grouped(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Opening it from the panel builds one checkbox per catalog entry."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger="window.fim.openStatisticsSettings();",
        read=(
            "({"
            "boxes: document.querySelectorAll("
            "'#settings-statistics-list input[type=checkbox]').length, "
            "catalog: STATISTIC_CATALOG.length, "
            "groups: Array.from(document.querySelectorAll("
            "'#settings-statistics-list .settings-statistics-group h4'"
            ")).map((h) => h.textContent), "
            "open: document.getElementById('modal-settings').open"
            "})"
        ),
        # Both, not just the checkboxes: `loadSettingsDialog` builds the
        # list before it opens the dialog, so a read between the two
        # would see boxes with the dialog still closed.
        is_ready=lambda value: (
            value is not None and value["boxes"] > 0 and value["open"] is True
        ),
    )

    assert settled["open"] is True
    assert settled["boxes"] == settled["catalog"]
    assert "Nei distances" in settled["groups"]


def test_the_chooser_marks_the_expensive_statistics_as_lengthening_runs(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """E_ST, K_ST, A_CGD, δG and I carry a run-time warning; they start hidden.

    Showing one makes every new run compute them each generation
    (`Api._merge_default_run_settings`), so the chooser says so on each of
    them, in a tooltip and in a visible line, and nowhere else.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger="window.fim.openStatisticsSettings();",
        read=(
            "document.getElementById('modal-settings').open ? "
            "Object.fromEntries(Array.from(document.querySelectorAll("
            "'#settings-statistics-list .settings-statistics-item')).map("
            "(item) => [item.querySelector('input').value, {"
            "title: item.title, "
            "cost: item.querySelector('.settings-statistics-cost') "
            "? item.querySelector('.settings-statistics-cost').textContent : null, "
            "checked: item.querySelector('input').checked"
            "}])) : null"
        ),
    )

    expensive = ("E_ST", "K_ST", "A_CGD", "Delta", "MI")
    for key in expensive:
        assert "take longer" in settled[key]["title"], key
        assert settled[key]["cost"] == settled[key]["title"], key
        assert settled[key]["checked"] is False, key
    for key, item in settled.items():
        if key not in expensive:
            assert item["cost"] is None, key
    assert settled["D"]["checked"] is True


def test_the_nei_distances_preset_applies_to_the_panel_at_once(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The preset shows Nei's pooled-rule distances and hides the rest."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "window.fim.openStatisticsSettings().then(() => "
            "document.querySelector('[data-statistics-preset=distances]').click());"
        ),
        read=_ROW_STATE,
        is_ready=lambda value: value is not None and value["neiAll"] is False,
    )

    assert settled["d"] is True
    assert settled["neiAll"] is False
    assert settled["neiPair"] is False
    assert settled["neiLocusMean"] is True
    assert settled["pairHeading"] is False
    assert all(key.startswith("NEI_D_") for key in settled["shown"])


def test_the_filter_narrows_the_list_to_matching_statistics(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Typing "Jost" leaves only entries whose text mentions it visible."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "window.fim.openStatisticsSettings().then(() => {"
            "const filter = document.getElementById('settings-statistics-filter');"
            "filter.value = 'Jost';"
            "filter.dispatchEvent(new Event('input'));"
            "window.__fimFilterApplied = true;"
            "});"
        ),
        read=(
            "window.__fimFilterApplied ? Array.from(document.querySelectorAll("
            "'#settings-statistics-list .settings-statistics-item')"
            ").filter((item) => !item.hidden).map((item) => "
            "item.querySelector('input').value) : null"
        ),
        is_ready=lambda value: value is not None,
    )

    assert "D" in settled
    assert "NEI_D_PAIR_ARITH" in settled
    assert "NEI_D_PAIR_GEO" not in settled


def test_pair_rows_follow_the_deme_pair_chosen_for_the_scatter(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    """After a run: demes 1 and 2 by default; choosing 1 and 3 updates them."""
    set_fields = (
        "function setField(name, value) {"
        "const field = document.getElementById(`field-${name}`);"
        "field.value = value;"
        "field.dispatchEvent(new Event('input', {bubbles: true}));"
        "}"
        "setField('N', '20'); setField('d', '3'); setField('seed', '20260814');"
        "setField('m_rate', '0.1'); setField('mu_value', '0.01');"
        "setField('locus_lengths', '200');"
    )
    pair_state = (
        "({"
        "heading: document.getElementById('pair-statistics-heading-text').textContent, "
        "value: document.getElementById('stat-NEI_D_PAIR_GEO').title, "
        "hidden: document.getElementById('stat-NEI_D_PAIR_GEO').hidden, "
        "state: window.fim.getRunViewState(), "
        "pending: window.__fimScrubberPending"
        "})"
    )
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            poll_page(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(
                "window.fim.setShownStatistics(['D', 'NEI_D_PAIR_GEO']);"
                + set_fields
                + "document.getElementById('run-button').click();"
            )
            default_pair = poll_page(
                window,
                pair_state,
                lambda value: value["state"] == "completed" and value["pending"] == 0,
            )
            window.evaluate_js(
                "document.getElementById('run-x-deme').value = '1';"
                "document.getElementById('run-y-deme').value = '3';"
                "document.getElementById('run-y-deme')"
                ".dispatchEvent(new Event('change'));"
            )
            chosen_pair = poll_page(
                window,
                pair_state,
                lambda value: (
                    value["heading"] == "Demes 1 and 3" and value["pending"] == 0
                ),
            )
            outcome.put({"default": default_pair, "chosen": chosen_pair})
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=120)

    assert settled["default"]["hidden"] is False
    assert settled["default"]["heading"] == "Demes 1 and 2"
    assert settled["default"]["value"].startswith("Nei D_pair")
    assert settled["chosen"]["heading"] == "Demes 1 and 3"
