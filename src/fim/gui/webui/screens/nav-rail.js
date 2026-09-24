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

// One rail button, one destination each -- Run and Results used to be
// two separate buttons both resolving to `screen-run`, the rail
// dynamically relabeling one as "Results" by `runViewState` once a run
// completed (`resolveDestination`'s own docstring, below, used to
// explain that split); collapsed into this one "Run" entry instead, on
// a real, reported request, since the content underneath was always
// one unified view and splitting it into two genuinely separate
// layouts (design §16 phase 4's own "Run/Results side-by-side plots")
// never happened.
const STATIC_DESTINATION_TO_SCREEN = {
    home: "screen-open-run",
    configure: "screen-configure",
    explore: "screen-explore",
    run: "screen-run",
    compare: "screen-compare",
    help: "screen-help",
};

const railButtons = document.querySelectorAll(".rail-item");
const configureBackButton = document.getElementById("configure-back-button");
const configureExampleSelect = document.getElementById("configure-example-select");
const configureBanner = document.getElementById("configure-banner");

/**
 * Show (or, given a falsy `message`, hide) Configure's own banner --
 * the identical `showRunBanner`/`showOpenRunBanner`/`showCompareBanner`
 * shape every other screen already established, mirrored here rather
 * than shared, since each screen's own banner element and return target
 * are otherwise independent. `screens/presets.js`'s own `showExample
 * LoadNotice` is the first caller from outside this file.
 * @param {string} message
 */
function showConfigureBanner(message) {
    if (!message) {
        configureBanner.hidden = true;
        configureBanner.textContent = "";
        return;
    }
    configureBanner.hidden = false;
    configureBanner.textContent = message;
}

// Set once `refreshConfigureExampleOptions`'s own bridge call has
// settled and the dropdown genuinely lists this visit's own built-in
// examples -- the same `window.__fimHomeExampleOptionsReady` precedent
// (`screens/open-run.js`) for the identical reason: a test polling only
// "the select exists" could otherwise observe it with no example
// options yet, in the narrow window before this async call resolves.
window.__fimConfigureExampleOptionsReady = false;

/**
 * The rail destination that owns `screenId` -- every screen id now maps
 * to exactly one rail button (`STATIC_DESTINATION_TO_SCREEN`, above),
 * `screen-run` included, so this is a plain reverse lookup.
 * @param {string} screenId
 * @returns {string|null}
 */
function resolveDestination(screenId) {
    if (screenId === "screen-sweep") {
        // A sweep is set up from Configure and is a kind of run.
        return "configure";
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
const PLOIDY_NAMES = { 1: "haploid", 2: "diploid", 3: "triploid", 4: "tetraploid" };

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

    // Individuals per deme, followed by the ploidy that turns them into
    // gene copies -- "225 diploid", not a bare number whose meaning
    // depends on a field two screens away.
    const ploidyName = PLOIDY_NAMES[values.ploidy];
    if (ploidyName !== undefined && n !== "—") {
        n = `${n} ${ploidyName}`;
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
 * Populate `configure-example-select` with this visit's own built-in
 * worked examples -- `screens/presets.js`'s own shared `refreshExample
 * Options`, the identical source Home's own `home-example-select` uses.
 * Re-fetched on every visit to Configure, matching that same "never
 * trust a stale fetch across visits" precedent, even though the
 * built-in set itself never changes at runtime.
 */
async function refreshConfigureExampleOptions() {
    window.__fimConfigureExampleOptionsReady = false;
    await window.fim.refreshExampleOptions(configureExampleSelect);
    window.__fimConfigureExampleOptionsReady = true;
}

/**
 * Open the Configure landing destination (interim this phase -- see
 * this file's own module docstring). Exposed on `window.fim` the same
 * way every other destination's own `show*` entry point is, for the
 * rail's click handler below and for `run-view-initial.js`'s "invalid
 * field" routing to reach later if it chooses to; unused outside this
 * file for now.
 *
 * Screen history is owned centrally by `window.fim.showScreen`, so this
 * entry point only shows Configure and refreshes its example dropdown
 * and its own Study picker (`run-view-controls.js`'s own `run-study-
 * select` -- Run/Study/Experiment workflow-ergonomics design, `selby/
 * restricted`, item 3), so either reflects anything created since app
 * launch with no restart needed.
 */
async function showConfigureScreen() {
    window.fim.showScreen("screen-configure");
    await Promise.all([
        refreshConfigureExampleOptions(),
        window.fim.refreshRunStudySelectOptions(),
    ]);
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
        .addEventListener("click", () => {
            // Carries Configure's own *current* N/d/m/mu into Explore,
            // unlike `window.fim.menu.explore()` (the File menu/rail
            // button's own generic path, reachable from any screen, no
            // "current Configure values" to push) -- the symmetric fix
            // to the Explore-to-Study/Run handoff's own §"Current state"
            // (`20260918-claude-sonnet-5-explore-to-study-run-handoff-
            // design.md`, `selby/restricted`): this button previously
            // navigated to Explore relying entirely on `showExplore`'s
            // own once-per-launch seed, silently ignoring whatever
            // Configure was actually showing at the time.
            window.fim.showExplore(collectFormValues());
        });
    configureBackButton.addEventListener("click", () => {
        window.fim.navigateBack();
    });
    // A plain, immediately-acting pulldown, the identical "jump-start"
    // shortcut Home's own `home-example-select` offers
    // (`screens/open-run.js`) -- applies in place via `window.fim.
    // applyPreset` and resets to its own placeholder so the control
    // always reads as an action, never as "currently showing example X."
    // No navigation on success (unlike Home's own version): there is
    // nowhere else to jump to, since the point is loading a different
    // example without leaving Configure at all.
    configureExampleSelect.addEventListener("change", async () => {
        const presetId = configureExampleSelect.value;
        if (!presetId) {
            return;
        }
        // The option's own bare title (`refreshExampleOptions`'s own
        // `dataset.presetTitle`, `screens/presets.js`), not its visible
        // `textContent` -- the latter carries a "(view YAML only)"
        // suffix for the one example this affects, meant for the
        // pulldown's own label, not to be echoed back inside
        // `showExampleLoadNotice`'s own message.
        const presetTitle = configureExampleSelect.selectedOptions[0].dataset
            .presetTitle;
        configureExampleSelect.value = "";
        await window.fim.applyPreset(presetId, presetTitle);
    });

    // Matches `index.html`'s own static default (`screen-open-run` is
    // the only `.screen` section not marked `hidden` there) -- Home is
    // this app's landing destination (botanist GUI design doc §9), not
    // Run. `run-view-initial.js`'s own `initializeRunView` re-shows
    // Home explicitly moments later (`window.fim.showOpenRunScreen()`,
    // to populate it with real recent-runs/example data rather than
    // leaving `index.html`'s static empty markup showing), which calls
    // `showScreen` and so already updates this highlight on its own --
    // this call only keeps the rail correct for the brief window before
    // that happens, and stays correct even if that call is ever removed.
    updateRailHighlight("screen-open-run");
}

whenApiReady(wireNavRail);
