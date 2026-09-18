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
const openButton = document.getElementById("open-run-open-button");
const openRunBackButton = document.getElementById("open-run-back-button");
const homeNewRunButton = document.getElementById("home-new-run-button");
const homeExploreButton = document.getElementById("home-explore-button");
const homeExampleSelect = document.getElementById("home-example-select");
const homeNewStudyNameInput = document.getElementById("home-new-study-name");
const homeNewStudyDescriptionInput = document.getElementById(
    "home-new-study-description"
);
const homeNewStudyButton = document.getElementById("home-new-study-button");
const homeNewExperimentNameInput = document.getElementById(
    "home-new-experiment-name"
);
const homeNewExperimentDescriptionInput = document.getElementById(
    "home-new-experiment-description"
);
const homeNewExperimentButton = document.getElementById(
    "home-new-experiment-button"
);
const selectAllButton = document.getElementById("open-run-select-all-button");
const clearSelectionButton = document.getElementById(
    "open-run-clear-selection-button"
);
const deleteSelectedButton = document.getElementById(
    "open-run-delete-selected-button"
);
const selectionCountLabel = document.getElementById("open-run-selection-count");

let selectedTrajectoryPath = null;

// Every run/Study/Experiment fetched for this visit (`refreshRecentRuns`),
// unfiltered and ungrouped -- `renderRecentRuns` derives the actual
// displayed tree from these on every filter/group-toggle, so filtering
// and collapsing never re-hit the filesystem. Run/Study/Experiment
// hierarchy design (`20260917-claude-sonnet-5-run-study-experiment-
// hierarchy-design.md`, `selby/restricted`).
let allRecentRuns = [];
let allStudies = [];
let allExperiments = [];
// A Run's own directory, not a DOM row -- so "Select all" (below) can
// reach every loaded run regardless of which groups are currently
// collapsed, and a selection survives a re-render (a filter keystroke)
// untouched.
const selectedDirectories = new Set();
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
 * own `runs` rendered directly. Every group carries its own precomputed
 * `runCount` (a plain leaf-run total, used by `renderRecentRuns`'s own
 * bottom summary) and `countLabel` (the exact text `buildGroupHeaderRow`
 * shows in parentheses) -- `buildHomeGroups`, below, gives a Study/
 * Experiment group the identical two fields, so every group in the tree
 * is rendered by the same code with no per-kind special-casing.
 * @param {Array<object>} runs
 * @returns {Array<{id: string, label: string, runCount: number,
 *     countLabel: string, runs?: Array<object>,
 *     subgroups?: Array<{id: string, label: string, runs: Array<object>,
 *         runCount: number, countLabel: string}>}>}
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
                return {
                    id: bucketId,
                    label: bucketId,
                    runs: bucketRuns,
                    runCount: bucketRuns.length,
                    countLabel: String(bucketRuns.length),
                };
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
                    runCount: dateRuns.length,
                    countLabel: String(dateRuns.length),
                })
            );
            const runCount = subgroups.reduce(
                (total, subgroup) => total + subgroup.runCount,
                0
            );
            return {
                id: bucketId,
                label: bucketId,
                subgroups,
                runCount,
                countLabel: String(runCount),
            };
        }
    );
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

/**
 * Every Run directory already claimed by some Study -- Run/Study/
 * Experiment hierarchy design (`20260917-claude-sonnet-5-run-study-
 * experiment-hierarchy-design.md`, `selby/restricted`, §6): "Unsorted"
 * is every Run with no Study membership, decided here, client-side,
 * from `Api.list_studies`'s own already-fetched `runDirectories` --
 * never a second bridge round trip per Run row.
 * @param {Array<{runDirectories: Array<string>}>} studies
 * @returns {Set<string>}
 */
function claimedDirectories(studies) {
    const claimed = new Set();
    for (const study of studies) {
        for (const directory of study.runDirectories) {
            claimed.add(directory);
        }
    }
    return claimed;
}

/**
 * Whether `name` matches a filter's free text -- case-insensitive
 * substring, the same rule `matchesRecentRunsFilter` already applies to
 * a Run's own fields, extended to a Study/Experiment's own name.
 * @param {string} name
 * @param {string} filterText
 * @returns {boolean}
 */
function nameMatchesFilter(name, filterText) {
    return !filterText || name.toLowerCase().includes(filterText.toLowerCase());
}

