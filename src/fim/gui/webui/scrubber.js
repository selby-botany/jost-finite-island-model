"use strict";

/* Shared player mechanics (unified-run-view design §3.7, §8 Phase E,
 * and live supplemental animation design 20260915-claude-sonnet-5-
 * live-animation-scrubber-design.md) -- play/pause/scrub through an
 * array of frames across both completed-run replay and live running
 * states.
 *
 * Supports two operational modes:
 * 1. "replay" (completed run): time slider over sampled trajectory frames.
 * 2. "live" (running simulation): dynamically growing frame buffer with
 *    "live-tracking" by default (pinning to the latest reported generation)
 *    and "inspecting" mode when dragged back to a historical generation.
 */

// A watchable cadence: fast enough to read as motion rather than a
// slideshow, slow enough that individual frames (up to
// `GUI_ANIMATION_MAX_FRAMES` of them) do not blur past unreadably --
// the same constant the Tk-era `AnimationScreen`, and this component's
// own animation.js predecessor, both used.
const STEP_INTERVAL_MS = 150;

// A single-frame set has nothing to play -- Play stays disabled and
// scrubbing has nowhere to go.
const MINIMUM_FRAMES_TO_ANIMATE = 2;

const scrubberPlayButton = document.getElementById("scrubber-play-button");
const scrubberRange = document.getElementById("scrubber-range");
const scrubberLabel = document.getElementById("scrubber-label");

let frames = [];
let currentIndex = 0;
let playIntervalId = null;
let onFrame = null;
let scrubberMode = "replay";
let isLiveTracking = true;

function stopScrubber() {
    if (playIntervalId !== null) {
        clearInterval(playIntervalId);
        playIntervalId = null;
    }
    scrubberPlayButton.textContent = "Play";
}

function showCurrentFrame(isLiveHead = false) {
    const frame = frames[currentIndex];
    if (!frame) {
        return;
    }
    scrubberRange.value = String(currentIndex);
    if (scrubberMode === "live" && isLiveTracking) {
        scrubberLabel.textContent =
            `Generation ${frame.generation} (live, frame ${currentIndex + 1} / ${frames.length})`;
    } else if (scrubberMode === "live") {
        scrubberLabel.textContent =
            `Generation ${frame.generation} (inspecting, frame ${currentIndex + 1} / ${frames.length})`;
    } else {
        scrubberLabel.textContent =
            `Generation ${frame.generation} (frame ${currentIndex + 1} / ${frames.length})`;
    }
    if (onFrame !== null) {
        onFrame(frame, currentIndex, isLiveHead);
    }
}

function setCurrentIndex(index, userInitiated = false) {
    if (frames.length === 0) {
        return;
    }
    currentIndex = Math.min(Math.max(index, 0), frames.length - 1);
    if (userInitiated && scrubberMode === "live") {
        isLiveTracking = currentIndex === frames.length - 1;
    }
    showCurrentFrame(scrubberMode === "live" && isLiveTracking);
}

function stepForward() {
    if (currentIndex >= frames.length - 1) {
        stopScrubber();
        if (scrubberMode === "live") {
            isLiveTracking = true;
            showCurrentFrame(true);
        }
        return;
    }
    setCurrentIndex(currentIndex + 1, false);
    if (scrubberMode === "live" && currentIndex === frames.length - 1) {
        isLiveTracking = true;
        stopScrubber();
        showCurrentFrame(true);
    }
}

scrubberPlayButton.addEventListener("click", () => {
    if (playIntervalId !== null) {
        stopScrubber();
        return;
    }
    if (currentIndex >= frames.length - 1) {
        setCurrentIndex(0, false);
    }
    if (scrubberMode === "live") {
        isLiveTracking = false;
    }
    scrubberPlayButton.textContent = "Pause";
    playIntervalId = setInterval(stepForward, STEP_INTERVAL_MS);
});

