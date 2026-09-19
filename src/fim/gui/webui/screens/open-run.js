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
const homeNewExperimentButton = document.getElementById(
    "home-new-experiment-button"
);
const openRunTable = document.getElementById("open-run-table");
const toggleSelectButton = document.getElementById(
    "open-run-toggle-select-button"
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
// A Run's own directory/a Study's own id/an Experiment's own id, not a
// DOM row -- so "Select all" (below) can reach every loaded item
// regardless of which groups are currently collapsed, and a selection
// survives a re-render (a filter keystroke) untouched. Three separate
// sets, one per kind, rather than one tagged collection -- `Api.
// delete_selected`'s own three-phase (experiment, then study, then
// run) dispatch reads directly off these without needing to first sort
// a mixed collection by kind (`20260918-claude-sonnet-5-home-tree-
// reorg-design.md`, `selby/restricted`, §5).
const selectedRunDirectories = new Set();
const selectedStudyIds = new Set();
const selectedExperimentIds = new Set();
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
 * Keep only the most recent run for each distinct `runId` -- `run.
 * runId` is a deterministic hash of the run's own configuration
 * (`fim.engine.deterministic_run_id`), not a per-invocation random id,
 * so running the identical configuration more than once produces
 * several real, distinct run directories that all share one `runId`.
 * Showing every one of those inside the same Study's own expanded row
 * is just noise -- reported live: two rows reading the exact same
 * `run-<hash>` label, a few dozen seconds apart, both under one
 * Study's own "Today" bucket. Compares `endedAt` directly (ISO-8601,
 * `Z`-suffixed, so a plain string compare already sorts chronologically)
 * rather than assuming `runs` arrives in any particular order --
 * `Api.get_study_run_summary`'s own list follows `study_run_
 * directories`'s storage order, not necessarily newest-first the way
 * `Api.list_home_runs`'s own already is.
 * @param {Array<object>} runs
 * @returns {Array<object>}
 */
function dedupeMostRecentPerRunId(runs) {
    const mostRecentById = new Map();
    for (const run of runs) {
        const existing = mostRecentById.get(run.runId);
        if (!existing || run.endedAt > existing.endedAt) {
            mostRecentById.set(run.runId, run);
        }
    }
    return runs.filter((run) => mostRecentById.get(run.runId) === run);
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
 *
 * `idPrefix` namespaces every id this call produces (including the
 * nested per-date ids under "Earlier") -- needed once this function
 * became callable more than once per render, one call per expanded
 * Study (`studyGroup`, above): `collapsedGroupIds`/`knownGroupIds` are
 * flat sets keyed by plain string id, so two different Studies each
 * showing their own "Today" bucket would otherwise collide under the
 * identical bare id `"Today"`, collapsing one when the botanist meant
 * to collapse only the other. Empty (the historical, single-caller
 * default) when omitted, matching this function's own original
 * behavior exactly for its one remaining bare caller, if any.
 * @param {Array<object>} runs
 * @param {string} [idPrefix]
 * @returns {Array<{id: string, label: string, runCount: number,
 *     countLabel: string, runs?: Array<object>,
 *     subgroups?: Array<{id: string, label: string, runs: Array<object>,
 *         runCount: number, countLabel: string}>}>}
 */
function groupRecentRuns(runs, idPrefix = "") {
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
                    id: `${idPrefix}${bucketId}`,
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
                    id: `${idPrefix}Earlier:${dateLabel}`,
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
                id: `${idPrefix}${bucketId}`,
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
 * Whether `name` matches a filter's free text -- a plain, case-
 * insensitive substring match against a Study/Experiment's own name.
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
 *
 * Once fetched, the flat cached list is wrapped into the identical
 * Today/Yesterday/Earlier(-by-date) `subgroups` shape `groupRecentRuns`
 * already built for the now-removed "Unsorted" bucket (`20260918-
 * claude-sonnet-5-home-tree-reorg-design.md`, `selby/restricted`, §6):
 * every unattached run now lands in the always-present default Study
 * (§1/§2 of that same document) rather than a separate, larger
 * "Unsorted" group, so a real Study's own row needs this same
 * chronological browsing aid, not only a flat list -- a real
 * regression that document flags explicitly if left unaddressed. The
 * *not-yet-fetched* case is untouched: still a bare `runs: null`, never
 * reached by `renderGroup` for a still-collapsed group either way.
 * @param {{studyId: string, name: string, runCount: number,
 *     runDirectories: Array<string>}} study
 * @returns {object}
 */
function studyGroup(study) {
    if (!window.__fimStudyRunsCache) {
        window.__fimStudyRunsCache = {};
    }
    const cachedRuns = window.__fimStudyRunsCache[study.studyId];
    const base = {
        id: `study:${study.studyId}`,
        label: study.name,
        kind: "study",
        studyId: study.studyId,
        runCount: study.runCount,
        countLabel: `${study.runCount} run${study.runCount === 1 ? "" : "s"}`,
        runDirectories: study.runDirectories,
    };
    if (cachedRuns === undefined) {
        return { ...base, runs: null };
    }
    return {
        ...base,
        subgroups: groupRecentRuns(cachedRuns, `${base.id}:`),
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
 * Count the distinct run directories reachable from `groups` -- a run
 * added to more than one Study (`add_run_to_study` only ever appends,
 * never detaches from a prior Study, so this is a real, reachable data
 * shape, not a hypothetical one) is counted once here, never once per
 * Study it happens to belong to.
 *
 * A naive `groups.reduce((total, group) => total + group.runCount, 0)`
 * -- this function's own former shape -- double(-or-more)-counts any
 * such run instead: confirmed live on a checkout with heavy manual
 * "Add to study…"/"Add to experiment…" use, where the very same two
 * runs had been added to several different Studies, producing a
 * nonsensical "722 of 2 runs" label with no filter text even typed
 * (`visibleRuns` and `totalRuns` are supposed to be equal whenever
 * every group matches the filter trivially, which an empty filter
 * always does).
 * @param {Array<object>} groups
 * @returns {number}
 */
function distinctRunCount(groups) {
    const seen = new Set();
    const visit = (group) => {
        if (group.kind === "study") {
            for (const directory of group.runDirectories) {
                seen.add(directory);
            }
        } else if (group.kind === "experiment") {
            group.subgroups.forEach(visit);
        }
    };
    groups.forEach(visit);
    return seen.size;
}

/**
 * Build Home's own top-level tree: every Experiment, then every Study
 * not inside one -- no "Unsorted" bucket, no floating run, ever
 * (`20260918-claude-sonnet-5-home-tree-reorg-design.md`, `selby/
 * restricted`, §1/§2/§7): the always-present default Experiment/Study
 * (`fim.persistence.groups.ensure_default_study`) is an ordinary entry
 * in these same two lists, ordered first because it is, by
 * construction, the oldest one whenever it exists at all (`Api.list_
 * experiments`/`list_studies`'s own oldest-first ordering) -- this
 * function needs no special-casing to put it there. `runs` (every run,
 * unconditionally, `Api.list_home_runs`) is no longer read here at
 * all: every run now belongs to some real Study, so there is nothing
 * left for this function to place outside one.
 *
 * A filter's free text matches a Study/Experiment by name (`
 * nameMatchesFilter`); an Experiment matching by name keeps every one
 * of its own Studies, not just ones that would themselves match. A
 * Study's own run-level filtering (matching a run's own N/d/m/etc.)
 * happens once it is actually expanded, unaffected by this function.
 * @param {Array<object>} studies
 * @param {Array<object>} experiments
 * @param {string} filterText
 * @returns {Array<object>}
 */
function buildHomeGroups(studies, experiments, filterText) {
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
    return [...experimentGroups, ...standaloneStudyGroups];
}

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
            checkbox.checked = selectedRunDirectories.has(run.directory);
            checkbox.addEventListener("click", (event) => event.stopPropagation());
            checkbox.addEventListener("change", () => {
                if (checkbox.checked) {
                    selectedRunDirectories.add(run.directory);
                } else {
                    selectedRunDirectories.delete(run.directory);
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
 * Show an inline name-prompt right after `triggerButton` -- the input-
 * taking counterpart to `confirmThenRun`, above, for an action that
 * needs one short string rather than only a yes/no. Reused by `build
 * CreateStudyButton`'s own "Create study…", the row-level counterpart
 * to Home's own former "New study" card (`20260918-claude-sonnet-5-
 * home-tree-reorg-design.md`, `selby/restricted`, §4).
 * @param {HTMLButtonElement} triggerButton
 * @param {string} placeholder
 * @param {(name: string) => Promise<void>} action
 */
function promptForNameThenRun(triggerButton, placeholder, action) {
    if (triggerButton._fimPromptRow) {
        return;
    }
    triggerButton.disabled = true;
    const row = document.createElement("span");
    row.className = "open-run-inline-confirm";
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = placeholder;
    input.setAttribute("aria-label", placeholder);
    input.addEventListener("click", (event) => event.stopPropagation());
    row.appendChild(input);
    const createButton = document.createElement("button");
    createButton.type = "button";
    createButton.textContent = "Create";
    createButton.addEventListener("click", async (event) => {
        event.stopPropagation();
        const name = input.value.trim();
        if (!name) {
            input.focus();
            return;
        }
        row.remove();
        triggerButton._fimPromptRow = null;
        triggerButton.disabled = false;
        await action(name);
    });
    row.appendChild(createButton);
    const cancelButton = document.createElement("button");
    cancelButton.type = "button";
    cancelButton.textContent = "Cancel";
    cancelButton.addEventListener("click", (event) => {
        event.stopPropagation();
        row.remove();
        triggerButton._fimPromptRow = null;
        triggerButton.disabled = false;
    });
    row.appendChild(cancelButton);
    triggerButton.after(row);
    triggerButton._fimPromptRow = row;
    input.focus();
}

// "Create experiment…" has no row to hang off of -- an Experiment is
// the tree's own outermost level -- so it lives beside the filter input
// instead, the one page-level creation action `.home-cards`' own
// removal (§7) still needs: every other card's action moved onto a
// row (§4), but there is no row a *new* Experiment could belong to
// before it exists. The always-present default Experiment (§1) means
// this is never required for a first "do a run," only for a botanist
// who deliberately wants a second one.
homeNewExperimentButton.addEventListener("click", (event) => {
    event.stopPropagation();
    promptForNameThenRun(homeNewExperimentButton, "Experiment name", async (name) => {
        const created = await window.pywebview.api.create_experiment(name);
        if (!created.ok) {
            showOpenRunBanner(created.message);
            return;
        }
        await refreshRecentRuns();
    });
});

/**
 * "Create study…" on an Experiment row -- one user action instead of
 * today's two disconnected ones (create a Study anywhere, then a
 * separate "Add to experiment…" picker): `Api.create_study` followed
 * immediately by `Api.add_study_to_experiment`, both against this
 * row's own Experiment (`20260918-claude-sonnet-5-home-tree-reorg-
 * design.md`, `selby/restricted`, §4).
 * @param {{experimentId: string}} group
 * @returns {HTMLButtonElement}
 */
function buildCreateStudyButton(group) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "open-run-group-action-button";
    button.textContent = "Create study…";
    button.addEventListener("click", (event) => {
        event.stopPropagation();
        promptForNameThenRun(button, "Study name", async (name) => {
            const created = await window.pywebview.api.create_study(name);
            if (!created.ok) {
                showOpenRunBanner(created.message);
                return;
            }
            const added = await window.pywebview.api.add_study_to_experiment(
                group.experimentId,
                created.studyId
            );
            if (!added.ok) {
                showOpenRunBanner(added.message);
                return;
            }
            await refreshRecentRuns();
        });
    });
    return button;
}

/**
 * "Create run…" on a Study row -- navigates to Configure with this
 * Study hard-selected on `run-study-select` (`20260918-claude-sonnet-
 * 5-home-tree-reorg-design.md`, `selby/restricted`, §4): unlike the
 * Explore handoff's own "New study…" soft nudge, a botanist reaching
 * Configure this way has already explicitly chosen this Study by
 * clicking its own row, so the selection is set outright, not merely
 * pre-selected -- still fully changeable afterward, never locking the
 * botanist in.
 * @param {{studyId: string}} group
 * @returns {HTMLButtonElement}
 */
function buildCreateRunButton(group) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "open-run-group-action-button primary-action";
    button.textContent = "Create run…";
    button.addEventListener("click", async (event) => {
        event.stopPropagation();
        await window.fim.showConfigureScreen();
        window.fim.selectStudyForNewRun(group.studyId);
    });
    return button;
}

/**
 * "Re-run every configuration in this Study" -- the concrete, buildable-
 * today answer to "the whole Study, for completeness" (`20260918-claude-
 * sonnet-5-explore-to-study-run-handoff-design.md`, `selby/restricted`,
 * §4/§8): re-submits every member run's own already-saved parameters as
 * a brand-new run, attaching each result back to this same Study. Draws
 * a fresh seed by default, or reuses each run's own original one, per
 * the botanist's own Settings choice (§5) -- this button takes no
 * per-click choice of its own. Disabled while empty (nothing to
 * re-run) or already running (`Api.rerun_study` blocks until every
 * configuration has been attempted -- no live, per-configuration
 * progress push exists yet, §4's own documented scope-narrowing for
 * this first version).
 * @param {object} group
 * @returns {HTMLButtonElement}
 */
function buildRerunStudyButton(group) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "open-run-group-action-button";
    button.textContent = "Re-run all…";
    button.disabled = group.runCount === 0;
    button.addEventListener("click", async (event) => {
        event.stopPropagation();
        button.disabled = true;
        const originalText = button.textContent;
        button.textContent = "Re-running…";
        const result = await window.pywebview.api.rerun_study(group.studyId);
        button.textContent = originalText;
        if (!result.ok) {
            showOpenRunBanner(result.message);
            button.disabled = group.runCount === 0;
            return;
        }
        if (result.failed > 0) {
            showOpenRunBanner(
                `Re-ran ${result.completed} of ${result.completed + result.failed} `
                    + "configuration(s); the rest failed or no longer validate."
            );
        } else {
            showOpenRunBanner("");
        }
        await refreshRecentRuns();
    });
    return button;
}

/**
 * Build a Study/Experiment group header's own extra action controls
 * (`20260918-claude-sonnet-5-home-tree-reorg-design.md`, `selby/
 * restricted`, §4/§5): a Study row leads with "Create run…" (navigates
 * to Configure, this Study hard-selected); an Experiment row leads
 * with "Create study…" (inline name prompt, nests the new Study in
 * this Experiment immediately) -- each the primary, first-reached-for
 * action for its own row. Then Copy, "Re-run all…" (Study only), and
 * (Study only) "Add to experiment…". No standalone "Delete…" button
 * for either kind anymore -- deletion is the header row's own
 * checkbox (`buildGroupSelectCheckbox`, `buildGroupHeaderRow`) plus the
 * one shared "Delete selected" toolbar action, reviewed as a whole
 * before anything is actually removed, the same idiom a Run row's own
 * checkbox already established (§5: "deletion... should not... happen
 * accidentally"). A plain date-bucket group gets none of this at all
 * (`buildGroupHeaderRow` only calls this for `kind === "study"` or
 * `"experiment"`).
 *
 * "Copy" needs no name prompt (see `confirmThenRun`'s own docstring for
 * why this codebase has no dialogs at all): it derives `"<name> copy"`
 * outright, non-destructive and instantly renamable/deletable again if
 * unwanted, unlike deletion.
 * @param {object} group
 * @returns {HTMLSpanElement}
 */
function buildGroupActionControls(group) {
    const container = document.createElement("span");
    container.className = "open-run-group-actions";

    if (group.kind === "study") {
        container.appendChild(buildCreateRunButton(group));
    }
    if (group.kind === "experiment") {
        container.appendChild(buildCreateStudyButton(group));
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

    if (group.kind === "study") {
        container.appendChild(buildRerunStudyButton(group));
    }

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
/**
 * A Study/Experiment group header's own selection checkbox -- the
 * identical bulk Select/Select all/Delete idiom a Run row's own
 * checkbox already established, generalized to these two kinds
 * (`20260918-claude-sonnet-5-home-tree-reorg-design.md`, `selby/
 * restricted`, §5), replacing each row's own former standalone
 * "Delete…" button in favor of one selection, reviewed as a whole,
 * before the one "Delete selected" action that actually removes
 * anything.
 * @param {{kind: "study" | "experiment", studyId?: string,
 *     experimentId?: string, label: string}} group
 * @returns {HTMLInputElement}
 */
function buildGroupSelectCheckbox(group) {
    const selected = group.kind === "study" ? selectedStudyIds : selectedExperimentIds;
    const id = group.kind === "study" ? group.studyId : group.experimentId;
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "open-run-select-checkbox";
    checkbox.setAttribute("aria-label", `Select ${group.label} for deletion`);
    checkbox.checked = selected.has(id);
    checkbox.addEventListener("click", (event) => event.stopPropagation());
    checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
            selected.add(id);
        } else {
            selected.delete(id);
        }
        updateSelectionToolbar();
    });
    return checkbox;
}

function buildGroupHeaderRow(group, nested = false) {
    const row = document.createElement("tr");
    row.className = "open-run-group-header";
    if (nested) {
        row.classList.add("open-run-group-header-nested");
    }
    const cell = document.createElement("td");
    cell.colSpan = 6;
    // A `<td>` itself as the flex container (an earlier version of this
    // function) computed `display: flex` correctly but did not actually
    // lay its children out that way under this project's own bundled
    // WebKit -- a real, confirmed rendering quirk, not merely a guess --
    // so the flex row lives on a plain wrapper `<span>` nested inside
    // the cell instead, the far more common (and reliably supported)
    // "flex container inside a table cell" shape.
    const cellRow = document.createElement("span");
    cellRow.className = "open-run-group-header-cell";
    cell.appendChild(cellRow);
    if (group.kind === "study" || group.kind === "experiment") {
        cellRow.appendChild(buildGroupSelectCheckbox(group));
    }
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
                ? dedupeMostRecentPerRunId(result.runs)
                : [];
        }
        if (expanding) {
            collapsedGroupIds.delete(group.id);
        } else {
            collapsedGroupIds.add(group.id);
        }
        renderRecentRuns();
    });
    cellRow.appendChild(toggle);
    if (group.kind === "study" || group.kind === "experiment") {
        cellRow.appendChild(buildGroupActionControls(group));
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
    const groups = buildHomeGroups(allStudies, allExperiments, filterText);
    // Decide "closed by default" for any group this launch has never
    // seen before *first*, so the very first render after a fresh fetch
    // already reflects that default rather than briefly flashing open.
    ensureGroupDefaults(groups);
    for (const group of groups) {
        renderGroup(group);
    }
    const totalRuns = allRecentRuns.length;
    const visibleRuns = distinctRunCount(groups);
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
    // (`selectedRunDirectories`/`selectedStudyIds`/`selectedExperiment
    // Ids`) IS reset -- a bulk delete or a fresh visit should never
    // carry a stale selection referencing an item that may no longer
    // even be listed. Select mode (checkbox visibility) resets with it,
    // for the same reason: nothing selected means no reason to keep
    // showing the checkboxes a fresh visit never asked for.
    selectedRunDirectories.clear();
    selectedStudyIds.clear();
    selectedExperimentIds.clear();
    setSelectMode(false);
    recentRunsFilterInput.value = "";
    [allRecentRuns, allStudies, allExperiments] = await Promise.all([
        window.pywebview.api.list_home_runs(),
        window.pywebview.api.list_studies(),
        window.pywebview.api.list_experiments(),
    ]);
    // A checkout that has never run anything, and never created a
    // Study/Experiment by hand, has nothing to click "Create run…" on
    // yet -- `ensure_default_study` is lazy on the Python side (design
    // doc §1: never called eagerly at launch), so this is the one place
    // that materializes it for real, exactly when the tree would
    // otherwise render completely empty. Sequential, not folded into
    // the `Promise.all` above, so this never races the initial fetch:
    // both lists are already known empty before creating anything.
    if (allStudies.length === 0 && allExperiments.length === 0) {
        await window.pywebview.api.ensure_default_study();
        [allStudies, allExperiments] = await Promise.all([
            window.pywebview.api.list_studies(),
            window.pywebview.api.list_experiments(),
        ]);
    }
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
 * Total selected count across all three kinds -- Runs, Studies, and
 * Experiments (`20260918-claude-sonnet-5-home-tree-reorg-design.md`,
 * `selby/restricted`, §5). Deliberately a flat sum, not inflated by
 * counting a selected Study/Experiment's own member Runs a second time
 * even if any of them also happen to be individually selected --
 * `buildDeleteSelectedMessage`, below, is what actually spells out the
 * full cascade a botanist is about to trigger, so this count alone
 * never has to.
 * @returns {number}
 */
