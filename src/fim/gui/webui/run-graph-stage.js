/*
 * The Run card's graph stage: one graph at a time, chosen from a
 * selector, driven by a scrubber down its left edge, and openable in a
 * zoom frame.
 *
 * Replaces the card's earlier quadrant of four simultaneous panels,
 * which was reported as unworkable: each panel was too small to read,
 * the row wrapped unpredictably with window width, and the shared
 * scrubber sat below the fold under the whole stack.
 *
 * Division of labor with the `run-view-*.js` render functions: they
 * decide *whether* a graph has data (`setGraphAvailable`) and *how* to
 * repaint it (`registerGraphDraw`); this module alone decides which one
 * is on screen. That split matters because a hidden pane has no layout
 * box, so a canvas inside it cannot be sized or drawn correctly -- the
 * pane that becomes visible is therefore always repainted at the moment
 * it becomes visible, never earlier. The same holds one level up, when
 * the whole Run card was hidden at draw time (an opened run draws
 * before its own `showScreen`): the per-pane `ResizeObserver` wiring
 * below repaints again the moment the pane's box actually exists, so
 * "repaint at visibility" means the *layout* kind, not only the
 * attribute kind.
 */

// The panes this stage can show, in selector order. `key` is the name
// the render functions use; `titleId` names the pane's own `<h4>`,
// which stays in the DOM (visually hidden) as both the source of the
// option label and the pane's accessible name.
const RUN_GRAPHS = [
    { key: "scatter", paneId: "run-scatter-card", titleId: "run-scatter-title" },
    { key: "trajectory", paneId: "run-trajectory-frame", titleId: "run-trajectory-title" },
    {
        key: "alleleComposition",
        paneId: "allele-composition-card",
        titleId: "allele-composition-title",
    },
    {
        key: "frequencySpectrum",
        paneId: "frequency-spectrum-card",
        titleId: "frequency-spectrum-title",
    },
    { key: "ibd", paneId: "ibd-card", titleId: "ibd-title" },
];

const ZOOM_STEP = 0.25;
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 4;

const DEFAULT_GRAPH_KEY = "trajectory";

const graphAvailability = new Map();
const graphRedrawers = new Map();
// The graph the user asked for, which is not always the one on screen.
// Kept apart from `selectedGraphKey` because availability arrives in an
// order nobody controls: the scatter declares itself at `load`, long
// before a run has produced a trajectory. Collapsing the two would let
// that first fallback overwrite the preference permanently, so the
// trajectory would never appear even once it had data -- which is
// exactly the defect this pair replaced.
let preferredGraphKey = DEFAULT_GRAPH_KEY;
let selectedGraphKey = DEFAULT_GRAPH_KEY;
let zoomedGraphKey = null;
let zoomScale = 1;
// Which statistics table `openGraphZoom` moved, if any -- remembered
// rather than re-queried at close time, because by then it has been
// moved *out* of `#run-plot-row`, so `activeStatsTable()`'s own
// `#run-plot-row > ...` scope would no longer find it.
let zoomedStatsTable = null;
// Guards the repaint at the end of `syncRunGraphStage`, not the sync
// itself. A render function is allowed to call `setGraphAvailable` --
// `renderTrajectory` does, every time it decides it has something to
// draw -- and that call syncs the stage, which would repaint by calling
// the very render function already running. The DOM half of the sync
// still runs in that nested call, so nothing is left stale; only the
// recursive repaint is skipped.
let redrawingActiveGraph = false;

/**
 * Look up one entry of `RUN_GRAPHS` by key.
 *
 * @param {string} key
 * @returns {{key: string, paneId: string, titleId: string}|undefined}
 */
function runGraphEntry(key) {
    return RUN_GRAPHS.find((entry) => entry.key === key);
}

/**
 * Return the pane element for one graph key.
 *
 * @param {string} key
 * @returns {HTMLElement|null}
 */
function runGraphPane(key) {
    const entry = runGraphEntry(key);
    return entry ? document.getElementById(entry.paneId) : null;
}

/**
 * Return the keys of every graph that currently has data to show.
 *
 * @returns {Array<string>}
 */
