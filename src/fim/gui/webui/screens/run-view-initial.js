"use strict";

/* The unified run view's `initial` state (unified-run-view design
 * §3.2.1, §3.2.2, §8 Phase E/F) -- renders the p_0 scatter, axis
 * labels, six statistics, and a generation-0 progress bar as soon as
 * the form has valid values (Phase F: `Api.get_initial_state_panels`),
 * so the canvas is never blank at startup or after a reset.
 */

// Declared here, not in `run-view-running.js`/`run-view-completed.js`
// (both of which need most of these too): every classic script on this
// page shares one global scope, so a `const` declared in one file is
// visible by bare name in every other -- declaring it twice would be a
// `SyntaxError`, not a shadow (`test_webui_global_scope.py`'s own
// module docstring). `run-view-initial.js` loads first among the three
// state files, so it is the natural, single owner.
const runCanvas = document.getElementById("run-canvas");
const runPlotTitle = document.getElementById("run-plot-title");
const runProgress = document.getElementById("run-progress");
const runCompleted = document.getElementById("run-completed");
const batchResultsTable = document.getElementById("batch-results-table");
const scrubberControls = document.getElementById("scrubber-controls");
const runDemePairSelector = document.getElementById("run-deme-pair-selector");
const runXDeme = document.getElementById("run-x-deme");
const runYDeme = document.getElementById("run-y-deme");
const runDemePairSelfNote = document.getElementById("run-deme-pair-self-note");

// The progress bar / label elements (declared in run-view-running.js
// but needed here too -- run-view-initial.js loads before run-view-
// running.js, so these declarations must live here). Run-view-running.js
// reads these by bare name without re-declaring them.
const progressBar = document.getElementById("progress-generation");
const progressLabel = document.getElementById("progress-generation-label");

// The p_0 statistics panel: a `<div>` inside `run-completed` whose
// six `[data-stat]` children are the same meter slots the completed
// state uses, reused here to show gen-0 statistics without duplicating
// the meter markup.
const initialStats = document.getElementById("initial-stats");

const alleleCompositionCard = document.getElementById("allele-composition-card");
const alleleCompositionTitle = document.getElementById("allele-composition-title");
const alleleCompositionCanvas = document.getElementById("allele-composition-canvas");
const alleleCompositionLegend = document.getElementById("allele-composition-legend");
const alleleCompositionNote = document.getElementById("allele-composition-note");
const frequencySpectrumCard = document.getElementById("frequency-spectrum-card");
const frequencySpectrumTitle = document.getElementById("frequency-spectrum-title");
const frequencySpectrumCanvas = document.getElementById("frequency-spectrum-canvas");
const frequencySpectrumNote = document.getElementById("frequency-spectrum-note");
const ibdCard = document.getElementById("ibd-card");
const ibdTitle = document.getElementById("ibd-title");
const ibdCanvas = document.getElementById("ibd-canvas");
const ibdNote = document.getElementById("ibd-note");

function clearRunCanvas() {
    _currentPanel = null;
    runCanvas.getContext("2d").clearRect(0, 0, runCanvas.width, runCanvas.height);
}

/**
 * Draw the per-deme stacked allele-composition barplot
 * (`fim.gui.literature_visuals.allele_composition_payload`'s own
 * docstring has the full "why a dense 1-based legend, not the raw
 * internal allele id" account -- `payload.alleles`/`payload.demes`
 * already carry that remapped, display-ready shape, so this function
 * only draws what it is given.
 */
