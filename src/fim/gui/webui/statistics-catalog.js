"use strict";

/* The statistic catalog, received once from Python, and everything the
 * page builds from it.
 *
 * `fim.statistics.catalog` is the one definition of every statistic;
 * `Api.get_statistics_catalog` sends it here. From it this script builds
 * the statistics-panel rows, the results tables' statistic columns and
 * the convergence-statistic checkboxes, and it owns which statistics are
 * *shown* (the Settings dialog's "Statistics shown" choice). Showing or
 * hiding a statistic only changes what is on screen: every statistic is
 * still computed and saved.
 *
 * The lists below start empty and are filled in place (never replaced)
 * by `loadStatisticsCatalog`, which `initializeRunView` awaits before
 * the page is marked ready, so every later reader sees them full.
 */

// Every catalog entry, in display order.
const STATISTIC_CATALOG = [];

// Keys of every global statistic (one value per generation): the
// statistics-panel rows and the results tables' columns.
const STATISTIC_NAMES = [];

// Global statistics with a per-generation history, so a trajectory
// curve: the only ones the trajectory chart plots.
const TRAJECTORY_STATISTIC_NAMES = [];

// Statistics of the deme pair chosen for the scatter plot.
const PAIR_STATISTIC_NAMES = [];

// Statistics a run may stop on: one checkbox each.
const CONVERGENCE_STATISTIC_KEYS = [];

// Trajectory curves that start hidden (the catalog's `default_plotted`).
const DEFAULT_HIDDEN_TRAJECTORY_STATISTICS = [];

// The keys currently shown, and what a fresh install shows.
const shownStatistics = new Set();
const defaultShownStatistics = new Set();

const _statisticSpecs = new Map();
const _statisticCatalogReadyCallbacks = [];

/**
 * Return one statistic's catalog entry, or `null` for a name the catalog
 * does not hold (Explore's predicted quantities).
 *
 * @param {string} key
 * @returns {object|null}
 */
function statisticSpec(key) {
    return _statisticSpecs.get(key) || null;
}

/**
 * Whether `key` is currently shown.
 *
 * @param {string} key
 * @returns {boolean}
 */
function isStatisticShown(key) {
    return shownStatistics.has(key);
}

/**
 * Whether `key` is on a fixed 0-to-1 scale (a proportion), so a chart
 * draws it on a fixed axis rather than fitting the axis to the data.
 *
 * @param {string} key
 * @returns {boolean}
 */
function isProportionStatistic(key) {
    const spec = statisticSpec(key);
    return Boolean(spec && spec.bounds[0] === 0 && spec.bounds[1] === 1);
}

/**
 * A short plain-text name for charts and readouts: the catalog's own
 * text label for a Nei family member, the familiar "G ST" spacing for
 * the rest.
 *
 * @param {string} key
 * @returns {string}
 */
function statisticDisplayName(key) {
    const spec = statisticSpec(key);
    if (spec && spec.nei) {
        return spec.label_text;
    }
    return key.replace("_", " ");
}

/**
 * Run `callback` once the catalog has arrived (immediately if it has).
 *
 * @param {() => void} callback
 */
function onStatisticCatalogReady(callback) {
    if (STATISTIC_CATALOG.length > 0) {
        callback();
        return;
    }
    _statisticCatalogReadyCallbacks.push(callback);
}

/**
 * Whether a Nei distance's value string means "infinite": the server
 * sends `undefined` for a distance whose identity is 0 (no allele
 * shared), because saved results are strict JSON with no infinity.
 *
 * @param {string} key
 * @param {*} value
 * @returns {boolean}
 */
function isInfiniteStatisticValue(key, value) {
    const spec = statisticSpec(key);
    return Boolean(
        spec && spec.nei && spec.nei[0] === "distance" && value === "undefined"
    );
}

/**
 * Build one statistics-panel row placeholder for `key`.
 *
 * @param {string} key
 * @param {"id"|"data"} addressing `id="stat-KEY"` or `data-stat="KEY"`
 * @returns {HTMLTableRowElement}
 */
function _statisticRow(key, addressing) {
    const row = document.createElement("tr");
    if (addressing === "id") {
        row.id = `stat-${key}`;
    } else {
        row.dataset.stat = key;
    }
    row.dataset.shownKey = key;
    return row;
}

/**
 * Build the rows, columns and checkboxes the catalog defines.
 */
