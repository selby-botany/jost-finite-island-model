/*
 * The Run card's graph stage: the graphs the user chose to show together
 * (the scatter plot and the trajectories by default), in a grid whose
 * column count comes from Settings, driven by one shared scrubber down
 * its left edge, and openable in a zoom frame.
 *
 * Several graphs at once is deliberate and user-controlled (design
 * `20260923-claude-sonnet-5-multi-graph-run-card-and-scatter-encoding-
 * design.md`): the scatter and the trajectories are two views of the same
 * generation, and seeing them side by side is how people learn to connect
 * them. The single-graph stage this replaced is still one setting away
 * (choose one graph); `showGraph` keeps meaning "only this one".
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
    { key: "scatter", paneId: "run-scatter-card", titleId: "run-scatter-title", weight: 1 },
    {
        key: "trajectory",
        paneId: "run-trajectory-frame",
        titleId: "run-trajectory-title",
        weight: 1.5,
    },
    {
        key: "alleleComposition",
        paneId: "allele-composition-card",
        titleId: "allele-composition-title",
        weight: 1.3,
    },
    {
        key: "frequencySpectrum",
        paneId: "frequency-spectrum-card",
        titleId: "frequency-spectrum-title",
        weight: 1.3,
    },
    // Never offered: this card intentionally shows four graphs, not five
    // (app.css). Kept so the payload stays wired for Python callers.
    { key: "ibd", paneId: "ibd-card", titleId: "ibd-title", weight: 1.3, offered: false },
];

const ZOOM_STEP = 0.25;
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 4;

// What a fresh install shows together, matching `fim.gui.preferences.
// DEFAULT_RUN_GRAPHS`; replaced by the saved choice once the bridge is up.
const DEFAULT_GRAPH_KEYS = ["scatter", "trajectory"];
const DEFAULT_GRAPH_COLUMNS = 2;
// A pane narrower than this is unreadable, so the effective column count
// drops until every column can have at least this much (rows then follow).
const MIN_PANE_WIDTH_PX = 220;
const GRAPH_GAP_PX = 16;

const graphAvailability = new Map();
const graphRedrawers = new Map();
// The graphs the user asked for, which are not always the ones on screen.
// Kept apart from `visibleGraphKeys` because availability arrives in an
// order nobody controls: the scatter declares itself at `load`, long
// before a run has produced a trajectory. Collapsing the two would let
// that first fallback overwrite the preference permanently, so the
// trajectory would never appear even once it had data -- which is
// exactly the defect this pair replaced. The preference is only ever
// changed by the user (or `showGraph`); what is *shown* is recomputed
// against what has data on every sync.
let preferredGraphKeys = [...DEFAULT_GRAPH_KEYS];
let visibleGraphKeys = [];
// The graph "the" active one for callers that ask for a single graph
// (`getActiveGraph`): the pane last double-clicked, else the first shown.
let focusedGraphKey = null;
let graphColumns = DEFAULT_GRAPH_COLUMNS;
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
 * Return whether a graph is offered in the menu at all.
 *
 * @param {{offered?: boolean}} entry
 * @returns {boolean}
 */
function isOffered(entry) {
    return entry.offered !== false;
}

/**
 * Rebuild the Graphs menu from what is available and chosen.
 *
 * Every offered graph is listed; one with no data yet is disabled rather
 * than hidden, so the menu does not reshuffle as a run produces data.
 * The last chosen graph that has data cannot be unchecked -- the stage
 * is never left blank by a click.
 *
 * @param {Array<string>} available
 * @returns {void}
 */
