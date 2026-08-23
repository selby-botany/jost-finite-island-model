"use strict";

/* Screen 2: running, as a live scatter (design doc §0.5, §4.2) -- the
 * direct answer to "the botanists want to see scatterplots... live".
 *
 * `Api.start_run` pushes `fim.onRunProgress`/`onRunDone`/`onRunCancelled`/
 * `onRunError` (scalar) or `onBatchProgress`/`onBatchDone`/
 * `onBatchCancelled`/`onBatchError` (batch -- design §4.1's "n_replicates
 * *is* the toggle") from a background thread as a run proceeds (design
 * §3.4); this file's only job is reacting to those pushes -- it never
 * calls into the bridge itself except to fire "Cancel". One screen, one
 * set of DOM elements, either way (design §4.4's "the same screen, not
 * two different ones" principle, applied here too): the batch handlers
 * repurpose the same progress bar/label/canvas the scalar ones use,
 * showing replicate-reporting progress and a *pooled* scatter
 * (`fim.viz.scatter.pooled_scatter_panels`) instead of one run's own
 * generation and single-state scatter.
 *
 * `panels` (from `fim.viz.scatter.scatter_panels`/`pooled_scatter_
 * panels`, design §3.5) can hold more than one 2-D panel for
 * `3 <= d <= 6` (one per deme pair) -- every panel is drawn, as a
 * small-multiples grid when there is more than one
 * (visualization-and-config-editors design §3.1; supersedes Milestone
 * W3's own "draws only the first panel" scope line).
 */

const progressCanvas = document.getElementById("progress-canvas");
const progressBar = document.getElementById("progress-generation");
const progressLabel = document.getElementById("progress-generation-label");
const progressBanner = document.getElementById("progress-banner");
const cancelButton = document.getElementById("cancel-run-button");

function showProgressBanner(message) {
    if (!message) {
        progressBanner.hidden = true;
        progressBanner.textContent = "";
        return;
    }
    progressBanner.hidden = false;
    progressBanner.textContent = message;
}

function drawProgressPanels(panels) {
    if (!panels || panels.length === 0) {
        return;
    }
    if (panels.length === 1) {
        drawScatter(progressCanvas, panels[0].points, {
            xLabel: panels[0].x_label,
            yLabel: panels[0].y_label,
        });
    } else {
        drawScatterGrid(progressCanvas, panels);
    }
}

window.fim.onRunProgress = function onRunProgress(payload) {
    progressBar.max = payload.maxGenerations;
    progressBar.value = payload.generation;
    progressLabel.textContent = `${payload.generation} / ${payload.maxGenerations}`;
    drawProgressPanels(payload.panels);
};

window.fim.onRunDone = function onRunDone(payload) {
    cancelButton.disabled = true;
    window.fim.showResults(payload);
};

window.fim.onRunCancelled = function onRunCancelled(generation) {
    showProgressBanner(`Run cancelled at generation ${generation}; no artifacts were written.`);
    cancelButton.disabled = true;
};

window.fim.onRunError = function onRunError(message) {
    showProgressBanner(message);
    cancelButton.disabled = true;
};

window.fim.onBatchProgress = function onBatchProgress(payload) {
    progressBar.max = payload.replicateCount;
    progressBar.value = payload.reportedReplicateCount;
    progressLabel.textContent =
        `${payload.reportedReplicateCount} / ${payload.replicateCount} replicates reporting`;
    drawProgressPanels(payload.panels);
};

window.fim.onBatchDone = function onBatchDone(payload) {
    cancelButton.disabled = true;
    window.fim.showBatchResults(payload);
};

window.fim.onBatchCancelled = function onBatchCancelled(payload) {
    showProgressBanner(
        `Batch cancelled (replicate ${payload.replicateIndex}, ` +
            `generation ${payload.generation}); no artifacts were written.`
    );
    cancelButton.disabled = true;
};

window.fim.onBatchError = function onBatchError(message) {
    showProgressBanner(message);
    cancelButton.disabled = true;
};

cancelButton.addEventListener("click", () => {
    cancelButton.disabled = true;
    window.pywebview.api.cancel_run();
});
