"use strict";

/* App-wide bootstrap and the small shared namespace every screen script
 * attaches its own bridge-push handlers to (design doc §4, §7.2-§7.4).
 *
 * `window.fim` exists for exactly one reason: `Api.start_run` (design
 * §3.4's "push, not poll") calls `window.evaluate_js("fim.onRunProgress(
 * ...)")` from a background thread whenever a run reports progress --
 * that call needs a stable, always-present global to land on regardless
 * of which screen happens to be showing, so `showScreen`/`onRun*` live
 * here rather than inside `screens/run-view-running.js` itself (which
 * only *implements* what `onRunProgress` etc. actually do). `showScreen`
 * itself now toggles among only three top-level screens (`screen-run`,
 * `screen-open-run`, `screen-help`, unified-run-view design §3.2/§8
 * Phase E) rather than the six it originally did -- the run view's own
 * three *states* (`getRunViewState`/`setRunViewState` below) are a
 * separate, narrower concept from which top-level screen is showing.
 */

// `initial` | `running` | `completed` (design §3.2.1) -- which of
// `run-view-initial.js`/`run-view-running.js`/`run-view-completed.js`
// currently owns `screen-run`'s own state-conditional content. Each of
// those files' own `enterXState()` function is what actually flips
// this (and the DOM to match); nothing here decides transitions on its
// own, this is only the one shared place every file can both set and
// read it.
let runViewState = "initial";

// The most recently completed run's own output directory, `null` until
// one exists -- `run-view-completed.js`'s own `enterCompletedState`
// sets this; `run-view-controls.js`'s "Open output folder" button and
// `fim.menu.revealOutputFolder` both read it via the getter below,
// rather than each screen tracking its own copy the way `results.js`/
// `batch-results.js` used to (one state model, not two, design §3.2.5,
// applied here too).
let completedOutputDirectory = null;

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

    /** @returns {"initial"|"running"|"completed"} */
    getRunViewState() {
        return runViewState;
    },

    /** @param {"initial"|"running"|"completed"} state */
    setRunViewState(state) {
        runViewState = state;
    },

    /** @returns {string|null} */
    getCompletedOutputDirectory() {
        return completedOutputDirectory;
    },

    /** @param {string|null} outputDirectory */
    setCompletedOutputDirectory(outputDirectory) {
        completedOutputDirectory = outputDirectory;
    },

    onRunProgress() {
        // Overridden by screens/run-view-running.js.
    },
    onRunDone() {
        // Overridden by screens/run-view-running.js.
    },
    onRunCancelled() {
        // Overridden by screens/run-view-running.js.
    },
    onRunError() {
        // Overridden by screens/run-view-running.js.
    },

    // The batch (`n_replicates > 1`) counterparts `Api._start_batch_run`
    // pushes instead (design §4.1's "n_replicates *is* the toggle" —
    // the same one `start_run` call, a different message shape).
    onBatchProgress() {
        // Overridden by screens/run-view-running.js.
    },
    onBatchDone() {
        // Overridden by screens/run-view-running.js.
    },
    onBatchCancelled() {
        // Overridden by screens/run-view-running.js.
    },
    onBatchError() {
        // Overridden by screens/run-view-running.js.
    },
    openConfiguration() {
        // Overridden by screens/run-view-controls.js.
    },
    saveConfiguration() {
        // Overridden by screens/run-view-controls.js.
    },

    /**
     * Wire the axis selectors used to choose a focused deme pair.
     *
     * The selectors apply immediately on change; there is no separate
     * "Show pair" button. "Show overview" remains explicit so the user
     * can return from a single-pair view to the default multi-panel
     * overview.
     *
     * @param {Object} config
     * @param {HTMLSelectElement} config.xSelect
     * @param {HTMLSelectElement} config.ySelect
     * @param {HTMLButtonElement} config.showOverviewButton
     * @param {HTMLElement} config.container - Hidden when `demeCount < 2`.
     * @param {number} config.demeCount
     * @param {(x: number, y: number) => (void|Promise<void>)} config.onShowPair
     * @param {() => (void|Promise<void>)} config.onShowOverview
     */
    wireDemePairSelector(config) {
        const {
            xSelect,
            ySelect,
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

        function forceDistinctSelect(changed) {
            if (xSelect.value !== ySelect.value) {
                return;
            }
            const other = changed === xSelect ? ySelect : xSelect;
            const current = Number(changed.value);
            const replacement = current === demeCount ? current - 1 : current + 1;
            other.value = String(replacement);
        }

        async function applyPairSelection() {
            await onShowPair(Number(xSelect.value), Number(ySelect.value));
        }

        xSelect.onchange = async () => {
            forceDistinctSelect(xSelect);
            await applyPairSelection();
        };
        ySelect.onchange = async () => {
            forceDistinctSelect(ySelect);
            await applyPairSelection();
        };

        showOverviewButton.onclick = async () => {
            await onShowOverview();
        };
    },

    /**
     * Wire one Configure modal's own close affordances (unified-run-view
     * design §3.1.1) the first time it is opened: a native `<dialog>`
     * already gives focus-trapping and Escape-to-close for free once
     * shown via `showModal()`, but backdrop-click-to-close is not
     * automatic -- a click that lands on the dialog element itself
     * (rather than on any of its content) is exactly a click on the
     * `::backdrop`, since the content box is what the content elements
     * themselves absorb the click on. Idempotent (`dataset.fimWired`)
     * so a modal reopened many times only gets one set of listeners.
     * @param {string} dialogId
     */
    wireModal(dialogId) {
        const dialog = document.getElementById(dialogId);
        if (dialog === null || dialog.dataset.fimWired === "true") {
            return;
        }
        dialog.dataset.fimWired = "true";
        dialog.addEventListener("click", (event) => {
            if (event.target === dialog) {
                dialog.close();
            }
        });
        const closeButton = dialog.querySelector("[data-modal-close]");
        if (closeButton !== null) {
            closeButton.addEventListener("click", () => dialog.close());
        }
    },

    /**
     * Open one Configure section's modal by name (`modal-<name>`),
     * wiring its close behavior on first use. The Configure menu's own
     * dispatch target for every section §3.1.3 has not promoted to a
     * direct value-selector leaf, and `screens/run-view-controls.js`'s
     * own error-routing (an invalid field on "Run simulation") for the
     * same set.
     * @param {string} name
     */
    openConfigModal(name) {
        const dialogId = `modal-${name}`;
        window.fim.wireModal(dialogId);
        const dialog = document.getElementById(dialogId);
        if (dialog !== null) {
            dialog.showModal();
        }
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
            // Overridden by screens/run-view-initial.js.
        },
        configureTab() {
            // Overridden by screens/config-modals.js.
        },
        openConfiguration() {
            window.fim.showScreen("screen-run");
            window.fim.openConfiguration();
        },
        saveConfiguration() {
            window.fim.showScreen("screen-run");
            window.fim.saveConfiguration();
        },
        runSimulation() {
            window.fim.showScreen("screen-run");
            document.getElementById("run-button").click();
        },
        openRun() {
            window.fim.showOpenRunScreen();
        },
        cancelRun() {
            document.getElementById("cancel-run-button").click();
        },
        revealOutputFolder() {
            // One button now, regardless of scalar/batch (design §8
            // Phase E): `onOpenFolderClicked`'s own no-op-if-nothing-
            // completed-yet check already covers "no completed run's
            // output folder to reveal" -- no screen/state check needed
            // here at all.
            document.getElementById("open-folder-button").click();
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
            // already-showing `completed` view is not retroactively
            // reformatted, only the next run's own results).
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
