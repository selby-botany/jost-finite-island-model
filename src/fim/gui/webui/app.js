"use strict";

/* App-wide bootstrap and the small shared namespace every screen script
 * attaches its own bridge-push handlers to (design doc §4, §7.2-§7.4).
 *
 * `window.fim` exists for exactly one reason: `Api.start_run` (design
 * §3.4's "push, not poll") calls `window.evaluate_js("fim.onRunProgress(
 * ...)")` from a background thread whenever a run reports progress --
 * that call needs a stable, always-present global to land on regardless
 * of which screen happens to be showing, so `showScreen`/`onRun*` live
 * here rather than inside `screens/progress.js` itself (which only
 * *implements* what `onRunProgress` etc. actually do once Screen 2
 * exists -- see that file). Screens not yet built register no-op
 * handlers here implicitly, by simply not overriding them.
 */

const fim = {
    /**
     * Show exactly one top-level `.screen` section, hiding the rest.
     * @param {string} screenId
     */
    showScreen(screenId) {
        for (const section of document.querySelectorAll(".screen")) {
            section.hidden = section.id !== screenId;
        }
    },

    onRunProgress() {
        // Overridden once Screen 2 (screens/progress.js) exists.
    },
    onRunDone() {
        // Overridden once Screen 2 (screens/progress.js) exists.
    },
    onRunCancelled() {
        // Overridden once Screen 2 (screens/progress.js) exists.
    },
    onRunError() {
        // Overridden once Screen 2 (screens/progress.js) exists.
    },

    // The batch (`n_replicates > 1`) counterparts `Api._start_batch_run`
    // pushes instead (design §4.1's "n_replicates *is* the toggle" —
    // the same one `start_run` call, a different message shape). No-op
    // stubs for now, matching the scalar handlers' own walking-skeleton
    // precedent above: `fim.gui.app._drain_batch_messages` already
    // calls these for real (Milestone W5's backend half), so a real
    // batch run does not throw a `JavascriptException` calling an
    // undefined function — Screen 2/4's own batch-aware rendering is
    // Milestone W5's remaining, frontend half.
    onBatchProgress() {
        // Overridden once the batch progress screen extension exists.
    },
    onBatchDone() {
        // Overridden once Screen 4 (screens/batch-results.js) exists.
    },
    onBatchCancelled() {
        // Overridden once the batch progress screen extension exists.
    },
    onBatchError() {
        // Overridden once the batch progress screen extension exists.
    },

    /**
     * Wire a "compare two demes directly" selector -- Screens 3 and 4's
     * own shared answer to a large-`d` run's PCA overview (`fim.viz.
     * scatter.panels_from_points`' own PCA fallback once `d` exceeds
     * `PAIRWISE_MAX_DEMES`) not showing any one deme pair directly, and
     * Screen 5's own identical choice extended across a whole animated
     * trajectory rather than one static state. Two axis dropdowns and a
     * "Show pair"/"Show overview" pair of buttons -- what each one
     * *does* is entirely up to the caller's own `onShowPair`/`onShow
     * Overview` (a single-panel redraw for Screens 3/4, a whole-
     * trajectory frame-set swap for Screen 5); this function owns only
     * the dropdown population and the "X and Y cannot match" enable/
     * disable rule every caller shares.
     *
     * @param {Object} config
     * @param {HTMLSelectElement} config.xSelect
     * @param {HTMLSelectElement} config.ySelect
     * @param {HTMLButtonElement} config.showPairButton
     * @param {HTMLButtonElement} config.showOverviewButton
     * @param {HTMLElement} config.container - Hidden entirely when
     *     `demeCount < 2` (a scatter needs two distinct demes).
     * @param {number} config.demeCount
     * @param {(x: number, y: number) => (void|Promise<void>)}
     *     config.onShowPair - Called with the two chosen 1-based deme
     *     numbers on "Show pair" click; may be `async`.
     * @param {() => void} config.onShowOverview - Called on "Show
     *     overview" click.
     */
    wireDemePairSelector(config) {
        const {
            xSelect,
            ySelect,
            showPairButton,
            showOverviewButton,
            container,
            demeCount,
            onShowPair,
            onShowOverview,
        } = config;
        if (!demeCount || demeCount < 2) {
            container.hidden = true;
            return;
        }
        container.hidden = false;
        xSelect.replaceChildren();
        ySelect.replaceChildren();
        for (let deme = 1; deme <= demeCount; deme += 1) {
            const xOption = document.createElement("option");
            xOption.value = String(deme);
            xOption.textContent = `Deme ${deme}`;
            xSelect.appendChild(xOption);
            ySelect.appendChild(xOption.cloneNode(true));
        }
        xSelect.value = "1";
        ySelect.value = "2";

        function updateShowPairEnabled() {
            showPairButton.disabled = xSelect.value === ySelect.value;
        }
        xSelect.onchange = updateShowPairEnabled;
        ySelect.onchange = updateShowPairEnabled;
        updateShowPairEnabled();

        showPairButton.onclick = async () => {
            await onShowPair(Number(xSelect.value), Number(ySelect.value));
        };

        showOverviewButton.onclick = () => {
            onShowOverview();
        };
    },

    /**
     * The native File/Run/Help menu bar's own dispatch target (in-app
     * help design §4.5) -- every native `MenuAction` callback in
     * `fim.gui.app._build_menu` calls exactly one `fim.menu.*` method
     * here via `window.evaluate_js`, the same "no-op stub, overridden by
     * whichever screen owns the real behavior" shape `onRunProgress`/
     * `onBatchDone`/etc. above already established. No item is ever
     * disabled natively (pywebview's own dynamic per-item enable/disable
     * support is unconfirmed): every item stays clickable from any
     * screen, and each method here decides what "clickable" means --
     * navigate-then-act for an always-meaningful item, a plain no-op for
     * a data-dependent one with nothing to act on from the current
     * screen (the same tolerance `cancel-run-button` already has,
     * design §4.5's own table).
     */
    menu: {
        newConfiguration() {
            // Overridden by screens/input.js.
        },
        configureTab() {
            // Overridden by screens/input.js.
        },
        openConfiguration() {
            window.fim.showScreen("screen-input");
            document.getElementById("load-yaml-button").click();
        },
        saveConfiguration() {
            window.fim.showScreen("screen-input");
            document.getElementById("save-yaml-button").click();
        },
        runSimulation() {
            window.fim.showScreen("screen-input");
            document.getElementById("run-button").click();
        },
        openRun() {
            window.fim.showOpenRunScreen();
        },
        cancelRun() {
            document.getElementById("cancel-run-button").click();
        },
        revealOutputFolder() {
            if (!document.getElementById("screen-batch-results").hidden) {
                document.getElementById("batch-open-folder-button").click();
            } else if (!document.getElementById("screen-results").hidden) {
                document.getElementById("open-folder-button").click();
            }
            // Otherwise: no completed run's output folder to reveal from
            // whatever screen is currently showing -- a silent no-op.
        },
        animate() {
            if (!document.getElementById("screen-results").hidden) {
                document.getElementById("animate-button").click();
            }
        },
        help(topic) {
            window.fim.showHelp(topic);
        },
        async setSignificantDigits(digits) {
            // Screen-agnostic (design §4.5's own "always clickable"
            // table), unlike `configureTab`/`newConfiguration`: no
            // screen owns "how many digits does the GUI display", so
            // this calls the bridge directly rather than delegating to
            // whichever screen is currently showing. Purely cosmetic
            // and forward-looking (`Api.set_significant_digits`'s own
            // docstring: "no record" — nothing on disk changes, and an
            // already-open Screen 3/4 is not retroactively reformatted,
            // only the next run's own results).
            const result = await window.pywebview.api.set_significant_digits(digits);
            if (!result.ok) {
                window.alert(`Could not change significant digits: ${result.message}`);
            }
        },
        async openExternal(url) {
            // `window.__fimMenuOpenExternalSettled` -- the same settle-
            // flag fix, and the same reason, as `progress.js`'s own
            // `cancelButton` handler: see that file for the full hazard
            // this closes. A separate flag from `help.js`'s own
            // `__fimHelpExternalLinkSettled` on purpose -- this is an
            // independent call site (the Help menu's "Documentation on
            // GitHub" item), not the in-page Help-screen link, so a test
            // exercising one must not be satisfied by the other having
            // settled.
            window.__fimMenuOpenExternalSettled = false;
            await window.pywebview.api.open_external_link(url);
            window.__fimMenuOpenExternalSettled = true;
        },
        async checkForUpdates() {
            const result = await window.pywebview.api.check_for_updates();
            if (!result.ok) {
                window.alert(`Update check failed: ${result.message}`);
            } else if (result.available) {
                window.alert(
                    `A newer fim release is available: ${result.latest}\n${result.url}`
                );
            } else {
                window.alert(`fim ${result.current} is current.`);
            }
        },
        async about() {
            const info = await window.pywebview.api.get_about_info();
            window.alert(`fim ${info.version}\n${info.license}\n${info.repository}`);
        },
    },
};

window.fim = fim;

async function connectBridge() {
    const status = document.getElementById("bridge-status");
    try {
        const pong = await window.pywebview.api.ping();
        status.textContent = `Bridge connected (${pong}).`;
    } catch (error) {
        status.textContent = `Bridge error: ${error}`;
    }
}

function whenApiReady(callback) {
    if (window.pywebview && window.pywebview.api) {
        callback();
        return;
    }
    window.addEventListener("pywebviewready", callback, { once: true });
}

whenApiReady(connectBridge);
