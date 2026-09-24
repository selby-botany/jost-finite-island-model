"use strict";

/* Explore's "Choose a sweep" panel (`20260923-claude-sonnet-5-sweep-as-
 * study-implementation-plan.md`, `selby/restricted`, §7.2, §7.3): the
 * front end of a sweep. For the parameter(s) the current view shows, pick
 * an interval, a count and a spacing (`axis-range.js`), see the exact
 * points drawn and listed and how many the plan makes (`Api.plan_sweep`,
 * no run), then "Sweep this for real" seeds Configure from the four
 * fields and sets the sweep there (Sweep box on, axes saved).
 *
 * Explore works in gene copies per deme; a sweep's `N` counts
 * individuals, the number shown everywhere else. The conversion (divide
 * by the ploidy, which the bridge fills in from Settings) happens here at
 * the seam, so the sweep screen receives individuals.
 *
 * "Even in the predicted response" places points where a closed-form
 * statistic changes: the interval is sampled densely, and the points sit
 * at equal steps of the statistic's cumulative change, so they crowd the
 * steep part (the migration threshold) and thin out on the plateaus. The
 * result is an explicit list, so the sweep stays reproducible without the
 * theory. It is a hint about where to spend simulations, not a substitute
 * for them: the closed form assumes equilibrium and the island model.
 */

const exploreSweepToggle = document.getElementById("explore-sweep-toggle");
const exploreSweepPanel = document.getElementById("explore-sweep-panel");
const exploreSweepAxes = document.getElementById("explore-sweep-axes");
const exploreSweepStatistic = document.getElementById("explore-sweep-statistic");
const exploreSweepPlan = document.getElementById("explore-sweep-plan");
const exploreSweepButton = document.getElementById("explore-sweep-button");

// Mirrors `fim.gui.app._EQUILIBRIUM_SWEEP_DOMAINS` (a test asserts they
// agree), with each axis's default spacing and whether it is a count.
const EXPLORE_SWEEP_AXES = {
    N: { domain: [10, 5000], log: true, integer: true },
    d: { domain: [2, 50], log: false, integer: true },
    m: { domain: [0.0001, 0.5], log: true, integer: false },
    mu: { domain: [0.000001, 0.1], log: true, integer: false },
};
const EXPLORE_RESPONSE_SAMPLES = 200;
const EXPLORE_SWEEP_DEBOUNCE_MS = 250;

let exploreSweepControls = [];
let exploreSweepTimer = null;
let exploreSweepSequence = 0;

window.__fimExploreSweepReady = false;

for (const name of ["D", "G_ST", "E_ST", "H_S", "H_T"]) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name.replace("_", " ");
    exploreSweepStatistic.appendChild(option);
}

/**
 * The parameter keys the current view shows: one in curve mode, two in
 * surface mode.
 * @returns {string[]}
 */
function exploreSweepKeys() {
    return isExploreSurface() ? [exploreAxis.value, exploreAxisY.value] : [exploreAxis.value];
}

/**
 * The entered value of one Explore parameter, as a number.
 * @param {string} key
 * @returns {number}
 */
function exploreEnteredValue(key) {
    const field = { N: exploreN, d: exploreD, m: exploreM, mu: exploreMu }[key];
    return Number(field.value);
}

/**
 * A sensible opening interval for an axis: a decade either side of the
 * entered value for a rate or size, and 2 to 16 for the deme count.
 * @param {string} key
 * @returns {{start: number, stop: number, count: number}}
 */
function exploreDefaultInterval(key) {
    const { domain } = EXPLORE_SWEEP_AXES[key];
    if (key === "d") {
        return { start: 2, stop: 16, count: 4 };
    }
    const entered = exploreEnteredValue(key);
    if (!(entered > 0)) {
        return { start: domain[0], stop: domain[1], count: 5 };
    }
    return {
        start: Math.max(domain[0], entered / 10),
        stop: Math.min(domain[1], entered * 10),
        count: 5,
    };
}

/**
 * Sample `[low, high]` densely and return `count` values at equal steps
 * of the chosen statistic's cumulative change.
 * @param {string} key
 * @param {number} low
 * @param {number} high
 * @param {number} count
 * @returns {Promise<number[]>}
 */