function totalSelectedCount() {
    return (
        selectedRunDirectories.size + selectedStudyIds.size + selectedExperimentIds.size
    );
}

/**
 * Reflect the current selection's own total size in the bulk-selection
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
    const count = totalSelectedCount();
    deleteSelectedButton.disabled = count === 0;
    deleteSelectedButton.textContent =
        count === 0 ? "Delete selected" : `Delete selected (${count})`;
    selectionCountLabel.textContent = count === 0 ? "" : `${count} selected`;
}

/**
 * Spell out exactly what "Delete selected" is about to remove -- never
 * understating a Study/Experiment's own cascade, the reason this
 * codebase never offers a bare row-level "Delete…" button anymore
 * (`20260918-claude-sonnet-5-home-tree-reorg-design.md`, `selby/
 * restricted`, §5): a botanist who selected two Experiments and one
 * bare Run sees exactly that named, not a single opaque number.
 * @returns {string}
 */
function buildDeleteSelectedMessage() {
    const parts = [];
    if (selectedExperimentIds.size > 0) {
        parts.push(
            `${selectedExperimentIds.size} experiment(s) (and everything inside them)`
        );
    }
    if (selectedStudyIds.size > 0) {
        parts.push(`${selectedStudyIds.size} stud(y/ies) (and its/their own runs)`);
    }
    if (selectedRunDirectories.size > 0) {
        parts.push(`${selectedRunDirectories.size} individual run(s)`);
    }
    return `Delete ${parts.join(", ")}?`;
}

