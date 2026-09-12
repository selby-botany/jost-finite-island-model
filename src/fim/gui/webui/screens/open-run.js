"use strict";

/* Screen 6: open an existing run (design doc §4.6) -- pick a persisted
 * trajectory and generation, then re-analyze it, matching `fim stats`.
 * This is also the Home rail destination (`screens/nav-rail.js`'s own
 * `resolveDestination` map: `"home": "screen-open-run"`) -- one shared
 * screen, not two, so home enrichment design doc `20260909-claude-
 * sonnet-5-home-enrichment-design.md`'s (`selby/restricted`) per-row
 * config summary and final statistics/outcome columns (`Api.list_home_
 * runs`, approach A1: reads each row's own already-computed `report.
 * json`/`summary.json`, never `trajectory.jsonl`, never re-derived)
 * show up identically whether this screen was reached via the rail or
 * via the File menu's own "Open run…" action.
 *
 * Reached from the File menu's own "Open run…" action (`app.js`'s
 * `fim.menu.openRun`). Opening succeeds by handing `Api.open_run`'s
 * own `completed`-shaped payload straight to `window.fim.
 * enterCompletedState`, landing directly in that state (unified-run-view
 * design §3.2.1) -- literal reuse of the same rendering path a live
 * run's own `onRunDone` already uses, not a second one.
 */

const openRunBanner = document.getElementById("open-run-banner");
const recentRunsBody = document.getElementById("open-run-recent-runs-body");
const recentRunsFilterInput = document.getElementById("open-run-filter");
const recentRunsCountLabel = document.getElementById("open-run-count");
const browseButton = document.getElementById("browse-trajectory-button");
const generationValueInput = document.getElementById("open-run-generation-value");
const differentiationOrdersInput = document.getElementById(
    "open-run-differentiation-orders"
);
const openButton = document.getElementById("open-run-open-button");
const openRunBackButton = document.getElementById("open-run-back-button");
const homeNewRunButton = document.getElementById("home-new-run-button");
const homeExploreButton = document.getElementById("home-explore-button");
const homeExampleSelect = document.getElementById("home-example-select");

let selectedTrajectoryPath = null;

// Every run fetched for this visit (`refreshRecentRuns`), unfiltered and
// ungrouped -- `renderRecentRuns` derives the actual displayed rows from
// this on every filter/group-toggle, so filtering and collapsing never
// re-hit the filesystem.
let allRecentRuns = [];
// Group ids the user has explicitly collapsed -- persists across visits
// within this launch (`refreshRecentRuns` no longer resets this; see
// its own comment for why). A group id is stable across re-fetches
// within one calendar day ("Today"/"Yesterday"/"Earlier", or an
// "Earlier"-nested literal date), so "the same group the user last
// toggled" genuinely means the same thing on a later visit, not merely
// a coincidentally-reused label.
const collapsedGroupIds = new Set();
// Group ids `ensureGroupDefaults` has already decided a default for --
// distinct from `collapsedGroupIds` itself (which a user's own toggle
// mutates freely): this is what lets a *newly appeared* group (a run
// finishing today for the first time this launch, say) start collapsed
// by default without also silently re-collapsing a group the user
// already explicitly opened on an earlier visit.
const knownGroupIds = new Set();

const _ONE_DAY_MS = 24 * 60 * 60 * 1000;
// Priority order for rendering -- runs are already newest-first
// (`Api.list_home_runs`), but this makes bucket ordering an explicit,
// tested invariant rather than an accident of that sort order.
const _DATE_BUCKET_ORDER = ["Today", "Yesterday", "Earlier", "Unknown date"];

/**
 * Bucket one run's own `endedAt` into a coarse "Today"/"Yesterday"/
 * "Earlier" label -- the only grouping signal `RecentRun` exposes today
 * (large-sweep architecture roadmap doc `20260907-claude-sonnet-5-
 * large-sweep-architecture-roadmap.md`, `selby/restricted`, describes a
 * future `SweepManifest` one level above today's `BatchManifest`; once
 * that exists, a sweep-id group key can be added as a second, preferred
 * bucketing source here without touching `renderRecentRuns` itself).
 * `"Earlier"` is further broken down by its own literal calendar date
 * (`groupRecentRuns`, below) -- this function only ever needs to tell
 * "today," "yesterday," and "everything else" apart.
 * @param {string} endedAt
 * @returns {string}
 */