function drawAlleleComposition(canvas, payload) {
    const context = canvas.getContext("2d");
    canvas.width = canvas.clientWidth || canvas.width;
    canvas.height = canvas.clientHeight || canvas.height;
    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);
    if (!payload || !payload.demes || payload.demes.length === 0) {
        return;
    }
    const colors = Object.fromEntries(
        (payload.alleles || []).map((allele) => [allele.key, allele.color])
    );
    const plotLeft = 36;
    const plotRight = width - 12;
    const plotTop = 12;
    const plotBottom = height - 28;
    const barGap = 4;
    const barWidth =
        (plotRight - plotLeft - barGap * (payload.demes.length - 1)) /
        payload.demes.length;
    const style = getComputedStyle(document.documentElement);
    const borderColor = style.getPropertyValue("--fim-border").trim();
    const mutedColor = style.getPropertyValue("--fim-muted").trim();

    context.strokeStyle = borderColor;
    context.strokeRect(plotLeft, plotTop, plotRight - plotLeft, plotBottom - plotTop);
    context.font = "10px sans-serif";
    context.textAlign = "center";
    context.textBaseline = "top";
    for (const [index, deme] of payload.demes.entries()) {
        const left = plotLeft + index * (barWidth + barGap);
        let top = plotBottom;
        for (const segment of deme.segments) {
            const segmentHeight = segment.value * (plotBottom - plotTop);
            top -= segmentHeight;
            context.fillStyle = colors[segment.key] || colors.other || "#999999";
            context.fillRect(left, top, barWidth, segmentHeight);
        }
        context.fillStyle = mutedColor;
        context.fillText(String(deme.deme), left + barWidth / 2, plotBottom + 5);
    }
}

function drawFrequencySpectrum(canvas, payload) {
    const context = canvas.getContext("2d");
    canvas.width = canvas.clientWidth || canvas.width;
    canvas.height = canvas.clientHeight || canvas.height;
    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);
    const bins = payload ? payload.bins || [] : [];
    if (bins.length === 0) {
        return;
    }
    const plotLeft = 36;
    const plotRight = width - 12;
    const plotTop = 12;
    const plotBottom = height - 28;
    const overlay = payload.betaOverlay || [];
    const maxCount = Math.max(
        1,
        ...bins.map((bin) => bin.count),
        ...overlay.map((point) => point.expectedCount)
    );
    const style = getComputedStyle(document.documentElement);
    const borderColor = style.getPropertyValue("--fim-border").trim();
    const accentColor = style.getPropertyValue("--fim-accent").trim();
    const mutedColor = style.getPropertyValue("--fim-muted").trim();

    context.strokeStyle = borderColor;
    context.strokeRect(plotLeft, plotTop, plotRight - plotLeft, plotBottom - plotTop);
    context.fillStyle = accentColor;
    bins.forEach((bin, index) => {
        const left = plotLeft + (index / bins.length) * (plotRight - plotLeft);
        const right = plotLeft + ((index + 1) / bins.length) * (plotRight - plotLeft);
        const barHeight = (bin.count / maxCount) * (plotBottom - plotTop);
        context.fillRect(left + 1, plotBottom - barHeight, right - left - 2, barHeight);
    });
    if (overlay.length > 0) {
        context.strokeStyle = "#d55e00";
        context.lineWidth = 2;
        context.beginPath();
        overlay.forEach((point, index) => {
            const x = plotLeft + point.x * (plotRight - plotLeft);
            const y =
                plotBottom -
                (point.expectedCount / maxCount) * (plotBottom - plotTop);
            if (index === 0) {
                context.moveTo(x, y);
            } else {
                context.lineTo(x, y);
            }
        });
        context.stroke();
    }
    context.fillStyle = mutedColor;
    context.font = "10px sans-serif";
    context.textAlign = "center";
    context.fillText("0", plotLeft, plotBottom + 5);
    context.fillText("1", plotRight, plotBottom + 5);
}