// Bulk "Select/Delete/Delete all" idiom (design doc §10, resolved
// directly by the project owner: thousands of Unsorted runs could not
// realistically be deleted one at a time through the GUI; generalized
// to Study/Experiment rows too, `20260918-claude-sonnet-5-home-tree-
// reorg-design.md`, `selby/restricted`, §5).
//
// Checkboxes are hidden by default (`#open-run-table`'s own
// `open-run-selecting` class, `app.css`) -- a checkbox on every row is
// noise on a screen a botanist mostly uses to look, not to bulk-
// delete. "Select" toggles that visibility alone; it never touches the
// underlying selection state itself, so toggling it off and back on
// leaves whatever was already selected exactly as it was.
function setSelectMode(selecting) {
    openRunTable.classList.toggle("open-run-selecting", selecting);
    toggleSelectButton.setAttribute("aria-pressed", String(selecting));
}

toggleSelectButton.addEventListener("click", () => {
    setSelectMode(!openRunTable.classList.contains("open-run-selecting"));
});

// "Select all" selects every currently *loaded* Run/Study/Experiment
// (`allRecentRuns`/`allStudies`/`allExperiments`), not merely whatever
// rows happen to be in the DOM right now -- a collapsed group's own
// runs are just as selectable this way as an expanded one's, composing
// into "delete everything" with no separate button needed for that
// case specifically. Also turns select mode on: selecting everything
// while the checkboxes stay hidden would otherwise look like nothing
// happened at all.
selectAllButton.addEventListener("click", () => {
    for (const run of allRecentRuns) {
        selectedRunDirectories.add(run.directory);
    }
    for (const study of allStudies) {
        selectedStudyIds.add(study.studyId);
    }
    for (const experiment of allExperiments) {
        selectedExperimentIds.add(experiment.experimentId);
    }
    setSelectMode(true);
    updateSelectionToolbar();
    renderRecentRuns();
});

