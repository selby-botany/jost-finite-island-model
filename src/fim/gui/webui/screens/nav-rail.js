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
const configureBanner = document.getElementById("configure-banner");
const documentSwitcher = document.getElementById("document-switcher");
const documentNewButton = document.getElementById("document-new-button");
const closeDocumentDialog = document.getElementById(
    "modal-confirm-close-document"
);
const closeDocumentMessage = document.getElementById(
    "confirm-close-document-message"
);
const closeDocumentError = document.getElementById(
    "confirm-close-document-error"
);
const closeDocumentKeepButton = document.getElementById(
    "confirm-close-document-keep-button"
);
const closeDocumentDiscardButton = document.getElementById(
    "confirm-close-document-discard-button"
);
const closeDocumentSaveButton = document.getElementById(
    "confirm-close-document-save-button"
);
const closeDocumentJobButton = document.getElementById(
    "confirm-close-document-job-button"
);
const documentLimitNotice = document.getElementById("document-limit-notice");
const documentLimitMessage = document.getElementById("document-limit-message");
const documentCloseResultsButton = document.getElementById(
    "document-close-results-button"
);
const documentContextMenu = document.getElementById("document-context-menu");
const documentCloseOtherTabsButton = document.getElementById(
    "document-close-other-tabs"
);
const documentContextCloseResultsButton = document.getElementById(
    "document-close-all-results"
);
const DOCUMENT_SOFT_LIMIT = 12;
let documentSwitcherFocusId = null;
let closeDocumentRequestId = null;
let documentContextTabId = null;
let bulkCloseQueue = null;

/** Describe a document without treating its label as an identity. */
function documentSwitcherLabel(state) {
    const name =
        state.runControls?.runName?.trim() ||
        state.runTitle?.state?.runName ||
        (state.label === "Untitled" ? "New configuration" : state.label);
    const phase = state.runTitle?.state?.phase;
    const status = phase && phase !== "initial" ? phase : state.kind;
    const priority = state.effectivePriority
        ? ` (${state.effectivePriority[0].toUpperCase()}${state.effectivePriority.slice(1)} priority)`
        : "";
    return `${name} - ${status}${priority}${state.formDirty ? " *" : ""}`;
}

/** Select an existing document without starting or stopping its job. */
function activateWorkspaceDocument(tabId) {
    const workspace = window.fim.workspace;
    workspace.select(tabId);
    const state = workspace.get(tabId);
    const destination = state.history[state.historyIndex];
    window.fim.showScreen(
        ["screen-configure", "screen-run", "screen-sweep"].includes(destination)
            ? destination
            : state.kind === "draft" ? "screen-configure" : "screen-run",
        { recordHistory: false }
    );
    document.getElementById(`document-select-${tabId}`).scrollIntoView({
        block: "nearest",
        inline: "nearest",
    });
}

window.fim.activateWorkspaceDocument = activateWorkspaceDocument;

function closeDocumentHasActiveJob(state) {
    return (
        state.attachedJobId !== null &&
        state.attachedJobId !== undefined &&
        state.jobState !== "finished"
    );
}

function advanceBulkCloseQueue() {
    if (bulkCloseQueue === null || bulkCloseQueue.currentId !== null) {
        return;
    }
    while (bulkCloseQueue.index < bulkCloseQueue.tabIds.length) {
        const tabId = bulkCloseQueue.tabIds[bulkCloseQueue.index];
        bulkCloseQueue.index += 1;
        if (!window.fim.workspace.get(tabId)) {
            continue;
        }
        bulkCloseQueue.currentId = tabId;
        window.fim.requestCloseDocument(tabId);
        return;
    }
    bulkCloseQueue = null;
}

function startBulkClose(tabIds) {
    if (bulkCloseQueue !== null) {
        return;
    }
    bulkCloseQueue = {
        tabIds: [...new Set(tabIds)],
        index: 0,
        currentId: null,
    };
    advanceBulkCloseQueue();
}