function dateBucketFor(endedAt) {
    const parsed = new Date(endedAt);
    if (Number.isNaN(parsed.getTime())) {
        return "Unknown date";
    }
    const now = new Date();
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const startOfParsedDay = new Date(
        parsed.getFullYear(),
        parsed.getMonth(),
        parsed.getDate()
    );
    const ageDays = Math.round(
        (startOfToday.getTime() - startOfParsedDay.getTime()) / _ONE_DAY_MS
    );
    if (ageDays <= 0) {
        return "Today";
    }
    if (ageDays === 1) {
        return "Yesterday";
    }
    return "Earlier";
}

/**
 * Format one run's own `endedAt` as a literal `YYYY-MM-DD` calendar
 * date, in local time (matching `dateBucketFor`'s own local-time day
 * boundary) -- `"Earlier"`'s own per-date sub-group label/id.
 * @param {string} endedAt
 * @returns {string}
 */
function calendarDateLabel(endedAt) {
    const parsed = new Date(endedAt);
    const year = parsed.getFullYear();
    const month = String(parsed.getMonth() + 1).padStart(2, "0");
    const day = String(parsed.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
}

/**
 * Strip sub-second precision from an ISO-8601 `endedAt` timestamp for
 * display (`.292444Z` -> `Z`) -- the underlying value (used unchanged
 * for date-bucketing and free-text filtering) keeps its full precision;
 * only the rendered "Ended" column text is trimmed, since a human
 * reader never needs microsecond resolution to recognize when a run
 * finished.
 * @param {string} endedAt
 * @returns {string}
 */
function formatEndedAt(endedAt) {
    return endedAt.replace(/\.\d+(?=Z?$)/, "");
}

/**
 * Whether `run` matches a free-text filter -- run ID, label, and ended
 * date, the same fields the table itself shows, so "what you can read
 * is what you can search for."
 * @param {{runId: string, label: string, endedAt: string}} run
 * @param {string} filterText
 * @returns {boolean}
 */
function matchesRecentRunsFilter(run, filterText) {
    if (!filterText) {
        return true;
    }
    const haystack = `${run.runId} ${run.label} ${run.endedAt}`.toLowerCase();
    return haystack.includes(filterText.toLowerCase());
}

/**
 * Split `runs` into date-bucket groups, in `_DATE_BUCKET_ORDER`.
 * `"Earlier"` is itself a parent group whose own `subgroups` further
 * split its runs by literal calendar date (newest first -- `runs`
 * itself already arrives newest-first from `Api.list_home_runs`, and
 * `Map` iteration order is insertion order, so no separate sort is
 * needed) -- every other top-level bucket is a plain leaf group, its
 * own `runs` rendered directly.
 * @param {Array<object>} runs
 * @returns {Array<{id: string, label: string, runs?: Array<object>,
 *     subgroups?: Array<{id: string, label: string, runs: Array<object>}>}>}
 */
function groupRecentRuns(runs) {
    const buckets = new Map();
    for (const run of runs) {
        const bucketId = dateBucketFor(run.endedAt);
        if (!buckets.has(bucketId)) {
            buckets.set(bucketId, []);
        }
        buckets.get(bucketId).push(run);
    }
    return _DATE_BUCKET_ORDER.filter((bucketId) => buckets.has(bucketId)).map(
        (bucketId) => {
            const bucketRuns = buckets.get(bucketId);
            if (bucketId !== "Earlier") {
                return { id: bucketId, label: bucketId, runs: bucketRuns };
            }
            const dateGroups = new Map();
            for (const run of bucketRuns) {
                const dateLabel = calendarDateLabel(run.endedAt);
                if (!dateGroups.has(dateLabel)) {
                    dateGroups.set(dateLabel, []);
                }
                dateGroups.get(dateLabel).push(run);
            }
            const subgroups = Array.from(dateGroups.entries()).map(
                ([dateLabel, dateRuns]) => ({
                    id: `Earlier:${dateLabel}`,
                    label: dateLabel,
                    runs: dateRuns,
                })
            );
            return { id: bucketId, label: bucketId, subgroups };
        }
    );
}

/**
 * Total run count for a group, recursing into `subgroups` for a parent
 * group like `"Earlier"` -- `buildGroupHeaderRow`'s own `(N)` count, so
 * a parent group's own header reads "Earlier (12)" for its full,
 * combined count, not just however many of its own direct `runs` it
 * has (a parent group's own `runs` is `undefined`, never an empty
 * placeholder array, so this cannot silently read `0` for one instead
 * of actually summing its children).
 * @param {{runs?: Array<object>, subgroups?: Array<object>}} group
 * @returns {number}
 */
function groupRunCount(group) {
    return group.runs
        ? group.runs.length
        : group.subgroups.reduce((total, subgroup) => total + groupRunCount(subgroup), 0);
}

/**
 * First-visit-only bookkeeping: any group id not already seen this
 * launch defaults to collapsed (design ask: closed submenus by
 * default) and is recorded as seen, so a *later* re-render (a filter
 * keystroke, a fresh fetch) never re-applies that default over
 * whatever the user has since done to it -- recurses into a parent
 * group's own `subgroups` so a newly-appeared per-date sub-group under
 * "Earlier" gets the identical treatment its own top-level siblings do.
 * @param {Array<object>} groups
 */
function ensureGroupDefaults(groups) {
    for (const group of groups) {
        if (!knownGroupIds.has(group.id)) {
            knownGroupIds.add(group.id);
            collapsedGroupIds.add(group.id);
        }
        if (group.subgroups) {
            ensureGroupDefaults(group.subgroups);
        }
    }
}

// Home's own shortcut cards (design §9, slice 3): both delegate to an
// already-existing entry point exactly the rail's own buttons use
// (`screens/nav-rail.js`'s own `wireNavRail`) -- pure navigation, no
// bridge call of its own. "New run" shows Configure with whatever is
// already loaded (`loadInitialForm`'s own "prefer the last submitted
// form" behavior); a worked example is still one click away there via
// "Load example…", unchanged.
homeNewRunButton.addEventListener("click", () => {
    window.fim.showConfigureScreen();
});

homeExploreButton.addEventListener("click", () => {
    window.fim.menu.explore();
});

// Set once `refreshHomeExampleOptions`'s own bridge call has settled and
// the dropdown genuinely lists this visit's own built-in examples --
// `window.__fimXReady`-flag precedent (`presets.js`'s own
// `__fimPresetsListReady`, `__fimOpenRunRecentRunsLoaded` above) for the
// identical reason: a test polling only "the select exists" could
// otherwise observe it with no example options yet, in the narrow
// window before this async call resolves.
window.__fimHomeExampleOptionsReady = false;

/**
 * Populate `home-example-select` with this visit's own built-in worked
 * examples -- `Api.list_presets`'s own combined list, filtered to
 * `builtin` entries only (design ask: "one of the examples," not every
 * user-saved configuration too; the full combined list stays reachable
 * only from the picker `fim.menu.loadExample` opens). Re-fetched on
 * every visit to Home rather than once, matching `refreshRecentRuns`'s
 * own "never trust a stale fetch across visits" precedent, even though
 * the built-in set itself never changes at runtime.
 */
async function refreshHomeExampleOptions() {
    window.__fimHomeExampleOptionsReady = false;
    const placeholder = homeExampleSelect.options[0];
    homeExampleSelect.replaceChildren(placeholder);
    homeExampleSelect.value = "";
    const result = await window.pywebview.api.list_presets();
    const examples = result.ok
        ? result.presets.filter((preset) => preset.builtin)
        : [];
    for (const example of examples) {
        const option = document.createElement("option");
        option.value = example.id;
        option.textContent = example.title;
        homeExampleSelect.appendChild(option);
    }
    window.__fimHomeExampleOptionsReady = true;
}

// A plain, immediately-acting pulldown (botanist GUI design doc's own
// "jump-start" precedent for a shortcut like this): picking an example
// applies it and navigates straight to Configure, then resets to its
// own placeholder so the control always reads as an action, never as
// "currently showing example X" -- Configure itself, not this select,
// is where the loaded values are actually reviewed. Reuses `presets.js`'s
// own `applyPreset` (the exact mechanism `modal-presets`'s own picker
// already calls) via `window.fim.applyPreset`, so this is genuinely a
// second entry point to one mechanism, not a second, independent way of
// loading a preset's values. Only navigates on a successful apply --
// `applyPreset`'s own rejected-values path already shows an alert, and
// jumping to Configure on top of that would land on an unchanged form
// right after telling the user why nothing changed.
homeExampleSelect.addEventListener("change", async () => {
    const presetId = homeExampleSelect.value;
    if (!presetId) {
        return;
    }
    const presetTitle = homeExampleSelect.selectedOptions[0].textContent;
    homeExampleSelect.value = "";
    const applied = await window.fim.applyPreset(presetId, presetTitle);
    if (applied) {
        window.fim.showConfigureScreen();
    }
});

function showOpenRunBanner(message) {
    if (!message) {
        openRunBanner.hidden = true;
        openRunBanner.textContent = "";
        return;
    }
    openRunBanner.hidden = false;
    openRunBanner.textContent = message;
}

function setSelectedTrajectory(path) {
    selectedTrajectoryPath = path;
    openButton.disabled = path === null;
}

function generationMode() {
    const checked = document.querySelector(
        'input[name="open_run_generation_mode"]:checked'
    );
    return checked ? checked.value : "final";
}

/**
 * Render one row's own config summary as a compact, single-line string
 * (home enrichment design doc `20260909-claude-sonnet-5-home-
 * enrichment-design.md`, `selby/restricted`). `Object.entries` walks
 * `configSummary` in `Api.list_home_runs`'s own fixed key order (`N`,
 * `d`, `seed`, `m`, `mu`, `mutation_model`) — JSON preserves object key
 * order, so nothing here needs to know that order itself.
 * @param {Record<string, string> | null} configSummary
 * @returns {string}
 */
function formatRowConfigSummary(configSummary) {
    if (!configSummary) {
        return "";
    }
    return Object.entries(configSummary)
        .map(([key, value]) => `${key}=${value}`)
        .join(" ");
}

/**
 * Render one row's own final statistics as a compact, single-line
 * string -- a point value for a scalar run, or `"mean [low, high]"`
 * for a batch's own confidence interval, the identical text `webui/
 * meters.js`'s own `buildCiMeter` already shows in its tooltip (not a
 * second, independently worded CI format). A batch row's own six
 * statistics share one `sampleCount` (every one was computed from the
 * same replicate set), so `meters.js`'s own `ciCaption` (design doc
 * §7.2's "uncertainty across N independent replicates" re-labeling)
 * is stated once, as a leading note, rather than six times over --
 * repeating an identical caption after every one of six statistics
 * would bury the actual numbers this cell exists to show.
 * @param {Record<string, string | {mean: string, low: string, high: string, sampleCount: number}> | null} statistics
 * @returns {string}
 */
function formatRowStatistics(statistics) {
    if (!statistics) {
        return "";
    }
    const entries = Object.entries(statistics);
    const values = entries
        .map(([name, value]) =>
            typeof value === "string"
                ? `${name}=${value}`
                : `${name}=${value.mean} [${value.low}, ${value.high}]`
        )
        .join(" ");
    const firstInterval = entries.map(([, value]) => value).find(
        (value) => typeof value !== "string"
    );
    return firstInterval
        ? `(${ciCaption(firstInterval.sampleCount)}) ${values}`
        : values;
}

/**
 * Build one replicate's own `<tr>` for an expanded batch row -- home
 * enrichment design doc `20260909-claude-sonnet-5-home-enrichment-
 * design.md`'s (`selby/restricted`) approach B1: clicking it selects
 * that one replicate's own trajectory for "Open ▶", the identical
 * selection mechanism a scalar row's own row-click already uses, so a
 * replicate never has to be re-found from its own separate batch
 * results screen just to open it directly.
 * @param {{replicateId: string, trajectoryPath: string, statistics: object | null}} replicate
 * @returns {HTMLTableRowElement}
 */
function buildReplicateRow(replicate) {
    const row = document.createElement("tr");
    row.className = "open-run-replicate-row";
    const cells = [
        "",
        "",
        replicateLabel(replicate.replicateId),
        "",
        formatRowStatistics(replicate.statistics),
    ];
    cells.forEach((value, index) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        if (index === 4) {
            cell.className = "open-run-summary-cell";
            cell.title = value;
        }
        row.appendChild(cell);
    });
    row.addEventListener("click", (event) => {
        event.stopPropagation();
        for (const sibling of recentRunsBody.querySelectorAll("tr")) {
            sibling.classList.remove("selected");
        }
        row.classList.add("selected");
        showOpenRunBanner("");
        setSelectedTrajectory(replicate.trajectoryPath);
    });
    return row;
}