function availableGraphKeys() {
    return RUN_GRAPHS.filter((entry) => graphAvailability.get(entry.key) === true).map(
        (entry) => entry.key
    );
}

/**
 * Repaint one graph, if it has registered how.
 *
 * Silently does nothing for a graph that has no redrawer yet (the
 * stage syncs on every availability change, including ones that happen
 * before the first draw).
 *
 * @param {string} key
 * @returns {void}
 */
function redrawGraph(key) {
    const redraw = graphRedrawers.get(key);
    if (typeof redraw === "function") {
        redraw();
    }
}

// A pane can also *gain* a layout box without the stage itself changing
// anything: `enterCompletedState` draws a freshly opened run while the
// whole Run card is still hidden (its own `showScreen("screen-run")`
// runs last), so every canvas in it has `clientWidth === 0` and draws
// at the default 300x150 buffer, which CSS then stretches to the real
// pane size -- reported live as a blurry trajectory with crowded axis
// labels, staying that way until the next explicit redraw (a scrubber
// move) repainted at the now-real size. The scatter pane alone escaped
// this by having its own `ResizeObserver` (`scatter.js`); the same
// treatment is now the stage's own, uniform across every pane: repaint
// whenever a pane's layout box actually changes size, which is exactly
// what "the pane became drawable" means -- it covers the screen-unhide
// case, the zoom frame, and window resizes alike. A zero-sized
// transition (the pane or its screen just got hidden) is skipped:
// there is nothing to size a redraw to, and the draw itself is what
// syncs the buffer (`canvas.width = canvas.clientWidth || ...`), so
// this settles after at most one repaint per real size change.
for (const entry of RUN_GRAPHS) {
    const pane = document.getElementById(entry.paneId);
    const canvas = pane ? pane.querySelector("canvas") : null;
    if (canvas === null) {
        continue;
    }
    new ResizeObserver(() => {
        if (canvas.clientWidth > 0) {
            redrawGraph(entry.key);
        }
    }).observe(canvas);
}

/**
 * Rebuild the selector, show exactly one pane, and repaint it.
 *
 * Idempotent: safe to call after any availability change, selection
 * change, or zoom transition.
 *
 * @returns {void}
 */
function syncRunGraphStage() {
    const select = document.getElementById("run-graph-select");
    const stage = document.getElementById("run-graph-stage");
    if (!select || !stage) {
        return;
    }
    const available = availableGraphKeys();

    // Keep the selection on a graph that still has data; fall back to
    // the first one that does, so a state change that retires the
    // showing graph does not leave the stage blank.
    //
    // Resolve the preference against what actually has data. The
    // preference itself is never written here, so a graph that is
    // merely not ready yet is shown as soon as it becomes ready.
    if (available.includes(preferredGraphKey)) {
        selectedGraphKey = preferredGraphKey;
    } else {
        selectedGraphKey = available.length > 0 ? available[0] : null;
    }

    // Rebuild the options only when the set or the labels changed --
    // replacing them unconditionally would drop an open dropdown.
    const labels = available.map((key) => {
        const entry = runGraphEntry(key);
        const title = document.getElementById(entry.titleId);
        return title ? title.textContent : key;
    });
    const signature = available.map((key, index) => `${key}\u0000${labels[index]}`).join("\u0001");
    if (select.dataset.optionSignature !== signature) {
        select.dataset.optionSignature = signature;

        // Reuse each existing `<option>` rather than replacing the lot.
        // The set really does change mid-run -- the scatter is the only
        // graph with data until the first progress message arrives, and
        // then three more appear at once -- so this rebuild runs while
        // the user may already have chosen something. Discarding and
        // recreating the selected option leaves the native popup button
        // able to paint a stale label beside a correct `value`, which is
        // what a reported "the pull-down disagrees with the graph"
        // screenshot looks like. Appending an existing child moves it,
        // so order still follows `available`.
        const existing = new Map(
            Array.from(select.options).map((option) => [option.value, option])
        );
        for (let index = 0; index < available.length; index += 1) {
            const key = available[index];
            const option = existing.get(key) ?? document.createElement("option");
            option.value = key;
            option.textContent = labels[index];
            select.appendChild(option);
            existing.delete(key);
        }
        for (const option of existing.values()) {
            option.remove();
        }
    }
    if (selectedGraphKey !== null) {
        select.value = selectedGraphKey;
    }
    select.disabled = available.length < 2;
    stage.hidden = available.length === 0;

    // One pane visible, and only while it is not away in the zoom
    // frame -- a pane that has been moved out of the stage must not be
    // un-hidden back into a stage it no longer occupies.
    for (const entry of RUN_GRAPHS) {
        const pane = document.getElementById(entry.paneId);
        if (!pane) {
            continue;
        }
        const shown = entry.key === selectedGraphKey && graphAvailability.get(entry.key) === true;
        pane.hidden = !shown;
    }
    if (selectedGraphKey !== null && !redrawingActiveGraph) {
        redrawingActiveGraph = true;
        try {
            redrawGraph(selectedGraphKey);
        } finally {
            redrawingActiveGraph = false;
        }
    }
}