window.fim.closeOtherDocuments = function closeOtherDocuments(keepTabId) {
    const workspace = window.fim.workspace;
    if (!workspace.documentTabsEnabled()) {
        return;
    }
    const preservedId = keepTabId ?? workspace.selectedId();
    if (preservedId === null || !workspace.get(preservedId)) {
        return;
    }
    activateWorkspaceDocument(preservedId);
    startBulkClose(workspace.ids().filter((tabId) => tabId !== preservedId));
};

window.fim.closeAllResultDocuments = function closeAllResultDocuments() {
    const workspace = window.fim.workspace;
    if (!workspace.documentTabsEnabled()) {
        return;
    }
    const resultIds = workspace.ids().filter((tabId) => {
        const kind = workspace.get(tabId).kind;
        return kind === "result" || kind === "sweep-results";
    });
    startBulkClose(resultIds);
};

function renderCloseDocumentDialog(tabId) {
    const state = window.fim.workspace.get(tabId);
    if (!state) {
        closeDocumentDialog.close();
        closeDocumentRequestId = null;
        return;
    }
    closeDocumentRequestId = tabId;
    closeDocumentError.hidden = true;
    closeDocumentError.textContent = "";
    closeDocumentDiscardButton.hidden = !state.formDirty;
    closeDocumentSaveButton.hidden = !state.formDirty;
    closeDocumentJobButton.hidden =
        !closeDocumentHasActiveJob(state) && !state.closeReleaseFailed;
    if (state.closeReleaseFailed) {
        closeDocumentJobButton.textContent = "Retry close";
    }
    if (state.jobState === "starting") {
        closeDocumentMessage.textContent =
            "This document is starting a job. It will stay open until the start is accepted or refused.";
        closeDocumentDiscardButton.hidden = true;
        closeDocumentSaveButton.hidden = true;
        closeDocumentJobButton.hidden = true;
    } else if (closeDocumentHasActiveJob(state)) {
        const savedRun = state.jobKind === "saved-run";
        closeDocumentMessage.textContent = savedRun
            ? "A saved run is active. Pause it at a checkpoint before closing this document."
            : state.jobKind === "sweep"
              ? "A sweep is active. Stop it before closing this document; its Study will remain available."
              : "A run is active. Stop it before closing this document.";
        closeDocumentJobButton.textContent = savedRun
            ? "Pause and close"
            : "Stop and close";
    } else if (state.closeReleaseFailed) {
        closeDocumentMessage.textContent =
            "The job has stopped, but its document could not be released. Retry closing it.";
        closeDocumentJobButton.textContent = "Retry close";
    } else if (state.formDirty) {
        closeDocumentMessage.textContent =
            "This configuration has unsaved changes. Save it as a saved run, discard the changes, or keep working.";
    } else {
        closeDocumentMessage.textContent = "Close this document?";
        closeDocumentRequestId = null;
        void finishClosingDocument(tabId);
        return;
    }
    if (!closeDocumentDialog.open) {
        closeDocumentDialog.showModal();
    }
}

async function finishClosingDocument(tabId) {
    const state = window.fim.workspace.get(tabId);
    if (!state) {
        return;
    }
    const jobId = state.attachedJobId;
    try {
        if (jobId) {
            await window.fim.jobs.closeDocument(jobId);
        }
        state.closeReleaseFailed = false;
        const wasSelected = window.fim.workspace.selectedId() === tabId;
        window.fim.workspace.closeDocument(tabId);
        if (closeDocumentRequestId === tabId) {
            closeDocumentRequestId = null;
            closeDocumentDialog.close();
        }
        if (wasSelected) {
            const nextId = window.fim.workspace.selectedId();
            if (nextId === null) {
                window.fim.menu.openRun();
            } else {
                activateWorkspaceDocument(nextId);
            }
        }
        window.fim.refreshUnsavedEdits();
        if (bulkCloseQueue?.currentId === tabId) {
            bulkCloseQueue.currentId = null;
            advanceBulkCloseQueue();
        }
    } catch (error) {
        if (jobId && state.jobState === "finished") {
            state.closeReleaseFailed = true;
            closeDocumentJobButton.hidden = false;
            closeDocumentJobButton.textContent = "Retry close";
        }
        closeDocumentJobButton.disabled = false;
        closeDocumentKeepButton.disabled = false;
        closeDocumentSaveButton.disabled = false;
        closeDocumentError.textContent =
            error instanceof Error ? error.message : String(error);
        closeDocumentError.hidden = false;
    }
}