/**
 * Expand or collapse one batch row's own replicate list, in place --
 * fetched at most once per row (`window.__fimBatchReplicateCache`,
 * keyed by directory), lazily, only the first time this actually runs
 * (approach B1's own reasoning: a `results/` directory with many old
 * batches should not pay for every one's own replicate list, expanded
 * or not).
 * @param {HTMLTableRowElement} batchRow
 * @param {HTMLButtonElement} toggleButton
 * @param {string} directory
 */
async function toggleBatchRow(batchRow, toggleButton, directory) {
    const expandedRows = batchRow._fimExpandedRows;
    if (expandedRows) {
        for (const row of expandedRows) {
            row.remove();
        }
        batchRow._fimExpandedRows = null;
        toggleButton.textContent = "▸";
        toggleButton.setAttribute("aria-expanded", "false");
        return;
    }
    if (!window.__fimBatchReplicateCache) {
        window.__fimBatchReplicateCache = {};
    }
    let replicates = window.__fimBatchReplicateCache[directory];
    if (!replicates) {
        const result = await window.pywebview.api.get_batch_replicate_summary(
            directory
        );
        replicates = result.ok ? result.replicates : [];
        window.__fimBatchReplicateCache[directory] = replicates;
    }
    const rows = replicates.map(buildReplicateRow);
    let anchor = batchRow;
    for (const row of rows) {
        anchor.after(row);
        anchor = row;
    }
    batchRow._fimExpandedRows = rows;
    toggleButton.textContent = "▾";
    toggleButton.setAttribute("aria-expanded", "true");
}

