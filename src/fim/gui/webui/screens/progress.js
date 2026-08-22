"use strict";

/* Screen 2: running, as a live scatter (design doc §0.5, §4.2) -- the
 * direct answer to "the botanists want to see scatterplots... live".
 *
 * `Api.start_run` pushes `fim.onRunProgress`/`onRunDone`/`onRunCancelled`/
 * `onRunError` from a background thread as a scalar run proceeds (design
 * §3.4); this file's only job is reacting to those pushes -- it never
 * calls into the bridge itself except to fire "Cancel".
 *
 * `panels` (from `fim.viz.scatter.scatter_panels`, design §3.5) can hold
 * more than one 2-D panel for `3 <= d <= 6` (one per deme pair) -- this
 * screen draws only the first panel onto its one `<canvas>` for now,
 * a deliberate scope line for Milestone W3 (the common case, `d == 2`
 * or `d > 6`, both already draw everything scatter_panels returns) --
 * a multi-panel grid is a follow-up, not silently dropped data (every
 * panel is still present in `panels`, just not all rendered yet).
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

function drawFirstPanel(panels) {
    if (!panels || panels.length === 0) {
        return;
    }
    const panel = panels[0];
    drawScatter(progressCanvas, panel.points, {
        xLabel: panel.x_label,
        yLabel: panel.y_label,
    });
}

window.fim.onRunProgress = function onRunProgress(payload) {
    progressBar.max = payload.maxGenerations;
    progressBar.value = payload.generation;
    progressLabel.textContent = `${payload.generation} / ${payload.maxGenerations}`;
    drawFirstPanel(payload.panels);
};

window.fim.onRunDone = function onRunDone(payload) {
    drawFirstPanel(payload.panels);
    showProgressBanner(
        payload.report.converged
            ? `Converged at generation ${payload.report.generation}.`
            : `Reached the generation cap (${payload.report.generation}), not converged.`
    );
    cancelButton.disabled = true;
    // Milestone W4 replaces this with the real results screen
    // (design §4.3); Screen 2 stays showing the run's own final scatter
    // and summary text in the meantime rather than a dead end.
};

window.fim.onRunCancelled = function onRunCancelled(generation) {
    showProgressBanner(`Run cancelled at generation ${generation}; no artifacts were written.`);
    cancelButton.disabled = true;
};

window.fim.onRunError = function onRunError(message) {
    showProgressBanner(message);
    cancelButton.disabled = true;
};

cancelButton.addEventListener("click", () => {
    cancelButton.disabled = true;
    window.pywebview.api.cancel_run();
});