/**
 * Declare whether one graph has data to show.
 *
 * @param {string} key one of `RUN_GRAPHS`' own keys
 * @param {boolean} available
 * @returns {void}
 */
window.fim.setGraphAvailable = function setGraphAvailable(key, available) {
    graphAvailability.set(key, Boolean(available));
    syncRunGraphStage();
};

/**
 * Declare how to repaint one graph.
 *
 * The callback is invoked whenever that graph becomes the visible one,
 * including after a zoom-frame resize, so it must be safe to call any
 * number of times with no new data.
 *
 * @param {string} key
 * @param {Function} redraw
 * @returns {void}
 */
window.fim.registerGraphDraw = function registerGraphDraw(key, redraw) {
    graphRedrawers.set(key, redraw);
};

/**
 * Repaint whichever graph is showing, wherever it is showing.
 *
 * @returns {void}
 */
window.fim.redrawActiveGraph = function redrawActiveGraph() {
    const key = zoomedGraphKey === null ? selectedGraphKey : zoomedGraphKey;
    if (key !== null) {
        redrawGraph(key);
    }
};

/**
 * Show one graph on the stage.
 *
 * @param {string} key
 * @returns {void}
 */
window.fim.showGraph = function showGraph(key) {
    preferredGraphKey = key;
    syncRunGraphStage();
};

/**
 * Return the key of the graph currently showing, for tests and callers
 * that need to ask rather than assume.
 *
 * @returns {string|null}
 */
window.fim.getActiveGraph = function getActiveGraph() {
    return zoomedGraphKey === null ? selectedGraphKey : zoomedGraphKey;
};

/**
 * Forget which graphs have data, so a stale pane from the previous
 * state cannot appear on the next one's stage.
 *
 * Deliberately leaves `preferredGraphKey` alone. Resetting it here made
 * a run's end yank the stage back to the trajectory out from under
 * whoever was reading a different graph at the time -- reported as
 * unexpected. The preference is the user's, so it is sticky for the
 * session: only the resolution against what currently has data is
 * thrown away and recomputed.
 *
 * @returns {void}
 */
window.fim.resetGraphStage = function resetGraphStage() {
    graphAvailability.clear();
    selectedGraphKey = null;
    syncRunGraphStage();
};

/**
 * Return whichever of the Run card's own statistics tables is
 * currently showing.
 *
 * `initial-stats`/`results-stats`/`batch-results-summary` sit as direct
 * children of `#run-plot-row`, and exactly one is ever visible at once
 * (the same invariant `#results-stats`'s own comment in `index.html`
 * establishes) -- whichever that is, is the one the zoom frame should
 * show beside the graph too, without this module needing to know which
 * run state produced it.
 *
 * @returns {HTMLElement|null}
 */
function activeStatsTable() {
    return document.querySelector("#run-plot-row > table.stats-table:not([hidden])");
}

/**
 * Apply the current zoom factor to the pane inside the zoom frame.
 *
 * Sets an explicit pixel size rather than a CSS transform so the canvas
 * inside is really re-sized and really re-drawn: zooming in gains
 * resolution instead of magnifying a bitmap.
 *
 * @returns {void}
 */