/**
 * Build one Study's own tree group -- a lazy leaf: `runs` is `null`
 * until the row's own expand control has actually been clicked at least
 * once (`buildGroupHeaderRow`'s own toggle handler), read back here from
 * `window.__fimStudyRunsCache` so a group rebuilt on every keystroke
 * (`renderRecentRuns`) never re-fetches a Study already expanded earlier
 * in this visit -- the identical cache-survives-rebuild shape
 * `toggleBatchRow`'s own `window.__fimBatchReplicateCache` already
 * established one level down.
 * @param {{studyId: string, name: string, runCount: number}} study
 * @returns {object}
 */
function studyGroup(study) {
    if (!window.__fimStudyRunsCache) {
        window.__fimStudyRunsCache = {};
    }
    const cachedRuns = window.__fimStudyRunsCache[study.studyId];
    return {
        id: `study:${study.studyId}`,
        label: study.name,
        kind: "study",
        studyId: study.studyId,
        runCount: study.runCount,
        countLabel: `${study.runCount} run${study.runCount === 1 ? "" : "s"}`,
        runs: cachedRuns === undefined ? null : cachedRuns,
    };
}

/**
 * Build one Experiment's own tree group -- its member Studies are
 * already fully known from `list_studies`/`list_experiments` (both
 * fetched eagerly, `refreshRecentRuns`), so unlike a Study's own Run
 * list, expanding an Experiment needs no bridge call of its own at all
 * (design doc §6).
 * @param {{experimentId: string, name: string, studyCount: number,
 *     studyIds: Array<string>}} experiment
 * @param {Map<string, object>} studiesById
 * @returns {object}
 */
function experimentGroup(experiment, studiesById) {
    const subgroups = experiment.studyIds
        .map((studyId) => studiesById.get(studyId))
        .filter((study) => study !== undefined)
        .map(studyGroup);
    return {
        id: `experiment:${experiment.experimentId}`,
        label: experiment.name,
        kind: "experiment",
        experimentId: experiment.experimentId,
        runCount: subgroups.reduce((total, subgroup) => total + subgroup.runCount, 0),
        countLabel: `${experiment.studyCount} stud${
            experiment.studyCount === 1 ? "y" : "ies"
        }`,
        subgroups,
    };
}

/**
 * Build Home's own top-level tree: every Experiment, every Study not
 * inside one, and an "Unsorted" bucket for every Run in neither --
 * itself grouped by date exactly as `groupRecentRuns` already grouped
 * every run before Study/Experiment existed. On a checkout with no
 * Study/Experiment ever created, returns the plain date-bucket groups
 * directly, matching that exact prior rendering byte-for-byte -- no
 * empty "Unsorted" wrapper cluttering a first-run screen (design doc §6:
 * "zero required migration").
 *
 * A filter's free text matches a Study/Experiment by name (`
 * nameMatchesFilter`) and a Run by its own existing fields
 * (`matchesRecentRunsFilter`, applied to "Unsorted" exactly as before);
 * an Experiment matching by name keeps every one of its own Studies,
 * not just ones that would themselves match.
 * @param {Array<object>} runs
 * @param {Array<object>} studies
 * @param {Array<object>} experiments
 * @param {string} filterText
 * @returns {Array<object>}
 */