function drawIbdCurve(canvas, payload) {
    const context = canvas.getContext("2d");
    canvas.width = canvas.clientWidth || canvas.width;
    canvas.height = canvas.clientHeight || canvas.height;
    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);
    const points = payload ? payload.points || [] : [];
    if (points.length === 0) {
        return;
    }
    const plotLeft = 42;
    const plotRight = width - 12;
    const plotTop = 12;
    const plotBottom = height - 28;
    const maxDistance = Math.max(...points.map((point) => point.distance));
    const maxIdentity = Math.max(1, ...points.map((point) => point.meanIdentity));
    const style = getComputedStyle(document.documentElement);
    const borderColor = style.getPropertyValue("--fim-border").trim();
    const accentColor = style.getPropertyValue("--fim-accent").trim();
    const mutedColor = style.getPropertyValue("--fim-muted").trim();

    function xToPixel(distance) {
        return plotLeft + (distance / maxDistance) * (plotRight - plotLeft);
    }
    function yToPixel(value) {
        return plotBottom - (value / maxIdentity) * (plotBottom - plotTop);
    }

    context.strokeStyle = borderColor;
    context.strokeRect(plotLeft, plotTop, plotRight - plotLeft, plotBottom - plotTop);
    if (payload.fit && payload.fit.points) {
        context.strokeStyle = "#d55e00";
        context.setLineDash([4, 4]);
        context.beginPath();
        payload.fit.points.forEach((point, index) => {
            const x = xToPixel(point.distance);
            const y = yToPixel(point.meanIdentity);
            if (index === 0) {
                context.moveTo(x, y);
            } else {
                context.lineTo(x, y);
            }
        });
        context.stroke();
        context.setLineDash([]);
    }
    context.fillStyle = accentColor;
    for (const point of points) {
        context.beginPath();
        context.arc(
            xToPixel(point.distance),
            yToPixel(point.meanIdentity),
            4,
            0,
            2 * Math.PI
        );
        context.fill();
    }
    context.fillStyle = mutedColor;
    context.font = "10px sans-serif";
    context.textAlign = "center";
    context.fillText("migration-graph distance", (plotLeft + plotRight) / 2, height - 12);
}

/**
 * Render the three literature-derived supplemental graphs.
 *
 * @param {{alleleComposition: object, frequencySpectrum: object, isolationByDistance: object|null}|undefined} visuals
 */
function renderSupplementalPanels(visuals) {
    alleleCompositionCard.hidden = !visuals || !visuals.alleleComposition;
    frequencySpectrumCard.hidden = !visuals || !visuals.frequencySpectrum;
    ibdCard.hidden = !visuals || !visuals.isolationByDistance;
    if (!visuals) {
        return;
    }
    if (visuals.alleleComposition) {
        alleleCompositionTitle.textContent = visuals.alleleComposition.title;
        alleleCompositionNote.textContent = visuals.alleleComposition.note;
        alleleCompositionLegend.replaceChildren();
        for (const allele of visuals.alleleComposition.alleles) {
            const item = document.createElement("span");
            item.className = "run-supplemental-legend-item";
            const swatch = document.createElement("span");
            swatch.className = "swatch";
            swatch.style.background = allele.color;
            item.appendChild(swatch);
            item.append(allele.label);
            alleleCompositionLegend.appendChild(item);
        }
        drawAlleleComposition(alleleCompositionCanvas, visuals.alleleComposition);
    }
    if (visuals.frequencySpectrum) {
        frequencySpectrumTitle.textContent = visuals.frequencySpectrum.title;
        frequencySpectrumNote.textContent = visuals.frequencySpectrum.note;
        drawFrequencySpectrum(frequencySpectrumCanvas, visuals.frequencySpectrum);
    }
    if (visuals.isolationByDistance) {
        ibdTitle.textContent = visuals.isolationByDistance.title;
        ibdNote.textContent = visuals.isolationByDistance.note;
        drawIbdCurve(ibdCanvas, visuals.isolationByDistance);
    }
}

/**
 * Render the p_0 scatter and decorations (Phase F).
 *
 * Fetches `Api.get_initial_state_panels` with the current form values,
 * then draws panels, statistics, and the gen-0 progress bar. Silently
 * leaves everything blank if the form does not yet have valid values.
 */