clearSelectionButton.addEventListener("click", () => {
    selectedRunDirectories.clear();
    selectedStudyIds.clear();
    selectedExperimentIds.clear();
    renderRecentRuns();
});

deleteSelectedButton.addEventListener("click", () => {
    if (totalSelectedCount() === 0) {
        return;
    }
    confirmThenRun(deleteSelectedButton, buildDeleteSelectedMessage(), async () => {
        const items = [
            ...Array.from(selectedExperimentIds, (experimentId) => ({
                kind: "experiment",
                experimentId,
            })),
            ...Array.from(selectedStudyIds, (studyId) => ({ kind: "study", studyId })),
            ...Array.from(selectedRunDirectories, (directory) => ({
                kind: "run",
                directory,
            })),
        ];
        const result = await window.pywebview.api.delete_selected(items);
        if (!result.ok) {
            showOpenRunBanner(result.message);
            return;
        }
        await refreshRecentRuns();
    });
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
    // synchronously, before the `await` below ever suspends this
    // function, so a caller that does not await this call (every one
    // today) sees no change in when the screen itself appears. Awaiting
    // it here, rather than leaving it fully detached the way this
    // function used to, only matters to a caller that *does* await it --
    // `run-view-initial.js`'s own launch sequence, which needs the real
    // bridge call fully settled before flipping `window.
    // __fimRunViewReady`, the same flag every GUI test's own teardown
    // gates on. Without that, a launch-triggered `list_home_runs()` can
    // still be in flight when a test destroys its window moments later
    // (`test/gui/conftest.py`'s own module docstring records this exact
    // failure shape at length for this function's original, single
    // click-driven call site -- a second, always-fired call site at
    // bootstrap reintroduces it for every test, not just Home's own,
    // unless this function's own caller can wait for it).
    await refreshRecentRuns();
};