function syncGraphMenu(available) {
    const list = document.getElementById("run-graph-menu-list");
    const summary = document.getElementById("run-graph-menu-summary");
    if (!list) {
        return;
    }
    const chosen = new Set(visibleGraphKeys);
    const signature = RUN_GRAPHS.filter(isOffered)
        .map((entry) => {
            const title = document.getElementById(entry.titleId);
            const label = title ? title.textContent : entry.key;
            const usable = available.includes(entry.key);
            const only = chosen.size === 1 && chosen.has(entry.key);
            return `${entry.key}\u0000${label}\u0000${usable}\u0000${chosen.has(entry.key)}\u0000${only}`;
        })
        .join("\u0001");
    if (summary) {
        summary.textContent = `Graphs (${visibleGraphKeys.length})`;
    }
    if (list.dataset.signature === signature) {
        return;
    }
    list.dataset.signature = signature;
    list.replaceChildren();
    for (const entry of RUN_GRAPHS.filter(isOffered)) {
        const title = document.getElementById(entry.titleId);
        const usable = available.includes(entry.key);
        const label = document.createElement("label");
        label.className = "graph-menu-item";
        const box = document.createElement("input");
        box.type = "checkbox";
        box.value = entry.key;
        box.checked = chosen.has(entry.key);
        box.disabled = !usable || (chosen.size === 1 && chosen.has(entry.key));
        const text = document.createElement("span");
        text.textContent = title ? title.textContent : entry.key;
        if (!usable) {
            label.title = "No data for this graph yet";
        }
        label.append(box, text);
        list.appendChild(label);
    }
}

/**
 * Size the grid: how many columns, and how wide each is.
 *
 * The user's column setting is an upper bound. It is also capped by how
 * many graphs are shown (three columns for two graphs would leave an
 * empty cell) and by the width available, so that no pane is narrower
 * than `MIN_PANE_WIDTH_PX` -- rows then follow. Column widths are
 * weighted per graph (`weight` in `RUN_GRAPHS`) using the first row's
 * graphs, so the scatter, which reads fine small, gets less than the
 * trajectories, which do not.
 *
 * @returns {void}
 */
function applyGraphLayout() {
    const panels = document.getElementById("run-visual-panels");
    if (!panels) {
        return;
    }
    const width = panels.clientWidth;
    const fits =
        width > 0
            ? Math.max(1, Math.floor((width + GRAPH_GAP_PX) / (MIN_PANE_WIDTH_PX + GRAPH_GAP_PX)))
            : graphColumns;
    const columns = Math.max(1, Math.min(graphColumns, visibleGraphKeys.length, fits));
    const weights = visibleGraphKeys
        .slice(0, columns)
        .map((key) => runGraphEntry(key).weight ?? 1);
    panels.style.gridTemplateColumns = weights
        .map((weight) => `minmax(0, ${weight}fr)`)
        .join(" ");
    panels.dataset.graphColumns = String(columns);
    matchScatterAndTrajectoryHeights(panels, columns);
}

// The trajectory canvas is 4:3, so a pane's height moves by this much per
// pixel of its width; the scatter canvas is square, so it moves by one.
const TRAJECTORY_HEIGHT_PER_WIDTH = 0.75;

/**
 * Give the scatter and the trajectory panes the same height, without ever
 * distorting the scatter: the plot stays square, so the width the two
 * share is what moves. When the trajectory pane is taller, the scatter
 * takes width from it (growing, still square) until the heights meet; when
 * it is shorter, the reverse. Only the first row is considered, and only
 * when both panes sit in it.
 *
 * Measures the layout the default weights just produced (reading a size
 * forces layout, so this needs no animation frame, which a hidden window
 * never fires), then sets the two columns to widths that meet in the
 * middle.
 *
 * @param {HTMLElement} panels The `#run-visual-panels` grid.
 * @param {number} columns How many columns the first row has.
 */