/**
 * Build one run's own `<tr>` -- unchanged rendering, factored out of
 * `refreshRecentRuns` so `renderRecentRuns` can call it once per group
 * member on every filter/collapse re-render, not only on a fresh fetch.
 * @param {object} run
 * @returns {HTMLTableRowElement}
 */
function buildRunRow(run) {
    const row = document.createElement("tr");
    const configText = formatRowConfigSummary(run.configSummary);
    const statisticsText = formatRowStatistics(run.statistics);
    for (const [value, className, isLabelCell] of [
        [run.runId, null, false],
        [formatEndedAt(run.endedAt), null, false],
        [run.label, null, true],
        [configText, "open-run-summary-cell", false],
        [statisticsText, "open-run-summary-cell", false],
    ]) {
        const cell = document.createElement("td");
        // A batch row's own label cell gets an expand/collapse
        // toggle beside its text (design §9: "expandable to its
        // own replicate list") -- a scalar row's own label cell is
        // plain text, unchanged.
        if (isLabelCell && run.isBatch) {
            const toggleButton = document.createElement("button");
            toggleButton.type = "button";
            toggleButton.className = "open-run-replicate-toggle";
            toggleButton.textContent = "▸";
            toggleButton.setAttribute("aria-expanded", "false");
            toggleButton.setAttribute(
                "aria-label",
                `Show replicates for ${run.runId}`
            );
            toggleButton.addEventListener("click", (event) => {
                event.stopPropagation();
                toggleBatchRow(row, toggleButton, run.directory);
            });
            cell.appendChild(toggleButton);
            cell.appendChild(document.createTextNode(` ${value}`));
        } else {
            cell.textContent = value;
        }
        // `configText`/`statisticsText` can run long (six fields,
        // six statistics) -- capped and ellipsized in CSS, with the
        // full text still reachable on hover via `title` rather
        // than silently truncated with no way to see the rest.
        if (className !== null) {
            cell.className = className;
            cell.title = value;
        }
        row.appendChild(cell);
    }
    row.addEventListener("click", () => {
        for (const sibling of recentRunsBody.querySelectorAll("tr")) {
            sibling.classList.remove("selected");
        }
        row.classList.add("selected");
        if (run.isBatch) {
            // Design §0, §4.0 #9: a batch manifest has no single
            // trajectory of its own to verify or re-analyze here --
            // named explicitly rather than silently doing nothing
            // or attempting (and failing) to re-analyze it anyway.
            setSelectedTrajectory(null);
            showOpenRunBanner(
                "batch runs have no single trajectory — open a replicate " +
                    "from its own batch results screen instead"
            );
            return;
        }
        showOpenRunBanner("");
        setSelectedTrajectory(run.trajectoryPath);
    });
    return row;
}