function buildHomeGroups(runs, studies, experiments, filterText) {
    const studiesById = new Map(studies.map((study) => [study.studyId, study]));
    const groupedStudyIds = new Set();
    const experimentGroups = [];
    for (const experiment of experiments) {
        for (const studyId of experiment.studyIds) {
            groupedStudyIds.add(studyId);
        }
        if (nameMatchesFilter(experiment.name, filterText)) {
            experimentGroups.push(experimentGroup(experiment, studiesById));
        }
    }
    const standaloneStudyGroups = studies
        .filter(
            (study) =>
                !groupedStudyIds.has(study.studyId) &&
                nameMatchesFilter(study.name, filterText)
        )
        .map(studyGroup);
    const claimed = claimedDirectories(studies);
    const unsortedRuns = runs
        .filter((run) => !claimed.has(run.directory))
        .filter((run) => matchesRecentRunsFilter(run, filterText));
    const dateGroups = groupRecentRuns(unsortedRuns);
    if (studies.length === 0 && experiments.length === 0) {
        return dateGroups;
    }
    const unsortedGroup = {
        id: "Unsorted",
        label: "Unsorted",
        kind: "unsorted",
        runCount: unsortedRuns.length,
        countLabel: String(unsortedRuns.length),
        subgroups: dateGroups,
    };
    return [...experimentGroups, ...standaloneStudyGroups, unsortedGroup];
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
 * examples -- `screens/presets.js`'s own shared `refreshExampleOptions`
 * (design ask: "one of the examples," not every user-saved
 * configuration too; the full combined list stays reachable only from
 * the picker `fim.menu.loadExample` opens). Re-fetched on every visit to
 * Home rather than once, matching `refreshRecentRuns`'s own "never trust
 * a stale fetch across visits" precedent, even though the built-in set
 * itself never changes at runtime.
 */
async function refreshHomeExampleOptions() {
    window.__fimHomeExampleOptionsReady = false;
    await window.fim.refreshExampleOptions(homeExampleSelect);
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
// `applyPreset`'s own rejected-values path already shows an inline
// notice on this same screen (`showExampleLoadNotice`), and jumping to
// Configure on top of that would land on an unchanged form right after
// telling the user why nothing changed.
homeExampleSelect.addEventListener("change", async () => {
    const presetId = homeExampleSelect.value;
    if (!presetId) {
        return;
    }
    // The option's own bare title (`refreshExampleOptions`'s own
    // `dataset.presetTitle`), not its visible `textContent` -- the
    // latter carries a "(view YAML only)" suffix for the one example
    // this affects, meant for the pulldown's own label, not to be
    // echoed back inside `showExampleLoadNotice`'s own message.
    const presetTitle = homeExampleSelect.selectedOptions[0].dataset.presetTitle;
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

/**
 * Render one row's own config summary as a compact, single-line string
 * (home enrichment design doc `20260909-claude-sonnet-5-home-
 * enrichment-design.md`, `selby/restricted`). `Object.entries` walks
 * `configSummary` in `Api.list_home_runs`'s own fixed key order (`N`,
 * `d`, `m`, `mu`, `mutation_model`, `seed` — `seed` last, the field a
 * reader cares about least when scanning for "what's different about
 * this run") — JSON preserves object key order, so nothing here needs
 * to know that order itself.
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
 *
 * The same reasoning keeps `halfWidth`/`sampleStd` out of this cell,
 * even though the payload carries them (`fim.gui.app._interval_payload`):
 * §7.2 asks for those two numbers in "the meter's tooltip"
 * (`meters.js`'s own `buildCiMeter`), and six more "half-width X,
 * equivalent sample standard deviation Y" clauses on one line would
 * bury exactly what this cell is for.
 * @param {Record<string, string | {mean: string, low: string, high: string, sampleCount: number, halfWidth?: string, sampleStd?: string}> | null} statistics
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
        "",
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
    // Double-clicking a replicate row opens it directly (item 5), the
    // same "final generation, no sweep" shortcut a top-level run row's
    // own double-click gives (below) -- a replicate is one single
    // trajectory just like a scalar run is, so the same click-then-
    // Open-button round trip would otherwise be needed for no reason.
    row.addEventListener("dblclick", async (event) => {
        event.stopPropagation();
        await openTrajectory(replicate.trajectoryPath);
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
 * An immediately-acting "Add to study…" pulldown for one Run row -- see
 * `buildAddToExperimentSelect`'s own docstring for the shared idiom.
 * @param {string} directory
 * @returns {HTMLSelectElement}
 */
function buildAddToStudySelect(directory) {
    const select = document.createElement("select");
    select.className = "open-run-add-to-select";
    select.setAttribute("aria-label", "Add this run to a study");
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Add to study…";
    placeholder.selected = true;
    select.appendChild(placeholder);
    for (const study of allStudies) {
        const option = document.createElement("option");
        option.value = study.studyId;
        option.textContent = study.name;
        select.appendChild(option);
    }
    select.addEventListener("click", (event) => event.stopPropagation());
    select.addEventListener("change", async () => {
        const studyId = select.value;
        if (!studyId) {
            return;
        }
        const result = await window.pywebview.api.add_run_to_study(
            studyId,
            directory
        );
        if (!result.ok) {
            showOpenRunBanner(result.message);
        }
        await refreshRecentRuns();
    });
    return select;
}

/**
 * Build one run's own `<tr>` -- factored out of `refreshRecentRuns` so
 * `renderRecentRuns` can call it once per group member on every filter/
 * collapse re-render, not only on a fresh fetch. The first cell also
 * carries a bulk-selection checkbox, and a trailing "Actions" cell
 * carries an "Add to study…" pulldown -- both part of the Run/Study/
 * Experiment hierarchy design's own §10 bulk-delete idiom and §6 "Add to
 * study…" affordance.
 * @param {object} run
 * @returns {HTMLTableRowElement}
 */
function buildRunRow(run) {
    const row = document.createElement("tr");
    const configText = formatRowConfigSummary(run.configSummary);
    const statisticsText = formatRowStatistics(run.statistics);
    const runLabel = run.name ? `${run.name} (${run.runId})` : run.runId;
    for (const [value, className, isLabelCell, isRunIdCell] of [
        [runLabel, null, false, true],
        [formatEndedAt(run.endedAt), null, false, false],
        [run.label, null, true, false],
        [configText, "open-run-summary-cell", false, false],
        [statisticsText, "open-run-summary-cell", false, false],
    ]) {
        const cell = document.createElement("td");
        if (isRunIdCell) {
            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.className = "open-run-select-checkbox";
            checkbox.setAttribute("aria-label", `Select ${run.runId} for deletion`);
            checkbox.checked = selectedDirectories.has(run.directory);
            checkbox.addEventListener("click", (event) => event.stopPropagation());
            checkbox.addEventListener("change", () => {
                if (checkbox.checked) {
                    selectedDirectories.add(run.directory);
                } else {
                    selectedDirectories.delete(run.directory);
                }
                updateSelectionToolbar();
            });
            cell.appendChild(checkbox);
            cell.appendChild(document.createTextNode(` ${value}`));
        } else if (isLabelCell && run.isBatch) {
            // A batch row's own label cell gets an expand/collapse
            // toggle beside its text (design §9: "expandable to its
            // own replicate list") -- a scalar row's own label cell is
            // plain text, unchanged.
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
    const actionsCell = document.createElement("td");
    actionsCell.appendChild(buildAddToStudySelect(run.directory));
    row.appendChild(actionsCell);
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
    // Double-clicking a run row opens it directly (item 5) -- final
    // generation, no differentiation-q sweep, exactly what the "Open"
    // button does once a row is selected, without the intermediate
    // click. A batch row has no single trajectory to open this way
    // (the single-click handler above already explains why and shows
    // the banner saying so); the batch's own click already fired by
    // the time a real double-click's second `dblclick` event reaches
    // here, so this only needs to skip it, not repeat that message.
    row.addEventListener("dblclick", async () => {
        if (run.isBatch) {
            return;
        }
        await openTrajectory(run.trajectoryPath);
    });
    return row;
}

/**
 * Show an inline "are you sure?" confirmation right after `triggerButton`
 * -- this app's own established idiom for a hard-to-reverse action has
 * no native dialog anywhere in it (`window.confirm`/`window.prompt`
 * block indefinitely under this project's own hidden/headless pywebview
 * window, confirmed live rather than assumed, so neither is used
 * anywhere in this codebase). "Confirm"/"Cancel" render as two small
 * buttons in the row itself; `triggerButton` is disabled while they are
 * showing so a second click cannot queue a second confirmation.
 * @param {HTMLButtonElement} triggerButton
 * @param {string} message
 * @param {() => Promise<void>} action
 */
function confirmThenRun(triggerButton, message, action) {
    if (triggerButton._fimConfirmRow) {
        return;
    }
    triggerButton.disabled = true;
    const row = document.createElement("span");
    row.className = "open-run-inline-confirm";
    const text = document.createElement("span");
    text.textContent = message;
    row.appendChild(text);
    const confirmButton = document.createElement("button");
    confirmButton.type = "button";
    confirmButton.textContent = "Confirm";
    confirmButton.addEventListener("click", async (event) => {
        event.stopPropagation();
        row.remove();
        triggerButton._fimConfirmRow = null;
        await action();
    });
    row.appendChild(confirmButton);
    const cancelButton = document.createElement("button");
    cancelButton.type = "button";
    cancelButton.textContent = "Cancel";
    cancelButton.addEventListener("click", (event) => {
        event.stopPropagation();
        row.remove();
        triggerButton._fimConfirmRow = null;
        triggerButton.disabled = false;
    });
    row.appendChild(cancelButton);
    triggerButton.after(row);
    triggerButton._fimConfirmRow = row;
}

/**
 * Every currently-Unsorted run -- the same `claimedDirectories`/filter
 * `buildHomeGroups` already applies, exposed here for `buildAddRunTo
 * StudySelect`'s own picker. Only Unsorted runs are offered: a run
 * already in a *different* Study is left alone, matching this design's
 * own "no Run belongs to more than one Study" precedent
 * (`20260917-claude-sonnet-5-run-study-experiment-hierarchy-design.md`,
 * `selby/restricted`, §10) rather than silently creating one.
 * @returns {Array<object>}
 */
function currentlyUnsortedRuns() {
    const claimed = claimedDirectories(allStudies);
    return allRecentRuns.filter((run) => !claimed.has(run.directory));
}

/**
 * An immediately-acting "Add run…" pulldown for one Study row -- the
 * primary action on a Study's own header (Run/Study/Experiment
 * workflow-ergonomics design `20260917-claude-sonnet-5-run-study-
 * experiment-workflow-ergonomics.md`, `selby/restricted`, item 2/4):
 * assembling a Study by reaching out and claiming Unsorted runs, rather
 * than only by hunting through Unsorted for "Add to study…" one row at
 * a time. See `buildAddToExperimentSelect`'s own docstring for the
 * shared immediately-acting-pulldown idiom.
 * @param {string} studyId
 * @returns {HTMLSelectElement}
 */
function buildAddRunToStudySelect(studyId) {
    const select = document.createElement("select");
    select.className = "open-run-add-to-select";
    select.setAttribute("aria-label", "Add a run to this study");
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Add run…";
    placeholder.selected = true;
    select.appendChild(placeholder);
    for (const run of currentlyUnsortedRuns()) {
        const option = document.createElement("option");
        option.value = run.directory;
        option.textContent = run.name
            ? `${run.name} (${run.runId})`
            : `${run.runId} — ${formatEndedAt(run.endedAt)}`;
        select.appendChild(option);
    }
    select.addEventListener("click", (event) => event.stopPropagation());
    select.addEventListener("change", async () => {
        const directory = select.value;
        if (!directory) {
            return;
        }
        const result = await window.pywebview.api.add_run_to_study(
            studyId,
            directory
        );
        if (!result.ok) {
            showOpenRunBanner(result.message);
        }
        await refreshRecentRuns();
    });
    return select;
}

/**
 * Build a Study/Experiment group header's own extra action controls --
 * (for a Study only) "Add run…", the primary/first action, then Copy,
 * Delete, and (for a Study only) "Add to experiment…", appended beside
 * the toggle button in the same cell. An Experiment group gets Copy/
 * Delete only; a plain date-bucket/Unsorted group gets none of this at
 * all (`buildGroupHeaderRow` only calls this for `kind === "study"` or
 * `"experiment"`). "Add run…" leads, ahead of Copy/Delete/"Add to
 * experiment…": it is the operation a Study's own row exists for and,
 * per the same design document, the one most likely to be reached for
 * first -- putting the *Study's own parent* assignment ahead of it, as
 * this row's actions did before, answered the wrong question first.
 *
 * "Copy" needs no name prompt (see `confirmThenRun`'s own docstring for
 * why this codebase has no dialogs at all): it derives `"<name> copy"`
 * outright, non-destructive and instantly renamable/deletable again if
 * unwanted, unlike Delete.
 * @param {object} group
 * @returns {HTMLSpanElement}
 */
function buildGroupActionControls(group) {
    const container = document.createElement("span");
    container.className = "open-run-group-actions";

    if (group.kind === "study") {
        container.appendChild(buildAddRunToStudySelect(group.studyId));
    }

    const copyButton = document.createElement("button");
    copyButton.type = "button";
    copyButton.className = "open-run-group-action-button";
    copyButton.textContent = "Copy";
    copyButton.addEventListener("click", async (event) => {
        event.stopPropagation();
        const newName = `${group.label} copy`;
        const result =
            group.kind === "study"
                ? await window.pywebview.api.copy_study(group.studyId, newName)
                : await window.pywebview.api.copy_experiment(
                      group.experimentId,
                      newName
                  );
        if (!result.ok) {
            showOpenRunBanner(result.message);
            return;
        }
        await refreshRecentRuns();
    });
    container.appendChild(copyButton);

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "open-run-group-action-button";
    deleteButton.textContent = "Delete…";
    deleteButton.addEventListener("click", (event) => {
        event.stopPropagation();
        confirmThenRun(
            deleteButton,
            group.kind === "study"
                ? `Delete "${group.label}" and its ${group.runCount} run(s)?`
                : `Delete "${group.label}" and everything inside it?`,
            async () => {
                const result =
                    group.kind === "study"
                        ? await window.pywebview.api.delete_study(group.studyId)
                        : await window.pywebview.api.delete_experiment(
                              group.experimentId
                          );
                if (!result.ok) {
                    showOpenRunBanner(result.message);
                    return;
                }
                await refreshRecentRuns();
            }
        );
    });
    container.appendChild(deleteButton);

    if (group.kind === "study") {
        container.appendChild(buildAddToExperimentSelect(group.studyId));
    }
    return container;
}

/**
 * An immediately-acting "Add to experiment…" pulldown for one Study row
 * -- the identical `home-example-select` idiom (this same file, near the
 * top): picking an option performs the action and resets to its own
 * placeholder, so the control always reads as an action, never as
 * "currently showing experiment X".
 * @param {string} studyId
 * @returns {HTMLSelectElement}
 */
function buildAddToExperimentSelect(studyId) {
    const select = document.createElement("select");
    select.className = "open-run-add-to-select";
    select.setAttribute("aria-label", "Add this study to an experiment");
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Add to experiment…";
    placeholder.selected = true;
    select.appendChild(placeholder);
    for (const experiment of allExperiments) {
        const option = document.createElement("option");
        option.value = experiment.experimentId;
        option.textContent = experiment.name;
        select.appendChild(option);
    }
    select.addEventListener("click", (event) => event.stopPropagation());
    select.addEventListener("change", async () => {
        const experimentId = select.value;
        if (!experimentId) {
            return;
        }
        const result = await window.pywebview.api.add_study_to_experiment(
            experimentId,
            studyId
        );
        if (!result.ok) {
            showOpenRunBanner(result.message);
        }
        await refreshRecentRuns();
    });
    return select;
}

/**
 * Build one group's own header row -- a full-width toggle button naming
 * the group and its member count (design's own answer to "a
 * fantastically long results scroll": collapsing a group removes its
 * rows from the DOM entirely, not merely hiding them, so a large
 * `results/` directory never pays for rendering rows nobody asked to
 * see). Collapsed state survives across visits to this screen for the
 * life of the window (`collapsedGroupIds`, seeded per-group the first
 * time each group id is ever seen by `ensureGroupDefaults`).
 *
 * Expanding a Study group (`kind === "study"`) lazily fetches its own
 * Run list the first time, exactly the way `toggleBatchRow` already
 * fetches one batch's own replicate list one level down -- an
 * Experiment group needs no fetch of its own at all (`experimentGroup`
 * already built its `subgroups` from data `refreshRecentRuns` already
 * has in hand).
 * @param {object} group
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
    cell.colSpan = 6;
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "open-run-group-toggle";
    const collapsed = collapsedGroupIds.has(group.id);
    toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    toggle.textContent = `${collapsed ? "▸" : "▾"} ${group.label} (${group.countLabel})`;
    toggle.addEventListener("click", async () => {
        const expanding = collapsedGroupIds.has(group.id);
        if (
            expanding &&
            group.kind === "study" &&
            window.__fimStudyRunsCache[group.studyId] === undefined
        ) {
            const result = await window.pywebview.api.get_study_run_summary(
                group.studyId
            );
            window.__fimStudyRunsCache[group.studyId] = result.ok
                ? result.runs
                : [];
        }
        if (expanding) {
            collapsedGroupIds.delete(group.id);
        } else {
            collapsedGroupIds.add(group.id);
        }
        renderRecentRuns();
    });
    cell.appendChild(toggle);
    if (group.kind === "study" || group.kind === "experiment") {
        cell.appendChild(buildGroupActionControls(group));
    }
    row.appendChild(cell);
    return row;
}

/**
 * Append one group's own header row and, if expanded, its own content
 * -- recurses into `subgroups` for a parent group like `"Earlier"` or an
 * Experiment, so a leaf group (plain `runs`) and a parent group (nested
 * `subgroups`) render through the identical function, one level deeper
 * each time, rather than `renderRecentRuns` needing to know the tree's
 * own depth or shape up front. A Study group not yet expanded has `runs
 * === null` here, but that is never reached: this function returns
 * before touching `group.runs` for any still-collapsed group, and
 * `buildGroupHeaderRow`'s own toggle handler always populates it before
 * ever un-collapsing one.
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
 * Re-render the recent-runs table from `allRecentRuns`/`allStudies`/
 * `allExperiments` -- applies the current filter text and group-collapse
 * state, but never re-fetches (`refreshRecentRuns` owns the one
 * filesystem read per visit).
 */
function renderRecentRuns() {
    recentRunsBody.replaceChildren();
    const filterText = recentRunsFilterInput.value.trim();
    const groups = buildHomeGroups(
        allRecentRuns,
        allStudies,
        allExperiments,
        filterText
    );
    // Decide "closed by default" for any group this launch has never
    // seen before *first*, so the very first render after a fresh fetch
    // already reflects that default rather than briefly flashing open.
    ensureGroupDefaults(groups);
    for (const group of groups) {
        renderGroup(group);
    }
    const totalRuns = allRecentRuns.length;
    const visibleRuns = groups.reduce((total, group) => total + group.runCount, 0);
    recentRunsCountLabel.textContent =
        visibleRuns === totalRuns
            ? `${totalRuns} run${totalRuns === 1 ? "" : "s"}`
            : `${visibleRuns} of ${totalRuns} runs`;
    updateSelectionToolbar();
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
    // A fresh visit never shows a stale replicate/Study-run list fetched
    // for a *previous* visit's own now-discarded rows -- both caches are
    // keyed by id, not by row, so they would otherwise survive the next
    // `renderRecentRuns()` untouched.
    window.__fimBatchReplicateCache = {};
    window.__fimStudyRunsCache = {};
    // `collapsedGroupIds` is intentionally NOT reset here -- a group the
    // user collapsed or expanded on a previous visit should stay that
    // way (item 2b); `ensureGroupDefaults` (called from
    // `renderRecentRuns`) is what seeds a *new* group's default state
    // (closed) the first time this window ever sees it. A selection
    // (`selectedDirectories`) IS reset -- a bulk delete or a fresh visit
    // should never carry a stale selection referencing a Run that may no
    // longer even be listed.
    selectedDirectories.clear();
    recentRunsFilterInput.value = "";
    [allRecentRuns, allStudies, allExperiments] = await Promise.all([
        window.pywebview.api.list_home_runs(),
        window.pywebview.api.list_studies(),
        window.pywebview.api.list_experiments(),
    ]);
    // Clearing `__fimStudyRunsCache` just above means an *already
    // expanded* Study (its own group id already removed from
    // `collapsedGroupIds` by an earlier visit's own toggle click) would
    // otherwise reach `renderGroup` with `group.runs === null` -- that
    // function only ever populates a null `.runs` from inside a toggle
    // click, never from a plain re-render, so this refetches every
    // currently-expanded Study's own run list up front, before
    // `renderRecentRuns` ever runs, exactly once per Study actually
    // expanded (not every Study that merely exists).
    await Promise.all(
        allStudies
            .filter((study) => !collapsedGroupIds.has(`study:${study.studyId}`))
            .map(async (study) => {
                const result = await window.pywebview.api.get_study_run_summary(
                    study.studyId
                );
                window.__fimStudyRunsCache[study.studyId] = result.ok
                    ? result.runs
                    : [];
            })
    );
    renderRecentRuns();
    window.__fimOpenRunRecentRunsLoaded = true;
}

recentRunsFilterInput.addEventListener("input", () => {
    renderRecentRuns();
});

/**
 * Reflect `selectedDirectories`'s own current size in the bulk-selection
 * toolbar -- the "Delete selected" button's own enabled state and the
 * live count beside it. Called after every re-render and every checkbox
 * toggle, so it never drifts from the actual selection.
 */
function updateSelectionToolbar() {
    // Skip touching the button while its own inline confirmation is
    // showing (`confirmThenRun`) -- an unrelated re-render (a filter
    // keystroke) must not silently re-enable it or overwrite its label
    // out from under a confirmation the user has not yet acted on.
    if (deleteSelectedButton._fimConfirmRow) {
        return;
    }
    const count = selectedDirectories.size;
    deleteSelectedButton.disabled = count === 0;
    deleteSelectedButton.textContent =
        count === 0 ? "Delete selected" : `Delete selected (${count})`;
    selectionCountLabel.textContent = count === 0 ? "" : `${count} selected`;
}

// Bulk "Select/Delete/Delete all" idiom (design doc §10, resolved
// directly by the project owner: thousands of Unsorted runs could not
// realistically be deleted one at a time through the GUI). "Select all"
// selects every currently *loaded* run (`allRecentRuns`), not merely
// whatever rows happen to be in the DOM right now -- a collapsed group's
// own runs are just as selectable this way as an expanded one's,
// composing into "delete every run" with no separate button needed for
// that case specifically.
selectAllButton.addEventListener("click", () => {
    for (const run of allRecentRuns) {
        selectedDirectories.add(run.directory);
    }
    updateSelectionToolbar();
    renderRecentRuns();
});

clearSelectionButton.addEventListener("click", () => {
    selectedDirectories.clear();
    renderRecentRuns();
});

deleteSelectedButton.addEventListener("click", () => {
    const directories = Array.from(selectedDirectories);
    if (directories.length === 0) {
        return;
    }
    confirmThenRun(deleteSelectedButton, `Delete ${directories.length} run(s)?`, async () => {
        const result = await window.pywebview.api.delete_runs(directories);
        if (!result.ok) {
            showOpenRunBanner(result.message);
            return;
        }
        await refreshRecentRuns();
    });
});

/**
 * Read the visible name/description fields on the "New study"/"New
 * experiment" home card, create it, clear those fields, and refresh
 * Home so it appears immediately. Plain, always-visible text inputs on
 * the card itself, not a dialog or a new modal (see `confirmThenRun`'s
 * own docstring for why this codebase has no dialogs at all; every
 * existing modal here is reserved for a genuinely multi-field form,
 * which a bare name and one-line description is not).
 * @param {"study" | "experiment"} kind
 */
async function createGroup(kind) {
    const nameInput =
        kind === "study" ? homeNewStudyNameInput : homeNewExperimentNameInput;
    const descriptionInput =
        kind === "study"
            ? homeNewStudyDescriptionInput
            : homeNewExperimentDescriptionInput;
    const name = nameInput.value.trim();
    if (!name) {
        showOpenRunBanner(`a ${kind} needs a name`);
        return;
    }
    const description = descriptionInput.value.trim();
    const result =
        kind === "study"
            ? await window.pywebview.api.create_study(name, description)
            : await window.pywebview.api.create_experiment(name, description);
    if (!result.ok) {
        showOpenRunBanner(result.message);
        return;
    }
    showOpenRunBanner("");
    nameInput.value = "";
    descriptionInput.value = "";
    await refreshRecentRuns();
}

homeNewStudyButton.addEventListener("click", () => {
    createGroup("study");
});

homeNewExperimentButton.addEventListener("click", () => {
    createGroup("experiment");
});

browseButton.addEventListener("click", async () => {
    const result = await window.pywebview.api.browse_for_trajectory();
    if (!result.ok) {
        return;
    }
    showOpenRunBanner("");
    setSelectedTrajectory(result.path);
});

/**
 * Open `trajectoryPath` at its final generation, no differentiation-q
 * sweep, landing on the unified run view's own `completed` state --
 * the one operation both the "Open" button and a run row's own
 * double-click (item 5) reduce to, now that choosing a different
 * generation or a sweep happens on the Results card itself, after
 * opening (item 6, `run-view-completed.js`'s own `results-reanalyze-
 * button`), not before. Reports failure via this screen's own banner;
 * on success, resets the trajectory legend's own display-only
 * visibility toggle before entering `completed` (design §6.2's legend-
 * toggle; `run-view-completed.js`'s own `resetTrajectoryLegendVisibility`
 * doc comment names both points this happens at).
 * @param {string} trajectoryPath
 * @returns {Promise<void>}
 */
async function openTrajectory(trajectoryPath) {
    const result = await window.pywebview.api.open_run({ trajectoryPath });
    if (!result.ok) {
        showOpenRunBanner(result.message);
        return;
    }
    showOpenRunBanner("");
    window.fim.resetTrajectoryLegendVisibility();
    window.fim.enterCompletedState(result, false);
}

openButton.addEventListener("click", async () => {
    if (selectedTrajectoryPath === null) {
        showOpenRunBanner("no trajectory selected");
        return;
    }
    await openTrajectory(selectedTrajectoryPath);
});

openRunBackButton.addEventListener("click", () => {
    window.fim.navigateBack();
});

window.fim.showOpenRunScreen = async function showOpenRunScreen() {
    showOpenRunBanner("");
    setSelectedTrajectory(null);
    window.fim.showScreen("screen-open-run");
    // Still fire-and-forget from every existing (click-driven) caller's
    // own point of view -- the screen switch above already happened
    // synchronously, before either `await` below ever suspends this
    // function, so a caller that does not await this call (every one
    // today) sees no change in when the screen itself appears. Awaiting
    // both calls here, rather than leaving them fully detached the way
    // this function used to, only matters to a caller that *does* await
    // it -- `run-view-initial.js`'s own launch sequence, which needs
    // both real bridge calls fully settled before flipping `window.
    // __fimRunViewReady`, the same flag every GUI test's own teardown
    // gates on. Without that, a launch-triggered `list_home_runs()` can
    // still be in flight when a test destroys its window moments later
    // (`test/gui/conftest.py`'s own module docstring records this exact
    // failure shape at length for this function's original, single
    // click-driven call site -- a second, always-fired call site at
    // bootstrap reintroduces it for every test, not just Home's own,
    // unless this function's own caller can wait for it).
    await Promise.all([refreshRecentRuns(), refreshHomeExampleOptions()]);
};