scrubberRange.addEventListener("input", () => {
    stopScrubber();
    setCurrentIndex(Number(scrubberRange.value), true);
});

/**
 * Report the generation number of every frame the scrubber holds, in
 * slider order.
 *
 * Read-only introspection. Returns generations rather than the frames
 * themselves so callers cannot reach the panel payloads hanging off
 * them, and so crossing the Python bridge stays cheap.
 *
 * @returns {Array<number>}
 */
window.fim.getScrubberGenerations = function getScrubberGenerations() {
    return frames.map((frame) => frame.generation);
};

/**
 * Set the operational mode of the scrubber ("live" | "replay").
 *
 * @param {"live"|"replay"} mode
 */
window.fim.setScrubberMode = function setScrubberMode(mode) {
    scrubberMode = mode;
    if (mode === "live") {
        isLiveTracking = true;
    }
};

/**
 * Append one live frame during an active simulation run and update the
 * controls. If `isLiveTracking` is true, automatically advances the
 * cursor to the newest frame and repaints the view.
 *
 * @param {object} frame
 * @param {(frame: object, index: number, isLiveHead: boolean) => void} drawFrame
 */
window.fim.appendLiveFrame = function appendLiveFrame(frame, drawFrame) {
    scrubberMode = "live";
    onFrame = drawFrame;
    frames.push(frame);
    const canAnimate = frames.length >= MINIMUM_FRAMES_TO_ANIMATE;
    scrubberPlayButton.disabled = !canAnimate;
    scrubberRange.disabled = !canAnimate;
    scrubberRange.max = String(Math.max(frames.length - 1, 0));
    if (isLiveTracking) {
        currentIndex = frames.length - 1;
        showCurrentFrame(true);
    } else {
        scrubberRange.value = String(currentIndex);
        const curFrame = frames[currentIndex];
        if (curFrame) {
            scrubberLabel.textContent =
                `Generation ${curFrame.generation} (inspecting, frame ${currentIndex + 1} / ${frames.length})`;
        }
    }
};

/**
 * Load a fresh set of frames and (re)enable/disable the controls to
 * match -- the entry point for completed-run playback.
 *
 * @param {Array<{generation: number}>} newFrames
 * @param {(frame: object, index: number) => void} drawFrame - Called
 *     with the currently-displayed frame whenever it changes (an
 *     explicit scrub, playback, or this call itself) -- owns actually
 *     drawing it; this module knows nothing about panels or canvases.
 */
window.fim.setScrubberFrames = function setScrubberFrames(newFrames, drawFrame) {
    stopScrubber();
    scrubberMode = "replay";
    isLiveTracking = false;
    onFrame = null;
    frames = newFrames;
    currentIndex = 0;
    const canAnimate = frames.length >= MINIMUM_FRAMES_TO_ANIMATE;
    scrubberPlayButton.disabled = !canAnimate;
    scrubberRange.disabled = !canAnimate;
    scrubberRange.max = String(Math.max(frames.length - 1, 0));
    scrubberRange.value = "0";
    scrubberLabel.textContent =
        frames.length > 0
            ? `Generation ${frames[0].generation} (frame 1 / ${frames.length})`
            : "";
    // Only now does scrubbing/playback start actually drawing.
    onFrame = drawFrame;
};

/**
 * Stop playback and drop the current frame set -- the shared "starting
 * something new" reset every caller needs (a fresh run, a fresh
 * completed-run view), the same role `progress.js`'s own former
 * `resetBatchProgress` already had for its own module state.
 */
window.fim.resetScrubber = function resetScrubber() {
    stopScrubber();
    onFrame = null;
    frames = [];
    currentIndex = 0;
    scrubberMode = "replay";
    isLiveTracking = true;
    scrubberPlayButton.disabled = true;
    scrubberRange.disabled = true;
    scrubberRange.max = "0";
    scrubberRange.value = "0";
    scrubberLabel.textContent = "";
};