async function renderInitialPreview() {
    const values = collectFormValues();
    const result = await window.pywebview.api.get_initial_state_panels(values);
    // State may have changed while the bridge call was in flight --
    // only draw if still in `initial`.
    if (window.fim.getRunViewState() !== "initial") {
        return;
    }
    if (!result.ok) {
        return;
    }
    // Progress bar at generation 0.
    runProgress.hidden = false;
    progressBar.max = result.maxGenerations;
    progressBar.value = 0;
    progressLabel.textContent = `0 / ${result.maxGenerations}`;

    // Statistics for p_0.
    if (initialStats) {
        initialStats.hidden = false;
        for (const name of ["D", "G_ST", "E_ST", "K_ST", "H_S", "H_T", "H_ST"]) {
            const value = result.statistics[name];
            const slot = initialStats.querySelector(`[data-stat="${name}"]`);
            if (slot) {
                applyStatRow(slot, buildPointMeter(name, value));
            }
        }
    }

    // Scatter panel.
    const panels = result.panels;
    if (panels && panels.length > 0) {
        drawScatter(runCanvas, panels[0]);
    }

    // Initial-state axis selectors.
    runDemePairSelector.hidden = !panels || result.demeCount < 2;
    if (panels && panels.length > 0 && result.demeCount >= 2) {
        window.fim.wireDemePairSelector({
            xSelect: runXDeme,
            ySelect: runYDeme,
            container: runDemePairSelector,
            selfComparisonNote: runDemePairSelfNote,
            demeCount: result.demeCount,
            onShowPair: async (x, y) => {
                const pairResult =
                    await window.pywebview.api.get_initial_state_deme_pair_panel(
                        values,
                        x,
                        y
                    );
                if (pairResult.ok) {
                    drawScatter(runCanvas, pairResult.panel);
                }
            },
        });
    }
}

window.fim.renderInitialPreview = renderInitialPreview;

/**
 * Enter `initial`: hide every other state's own content, disable the
 * controls only `running`/`completed` make sense for, and optionally render
 * the p_0 preview (Phase F: scatter, statistics, gen-0 progress bar).
 *
 * @param {boolean} renderPreview
 */
function enterInitialState(renderPreview = true) {
    window.fim.setRunViewState("initial");
    window.fim.setCompletedOutputDirectory(null);
    runCompleted.hidden = true;
    batchResultsTable.hidden = true;
    scrubberControls.hidden = true;
    runDemePairSelector.hidden = true;
    runProgress.hidden = true;
    cancelButton.disabled = true;
    openFolderButton.hidden = true;
    if (runPlotTitle) {
        runPlotTitle.textContent = "FIM simulation — initial conditions (p₀)";
    }
    // `resultsBackButton`/`resultsStats`/`batchResultsTableEl` are all
    // declared in run-view-completed.js (loads after this file) but
    // always present by the time any user event or `whenApiReady`
    // callback fires. `resultsStats`/`batchResultsTableEl` now sit
    // beside the canvas as their own `.stats-table`s rather than inside
    // `runCompleted` (moved there so the active table renders next to
    // the plot instead of in a row underneath it), so hiding
    // `runCompleted` above no longer hides them for free -- each needs
    // its own `hidden` here.
    if (typeof resultsBackButton !== "undefined") {
        resultsBackButton.hidden = true;
    }
    if (typeof resultsHistoryBackButton !== "undefined") {
        resultsHistoryBackButton.hidden = true;
    }
    if (typeof resultsHistoryForwardButton !== "undefined") {
        resultsHistoryForwardButton.hidden = true;
    }
    if (typeof resultsStats !== "undefined") {
        resultsStats.hidden = true;
    }
    if (typeof batchResultsTableEl !== "undefined") {
        batchResultsTableEl.hidden = true;
    }
    if (initialStats) {
        initialStats.hidden = true;
    }
    alleleCompositionCard.hidden = true;
    frequencySpectrumCard.hidden = true;
    ibdCard.hidden = true;
    // Hides a previous run's own live/completed trajectory panel
    // (`run-view-completed.js`'s own `renderTrajectory`, guarded the
    // same "declared in a file that loads after this one" way as
    // `resultsBackButton` above) -- entering `initial` should never
    // leave stale trajectory data on screen, the same reason
    // `clearRunCanvas()` below clears the scatter canvas too.
    if (typeof renderTrajectory === "function") {
        renderTrajectory(undefined, undefined);
    }
    window.fim.resetScrubber();
    clearRunCanvas();
    // Render p_0 preview asynchronously -- do not await here since
    // `enterInitialState` is called synchronously from many sites.
    if (renderPreview) {
        renderInitialPreview();
    }
}

