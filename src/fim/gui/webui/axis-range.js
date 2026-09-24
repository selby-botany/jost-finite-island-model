"use strict";

/* Axis-range control (`20260923-claude-sonnet-5-sweep-as-study-
 * implementation-plan.md`, `selby/restricted`, §7.2): choose the values a
 * sweep takes along one parameter, from an interval, a count and a
 * spacing, while always showing the exact points that result.
 *
 * - Two sliders pick the ends of the interval (mapped by the axis's own
 *   scale, and snapped to whole numbers for an integer axis) and are kept
 *   in step with typed "from" and "to" fields.
 * - A count says how many points. Spacing is the axis's own (log or
 *   linear), or "even in the predicted response", which places points
 *   where a closed-form statistic actually changes and so needs a
 *   caller-supplied `responseValues` function.
 * - The resulting values are drawn as ticks along a strip and listed as
 *   chips. Removing a chip switches the axis to an explicit list of
 *   values ("custom"), since a hand-edited set is no longer described by
 *   an interval; "Back to the interval" restores it. The discrete case,
 *   whole numbers and de-duplication included, is therefore always visible
 *   rather than described.
 * - `getDefinition()` returns what `Api.plan_sweep` and the sweep screen
 *   accept: a `{start, stop, count, scale}` range, or a list of values.
 *
 * Knows nothing about Explore or the sweep screen.
 */

const AXIS_RANGE_SLIDER_STEPS = 1000;

/**
 * Format a number for a field: short, but exact enough to re-parse.
 * @param {number} value
 * @returns {string}
 */
function axisRangeFormat(value) {
    if (value !== 0 && (Math.abs(value) < 0.001 || Math.abs(value) >= 100000)) {
        return Number(value.toPrecision(4)).toExponential();
    }
    return String(Number(value.toPrecision(6)));
}

/**
 * Values from `low` to `high`, `count` of them, both ends exact.
 * @param {number} low
 * @param {number} high
 * @param {number} count
 * @param {boolean} log
 * @returns {number[]}
 */
function axisRangeSpaced(low, high, count, log) {
    if (count === 1) {
        return [low];
    }
    return Array.from({ length: count }, (_, step) => {
        if (step === 0) {
            return low;
        }
        if (step === count - 1) {
            return high;
        }
        const fraction = step / (count - 1);
        return log ? low * (high / low) ** fraction : low + (high - low) * fraction;
    });
}

/**
 * Round to an integer axis's whole numbers and drop repeats, in order.
 * @param {number[]} values
 * @param {boolean} integer
 * @returns {number[]}
 */
function axisRangeSnap(values, integer) {
    const snapped = values.map((value) =>
        integer ? Math.round(value) : Number(value.toPrecision(12))
    );
    return snapped.filter((value, index) => snapped.indexOf(value) === index);
}

/**
 * Create one axis-range control.
 *
 * @param {{
 *   key: string, label: string, domain: [number, number], log: boolean,
 *   integer: boolean, count?: number, start?: number, stop?: number,
 *   onChange: () => void,
 *   responseValues?: (low: number, high: number, count: number) => Promise<number[]>
 * }} options `domain` bounds the sliders; `log` is the default spacing.
 * @returns {{
 *   element: HTMLElement,
 *   getDefinition: () => ({key: string, range?: object, values?: number[]}),
 *   getValues: () => number[],
 *   ready: () => Promise<void>
 * }}
 */