/**
 * Build one date-bucket group's own header row -- a full-width toggle
 * button naming the bucket and its member count (design's own answer to
 * "a fantastically long results scroll": collapsing a bucket removes
 * its rows from the DOM entirely, not merely hiding them, so a large
 * `results/` directory never pays for rendering rows nobody asked to
 * see). Collapsed state survives across visits to this screen for the
 * life of the window (`collapsedGroupIds`, seeded per-group the first
 * time each group id is ever seen by `ensureGroupDefaults`).
 * @param {{id: string, label: string, runs?: Array<object>,
 *     subgroups?: Array<object>}} group
 * @param {boolean} [nested] True for a date sub-group rendered inside
 *     a parent bucket (e.g. one calendar day inside "Earlier"), so it
 *     can be indented to show its place in the hierarchy.
 * @returns {HTMLTableRowElement}
 */
function buildGroupHeaderRow(group, nested = false) {
    const row = document.createElement("tr");
    row.className = "open-run-group-header";
    if (nested) {
        row.classList.add("open-run-group-header-nested");
    }
    const cell = document.createElement("td");
    cell.colSpan = 5;
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "open-run-group-toggle";
    const collapsed = collapsedGroupIds.has(group.id);
    toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    toggle.textContent = `${collapsed ? "▸" : "▾"} ${group.label} (${groupRunCount(group)})`;
    toggle.addEventListener("click", () => {
        if (collapsedGroupIds.has(group.id)) {
            collapsedGroupIds.delete(group.id);
        } else {
            collapsedGroupIds.add(group.id);
        }
        renderRecentRuns();
    });
    cell.appendChild(toggle);
    row.appendChild(cell);
    return row;
}

