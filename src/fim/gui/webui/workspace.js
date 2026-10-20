"use strict";

const workspace = (() => {
    const orderedIds = [];
    const documents = new Map();
    const documentByJob = new Map();
    const documentByResource = new Map();
    let selectedId = null;
    let nextId = 1;

    // Existing screens use these globals until each call site migrates.
    const globalAliases = [
        "__fimAboutExternalLinkSettled",
        "__fimBatchReplicateCache",
        "__fimCancelRunSettled",
        "__fimCloseGuardReady",
        "__fimCompareRecentRunsLoaded",
        "__fimCompareResultsReady",
        "__fimDetailsDialogReady",
        "__fimDetailsSaved",
        "__fimExampleLoadSettled",
        "__fimExampleOpenSettled",
        "__fimExamplesDialogReady",
        "__fimExploreReady",
        "__fimExploreSurfaceReady",
        "__fimExploreSweepReady",
        "__fimGroupTogglesPending",
        "__fimHelpExternalLinkSettled",
        "__fimHomeRunLoadSettled",
        "__fimMenuOpenExternalSettled",
        "__fimOpen",
        "__fimOpenFolderSettled",
        "__fimOpenRunRecentRunsLoaded",
        "__fimPresetsListReady",
        "__fimPresetYamlCopyReady",
        "__fimPresetYamlReady",
        "__fimRunSettingsNoticeSettled",
        "__fimRunStartSettled",
        "__fimRunTitleReady",
        "__fimRunViewReady",
        "__fimSavedRunLoadSettled",
        "__fimScrubberPending",
        "__fimSettingsLoaded",
        "__fimSettingsSavesPending",
        "__fimStudyRunsCache",
        "__fimSweepCompareReady",
        "__fimSweepCostReady",
        "__fimSweepEtaReady",
        "__fimSweepFinished",
        "__fimSweepPlanReady",
        "__fimSweepResultsReady",
        "__fimValidationPending",
        "__fimWelcomeDismissSettled",
        "__fimXReady",
    ];

    function documentFor(tabId) {
        const state = documents.get(tabId);
        if (!state) {
            throw new Error(`Unknown document: ${tabId}`);
        }
        return state;
    }

    function signalName(alias) {
        const name = alias
            .replace(/^__fim/, "")
            .replace(/(?:Settled|Ready|Loaded)$/, "");
        return name.charAt(0).toLowerCase() + name.slice(1);
    }

    function createDocument(initialState = {}, options = {}) {
        const tabId = `document-${nextId}`;
        nextId += 1;
        const state = {
            ...initialState,
            tabId,
            kind: initialState.kind ?? "draft",
            label: initialState.label ?? "Untitled",
            resourceKey: initialState.resourceKey ?? null,
            readOnly: initialState.readOnly ?? false,
            capabilities: initialState.capabilities ?? {},
            history: initialState.history ?? ["screen-open-run"],
            historyIndex: initialState.historyIndex ?? 0,
            attachedJobId: initialState.attachedJobId ?? null,
            settleSignals: Object.create(null),
        };
        const resourceOwner = documentByResource.get(state.resourceKey);
        if (state.resourceKey !== null && resourceOwner !== undefined) {
            throw new Error(`Resource is already open: ${state.resourceKey}`);
        }
        const jobOwner = documentByJob.get(state.attachedJobId);
        if (state.attachedJobId !== null && jobOwner !== undefined) {
            throw new Error(`Job is already attached: ${state.attachedJobId}`);
        }
        documents.set(tabId, state);
        orderedIds.push(tabId);

        if (state.resourceKey !== null) {
            documentByResource.set(state.resourceKey, tabId);
        }
        if (state.attachedJobId !== null) {
            documentByJob.set(state.attachedJobId, tabId);
        }
        if (options.select !== false) {
            selectedId = tabId;
        }
        return tabId;
    }

    function closeDocument(tabId) {
        const state = documentFor(tabId);
        const index = orderedIds.indexOf(tabId);
        orderedIds.splice(index, 1);
        documents.delete(tabId);
        if (state.attachedJobId !== null) {
            documentByJob.delete(state.attachedJobId);
        }
        if (state.resourceKey !== null) {
            documentByResource.delete(state.resourceKey);
        }
        if (selectedId === tabId) {
            selectedId = orderedIds[Math.min(index, orderedIds.length - 1)] ?? null;
        }
    }

    function select(tabId) {
        documentFor(tabId);
        selectedId = tabId;
    }

    function setResourceKey(tabId, resourceKey) {
        const state = documentFor(tabId);
        const existingId = documentByResource.get(resourceKey);
        if (
            resourceKey !== null &&
            existingId !== undefined &&
            existingId !== tabId
        ) {
            throw new Error(`Resource is already open: ${resourceKey}`);
        }
        if (state.resourceKey !== null) {
            documentByResource.delete(state.resourceKey);
        }
        state.resourceKey = resourceKey;
        if (resourceKey !== null) {
            documentByResource.set(resourceKey, tabId);
        }
    }

    function attachJob(tabId, jobId) {
        const state = documentFor(tabId);
        const existingId = documentByJob.get(jobId);
        if (jobId !== null && existingId !== undefined && existingId !== tabId) {
            throw new Error(`Job is already attached: ${jobId}`);
        }
        if (state.attachedJobId !== null) {
            documentByJob.delete(state.attachedJobId);
        }
        state.attachedJobId = jobId;
        if (jobId !== null) {
            documentByJob.set(jobId, tabId);
        }
    }

    function settle(tabId, name, value) {
        documentFor(tabId).settleSignals[name] = value;
        return value;
    }

    function settled(tabId, name) {
        return documentFor(tabId).settleSignals[name];
    }

    function recordHistory(tabId, screenId) {
        const state = documentFor(tabId);
        const currentId = state.history[state.historyIndex];
        if (currentId === screenId) {
            return;
        }
        state.history = state.history.slice(0, state.historyIndex + 1);
        state.history.push(screenId);
        state.historyIndex = state.history.length - 1;
    }

    function moveHistory(tabId, offset) {
        const state = documentFor(tabId);
        const nextIndex = state.historyIndex + offset;
        if (nextIndex < 0 || nextIndex >= state.history.length) {
            return null;
        }
        state.historyIndex = nextIndex;
        return state.history[nextIndex];
    }

    function historyState(tabId) {
        const state = documentFor(tabId);
        return {
            back: state.historyIndex > 0,
            forward: state.historyIndex < state.history.length - 1,
        };
    }

    const api = {
        createDocument,
        closeDocument,
        select,
        selectedId: () => selectedId,
        selected: () => (selectedId === null ? null : documentFor(selectedId)),
        get: (tabId) => documents.get(tabId) ?? null,
        ids: () => [...orderedIds],
        documentForJob: (jobId) => documentByJob.get(jobId) ?? null,
        documentForResource: (resourceKey) =>
            documentByResource.get(resourceKey) ?? null,
        setResourceKey,
        attachJob,
        settle,
        settled,
        recordHistory,
        moveHistory,
        historyState,
    };

    const initialId = createDocument();
    selectedId = initialId;

    for (const alias of globalAliases) {
        Object.defineProperty(window, alias, {
            configurable: true,
            enumerable: true,
            get() {
                return selectedId === null
                    ? undefined
                    : settled(selectedId, signalName(alias));
            },
            set(value) {
                if (selectedId !== null) {
                    settle(selectedId, signalName(alias), value);
                }
            },
        });
    }

    return api;
})();

window.fimWorkspace = workspace;
