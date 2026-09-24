"use strict";

/* Shared player mechanics (unified-run-view design §3.7, §8 Phase E,
 * and live supplemental animation design 20260915-claude-sonnet-5-
 * live-animation-scrubber-design.md) -- drag/play/pause through an
 * array of frames across both completed-run replay and live running
 * states.
 *
 * Supports two operational modes:
 * 1. "replay" (completed run): drag or play through sampled trajectory frames.
 * 2. "live" (running simulation): dynamically growing frame buffer with
 *    "live-tracking" by default (pinning to the latest reported generation)
 *    and "inspecting" mode when dragged/played back to a historical generation.
 *
 * 2026-09-23 decluttering pass: the earlier centered "Play"/"Pause"
 * button and the verbose bottom label ("Generation N (frame X / Y)",
 * plus a parenthetical live/inspecting note) are gone. What remains is
 * the slider (dragging it still jumps straight to any frame, exactly
 * as before), a bare generation-number readout, and two triangle
 * buttons flanking the slider's own handle, one per direction, each
 * its own play/pause toggle (`playDirection`, below) starting from
 * wherever the handle currently sits -- see `index.html`'s own comment
 * on `#scrubber-controls` for the fuller rationale.
 */

// A watchable cadence: fast enough to read as motion rather than a
// slideshow, slow enough that individual frames (up to
// `GUI_ANIMATION_MAX_FRAMES` of them) do not blur past unreadably --
// the same constant the Tk-era `AnimationScreen`, and this component's
// own animation.js predecessor, both used.
const STEP_INTERVAL_MS = 150;

// A single-frame set has nothing to play or drag through -- every
// control stays disabled and there is nowhere to move to.
const MINIMUM_FRAMES_TO_ANIMATE = 2;

const scrubberForwardButton = document.getElementById("scrubber-step-forward");
const scrubberBackwardButton = document.getElementById("scrubber-step-backward");
const scrubberRange = document.getElementById("scrubber-range");
const scrubberLabel = document.getElementById("scrubber-label");

let frames = [];
let currentIndex = 0;
let playIntervalId = null;
// +1 while playing forward, -1 while playing backward, 0 while stopped --
// which button (if either) is currently the active play/pause toggle.
let playDirection = 0;
let onFrame = null;
let scrubberMode = "replay";
let isLiveTracking = true;

/**
 * Show the current generation in the readout above the slider. That readout
 * is the one place the generation is named; the graphs no longer repeat it.
 * Empty text clears it.
 *
 * @param {string} text
 */
function setGenerationText(text) {
    scrubberLabel.textContent = text;
}