async function exploreResponseValues(key, low, high, count) {
    const { log, integer } = EXPLORE_SWEEP_AXES[key];
    const useLog = log && low > 0;
    const samples = Array.from({ length: EXPLORE_RESPONSE_SAMPLES }, (_, step) => {
        const fraction = step / (EXPLORE_RESPONSE_SAMPLES - 1);
        const value = useLog ? low * (high / low) ** fraction : low + (high - low) * fraction;
        return integer ? Math.round(value) : value;
    });
    const unique = samples.filter((value, index) => samples.indexOf(value) === index);
    const entered = collectExploreValues();
    const curve = await window.pywebview.api.get_equilibrium_curve(
        key,
        unique,
        entered.n,
        entered.m,
        entered.mu,
        entered.d
    );
    const statistic = exploreSweepStatistic.value;
    if (!curve.ok || count < 2 || unique.length < 2) {
        return unique.slice(0, Math.max(count, 1));
    }
    const response = curve.points.map((point) => point[statistic]);
    if (response.some((value) => typeof value !== "number")) {
        return unique.slice(0, Math.max(count, 1));
    }
    const cumulative = [0];
    for (let index = 1; index < response.length; index += 1) {
        cumulative.push(cumulative[index - 1] + Math.abs(response[index] - response[index - 1]));
    }
    const total = cumulative[cumulative.length - 1];
    if (total === 0) {
        return unique.slice(0, count);
    }
    const chosen = [];
    for (let step = 0; step < count; step += 1) {
        const target = (step / (count - 1)) * total;
        let best = 0;
        for (let index = 0; index < cumulative.length; index += 1) {
            if (Math.abs(cumulative[index] - target) < Math.abs(cumulative[best] - target)) {
                best = index;
            }
        }
        chosen.push(unique[best]);
    }
    return chosen;
}

/**
 * Rebuild the axis controls for the parameters the view now shows,
 * keeping the interval of any axis that is still shown.
 */
async function rebuildExploreSweepAxes() {
    const previous = new Map(
        exploreSweepControls.map((control) => [control.key, control.control.getDefinition()])
    );
    exploreSweepAxes.replaceChildren();
    exploreSweepControls = [];
    for (const key of exploreSweepKeys()) {
        const info = EXPLORE_SWEEP_AXES[key];
        const opening = exploreDefaultInterval(key);
        const kept = previous.get(key);
        const control = window.fim.createAxisRange({
            key,
            label: exploreAxisLabel(key),
            domain: info.domain,
            log: info.log,
            integer: info.integer,
            count: kept && kept.range ? kept.range.count : opening.count,
            start: kept && kept.range ? kept.range.start : opening.start,
            stop: kept && kept.range ? kept.range.stop : opening.stop,
            responseValues: (low, high, count) =>
                exploreResponseValues(key, low, high, count),
            onChange: scheduleExploreSweepPlan,
        });
        exploreSweepControls.push({ key, control });
        exploreSweepAxes.appendChild(control.element);
    }
    await Promise.all(exploreSweepControls.map((entry) => entry.control.ready()));
}

/** Ask for a fresh plan once the user stops editing. */
function scheduleExploreSweepPlan() {
    window.__fimExploreSweepReady = false;
    window.clearTimeout(exploreSweepTimer);
    exploreSweepTimer = window.setTimeout(refreshExploreSweepPlan, EXPLORE_SWEEP_DEBOUNCE_MS);
}

/**
 * The ploidy the Explore fields are converted with (1 when none is set,
 * the same reading the bridge gives an unset ploidy).
 * @param {Record<string, string>} formValues
 * @returns {number}
 */
function explorePloidy(formValues) {
    const ploidy = Number(formValues.ploidy);
    return Number.isInteger(ploidy) && ploidy >= 1 ? ploidy : 1;
}

/**
 * A control's definition in the sweep's own units (individuals for `N`).
 * @param {{key: string, range?: object, values?: number[]}} definition
 * @param {number} ploidy
 * @returns {object}
 */
function exploreSweepDefinition(definition, ploidy) {
    if (definition.key !== "N") {
        return definition;
    }
    if (definition.range) {
        return {
            key: "N",
            range: {
                ...definition.range,
                start: Math.max(1, Math.round(definition.range.start / ploidy)),
                stop: Math.max(1, Math.round(definition.range.stop / ploidy)),
            },
        };
    }
    const individuals = definition.values.map((value) => Math.max(1, Math.round(value / ploidy)));
    return {
        key: "N",
        values: individuals.filter((value, index) => individuals.indexOf(value) === index),
    };
}