function createAxisRange(options) {
    const [domainLow, domainHigh] = options.domain;
    const useLog = options.log && domainLow > 0;
    const state = {
        start: options.start ?? domainLow,
        stop: options.stop ?? domainHigh,
        count: options.count ?? 4,
        spacing: "scale",
        custom: null,
        values: [],
        pending: Promise.resolve(),
    };

    const element = document.createElement("div");
    element.className = "axis-range";
    element.dataset.key = options.key;
    const title = document.createElement("div");
    title.className = "axis-range-title";
    title.textContent = options.label;

    const sliderPosition = (value) => {
        const scale = useLog ? Math.log : (x) => x;
        return Math.round(
            ((scale(value) - scale(domainLow)) / (scale(domainHigh) - scale(domainLow))) *
                AXIS_RANGE_SLIDER_STEPS
        );
    };
    const valueAt = (position) => {
        const fraction = position / AXIS_RANGE_SLIDER_STEPS;
        const value = useLog
            ? domainLow * (domainHigh / domainLow) ** fraction
            : domainLow + (domainHigh - domainLow) * fraction;
        return options.integer ? Math.round(value) : Number(value.toPrecision(6));
    };

    const makeEnd = (text) => {
        const row = document.createElement("label");
        row.className = "axis-range-end";
        const caption = document.createElement("span");
        caption.textContent = text;
        const slider = document.createElement("input");
        slider.type = "range";
        slider.min = "0";
        slider.max = String(AXIS_RANGE_SLIDER_STEPS);
        slider.setAttribute("aria-label", `${options.label} ${text}`);
        const field = document.createElement("input");
        field.type = "text";
        field.inputMode = "decimal";
        field.setAttribute("aria-label", `${options.label} ${text} value`);
        row.append(caption, slider, field);
        return { row, slider, field };
    };
    const from = makeEnd("from");
    const to = makeEnd("to");
    from.slider.className = "axis-range-from";
    from.field.className = "axis-range-from-value";
    to.slider.className = "axis-range-to";
    to.field.className = "axis-range-to-value";

    const settings = document.createElement("div");
    settings.className = "axis-range-settings";
    const countLabel = document.createElement("label");
    const countInput = document.createElement("input");
    countInput.type = "text";
    countInput.inputMode = "numeric";
    countInput.className = "axis-range-count";
    countInput.setAttribute("aria-label", `${options.label} number of points`);
    countLabel.append("points ", countInput);
    const spacingLabel = document.createElement("label");
    const spacingSelect = document.createElement("select");
    spacingSelect.className = "axis-range-spacing";
    spacingSelect.setAttribute("aria-label", `${options.label} spacing`);
    const spacingChoices = [["scale", useLog ? "even on a log scale" : "even steps"]];
    if (options.responseValues) {
        spacingChoices.push(["response", "even in the predicted response"]);
    }
    for (const [value, text] of spacingChoices) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = text;
        spacingSelect.appendChild(option);
    }
    spacingLabel.append("spacing ", spacingSelect);
    const restore = document.createElement("button");
    restore.type = "button";
    restore.className = "axis-range-restore";
    restore.textContent = "Back to the interval";
    restore.hidden = true;
    settings.append(countLabel, spacingLabel, restore);

    const ticks = document.createElement("div");
    ticks.className = "axis-range-ticks";
    const chips = document.createElement("div");
    chips.className = "axis-range-chips";
    element.append(title, from.row, to.row, settings, ticks, chips);

    const readEnds = () => {
        const low = Number(from.field.value);
        const high = Number(to.field.value);
        if (!Number.isFinite(low) || !Number.isFinite(high)) {
            return null;
        }
        return low <= high ? [low, high] : [high, low];
    };

    const draw = () => {
        ticks.replaceChildren();
        chips.replaceChildren();
        const span = useLog ? Math.log(domainHigh) - Math.log(domainLow) : domainHigh - domainLow;
        for (const value of state.values) {
            const tick = document.createElement("span");
            tick.className = "axis-range-tick";
            const offset = useLog ? Math.log(value) - Math.log(domainLow) : value - domainLow;
            tick.style.left = `${Math.min(100, Math.max(0, (offset / span) * 100))}%`;
            ticks.appendChild(tick);
            const chip = document.createElement("button");
            chip.type = "button";
            chip.className = "axis-range-chip";
            chip.textContent = `${axisRangeFormat(value)} ×`;
            chip.title = `Remove ${axisRangeFormat(value)} from the sweep`;
            chip.addEventListener("click", () => {
                state.custom = state.values.filter((entry) => entry !== value);
                if (state.custom.length === 0) {
                    state.custom = null;
                }
                recompute();
            });
            chips.appendChild(chip);
        }
        restore.hidden = state.custom === null;
        countInput.disabled = state.custom !== null;
        spacingSelect.disabled = state.custom !== null;
    };

    const recompute = () => {
        state.pending = (async () => {
            if (state.custom !== null) {
                state.values = state.custom;
            } else {
                const ends = readEnds();
                const count = Number(countInput.value);
                if (ends === null || !Number.isInteger(count) || count < 1) {
                    state.values = [];
                } else if (state.spacing === "response" && options.responseValues) {
                    state.values = axisRangeSnap(
                        await options.responseValues(ends[0], ends[1], count),
                        options.integer
                    );
                } else {
                    const log = useLog && ends[0] > 0;
                    state.values = axisRangeSnap(
                        axisRangeSpaced(ends[0], ends[1], count, log),
                        options.integer
                    );
                }
            }
            draw();
            options.onChange();
        })();
    };

    const setEnds = (low, high) => {
        from.field.value = axisRangeFormat(low);
        to.field.value = axisRangeFormat(high);
        from.slider.value = String(sliderPosition(low));
        to.slider.value = String(sliderPosition(high));
    };
    for (const end of [from, to]) {
        end.slider.addEventListener("input", () => {
            state.custom = null;
            end.field.value = axisRangeFormat(valueAt(Number(end.slider.value)));
            recompute();
        });
        end.field.addEventListener("change", () => {
            state.custom = null;
            const value = Number(end.field.value);
            if (Number.isFinite(value)) {
                end.slider.value = String(
                    Math.min(AXIS_RANGE_SLIDER_STEPS, Math.max(0, sliderPosition(value)))
                );
            }
            recompute();
        });
    }
    countInput.addEventListener("change", () => {
        state.custom = null;
        recompute();
    });
    spacingSelect.addEventListener("change", () => {
        state.spacing = spacingSelect.value;
        recompute();
    });
    restore.addEventListener("click", () => {
        state.custom = null;
        recompute();
    });

    setEnds(state.start, state.stop);
    countInput.value = String(state.count);
    recompute();

    return {
        element,
        getValues: () => state.values.slice(),
        ready: () => state.pending,
        getDefinition() {
            if (state.custom !== null || state.spacing === "response") {
                return { key: options.key, values: state.values.slice() };
            }
            const ends = readEnds();
            return {
                key: options.key,
                range: {
                    start: ends === null ? domainLow : ends[0],
                    stop: ends === null ? domainHigh : ends[1],
                    count: Number(countInput.value),
                    scale: useLog ? "log" : "linear",
                },
            };
        },
    };
}

window.fim.createAxisRange = createAxisRange;
