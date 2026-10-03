"""Headless functional tests for Settings' "Statistics shown" chooser.

`test/gui/test_app_api.py` proves the bridge methods as plain Python
calls; these prove the page builds the chooser from the statistic catalog,
applies a choice to the statistics panel at once, and hides the Nei
family by default (statistics catalog design, section 6.5).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import webview

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
