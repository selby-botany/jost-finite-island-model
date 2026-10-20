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
 * Return the index of whichever frame's own generation is closest to
 * `targetGeneration`, breaking a tie toward the earlier frame.
 *
 * Only ever called right after `decimateLiveFramesIfNeeded` has removed
 * frames out from under `currentIndex` -- `frames` is short enough even
 * for a very long run (bounded by `MAX_LIVE_SCRUBBER_FRAMES`) that a
 * linear scan here costs nothing next to how rarely decimation itself
 * runs.
 *
 * @param {number} targetGeneration
 * @returns {number}
 */
function nearestFrameIndexForGeneration(targetGeneration) {
    let nearestIndex = 0;
    let nearestDistance = Infinity;
    for (let index = 0; index < frames.length; index += 1) {
        const distance = Math.abs(frames[index].generation - targetGeneration);
        if (distance < nearestDistance) {
            nearestDistance = distance;
            nearestIndex = index;
        }
    }
    return nearestIndex;
}

/**
 * Keep a live run's own frame buffer bounded, however long the run goes.
 *
 * `appendLiveFrame` below calls this after every push. Below `MAX_LIVE_
 * SCRUBBER_FRAMES` it is a no-op. Once that cap is crossed, the first
 * frame (so the run's own start is never lost) and the newer half
 * (dense, since that is what live tracking and most scrubbing actually
 * look at) are kept whole; the older half -- everything else -- is
 * thinned to every other frame. Because the split point is recomputed
 * from the *current* length every time this runs, repeated crossings
 * thin progressively older stretches again each time they age out of
 * the newer half, so the buffer settles into roughly half the cap full
 * right after a thin and never grows past the cap by more than one
 * frame. `currentIndex`, if it is not tracking the live head, is
 * remapped to the nearest surviving generation -- indices into `frames`
 * are not stable across a thin, only generations are.
 */
function decimateLiveFramesIfNeeded() {
    if (frames.length <= MAX_LIVE_SCRUBBER_FRAMES) {
        return;
    }
    const trackedGeneration = isLiveTracking
        ? null
        : frames[currentIndex]
          ? frames[currentIndex].generation
          : null;
    const first = frames[0];
    const splitIndex = Math.floor(frames.length / 2);
    const olderMiddle = frames.slice(1, splitIndex);
    const newerHalf = frames.slice(splitIndex);
    const thinnedOlderMiddle = olderMiddle.filter((_frame, index) => index % 2 === 0);
    frames = [first, ...thinnedOlderMiddle, ...newerHalf];
    if (trackedGeneration !== null) {
        currentIndex = nearestFrameIndexForGeneration(trackedGeneration);
    }
}

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
    decimateLiveFramesIfNeeded();
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