function matchScatterAndTrajectoryHeights(panels, columns) {
    const firstRow = visibleGraphKeys.slice(0, columns);
    const scatterIndex = firstRow.indexOf("scatter");
    const trajectoryIndex = firstRow.indexOf("trajectory");
    if (scatterIndex === -1 || trajectoryIndex === -1) {
        return;
    }
    const scatter = document.getElementById(runGraphEntry("scatter").paneId);
    const trajectory = document.getElementById(runGraphEntry("trajectory").paneId);
    if (
        scatter.parentElement !== panels ||
        trajectory.parentElement !== panels ||
        scatter.hidden ||
        trajectory.hidden
    ) {
        return;
    }
    const scatterBox = scatter.getBoundingClientRect();
    const trajectoryBox = trajectory.getBoundingClientRect();
    if (scatterBox.width === 0 || trajectoryBox.width === 0) {
        return;
    }
    const shift =
        (trajectoryBox.height - scatterBox.height) / (1 + TRAJECTORY_HEIGHT_PER_WIDTH);
    const pair = scatterBox.width + trajectoryBox.width;
    const scatterWidth = Math.min(
        pair - MIN_PANE_WIDTH_PX,
        Math.max(MIN_PANE_WIDTH_PX, scatterBox.width + shift)
    );
    const widths = firstRow.map((key) => {
        if (key === "scatter") {
            return scatterWidth;
        }
        if (key === "trajectory") {
            return pair - scatterWidth;
        }
        return document.getElementById(runGraphEntry(key).paneId).getBoundingClientRect().width;
    });
    panels.style.gridTemplateColumns = widths
        .map((width) => `minmax(0, ${width}fr)`)
        .join(" ");
}

/**
 * Work out which graphs to show, show exactly those, and repaint them.
 *
 * Idempotent: safe to call after any availability change, selection
 * change, or zoom transition.
 *
 * @returns {void}
 */