window.fim.enterInitialState = enterInitialState;

/**
 * `fim.menu.newConfiguration` (native File menu) -- a genuine, explicit
 * reset to the true `STARTER_CONFIG` values, unlike `configureTab`'s
 * own "never resets a field" contract. No longer the same as a first
 * app launch (`initializeRunView`, below, prefers a saved form there
 * instead) -- an explicit "New configuration" click always means
 * starter values regardless of what got saved, which is exactly the
 * distinction P1 item 4's own design doc draws between the two.
 * Cycles `window.__fimRunViewReady` false-then-true around the reset so
 * a test (or anything else) waiting for "the form is in a fully settled
 * state" has one reliable signal, instead of racing a DOM value change
 * alone -- `resetInputForm` still has two more real bridge calls in
 * flight (`get_default_max_workers`, `revalidate`'s own `validate_form`)
 * after `field-N` itself already shows the new value.
 */
window.fim.menu.newConfiguration = async function newConfiguration() {
    window.fim.showScreen("screen-run");
    window.__fimRunViewReady = false;
    // The reset below supplies the values for the preview. Starting one
    // before that reset would leave an untracked bridge call in flight.
    enterInitialState(false);
    await resetInputForm();
    // Render after the form genuinely holds starter values.
    await renderInitialPreview();
    // An explicit reset to `STARTER_CONFIG` values is not "the loaded
    // preset, plus edits" any more (`screens/presets.js`'s own
    // `lastLoadedPresetTitle` docstring) -- "Duplicate current
    // configuration" has nothing left to fork until a preset is loaded
    // again.
    window.fim.clearLastLoadedPreset();
    window.__fimRunViewReady = true;
};

async function initializeRunView() {
    // Avoid starting a preview against the blank form before its initial
    // load has completed. `loadInitialForm` (not `resetInputForm`): a
    // fresh launch prefers the last successfully submitted form over
    // the true starter values -- `fim.menu.newConfiguration` above is
    // the only caller that still wants an unconditional reset.
    enterInitialState(false);
    // Before the form is populated, not after: this only rewrites two
    // `engine_backend` option *labels* (never a value), but doing it
    // first means no frame ever shows a label this install cannot
    // honor.
    await window.fim.applyEngineBackendAvailability();
    await loadInitialForm();
    await renderInitialPreview();
    // Home, not Run, is this app's landing destination on a fresh
    // launch (botanist GUI design doc §9: "Home replaces the current
    // 'Open a run' screen with a richer landing destination"). Routed
    // through the exact same entry point the rail's own Home button
    // uses (`open-run.js`'s `showOpenRunScreen`) rather than leaving
    // `index.html`'s static markup (already `screen-open-run` by
    // default -- see that file's own `.screen` sections) to stand on
    // its own: a real launch should show current recent-runs/example
    // data immediately, the same as clicking Home ever after, not a
    // static empty shell that only populates once something else
    // navigates there. Awaited, unlike every click-driven caller of
    // `showOpenRunScreen` -- this is the one caller that also needs to
    // know the real bridge calls it starts (`list_home_runs`, `list_
    // presets`) have actually settled before flipping `window.
    // __fimRunViewReady` below, the same "no un-awaited bridge call
    // still in flight when a test's own teardown destroys the window"
    // discipline `test/gui/conftest.py`'s own module docstring records
    // at length for this exact function's other call site.
    await window.fim.showOpenRunScreen();
    // Awaited before the ready flag flips, like the two calls above --
    // not fire-and-forget: an un-awaited bridge call still in flight
    // when a test's own teardown destroys the window is exactly the
    // stranded-non-daemon-thread hazard `test/gui/conftest.py`'s own
    // module docstring records at length (`screens/welcome.js`'s own
    // `maybeShowWelcome` awaits `Api.get_welcome_dismissed` internally).
    // The panel itself, once shown, still floats over an already-
    // correct initial view rather than blocking it from ever rendering
    // -- this only delays the *ready flag*, not the preview above it.
    await window.fim.maybeShowWelcome();
    window.__fimRunViewReady = true;
}

whenApiReady(initializeRunView);