function applyGraphZoom() {
    const graphColumn = document.getElementById("graph-zoom-graph-column");
    const level = document.getElementById("graph-zoom-level");
    if (!graphColumn || zoomedGraphKey === null) {
        return;
    }
    const pane = runGraphPane(zoomedGraphKey);
    if (pane) {
        // Width: clear the inline override and let the grid column
        // (`.run-graph-body`'s own `minmax(0, 1fr)`, reused verbatim
        // for this wrapper) give the pane's natural, unconstrained
        // width -- already correctly excluding both the scrubber's own
        // column beside it and the stats table beside *that*, with no
        // arithmetic here that could drift out of sync with either
        // one's own CSS.
        pane.style.width = "";
        const baseWidth = Math.max(pane.clientWidth, 320);
        // Height: the grid does not stretch its cross axis
        // (`align-items: start`, the same choice the stage itself
        // makes), so the natural pane height is just its own content
        // height -- far short of "as tall as the frame allows", which
        // is the whole point of zooming. The column's own rendered
        // height (forced to fill the frame by `#graph-zoom-body`'s
        // `align-items: stretch`) is the one that means that.
        const baseHeight = Math.max(graphColumn.clientHeight, 240);
        pane.style.width = `${Math.round(baseWidth * zoomScale)}px`;
        pane.style.height = `${Math.round(baseHeight * zoomScale)}px`;
    }
    if (level) {
        level.textContent = `${Math.round(zoomScale * 100)}%`;
    }
    redrawGraph(zoomedGraphKey);
}

/**
 * Change the zoom factor by one step, clamped to the supported range.
 *
 * @param {number} delta
 * @returns {void}
 */
function stepGraphZoom(delta) {
    zoomScale = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, zoomScale + delta));
    applyGraphZoom();
}

/**
 * Move one real element into the zoom frame, leaving a marker behind so
 * `restoreZoomedElement` can put it back in exactly the same spot.
 *
 * Shared by the pane, the scrubber, and the stats table -- moving the
 * real, live elements rather than cloning them means their canvases,
 * their wired handlers, and their module-level state (`scrubber.js`'s
 * own `document.getElementById` references, captured once at load,
 * stay valid no matter where the element currently sits) all keep
 * working unchanged; the zoom frame is not a second, parallel copy of
 * any of this to maintain.
 *
 * @param {HTMLElement} element
 * @param {string} placeholderId
 * @returns {void}
 */
function moveIntoZoomFrame(element, placeholderId) {
    const placeholder = document.createElement("div");
    placeholder.id = placeholderId;
    placeholder.hidden = true;
    element.parentElement.insertBefore(placeholder, element);
}

/**
 * Move one element moved by `moveIntoZoomFrame` back to its placeholder
 * and remove the placeholder.
 *
 * @param {HTMLElement|null} element
 * @param {string} placeholderId
 * @returns {void}
 */
function restoreFromZoomFrame(element, placeholderId) {
    const placeholder = document.getElementById(placeholderId);
    if (element && placeholder && placeholder.parentElement) {
        placeholder.parentElement.insertBefore(element, placeholder);
        placeholder.remove();
    }
}

/**
 * Open the showing graph in the zoom frame.
 *
 * Moves the real pane, the real scrubber, and whichever real statistics
 * table is currently showing rather than cloning any of them, so full
 * parity with the Run card's own graph-plus-scrubber-plus-statistics
 * experience costs no second implementation to keep in sync -- see
 * `index.html`'s own comment on `#graph-zoom-modal` for the fuller
 * reasoning (2026-09-23 follow-up: the frame used to show the bare
 * graph alone). A placeholder for each marks where it goes back.
 *
 * The scrubber and pane land in a wrapper that reuses `.run-graph-
 * body`'s own grid (`auto minmax(0, 1fr)`) verbatim, so a hidden
 * scrubber (a graph other than the trajectory, or a single-generation
 * run) collapses to nothing exactly as it does on the stage, with no
 * separate zoom-frame layout rule needed for that case.
 *
 * @returns {void}
 */