function syncRunGraphStage() {
    const stage = document.getElementById("run-graph-stage");
    if (!stage) {
        return;
    }
    const available = availableGraphKeys();

    // Show what the user chose *and* what has data, in card order. If
    // that leaves nothing (a run's first moments, before the chosen
    // graphs have data), fall back to the first graph that does, so a
    // state change never leaves the stage blank; the preference itself
    // is never written here, so a graph that is merely not ready yet
    // appears as soon as it becomes ready.
    visibleGraphKeys = RUN_GRAPHS.filter(
        (entry) => preferredGraphKeys.includes(entry.key) && available.includes(entry.key)
    ).map((entry) => entry.key);
    if (visibleGraphKeys.length === 0 && available.length > 0) {
        visibleGraphKeys = [available[0]];
    }
    if (!visibleGraphKeys.includes(focusedGraphKey)) {
        focusedGraphKey = visibleGraphKeys.length > 0 ? visibleGraphKeys[0] : null;
    }
    stage.hidden = available.length === 0;
    syncGraphMenu(available);

    // Only the chosen panes are visible -- and a pane that has been moved
    // out of the stage into the zoom frame must not be un-hidden back
    // into a stage it no longer occupies.
    for (const entry of RUN_GRAPHS) {
        const pane = document.getElementById(entry.paneId);
        if (!pane) {
            continue;
        }
        pane.hidden = !visibleGraphKeys.includes(entry.key);
    }
    applyGraphLayout();
    if (!redrawingActiveGraph) {
        redrawingActiveGraph = true;
        try {
            for (const key of visibleGraphKeys) {
                redrawGraph(key);
            }
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
 * Repaint whichever graphs are showing, wherever they are showing.
 *
 * While the zoom frame is open only the zoomed graph is repainted: the
 * others are hidden behind the modal and repaint when it closes.
 *
 * @returns {void}
 */
window.fim.redrawActiveGraph = function redrawActiveGraph() {
    if (zoomedGraphKey !== null) {
        redrawGraph(zoomedGraphKey);
        return;
    }
    for (const key of visibleGraphKeys) {
        redrawGraph(key);
    }
};

/**
 * Persist a display choice through the bridge, if it is up. Best
 * effort: a failure to save never changes what is on screen.
 *
 * @param {string} method
 * @param {...unknown} args
 * @returns {void}
 */
function saveRunCardChoice(method, ...args) {
    const api = window.pywebview && window.pywebview.api;
    if (api && typeof api[method] === "function") {
        api[method](...args).catch(() => {});
    }
}

/**
 * Show exactly these graphs (those with data), remembering the choice.
 *
 * @param {Array<string>} keys
 * @returns {void}
 */
window.fim.setVisibleGraphs = function setVisibleGraphs(keys) {
    const known = RUN_GRAPHS.map((entry) => entry.key);
    const chosen = known.filter((key) => keys.includes(key));
    if (chosen.length === 0) {
        return;
    }
    preferredGraphKeys = chosen;
    syncRunGraphStage();
    saveRunCardChoice("set_run_graphs", chosen);
};

/**
 * Show one graph on the stage, and only that one. Not remembered: this is
 * the programmatic "focus this graph" (tests, external callers), not the
 * user's own menu choice.
 *
 * @param {string} key
 * @returns {void}
 */
window.fim.showGraph = function showGraph(key) {
    preferredGraphKeys = [key];
    syncRunGraphStage();
};

/**
 * Set the column count the graphs are laid out in (Settings), applying
 * it now. Rows follow.
 *
 * @param {number} columns
 * @returns {void}
 */
window.fim.setGraphColumns = function setGraphColumns(columns) {
    graphColumns = Math.max(1, Math.floor(columns) || DEFAULT_GRAPH_COLUMNS);
    applyGraphLayout();
    window.fim.redrawActiveGraph();
};

/**
 * Return the keys of every graph currently on the stage, in card order.
 *
 * @returns {Array<string>}
 */
window.fim.getVisibleGraphs = function getVisibleGraphs() {
    return [...visibleGraphKeys];
};

/**
 * Return the key of the graph "the" caller should treat as active, for
 * tests and callers that need to ask rather than assume: the zoomed graph
 * while the zoom frame is open, else the pane last double-clicked, else
 * the first one shown.
 *
 * @returns {string|null}
 */
window.fim.getActiveGraph = function getActiveGraph() {
    return zoomedGraphKey === null ? focusedGraphKey : zoomedGraphKey;
};

/**
 * Forget which graphs have data, so a stale pane from the previous
 * state cannot appear on the next one's stage.
 *
 * Deliberately leaves `preferredGraphKeys` alone. Resetting it here made
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
    visibleGraphKeys = [];
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
        // Scale 1 means "exactly as large as the frame's graph column
        // leaves room for", so both base sizes come from that column
        // and never from the pane itself. An earlier version measured
        // the pane's own natural width instead, which is a function of
        // its canvas buffer -- i.e. of the *previous* zoom level's
        // size. Every step therefore compounded on the last one: 50%
        // drew at roughly a quarter, and Fit did not return to the
        // opening size.
        //
        // Width: the column minus the scrubber's own column and the
        // grid gap between the two tracks (present even when the
        // scrubber is hidden and its track is empty). Height: the
        // column's own rendered height, which `#graph-zoom-body`
        // stretches to fill the frame -- the grid itself does not
        // stretch its cross axis (`align-items: start`, as on the
        // stage), so the pane's natural height would only be its
        // content height.
        const scrubber = document.getElementById("scrubber-controls");
        const scrubberWidth =
            scrubber && scrubber.parentElement === graphColumn ? scrubber.offsetWidth : 0;
        const gap = parseFloat(getComputedStyle(graphColumn).columnGap) || 0;
        //
        // Measured off the column's border box (floored), not its
        // `clientWidth`/`clientHeight`: those shrink by a classic
        // scrollbar's thickness whenever the previous zoom level left
        // one showing, so Fit came back a scrollbar narrower than the
        // opening size. The border box is the same at every level.
        const columnBox = graphColumn.getBoundingClientRect();
        const baseWidth = Math.max(Math.floor(columnBox.width) - scrubberWidth - gap, 320);
        const baseHeight = Math.max(Math.floor(columnBox.height), 240);
        pane.style.width = `${Math.floor(baseWidth * zoomScale)}px`;
        pane.style.height = `${Math.floor(baseHeight * zoomScale)}px`;
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
 * Open one showing graph in the zoom frame.
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
 * @param {string} [requestedKey] the graph to zoom; defaults to the one
 *     last double-clicked, else the first shown.
 * @returns {void}
 */
window.fim.openGraphZoom = function openGraphZoom(requestedKey) {
    const modal = document.getElementById("graph-zoom-modal");
    const body = document.getElementById("graph-zoom-body");
    const key =
        requestedKey !== undefined && visibleGraphKeys.includes(requestedKey)
            ? requestedKey
            : focusedGraphKey;
    if (!modal || !body || key === null || zoomedGraphKey !== null) {
        return;
    }
    const entry = runGraphEntry(key);
    const pane = runGraphPane(key);
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

    zoomedGraphKey = key;
    focusedGraphKey = key;
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

    // The table goes inside its own block wrapper rather than straight
    // into the body: a table stretched to the row's height hands the
    // extra height to its rows (they spread apart), and `overflow` does
    // not reliably create a scrollport on a table box at all. The
    // wrapper stretches and scrolls; the table keeps its natural size.
    const statsColumn = document.createElement("div");
    statsColumn.id = "graph-zoom-stats-column";
    statsColumn.className = "graph-zoom-stats-column";
    if (statsTable) {
        statsColumn.appendChild(statsTable);
    }

    body.replaceChildren(graphColumn, ...(statsTable ? [statsColumn] : []));
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
    const statsColumn = document.getElementById("graph-zoom-stats-column");
    if (statsColumn) {
        statsColumn.remove();
    }
    zoomedGraphKey = null;
    zoomedStatsTable = null;
    zoomScale = 1;
    syncRunGraphStage();
}

/**
 * Which graph a double-click landed on, if any: the pane containing the
 * event target, mapped back to its key.
 *
 * @param {EventTarget|null} target
 * @returns {string|undefined}
 */
function graphKeyForTarget(target) {
    if (!(target instanceof Element)) {
        return undefined;
    }
    const entry = RUN_GRAPHS.find((candidate) => {
        const pane = document.getElementById(candidate.paneId);
        return pane !== null && pane.contains(target);
    });
    return entry ? entry.key : undefined;
}

/**
 * Read the saved display choices from the bridge and apply them.
 *
 * @returns {Promise<void>}
 */
async function loadRunCardLayout() {
    const layout = await window.pywebview.api.get_run_card_layout();
    preferredGraphKeys = layout.graphs;
    graphColumns = layout.columns;
    if (typeof window.fim.setScatterStyle === "function") {
        window.fim.setScatterStyle(layout.scatterStyle);
    }
    syncRunGraphStage();
}

window.addEventListener("load", () => {
    const menu = document.getElementById("run-graph-menu");
    const menuList = document.getElementById("run-graph-menu-list");
    const stage = document.getElementById("run-graph-stage");
    const panels = document.getElementById("run-visual-panels");
    const modal = document.getElementById("graph-zoom-modal");
    if (menuList) {
        menuList.addEventListener("change", () => {
            const keys = Array.from(menuList.querySelectorAll("input:checked")).map(
                (box) => box.value
            );
            window.fim.setVisibleGraphs(keys);
        });
    }
    if (menu) {
        // A native <details> stays open until told otherwise; close it
        // on any click outside so it behaves like a menu.
        document.addEventListener("click", (event) => {
            if (menu.open && !menu.contains(event.target)) {
                menu.open = false;
            }
        });
    }
    if (panels) {
        new ResizeObserver(() => {
            const before = panels.dataset.graphColumns;
            applyGraphLayout();
            if (panels.dataset.graphColumns !== before) {
                window.fim.redrawActiveGraph();
            }
        }).observe(panels);
    }
    if (stage) {
        // Only a graph itself opens the frame -- double-clicking the
        // menu or dragging the scrubber must not, and the graph zoomed is
        // the one under the pointer.
        stage.addEventListener("dblclick", (event) => {
            if (event.target.closest(".run-graph-toolbar, .scrubber-controls")) {
                return;
            }
            window.fim.openGraphZoom(graphKeyForTarget(event.target));
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
    whenApiReady(loadRunCardLayout);
});
