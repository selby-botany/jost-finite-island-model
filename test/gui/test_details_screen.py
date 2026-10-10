"""Headless functional tests for names, descriptions, and the details dialog.

Real DOM-driven proof that `webui/screens/details.js` and
`webui/screens/run-title.js` work end to end: every Experiment, Study,
and Run name shown carries its description as a tooltip, its details
open in `#modal-details` and save through the bridge, and the Run card's
title names the run's Experiment in place of a generic "FIM simulation".
`test/gui/test_app_api.py` proves the bridge methods as plain Python
calls; this file proves the page wires them together.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview
import yaml

from fim import cli, paths
from fim.gui.app import Api
from fim.persistence import groups

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"
_TITLE = "document.getElementById('run-plot-title').textContent"

# Mirrors `test/gui/test_results_screen.py`'s own identically-named
# constant -- a direct parallel, not a shared import, per this project's
# established per-test-file fixture convention.
_SET_TINY_FIELDS = """
function setField(name, value) {
    const field = document.getElementById(`field-${name}`);
    field.value = value;
    field.dispatchEvent(new Event('input', {bubbles: true}));
}
setField('N', '20');
setField('d', '2');
setField('seed', '20260814');
setField('m_rate', '0.1');
setField('mu_value', '0.01');
setField('locus_lengths', '200');
"""

# Opens Home and expands every group, several passes deep (an
# Experiment's Studies, then a Study's runs, which load lazily). Each
# pass waits for the previous one's Study fetches to land
# (`window.__fimGroupTogglesPending`, as `conftest.expand_every_group`
# does) and it ends once nothing is left collapsed. It used to
# pause a fixed 150 ms between six passes: a slower fetch then got its
# toggle clicked again, and its late re-render could drop the rows a
# test went on to read.
_OPEN_HOME_EXPANDED = """
window.fim.menu.openRun();
let passes = 0;
const expand = () => {
    if (
        window.__fimOpenRunRecentRunsLoaded !== true
        || window.__fimGroupTogglesPending > 0
    ) {
        setTimeout(expand, 20);
        return;
    }
    const collapsed = document.querySelectorAll(
        '.open-run-group-toggle[aria-expanded="false"]'
    );
    if (collapsed.length === 0) {
        window.__fimTestExpanded = true;
        return;
    }
    if (passes === 6) {
        // A tree-depth bound, not a time one: named in the failure.
        window.__fimTestExpanded = `${collapsed.length} toggles still collapsed`;
        return;
    }
    passes += 1;
    collapsed.forEach((toggle) => toggle.click());
    setTimeout(expand, 0);
};
expand();
"""


def _details_button_script(label: str) -> str:
    """Return JS clicking the "ⓘ" button on the group header naming `label`."""
    return (
        "(function(label) {"
        "for (const header of document.querySelectorAll('.open-run-group-header')) {"
        "  const toggle = header.querySelector('.open-run-group-toggle');"
        "  if (toggle && toggle.textContent.includes(label)) {"
        "    header.querySelector('.details-button').click();"
        "    return true;"
        "  }"
        "}"
        "return false; })"
        f"({label!r});"
    )


def test_home_shows_descriptions_and_saves_an_experiments_documentation(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """A group's tooltip shows its description; the dialog edits all three fields."""
    api = Api()
    experiment_id = api.create_experiment("Topology", "Ring vs island.")["experimentId"]
    api.create_study("Ring sweep", "How D responds to m.", experiment_id)

    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            _OPEN_HOME_EXPANDED
            + "const openDialog = () => {"
            + "  if (window.__fimTestExpanded !== true) {"
            + "    setTimeout(openDialog, 50); return;"
            + "  }"
            + _details_button_script("Topology")
            + "  const fill = () => {"
            + "    if (window.__fimDetailsDialogReady !== true) {"
            + "      setTimeout(fill, 50); return;"
            + "    }"
            + "    window.__fimOpenedWith = {"
            + "      name: document.getElementById('details-name').value,"
            + "      description:"
            + "        document.getElementById('details-description').value,"
            + "      documentationShown: !document.getElementById("
            + "        'details-documentation-field').hidden,"
            + "    };"
            + "    document.getElementById('details-description').value ="
            + "      'Ring, island, and stepping stone.';"
            + "    document.getElementById('details-documentation').value ="
            + "      'Question: does topology change D?';"
            + "    document.getElementById('details-form').requestSubmit();"
            + "  };"
            + "  fill();"
            + "};"
            + "openDialog();"
        ),
        read=(
            "({"
            "saved: window.__fimDetailsSaved === true, "
            "open: document.getElementById('modal-details').open, "
            "openedWith: window.__fimOpenedWith ?? null, "
            "tooltips: Array.from(document.querySelectorAll("
            "'.open-run-group-toggle')).map((toggle) => toggle.title)"
            "})"
        ),
        is_ready=lambda value: (
            value is not None
            and value.get("saved") is True
            and any(
                "Ring, island, and stepping stone." in tooltip
                for tooltip in value.get("tooltips", [])
            )
        ),
    )

    assert settled["openedWith"] == {
        "name": "Topology",
        "description": "Ring vs island.",
        "documentationShown": True,
    }
    assert settled["open"] is False
    assert any("How D responds to m." in tooltip for tooltip in settled["tooltips"])
    saved = groups.get_experiment(experiment_id)
    assert saved.description == "Ring, island, and stepping stone."
    assert saved.documentation == "Question: does topology change D?"