function setButtonActive(button, active) {
    button.classList.toggle("scrubber-step-active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
}

function stopScrubber() {
    if (playIntervalId !== null) {
        clearInterval(playIntervalId);
        playIntervalId = null;
    }
    playDirection = 0;
    setButtonActive(scrubberForwardButton, false);
    setButtonActive(scrubberBackwardButton, false);
}

function showCurrentFrame(isLiveHead = false) {
    const frame = frames[currentIndex];
    if (!frame) {
        return;
    }
    scrubberRange.value = String(currentIndex);
    setGenerationText(`Generation ${frame.generation}`);
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

function stepBackward() {
    if (currentIndex <= 0) {
        stopScrubber();
        return;
    }
    setCurrentIndex(currentIndex - 1, true);
}

// Each button is its own play/pause toggle for its own direction,
// starting from wherever the slider's handle (`currentIndex`) already
// sits -- whether it got there by a drag or by the other button's own
// playback: clicking a playing button stops it; clicking the other one
// switches direction without needing a separate stop first.
function togglePlay(direction) {
    if (playDirection === direction) {
        stopScrubber();
        return;
    }
    stopScrubber();
    if (direction === 1 && currentIndex >= frames.length - 1) {
        // Looping playback, same as the single "Play" button this
        // replaced: restart from the beginning rather than doing
        // nothing when already parked at the last frame.
        setCurrentIndex(0, false);
    } else if (direction === -1 && currentIndex <= 0) {
        setCurrentIndex(frames.length - 1, false);
    }
    if (scrubberMode === "live" && direction === 1) {
        isLiveTracking = false;
    }
    playDirection = direction;
    setButtonActive(direction === 1 ? scrubberForwardButton : scrubberBackwardButton, true);
    playIntervalId = setInterval(direction === 1 ? stepForward : stepBackward, STEP_INTERVAL_MS);
}

scrubberForwardButton.addEventListener("click", () => togglePlay(1));
scrubberBackwardButton.addEventListener("click", () => togglePlay(-1));

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
 * Return the default-pair scatter panels of the few frames just before
 * the current one, oldest first -- what the scatter's "trail" style draws
 * faded behind the current frame.
 *
 * Only when `panel` is the current frame's own default-pair panel: a pair
 * the botanist chose by hand has no matching history, so it gets none
 * rather than a trail of a different pair.
 *
 * @param {object} panel the panel being drawn
 * @param {number} count how many earlier frames at most
 * @returns {Array<object>}
 */
window.fim.getScrubberTrailPanels = function getScrubberTrailPanels(panel, count) {
    const current = frames[currentIndex];
    if (!current) {
        return [];
    }
    const own = (current.panels && current.panels[0]) || current.pairPanel;
    if (own !== panel && current.pairPanel !== panel) {
        return [];
    }
    return frames
        .slice(Math.max(0, currentIndex - count), currentIndex)
        .map((frame) => (frame.panels && frame.panels[0]) || frame.pairPanel)
        .filter(Boolean);
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
    scrubberForwardButton.disabled = !canAnimate;
    scrubberBackwardButton.disabled = !canAnimate;
    scrubberRange.disabled = !canAnimate;
    scrubberRange.max = String(Math.max(frames.length - 1, 0));
    if (isLiveTracking) {
        currentIndex = frames.length - 1;
        showCurrentFrame(true);
    } else {
        scrubberRange.value = String(currentIndex);
        const curFrame = frames[currentIndex];
        if (curFrame) {
            setGenerationText(`Generation ${curFrame.generation}`);
        }
    }
};

/**
 * Load a fresh set of frames and (re)enable/disable the controls to
 * match -- the entry point for completed-run playback.
 *
 * Does not draw: the caller has already painted whichever frame it
 * wants on screen, and `startIndex` tells this module which one that
 * was so the slider and the label agree with it.
 *
 * @param {Array<{generation: number}>} newFrames
 * @param {(frame: object, index: number) => void} drawFrame - Called
 *     with the currently-displayed frame whenever it changes (an
 *     explicit drag, playback, or this call itself) -- owns actually
 *     drawing it; this module knows nothing about panels or canvases.
 * @param {number} [startIndex=0] - Which frame is already on screen.
 *     Clamped into range, so a caller may pass `frames.length - 1`
 *     without first checking that the list is non-empty.
 */
window.fim.setScrubberFrames = function setScrubberFrames(newFrames, drawFrame, startIndex = 0) {
    stopScrubber();
    scrubberMode = "replay";
    isLiveTracking = false;
    onFrame = null;
    frames = newFrames;
    currentIndex = Math.min(Math.max(startIndex, 0), Math.max(frames.length - 1, 0));
    const canAnimate = frames.length >= MINIMUM_FRAMES_TO_ANIMATE;
    scrubberForwardButton.disabled = !canAnimate;
    scrubberBackwardButton.disabled = !canAnimate;
    scrubberRange.disabled = !canAnimate;
    scrubberRange.max = String(Math.max(frames.length - 1, 0));
    scrubberRange.value = String(currentIndex);
    setGenerationText(
        frames.length > 0 ? `Generation ${frames[currentIndex].generation}` : ""
    );
    // Only now does dragging/playback start actually drawing.
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
    scrubberForwardButton.disabled = true;
    scrubberBackwardButton.disabled = true;
    scrubberRange.disabled = true;
    scrubberRange.max = "0";
    scrubberRange.value = "0";
    setGenerationText("");
};