/**
 * Append one group's own header row and, if expanded, its own content
 * -- recurses into `subgroups` for a parent group like `"Earlier"`, so
 * a leaf group (plain `runs`) and a parent group (nested `subgroups`)
 * render through the identical function, one level deeper each time,
 * rather than `renderRecentRuns` needing to know the tree's own depth
 * up front.
 * @param {object} group
 * @param {boolean} [nested] See `buildGroupHeaderRow`.
 */
function renderGroup(group, nested = false) {
    recentRunsBody.appendChild(buildGroupHeaderRow(group, nested));
    if (collapsedGroupIds.has(group.id)) {
        return;
    }
    if (group.subgroups) {
        for (const subgroup of group.subgroups) {
            renderGroup(subgroup, true);
        }
    } else {
        for (const run of group.runs) {
            recentRunsBody.appendChild(buildRunRow(run));
        }
    }
}

/**
 * Re-render the recent-runs table from `allRecentRuns` -- applies the
 * current filter text and group-collapse state, but never re-fetches
 * (`refreshRecentRuns` owns the one filesystem read per visit).
 */
function renderRecentRuns() {
    recentRunsBody.replaceChildren();
    const filterText = recentRunsFilterInput.value.trim();
    const filtered = allRecentRuns.filter((run) =>
        matchesRecentRunsFilter(run, filterText)
    );
    const groups = groupRecentRuns(filtered);
    // Decide "closed by default" for any group this launch has never
    // seen before *first*, so the very first render after a fresh fetch
    // already reflects that default rather than briefly flashing open.
    ensureGroupDefaults(groups);
    for (const group of groups) {
        renderGroup(group);
    }
    recentRunsCountLabel.textContent =
        filtered.length === allRecentRuns.length
            ? `${allRecentRuns.length} run${allRecentRuns.length === 1 ? "" : "s"}`
            : `${filtered.length} of ${allRecentRuns.length} runs`;
}