def test_a_named_run_is_titled_with_its_experiment_and_its_name(
    fast_scalar_run_settings: Path, window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The Run card names the Experiment, then the run; each carries its description.

    The run is started from Configure with a name and description typed
    in and a Study (inside an Experiment) chosen, the whole path a
    botanist takes.
    """
    api = Api()
    experiment_id = api.create_experiment("Topology", "Ring vs island.")["experimentId"]
    study_id = api.create_study("Ring sweep", "", experiment_id)["studyId"]

    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            _SET_TINY_FIELDS
            + "window.fim.refreshRunStudySelectOptions().then(() => {"
            + f"  window.fim.selectStudyForNewRun({study_id!r});"
            + "  document.getElementById('run-name-input').value = 'Baseline';"
            + "  document.getElementById('run-description-input').value ="
            + "    'Low-m control.';"
            + "  document.getElementById('run-button').click();"
            + "});"
        ),
        read=(
            "({"
            "state: window.fim.getRunViewState(), "
            f"title: {_TITLE}, "
            "tooltips: Array.from(document.querySelectorAll("
            "'#run-plot-title .details-link')).map((part) => part.title), "
            "nameCleared: document.getElementById('run-name-input').value === ''"
            "})"
        ),
        is_ready=lambda value: (
            value is not None
            and value.get("state") == "completed"
            and str(value.get("title", "")).startswith("Topology — Baseline (")
        ),
    )

    assert settled["title"].startswith("Topology — Baseline (run-")
    assert settled["nameCleared"] is True
    [experiment_tooltip, run_tooltip] = settled["tooltips"]
    assert "Ring vs island." in experiment_tooltip
    assert "Low-m control." in run_tooltip
    [run_directory] = groups.study_run_directories(groups.get_study(study_id))
    details = api.get_details("run", str(run_directory))["details"]
    assert (details["name"], details["description"]) == ("Baseline", "Low-m control.")


def test_the_initial_title_names_the_chosen_studys_experiment(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Before any run, the title follows Configure's study choice."""
    experiment_id = Api().create_experiment("Topology")["experimentId"]
    study_id = Api().create_study("Ring sweep", "", experiment_id)["studyId"]

    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "window.fim.refreshRunStudySelectOptions().then(() => {"
            "  const select = document.getElementById('run-study-select');"
            f"  select.value = {study_id!r};"
            "  select.dispatchEvent(new Event('change'));"
            "});"
        ),
        read=_TITLE,
        is_ready=lambda value: value == "Topology — initial conditions (p₀)",
    )

    assert settled == "Topology — initial conditions (p₀)"


def test_a_runs_details_dialog_names_it_without_documentation(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """A run's dialog has no documentation field, and a typed name shows in Home."""
    results = paths.results_directory()
    results.mkdir(parents=True, exist_ok=True)
    config = results / "run-a.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "N": 20,
                "ploidy": "haploid",
                "d": 2,
                "m": 0.1,
                "mu": 0.01,
                "seed": 3,
                "loci": [{"locus_id": 1, "length": 200}],
                "max_generations": 10,
                # One run, not the default batch of up to 200 replicates:
                # the batch took most of a minute alone, and over two
                # under a loaded parallel run, for a test about naming.
                "n_replicates": 1,
                "stop_batch_early": False,
            }
        ),
        encoding="utf-8",
    )
    assert cli.main(["run", str(config), "-o", str(results / "run-a"), "--quiet"]) == 0

    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            _OPEN_HOME_EXPANDED
            + "const openDialog = () => {"
            + "  const button = document.querySelector("
            + "    '.open-run-run-row .details-button');"
            + "  if (window.__fimTestExpanded !== true || button === null) {"
            + "    setTimeout(openDialog, 50); return;"
            + "  }"
            + "  button.click();"
            + "  const fill = () => {"
            + "    if (window.__fimDetailsDialogReady !== true) {"
            + "      setTimeout(fill, 50); return;"
            + "    }"
            + "    window.__fimDocumentationHidden = document.getElementById("
            + "      'details-documentation-field').hidden;"
            + "    document.getElementById('details-name').value = 'Baseline';"
            + "    document.getElementById('details-form').requestSubmit();"
            + "  };"
            + "  fill();"
            + "};"
            + "openDialog();"
        ),
        read=(
            "({"
            "saved: window.__fimDetailsSaved === true, "
            "documentationHidden: window.__fimDocumentationHidden ?? null, "
            "tree: document.getElementById('open-run-recent-runs-body').textContent"
            "})"
        ),
        is_ready=lambda value: (
            value is not None
            and value.get("saved") is True
            and "Baseline (run-a)" in str(value.get("tree"))
        ),
    )

    assert settled["documentationHidden"] is True
    assert "Baseline (run-a)" in settled["tree"]