function _buildStatisticSlots() {
    const initial = document.querySelector("#initial-stats tbody");
    if (initial) {
        initial.replaceChildren(
            ...STATISTIC_NAMES.map((key) => _statisticRow(key, "data"))
        );
    }

    const results = document.querySelector("#results-stats tbody");
    if (results) {
        // The effective-allele rows are derived display rows, not
        // catalog statistics; the catalog rows go in front of them and
        // the deme-pair section after.
        const anchor = document.getElementById("stat-Ne_S");
        for (const key of STATISTIC_NAMES) {
            results.insertBefore(_statisticRow(key, "id"), anchor);
        }
        const heading = document.createElement("tr");
        heading.id = "pair-statistics-heading";
        heading.className = "stats-section-heading";
        const cell = document.createElement("th");
        cell.colSpan = 2;
        cell.id = "pair-statistics-heading-text";
        cell.textContent = "Deme pair";
        heading.appendChild(cell);
        results.appendChild(heading);
        for (const key of PAIR_STATISTIC_NAMES) {
            results.appendChild(_statisticRow(key, "id"));
        }
    }

    for (const row of document.querySelectorAll("tr[data-statistic-columns]")) {
        const trailing = Number(row.dataset.trailingColumns || "0");
        const before = trailing > 0 ? row.children[row.children.length - trailing] : null;
        for (const key of STATISTIC_NAMES) {
            const header = document.createElement("th");
            header.innerHTML = formatStatisticLabel(key);
            header.dataset.shownKey = key;
            row.insertBefore(header, before);
        }
    }

    const selector = document.getElementById("cs-selector");
    if (selector) {
        for (const key of CONVERGENCE_STATISTIC_KEYS) {
            const label = document.createElement("label");
            const input = document.createElement("input");
            input.type = "checkbox";
            input.name = `cs_${key}`;
            input.value = "true";
            input.setAttribute("form", "input-form");
            // `D` is the configuration's own default watched statistic.
            input.checked = key === "D";
            label.append(input, " ");
            label.insertAdjacentHTML("beforeend", formatStatisticLabel(key));
            selector.appendChild(label);
        }
    }
}

/**
 * Show or hide every element tagged with a statistic key to match the
 * shown set: panel rows, table headers and table cells alike.
 */
function applyStatisticVisibility() {
    for (const element of document.querySelectorAll("[data-shown-key]")) {
        element.hidden = !isStatisticShown(element.dataset.shownKey);
    }
    const heading = document.getElementById("pair-statistics-heading");
    if (heading) {
        heading.hidden = !PAIR_STATISTIC_NAMES.some((key) => isStatisticShown(key));
    }
}

/**
 * Replace the shown set and redraw what depends on it.
 *
 * @param {string[]} keys
 */
function setShownStatistics(keys) {
    shownStatistics.clear();
    for (const key of keys) {
        if (_statisticSpecs.has(key)) {
            shownStatistics.add(key);
        }
    }
    applyStatisticVisibility();
    if (window.fim && typeof window.fim.onShownStatisticsChanged === "function") {
        window.fim.onShownStatisticsChanged();
    }
}

/**
 * Fetch the catalog once and build everything that depends on it.
 *
 * @returns {Promise<void>}
 */
async function loadStatisticsCatalog() {
    if (STATISTIC_CATALOG.length > 0) {
        return;
    }
    const result = await window.pywebview.api.get_statistics_catalog();
    for (const spec of result.statistics) {
        STATISTIC_CATALOG.push(spec);
        _statisticSpecs.set(spec.key, spec);
        if (spec.scope === "global") {
            STATISTIC_NAMES.push(spec.key);
            if (spec.history !== "none") {
                TRAJECTORY_STATISTIC_NAMES.push(spec.key);
                if (!spec.default_plotted) {
                    DEFAULT_HIDDEN_TRAJECTORY_STATISTICS.push(spec.key);
                }
            }
        } else {
            PAIR_STATISTIC_NAMES.push(spec.key);
        }
        if (spec.convergence_eligible) {
            CONVERGENCE_STATISTIC_KEYS.push(spec.key);
        }
    }
    for (const key of result.defaultShown) {
        defaultShownStatistics.add(key);
    }
    _buildStatisticSlots();
    setShownStatistics(result.shown);
    for (const callback of _statisticCatalogReadyCallbacks.splice(0)) {
        callback();
    }
}

window.fim.loadStatisticsCatalog = loadStatisticsCatalog;
window.fim.setShownStatistics = setShownStatistics;
window.fim.getShownStatistics = () => [...shownStatistics];
