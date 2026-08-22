"""Unit tests for `fim.gui.app.Api`'s window-free bridge methods.

No display, no Tk import, no `gui` marker: `get_starter_form`,
`validate_form`, and `get_default_max_workers` never touch
`webview.windows[0]` — they call straight into `fim.gui.config_form`/
`fim.gui.batch_runner`, so they are exercised here as plain Python calls,
far cheaper and more direct than driving them through a real window and
`evaluate_js` (design doc §6.2's unit layer, applied to the bridge
itself). `load_yaml`/`save_yaml` do need a real window (a real file
dialog) and are covered instead in `test/gui/test_app.py`, marked `gui`.
"""

from __future__ import annotations

from fim.gui.app import Api
from fim.gui.batch_runner import default_max_workers
from fim.gui.config_form import starter_form_values


def test_get_starter_form_matches_config_form_directly() -> None:
    """The bridge method adds no logic of its own beyond `starter_form_values`."""
    assert Api().get_starter_form() == starter_form_values()


def test_get_default_max_workers_matches_batch_runner_directly() -> None:
    """The Batch tab's default is `batch_runner.default_max_workers`, not invented."""
    assert Api().get_default_max_workers() == default_max_workers()


def test_validate_form_accepts_the_starter_values() -> None:
    """The starter form is valid on its own — no field left in a rejecting state."""
    result = Api().validate_form(starter_form_values())

    assert result == {"ok": True}


def test_validate_form_rejects_and_locates_an_invalid_population_field() -> None:
    """An invalid `N` is rejected, named, and routed to the Population tab."""
    values = dict(starter_form_values())
    values["N"] = "not-a-number"

    result = Api().validate_form(values)

    assert result["ok"] is False
    assert result["field"] == "N"
    assert result["tab"] == "population"
    assert "N must be an integer" in result["message"]


def test_validate_form_rejects_and_locates_an_invalid_migration_rate() -> None:
    """An invalid scalar `m` rate routes to Migration via the composite selector."""
    values = dict(starter_form_values())
    values["m_mode"] = "scalar"
    values["m_rate"] = "not-a-number"

    result = Api().validate_form(values)

    assert result["ok"] is False
    assert result["tab"] == "migration"


def test_validate_form_rejects_an_invalid_choice_field() -> None:
    """A "choice"-kind field's own error is located exactly like an "int" field's."""
    values = dict(starter_form_values())
    values["deme_weighting"] = "not-a-real-choice"

    result = Api().validate_form(values)

    assert result["ok"] is False
    assert result["field"] == "deme_weighting"
    assert result["tab"] == "population"
    assert "deme_weighting" in result["message"]
