"use strict";

/* The persistent rail and always-visible parameter strip (botanist GUI
 * design doc `20260907-claude-sonnet-5-botanist-gui-redesign.md` §3.1,
 * §3.2) -- design §16's own "Suggested delivery phasing" phase 1
 * ("Shell and Configure"), landed across two commits: the rail/strip
 * shell first, then the two-panel Configure workspace (§4.1, §4.2) and
 * the native-menu trim (§3.3) that depended on it existing (`index.
 * html`'s own comment above `screen-configure` has the field-relocation
 * detail). Still deliberately not the whole design: Run and Results
 * still share one screen (splitting that is phase 4), and the "Advanced:
 * execution & performance" disclosure has never had a GUI control to
 * relocate in the first place (`index.html`'s own comment again).
 *
 * `showScreen` (`app.js`) is still the one function every screen file
 * already calls to change what is visible -- this file does not
 * introduce a second, parallel navigation concept. It only (a) adds
 * rail click handlers that call `showScreen` (via each destination's
 * own existing `fim.menu.*`/`fim.show*` entry point, so every screen's
 * own return-screen bookkeeping and refresh-on-open behavior keeps
 * working unchanged) and (b) teaches `showScreen` itself, via `window.
 * fim.updateRailHighlight` below, to keep the rail's own `aria-current`
 * in sync -- called from every existing call site for free, with no
 * call site needing to know the rail exists at all.
 */

// One rail button, one Configure destination. Run and Results
// deliberately both resolve to `screen-run` -- splitting that still-
// unified view's own content into two genuinely separate layouts is
// design §16 phase 4 ("Run/Results side-by-side plots"), not this
// phase's own scope; which rail item highlights while `screen-run` is
// showing is decided dynamically, by `runViewState`, in
// `resolveDestination` below rather than by this static map.
const STATIC_DESTINATION_TO_SCREEN = {
    home: "screen-open-run",
    configure: "screen-configure",
    explore: "screen-explore",
    compare: "screen-compare",
    help: "screen-help",
};

const railButtons = document.querySelectorAll(".rail-item");

/**
 * The rail destination that owns `screenId`, resolving `screen-run`'s
 * own two rail entries (`run`/`results`) by the run view's current
 * state rather than a static lookup -- the one screen id genuinely
 * shared by two rail buttons this phase.
 * @param {string} screenId
 * @returns {string|null}
 */
function resolveDestination(screenId) {
    if (screenId === "screen-run") {
        return window.fim.getRunViewState() === "completed" ? "results" : "run";
    }
    for (const [destination, mappedScreenId] of Object.entries(
        STATIC_DESTINATION_TO_SCREEN
    )) {
        if (mappedScreenId === screenId) {
            return destination;
        }
    }
    return null;
}

/**
 * Reflect `screenId` (whatever `showScreen` was just asked to show) on
 * the rail's own `aria-current`. Called from `showScreen` itself
 * (`app.js`), so every existing navigation call site -- `fim.menu.*`,
 * every screen's own "Back" button, `fim.showExplore`/`showOpenRunScreen`/
 * etc. -- keeps the rail in sync with no change of its own.
 * @param {string} screenId
 */
function updateRailHighlight(screenId) {
    const destination = resolveDestination(screenId);
    for (const button of railButtons) {
        const isCurrent = button.dataset.destination === destination;
        button.setAttribute("aria-current", String(isCurrent));
    }
}

window.fim.updateRailHighlight = updateRailHighlight;

/**
 * Format the FIM-parameters strip's own compact `N`/`d`/`m`/`mu` text
 * (design §3.2: "always shows the current configuration's N, d, m, and
 * mu in compact, always-current form... a compact summary" for a
 * non-scalar shape). Pure and synchronous -- every value it reads is
 * already on the page (`collectFormValues`'s own live `FormData` scan),
 * no bridge round trip needed just to keep this strip current.
 * @param {Record<string, string>} values
 * @returns {{N: string, d: string, m: string, mu: string}}
 */