async function refreshRecentRuns() {
    // `showOpenRunScreen` fires this without awaiting it (a real
    // filesystem scan should not block the screen transition), so
    // `window.__fimOpenRunRecentRunsLoaded` is the only observable
    // signal that this async call has actually settled -- tests poll
    // it before tearing down their window, rather than treating
    // "screen visible" (true the instant `showOpenRunScreen` returns,
    // well before this promise resolves) as proof this call is done.
    // Skipping that wait let a real window get destroyed while
    // `Api.list_recent_runs()`'s result was still in flight back to
    // pywebview's own JS bridge, surfacing as a `JavascriptException`
    // on pywebview's own delivery thread (`webview/util.py`'s
    // `js_bridge_call`) -- harmless to this page, but a real source of
    // an occasional, very slow interpreter-shutdown stall while that
    // thread outlived the window it was about to call back into.
    window.__fimOpenRunRecentRunsLoaded = false;
    // A fresh visit never shows a stale replicate list fetched for a
    // *previous* visit's own now-discarded rows -- `toggleBatchRow`'s
    // own cache is keyed by directory, not by row, so it would
    // otherwise survive the next `renderRecentRuns()` untouched.
    window.__fimBatchReplicateCache = {};
    // `collapsedGroupIds` is intentionally NOT reset here -- a group the
    // user collapsed or expanded on a previous visit should stay that
    // way (item 2b); `ensureGroupDefaults` (called from
    // `renderRecentRuns`) is what seeds a *new* group's default state
    // (closed) the first time this window ever sees it.
    recentRunsFilterInput.value = "";
    allRecentRuns = await window.pywebview.api.list_home_runs();
    renderRecentRuns();
    window.__fimOpenRunRecentRunsLoaded = true;
}

recentRunsFilterInput.addEventListener("input", () => {
    renderRecentRuns();
});

browseButton.addEventListener("click", async () => {
    const result = await window.pywebview.api.browse_for_trajectory();
    if (!result.ok) {
        return;
    }
    showOpenRunBanner("");
    setSelectedTrajectory(result.path);
});

openButton.addEventListener("click", async () => {
    if (selectedTrajectoryPath === null) {
        showOpenRunBanner("no trajectory selected");
        return;
    }
    const values = {
        trajectoryPath: selectedTrajectoryPath,
        generationMode: generationMode(),
        generation: generationValueInput.value,
        differentiationOrders: differentiationOrdersInput.value,
    };
    const result = await window.pywebview.api.open_run(values);
    if (!result.ok) {
        showOpenRunBanner(result.message);
        return;
    }
    showOpenRunBanner("");
    // A different persisted run being opened is the other of the two
    // points the trajectory legend's own display-only visibility toggle
    // resets (design §6.2's legend-toggle; `run-view-completed.js`'s own
    // `resetTrajectoryLegendVisibility` doc comment names both).
    window.fim.resetTrajectoryLegendVisibility();
    window.fim.enterCompletedState(result, false);
});

openRunBackButton.addEventListener("click", () => {
    window.fim.showScreen("screen-run");
});

window.fim.showOpenRunScreen = function showOpenRunScreen() {
    showOpenRunBanner("");
    setSelectedTrajectory(null);
    generationValueInput.value = "";
    differentiationOrdersInput.value = "";
    window.fim.showScreen("screen-open-run");
    refreshRecentRuns();
    refreshHomeExampleOptions();
};