async function waitForJobFinished(jobId) {
    for (let attempt = 0; attempt < 300; attempt += 1) {
        const snapshot = await window.pywebview.api.get_job_snapshot(jobId);
        if (!snapshot.ok) {
            throw new Error(snapshot.message || "Could not check the job state.");
        }
        if (snapshot.snapshot?.type === "finished") {
            return;
        }
        await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error("The job did not stop before the close wait expired.");
}

async function closeDocumentWithJob(tabId) {
    const state = window.fim.workspace.get(tabId);
    if (!state || !state.attachedJobId) {
        return;
    }
    closeDocumentJobButton.disabled = true;
    closeDocumentKeepButton.disabled = true;
    closeDocumentError.hidden = true;
    try {
        if (state.jobState !== "finished" && state.jobState !== "terminal") {
            let result;
            if (state.jobKind === "saved-run") {
                result = await window.pywebview.api.pause_run(
                    state.attachedJobId
                );
                if (!result.ok) {
                    throw new Error(result.message || "Could not pause the saved run.");
                }
            } else if (state.jobKind === "sweep") {
                await window.pywebview.api.cancel_sweep(
                    state.attachedJobId
                );
            } else {
                await window.pywebview.api.cancel_run(
                    state.attachedJobId
                );
            }
        }
        if (state.jobState !== "finished") {
            await waitForJobFinished(state.attachedJobId);
            state.jobState = "finished";
        }
        state.closeReleaseFailed = false;
        await finishClosingDocument(tabId);
    } catch (error) {
        closeDocumentError.textContent =
            error instanceof Error ? error.message : String(error);
        closeDocumentError.hidden = false;
        closeDocumentJobButton.disabled = false;
        closeDocumentKeepButton.disabled = false;
    }
}

window.fim.requestCloseDocument = function requestCloseDocument(tabId) {
    const state = window.fim.workspace.get(tabId);
    if (!state) {
        return;
    }
    if (state.jobState === "starting") {
        state.closeAfterStart = true;
        renderCloseDocumentDialog(tabId);
        return;
    }
    if (!state.formDirty && !closeDocumentHasActiveJob(state)) {
        void finishClosingDocument(tabId);
        return;
    }
    renderCloseDocumentDialog(tabId);
};

window.fim.resolveCloseDocument = async function resolveCloseDocument(action) {
    const tabId = closeDocumentRequestId;
    const state = tabId === null ? null : window.fim.workspace.get(tabId);
    if (!state) {
        closeDocumentDialog.close();
        closeDocumentRequestId = null;
        return;
    }
    if (action === "keep") {
        closeDocumentRequestId = null;
        closeDocumentDialog.close();
        if (bulkCloseQueue?.currentId === tabId) {
            bulkCloseQueue.currentId = null;
            advanceBulkCloseQueue();
        }
        return;
    }
    if (action === "save") {
        closeDocumentSaveButton.disabled = true;
        try {
            window.fim.workspace.select(tabId);
            const result = await window.fim.saveFormForLater();
            if (!result.ok) {
                throw new Error(result.message || "Could not save this configuration.");
            }
            if (state.formDirty) {
                throw new Error(
                    "The sweep setup is not saved with the run. Discard it or keep working."
                );
            }
            if (closeDocumentHasActiveJob(state)) {
                renderCloseDocumentDialog(tabId);
                return;
            }
            await finishClosingDocument(tabId);
        } catch (error) {
            closeDocumentError.textContent =
                error instanceof Error ? error.message : String(error);
            closeDocumentError.hidden = false;
        } finally {
            closeDocumentSaveButton.disabled = false;
        }
        return;
    }
    if (action === "discard") {
        if (closeDocumentHasActiveJob(state)) {
            state.formDirty = false;
            renderCloseDocumentDialog(tabId);
            return;
        }
        await finishClosingDocument(tabId);
        return;
    }
    if (action === "job") {
        await closeDocumentWithJob(tabId);
    }
};

/** Render keyed buttons so model updates never discard keyboard focus. */
function renderDocumentSwitcher() {
    const workspace = window.fim.workspace;
    documentSwitcher.hidden = !workspace.documentTabsEnabled();
    const ids = workspace.ids();
    const resultCount = ids.filter((tabId) => {
        const kind = workspace.get(tabId).kind;
        return kind === "result" || kind === "sweep-results";
    }).length;
    documentLimitNotice.hidden =
        documentSwitcher.hidden || ids.length <= DOCUMENT_SOFT_LIMIT;
    documentLimitMessage.textContent =
        `${ids.length} documents are open. Close all results to reduce the count.`;
    documentCloseResultsButton.disabled = resultCount === 0;
    documentCloseResultsButton.title =
        resultCount === 0 ? "There are no result documents to close." : "";
    if (documentContextTabId !== null && !ids.includes(documentContextTabId)) {
        closeDocumentContextMenu();
    }
    if (documentSwitcher.hidden) {
        return;
    }
    if (
        !ids.includes(documentSwitcherFocusId) ||
        !documentSwitcher.contains(document.activeElement)
    ) {
        documentSwitcherFocusId = workspace.selectedId() ?? ids[0] ?? null;
    }
    for (const item of documentSwitcher.querySelectorAll(
        ".document-switcher-item"
    )) {
        if (!ids.includes(item.dataset.tabId)) {
            item.remove();
        }
    }
    const labels = ids.map((tabId) => documentSwitcherLabel(workspace.get(tabId)));
    ids.forEach((tabId, index) => {
        let item = document.getElementById(`document-item-${tabId}`);
        if (!item) {
            item = document.createElement("div");
            item.id = `document-item-${tabId}`;
            item.className = "document-switcher-item";
            item.dataset.tabId = tabId;
            const button = document.createElement("button");
            button.type = "button";
            button.id = `document-select-${tabId}`;
            button.className = "document-select";
            button.addEventListener("click", () => {
                documentSwitcherFocusId = tabId;
                activateWorkspaceDocument(tabId);
                button.focus();
            });
            const close = document.createElement("button");
            close.type = "button";
            close.className = "document-close";
            close.textContent = "\u00d7";
            close.title = "Close this document";
            close.addEventListener("click", (event) => {
                event.stopPropagation();
                window.fim.requestCloseDocument(tabId);
            });
            item.append(button, close);
            documentSwitcher.insertBefore(item, documentNewButton);
        }
        const state = workspace.get(tabId);
        const label = labels.filter((value) => value === labels[index]).length > 1
            ? `${labels[index]} (${tabId})`
            : labels[index];
        const button = item.querySelector(".document-select");
        button.textContent = label;
        button.title = `${label}${state.resourceKey ? `\n${state.resourceKey}` : ""}`;
        button.setAttribute("aria-current", String(workspace.selectedId() === tabId));
        button.tabIndex = tabId === documentSwitcherFocusId ? 0 : -1;
        item.querySelector(".document-close").setAttribute("aria-label", `Close ${label}`);
    });
}

function closeDocumentContextMenu(restoreFocus = false) {
    documentContextMenu.hidden = true;
    const tabId = documentContextTabId;
    documentContextTabId = null;
    if (restoreFocus && tabId && window.fim.workspace.get(tabId)) {
        document.getElementById(`document-select-${tabId}`).focus();
    }
}

function showDocumentContextMenu(event, tabId) {
    event.preventDefault();
    documentContextTabId = tabId;
    documentContextMenu.hidden = false;
    const bounds = documentContextMenu.getBoundingClientRect();
    documentContextMenu.style.left =
        `${Math.max(8, Math.min(event.clientX, window.innerWidth - bounds.width - 8))}px`;
    documentContextMenu.style.top =
        `${Math.max(8, Math.min(event.clientY, window.innerHeight - bounds.height - 8))}px`;
    documentCloseOtherTabsButton.focus();
}

/** Rove focus without changing selection; native Enter/Space activate buttons. */
function onDocumentSwitcherKeydown(event) {
    const button = event.target.closest(".document-select");
    if (!button || event.altKey || event.ctrlKey || event.metaKey) {
        return;
    }
    const ids = window.fim.workspace.ids();
    const index = ids.indexOf(button.parentElement.dataset.tabId);
    if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        button.click();
        return;
    }
    if (event.key === "Delete") {
        event.preventDefault();
        window.fim.requestCloseDocument(button.parentElement.dataset.tabId);
        return;
    }
    const targets = {
        ArrowLeft: (index + ids.length - 1) % ids.length,
        ArrowRight: (index + 1) % ids.length,
        Home: 0,
        End: ids.length - 1,
    };
    if (!Object.hasOwn(targets, event.key)) {
        return;
    }
    event.preventDefault();
    documentSwitcherFocusId = ids[targets[event.key]];
    renderDocumentSwitcher();
    const target = document.getElementById(`document-select-${documentSwitcherFocusId}`);
    target.focus();
    target.scrollIntoView({ block: "nearest", inline: "nearest" });
}

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
    if (screenId === "screen-sweep-compare") {
        // Comparing sweeps starts from the studies on Home.
        return "home";
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
 * Return the number of distinct allele IDs an explicit `p_0`'s own JSON
 * text names, across every deme and locus -- `"explicit p₀"` mode's own
 * contribution to `formatAlleleCountSummary`, below.
 * @param {string} p0Json `field-p0_json`'s own current value
 *     (`webui/screens/p0-grid.js`'s own serialized `{alleleId:
 *     frequency}`-per-cell shape, one row per deme, one column per
 *     locus).
 * @returns {string} `"—"` if `p0Json` is empty, not valid JSON, or
 *     names no alleles at all.
 */
function distinctAlleleCountFromP0Json(p0Json) {
    let demes;
    try {
        demes = JSON.parse(p0Json || "[]");
    } catch {
        return "—";
    }
    const ids = new Set();
    for (const deme of demes) {
        for (const locus of deme) {
            for (const id of Object.keys(locus)) {
                ids.add(id);
            }
        }
    }
    return ids.size > 0 ? String(ids.size) : "—";
}

/**
 * Format the parameter strip's own `alleles` text: the configured
 * starting allele count, mirroring `fim.gui.app._initial_allele_count_
 * text`'s own identical per-mode derivation for a completed run's
 * `SimulationParams`.
 *
 * `initial_allele_count` (the Dirichlet draw's own founding count) is
 * also the right answer for `"equilibrium split"` mode -- its own
 * ancestral population draws its own founding alleles from that same
 * field before forwarding to equilibrium (`fim.model.initial.
 * EquilibriumSplitInitialCondition`) -- so only `"explicit p₀"` and
 * `"fixed per deme"` need their own derivation instead, since both set
 * an explicit `p_0` that `initial_allele_count` has nothing to do with.
 * @param {Record<string, string>} values
 * @returns {string}
 */
function formatAlleleCountSummary(values) {
    const mode = values.initial_conditions_mode;
    if (mode === "explicit_p0") {
        return distinctAlleleCountFromP0Json(values.p0_json);
    }
    if (mode === "fixed_per_deme") {
        if (values.fixed_per_deme_choice === "all_different") {
            const d = Number(values.d);
            return Number.isInteger(d) && d > 0 ? String(d) : "—";
        }
        return values.fixed_per_deme_choice === "all_but_one" ? "2" : "1";
    }
    return values.initial_allele_count || "—";
}

/**
 * Format the parameter strip's own `loci` text: the configured locus
 * length, or its average when the loci do not share one, mirroring
 * `fim.gui.app._locus_length_text`'s own identical formatting (one
 * decimal place, a trailing `.0` trimmed) for a completed run's
 * `SimulationParams`.
 * @param {Record<string, string>} values
 * @returns {string}
 */
function formatLocusLengthSummary(values) {
    let lengths;
    if (values.loci_mode === "custom") {
        try {
            lengths = JSON.parse(values.loci_json || "[]").map((locus) =>
                Number(locus.length)
            );
        } catch {
            lengths = [];
        }
    } else {
        lengths = (values.locus_lengths || "")
            .split(",")
            .map((item) => item.trim())
            .filter((item) => item !== "")
            .map(Number);
    }
    lengths = lengths.filter((length) => Number.isFinite(length));
    if (lengths.length === 0) {
        return "—";
    }
    if (new Set(lengths).size === 1) {
        return String(lengths[0]);
    }
    const average = lengths.reduce((sum, length) => sum + length, 0) / lengths.length;
    const rounded = average.toFixed(1);
    return `avg ${rounded.endsWith(".0") ? rounded.slice(0, -2) : rounded}`;
}

/**
 * Format the FIM-parameters strip's own compact `N`/`d`/`m`/`mu`/
 * `alleles`/`loci` text (design §3.2: "always shows the current
 * configuration's N, d, m, and mu in compact, always-current form...
 * a compact summary" for a non-scalar shape). Pure and synchronous --
 * every value it reads is already on the page (`collectFormValues`'s
 * own live `FormData` scan), no bridge round trip needed just to keep
 * this strip current.
 * @param {Record<string, string>} values
 * @returns {{N: string, d: string, m: string, mu: string,
 *     alleles: string, loci: string}}
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

    return {
        N: n,
        d,
        m,
        mu,
        alleles: formatAlleleCountSummary(values),
        loci: formatLocusLengthSummary(values),
    };
}

/**
 * Refresh the parameter strip from the form's current values. Called
 * from `revalidate()` (`config-modals.js`), which already runs on every
 * field change, so the strip needs no event listener of its own.
 * @param {Record<string, string>} values
 */
function updateParameterStrip(values) {
    writeParameterStripSlots(formatParameterStripSummary(values));
}

window.fim.updateParameterStrip = updateParameterStrip;

/**
 * Write `{N, d, m, mu, alleles, loci}` straight into the strip's own
 * six slots -- shared by `updateParameterStrip` (above, from the live
 * Configure form) and `updateParameterStripFromSummary` (below, from an
 * already-displayed run's own configuration), so the two can never
 * format the same six slots two different ways. A summary missing a
 * key (a bridge payload this page predates) simply leaves that slot's
 * own last text in place, rather than clearing it to blank.
 * @param {{N?: string, d?: string, m?: string, mu?: string,
 *     alleles?: string, loci?: string}} summary
 */
function writeParameterStripSlots(summary) {
    for (const key of /** @type {const} */ (
        ["N", "d", "m", "mu", "alleles", "loci"]
    )) {
        const slot = document.getElementById(`parameter-strip-${key}`);
        if (slot !== null && summary[key] !== undefined) {
            slot.textContent = summary[key];
        }
    }
}

/**
 * Set the parameter strip from a *displayed run's own* configuration --
 * `Api._run_config_summary`'s own already-formatted `{N, d, m, mu,
 * alleles, loci, mutation_model, seed}` strings, exactly as `list_
 * recent_runs`/`compare_runs`/`open_run`/`_pooled_batch_payload`
 * already send them (the strip itself only ever reads its own six
 * slots out of that dict; `mutation_model`/`seed` are Compare's/Home's
 * own, not shown here).
 *
 * Reported live: the strip is a single, always-visible widget shared
 * across every screen, but until this function existed it had exactly
 * one writer (`updateParameterStrip`, above, from the live Configure
 * form) -- so opening a *different*, already-completed run (from Home,
 * a "Generation" re-analysis, or a batch/Study reopen) left the strip
 * showing whatever the Configure form's own current values happened to
 * be, not the run actually on screen (a run submitted at `d=4`, viewed
 * while Configure's own form still read `d=20` from unrelated later
 * edits, showed "d 20" above graphs that were plainly a 4-deme run).
 * `run-view-completed.js`'s own `enterCompletedState` -- the one shared
 * entry point every completed-run view (live-just-finished, reopened,
 * re-analyzed) already funnels through -- calls this with the
 * displayed run's own `configSummary`, and `showConfigureScreen` below
 * calls `revalidate()` to hand the strip back to the live form the
 * moment Configure is shown again, so neither writer's own value is
 * ever left stale on the other's screen.
 * @param {{N: string, d: string, m: string, mu: string, alleles: string,
 *     loci: string}} summary
 */
function updateParameterStripFromSummary(summary) {
    writeParameterStripSlots(summary);
}

window.fim.updateParameterStripFromSummary = updateParameterStripFromSummary;

/**
 * Open the Configure landing destination (interim this phase -- see
 * this file's own module docstring). Exposed on `window.fim` the same
 * way every other destination's own `show*` entry point is, for the
 * rail's click handler below and for `run-view-initial.js`'s "invalid
 * field" routing to reach later if it chooses to; unused outside this
 * file for now.
 *
 * Screen history is owned centrally by `window.fim.showScreen`, so this
 * entry point only shows Configure and refreshes its own Study picker (`run-view-controls.js`'s own `run-study-
 * select` -- Run/Study/Experiment workflow-ergonomics design, `selby/
 * restricted`, item 3), so it reflects anything created since app
 * launch with no restart needed.
 */
async function showConfigureScreen() {
    window.fim.showScreen("screen-configure");
    // Hands the parameter strip back to the live form (`revalidate`,
    // `config-modals.js`) -- without this, arriving here straight from
    // a completed run's own view (`updateParameterStripFromSummary`'s
    // own doc comment) would leave that run's configuration showing
    // above a Configure form that no longer agrees with it.
    await Promise.all([
        revalidate(),
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
    documentSwitcher.addEventListener("keydown", onDocumentSwitcherKeydown);
    documentNewButton.addEventListener("click", () => {
        window.fim.menu.newConfiguration();
    });
    closeDocumentKeepButton.addEventListener("click", () => {
        window.fim.resolveCloseDocument("keep");
    });
    closeDocumentDiscardButton.addEventListener("click", () => {
        window.fim.resolveCloseDocument("discard");
    });
    closeDocumentSaveButton.addEventListener("click", () => {
        window.fim.resolveCloseDocument("save");
    });
    closeDocumentJobButton.addEventListener("click", () => {
        window.fim.resolveCloseDocument("job");
    });
    closeDocumentDialog.addEventListener("cancel", () => {
        closeDocumentRequestId = null;
        bulkCloseQueue = null;
    });
    documentSwitcher.addEventListener("contextmenu", (event) => {
        const item = event.target.closest(".document-switcher-item");
        if (item) {
            showDocumentContextMenu(event, item.dataset.tabId);
        }
    });
    document.addEventListener("pointerdown", (event) => {
        if (
            !documentContextMenu.hidden &&
            !documentContextMenu.contains(event.target)
        ) {
            closeDocumentContextMenu();
        }
    });
    document.addEventListener("keydown", (event) => {
        if (documentContextMenu.hidden) {
            return;
        }
        const items = [
            documentCloseOtherTabsButton,
            documentContextCloseResultsButton,
        ];
        const index = items.indexOf(document.activeElement);
        if (event.key === "Escape") {
            event.preventDefault();
            closeDocumentContextMenu(true);
        } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            const offset = event.key === "ArrowDown" ? 1 : -1;
            items[(index + offset + items.length) % items.length].focus();
        } else if (event.key === "Home" || event.key === "End") {
            event.preventDefault();
            items[event.key === "Home" ? 0 : items.length - 1].focus();
        } else if (event.key === "Tab") {
            closeDocumentContextMenu(true);
        }
    });
    documentCloseResultsButton.addEventListener("click", () => {
        window.fim.closeAllResultDocuments();
    });
    documentCloseOtherTabsButton.addEventListener("click", () => {
        const tabId = documentContextTabId;
        closeDocumentContextMenu();
        window.fim.closeOtherDocuments(tabId);
    });
    documentContextCloseResultsButton.addEventListener("click", () => {
        closeDocumentContextMenu();
        window.fim.closeAllResultDocuments();
    });
    documentSwitcher.addEventListener("focusin", (event) => {
        const button = event.target.closest(".document-select");
        if (button) {
            documentSwitcherFocusId = button.parentElement.dataset.tabId;
            renderDocumentSwitcher();
        }
    });
    runNameInput.addEventListener("input", renderDocumentSwitcher);
    window.fim.workspace.onChange(renderDocumentSwitcher);
    renderDocumentSwitcher();
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
                    window.fim.menu.help();
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