function formatParameterStripSummary(values) {
    const nItems = (values.N || "")
        .split(",")
        .map((item) => item.trim())
        .filter((item) => item !== "");
    let n = nItems.join(", ") || "—";
    if (nItems.length > 1) {
        const numbers = nItems.map(Number).filter((value) => !Number.isNaN(value));
        if (numbers.length === nItems.length) {
            const min = Math.min(...numbers);
            const max = Math.max(...numbers);
            n = `${nItems.length} demes, ${min}–${max}`;
        }
    }

    const d = values.d || "—";

    let m = "—";
    if (values.m_mode === "scalar") {
        m = values.m_rate || "—";
    } else if (values.m_mode === "topology") {
        m = `${values.m_topology || "topology"} (matrix)`;
    } else if (values.m_mode === "matrix") {
        m = "matrix";
    }

    let mu = "—";
    if (values.mu_mode === "mu") {
        mu = values.mu_value || "—";
    } else if (values.mu_mode === "mu_b") {
        mu = `derived from μᵦ`;
    }

    return { N: n, d, m, mu };
}

/**
 * Refresh the parameter strip from the form's current values. Called
 * from `revalidate()` (`config-modals.js`), which already runs on every
 * field change, so the strip needs no event listener of its own.
 * @param {Record<string, string>} values
 */
function updateParameterStrip(values) {
    const summary = formatParameterStripSummary(values);
    for (const key of /** @type {const} */ (["N", "d", "m", "mu"])) {
        const slot = document.getElementById(`parameter-strip-${key}`);
        if (slot !== null) {
            slot.textContent = summary[key];
        }
    }
}

window.fim.updateParameterStrip = updateParameterStrip;

/**
 * Open the Configure landing destination (interim this phase -- see
 * this file's own module docstring). Exposed on `window.fim` the same
 * way every other destination's own `show*` entry point is, for the
 * rail's click handler below and for `run-view-initial.js`'s "invalid
 * field" routing to reach later if it chooses to; unused outside this
 * file for now.
 */
function showConfigureScreen() {
    window.fim.showScreen("screen-configure");
}

window.fim.showConfigureScreen = showConfigureScreen;

/**
 * Wire the rail's own six destination buttons plus the interim
 * Configure landing page's own section buttons and shortcuts. Every
 * handler below delegates to an already-existing entry point
 * (`fim.menu.*`, `fim.show*`) rather than reimplementing that
 * destination's own open/refresh behavior a second time.
 */
function wireNavRail() {
    for (const button of railButtons) {
        button.addEventListener("click", () => {
            switch (button.dataset.destination) {
                case "home":
                    window.fim.menu.openRun();
                    break;
                case "configure":
                    window.fim.showConfigureScreen();
                    break;
                case "explore":
                    window.fim.menu.explore();
                    break;
                case "run":
                case "results":
                    window.fim.showScreen("screen-run");
                    break;
                case "compare":
                    window.fim.menu.compareRuns();
                    break;
                case "help":
                    window.fim.menu.help("usage");
                    break;
                default:
                    break;
            }
        });
    }

    for (const button of document.querySelectorAll(".parameter-strip-item")) {
        button.addEventListener("click", () => window.fim.showConfigureScreen());
    }

    // The rail's own identity mark doubles as the "About fim" shortcut
    // (design's own "brand mark opens About" convention) -- the same
    // `showAboutModal` the Help menu's "About fim" action and the
    // native macOS About panel handler (`app.py`) both already reach.
    document
        .getElementById("rail-brand-about")
        .addEventListener("click", () => window.fim.showAboutModal());

    // Configure's own footer actions (design §4's own mockup: "[Load
    // configuration…] [Save configuration…] [▶ Run] [🔮 Explore]").
    // Load/Save call `window.fim.openConfiguration`/`saveConfiguration`
    // directly (`run-view-controls.js`), not the File menu's own
    // `fim.menu.openConfiguration`/`saveConfiguration` wrappers -- those
    // jump to `screen-run` first, a menu-only behavior from before
    // Configure was its own destination with the form's own fields
    // directly visible on it; clicking Load/Save from here should keep
    // showing the very screen whose fields just changed, not navigate
    // away from it.
    document
        .getElementById("configure-load-button")
        .addEventListener("click", () => window.fim.openConfiguration());
    document
        .getElementById("configure-save-button")
        .addEventListener("click", () => window.fim.saveConfiguration());
    document
        .getElementById("configure-run-button")
        .addEventListener("click", () => window.fim.menu.runSimulation());
    document
        .getElementById("configure-explore-button")
        .addEventListener("click", () => window.fim.menu.explore());

    updateRailHighlight("screen-run");
}

whenApiReady(wireNavRail);