window.fim.openGraphZoom = function openGraphZoom() {
    const modal = document.getElementById("graph-zoom-modal");
    const body = document.getElementById("graph-zoom-body");
    if (!modal || !body || selectedGraphKey === null || zoomedGraphKey !== null) {
        return;
    }
    const entry = runGraphEntry(selectedGraphKey);
    const pane = runGraphPane(selectedGraphKey);
    if (!entry || !pane) {
        return;
    }
    const title = document.getElementById(entry.titleId);
    const heading = document.getElementById("graph-zoom-title");
    if (heading) {
        heading.textContent = title ? title.textContent : "";
    }

    const scrubberControls = document.getElementById("scrubber-controls");
    const statsTable = activeStatsTable();

    if (scrubberControls) {
        moveIntoZoomFrame(scrubberControls, "graph-zoom-scrubber-placeholder");
    }
    moveIntoZoomFrame(pane, "graph-zoom-placeholder");
    if (statsTable) {
        moveIntoZoomFrame(statsTable, "graph-zoom-stats-placeholder");
    }
    zoomedStatsTable = statsTable;

    zoomedGraphKey = selectedGraphKey;
    zoomScale = 1;
    pane.classList.add("graph-zoom-pane");

    const graphColumn = document.createElement("div");
    graphColumn.id = "graph-zoom-graph-column";
    // `run-graph-body`: the stage's own scrubber/pane grid, reused
    // verbatim. `graph-zoom-graph-column`: this wrapper's own sizing
    // beside the stats table (`app.css`).
    graphColumn.className = "run-graph-body graph-zoom-graph-column";
    if (scrubberControls) {
        graphColumn.appendChild(scrubberControls);
    }
    graphColumn.appendChild(pane);

    body.replaceChildren(graphColumn, ...(statsTable ? [statsTable] : []));
    modal.showModal();
    applyGraphZoom();
};

/**
 * Return the zoomed pane, scrubber, and stats table to the stage and
 * repaint the pane there.
 *
 * Wired to the frame's own `close` event, so Escape, the backdrop, and
 * the Close button all take the same path.
 *
 * @returns {void}
 */
function restoreZoomedPane() {
    if (zoomedGraphKey === null) {
        return;
    }
    const pane = runGraphPane(zoomedGraphKey);
    restoreFromZoomFrame(
        document.getElementById("scrubber-controls"),
        "graph-zoom-scrubber-placeholder"
    );
    if (pane) {
        pane.classList.remove("graph-zoom-pane");
        pane.style.width = "";
        pane.style.height = "";
    }
    restoreFromZoomFrame(pane, "graph-zoom-placeholder");
    restoreFromZoomFrame(zoomedStatsTable, "graph-zoom-stats-placeholder");
    const graphColumn = document.getElementById("graph-zoom-graph-column");
    if (graphColumn) {
        graphColumn.remove();
    }
    zoomedGraphKey = null;
    zoomedStatsTable = null;
    zoomScale = 1;
    syncRunGraphStage();
}

window.addEventListener("load", () => {
    const select = document.getElementById("run-graph-select");
    const stage = document.getElementById("run-graph-stage");
    const modal = document.getElementById("graph-zoom-modal");
    if (select) {
        select.addEventListener("change", () => {
            window.fim.showGraph(select.value);
        });
    }
    if (stage) {
        // Only the graph itself opens the frame -- double-clicking the
        // selector or dragging the scrubber must not.
        stage.addEventListener("dblclick", (event) => {
            if (event.target.closest(".run-graph-toolbar, .scrubber-controls")) {
                return;
            }
            window.fim.openGraphZoom();
        });
    }
    if (modal) {
        modal.addEventListener("close", restoreZoomedPane);
        const close = document.getElementById("graph-zoom-close");
        if (close) {
            close.addEventListener("click", () => modal.close());
        }
        const zoomIn = document.getElementById("graph-zoom-in");
        if (zoomIn) {
            zoomIn.addEventListener("click", () => stepGraphZoom(ZOOM_STEP));
        }
        const zoomOut = document.getElementById("graph-zoom-out");
        if (zoomOut) {
            zoomOut.addEventListener("click", () => stepGraphZoom(-ZOOM_STEP));
        }
        const fit = document.getElementById("graph-zoom-fit");
        if (fit) {
            fit.addEventListener("click", () => {
                zoomScale = 1;
                applyGraphZoom();
            });
        }
    }
    syncRunGraphStage();
});