/**
 * Seed the form values Explore's fields imply, or say why not.
 * @returns {Promise<{ok: boolean, values?: object, message?: string}>}
 */
function exploreFormValues() {
    const entered = collectExploreValues();
    return window.pywebview.api.get_starter_form_with_overrides({
        gene_copies: entered.n,
        d: entered.d,
        m_rate: entered.m,
        mu_value: entered.mu,
    });
}

/**
 * Plan the chosen sweep and show the count, and its dots on the map.
 */
async function refreshExploreSweepPlan() {
    const sequence = (exploreSweepSequence += 1);
    const seeded = await exploreFormValues();
    if (sequence !== exploreSweepSequence) {
        return;
    }
    if (!seeded.ok) {
        exploreSweepPlan.textContent = seeded.message;
        exploreSweepButton.disabled = true;
        window.__fimExploreSweepReady = true;
        return;
    }
    const ploidy = explorePloidy(seeded.values);
    const axes = exploreSweepControls.map((entry) =>
        exploreSweepDefinition(entry.control.getDefinition(), ploidy)
    );
    const plan = await window.pywebview.api.plan_sweep(seeded.values, { axes });
    if (sequence !== exploreSweepSequence) {
        return;
    }
    if (!plan.ok) {
        exploreSweepPlan.textContent = plan.message;
        exploreSweepButton.disabled = true;
    } else {
        const pieces = [`${plan.points.length} point${plan.points.length === 1 ? "" : "s"}`];
        if (plan.invalid.length > 0) {
            pieces.push(`${plan.invalid.length} invalid`);
        }
        if (plan.reusedCount > 0) {
            pieces.push(`${plan.reusedCount} already computed`);
        }
        exploreSweepPlan.textContent = `${pieces.join(", ")}.`;
        exploreSweepButton.disabled = plan.points.length === 0;
    }
    drawExploreSweepMarkers(plan, ploidy);
    window.__fimExploreSweepReady = true;
}

/**
 * Draw the planned points on the surface map (surface mode only).
 * @param {object} plan
 * @param {number} ploidy
 */
function drawExploreSweepMarkers(plan, ploidy) {
    if (!isExploreSurface() || !plan.ok || exploreSweepControls.length < 2) {
        window.fim.setExploreSurfaceMarkers([]);
        return;
    }
    const [xKey, yKey] = exploreSweepControls.map((entry) => entry.key);
    const inExploreUnits = (key, value) => (key === "N" ? value * ploidy : value);
    window.fim.setExploreSurfaceMarkers(
        plan.points.map((point) => ({
            x: inExploreUnits(xKey, point.coordinates[xKey]),
            y: inExploreUnits(yKey, point.coordinates[yKey]),
        }))
    );
}

exploreSweepToggle.addEventListener("click", async () => {
    const opening = exploreSweepPanel.hidden;
    exploreSweepPanel.hidden = !opening;
    exploreSweepToggle.setAttribute("aria-expanded", String(opening));
    if (opening) {
        await rebuildExploreSweepAxes();
    } else {
        window.fim.setExploreSurfaceMarkers([]);
    }
});

exploreSweepStatistic.addEventListener("change", async () => {
    await rebuildExploreSweepAxes();
});

for (const control of [exploreMode, exploreAxis, exploreAxisY]) {
    control.addEventListener("change", async () => {
        if (!exploreSweepPanel.hidden) {
            await rebuildExploreSweepAxes();
        }
    });
}

exploreSweepButton.addEventListener("click", async () => {
    const seeded = await exploreFormValues();
    if (!seeded.ok) {
        exploreBanner.textContent = seeded.message;
        exploreBanner.hidden = false;
        return;
    }
    const ploidy = explorePloidy(seeded.values);
    const axes = exploreSweepControls.map((entry) =>
        exploreSweepDefinition(entry.control.getDefinition(), ploidy)
    );
    // The same seeding "Run this for real" does: Configure takes the four
    // Explore fields. The sweep is then set on Configure (its Sweep box on,
    // the axes saved), and the botanist presses Run there.
    await window.fim.showConfigureScreen();
    applyFormValues(seeded.values);
    await revalidate();
    const set = await window.fim.setSweepConfiguration({ axes });
    if (!set.ok) {
        showConfigureBanner(set.message);
    }
});
