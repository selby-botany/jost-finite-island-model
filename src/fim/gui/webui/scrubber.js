"use strict";

/* Shared player mechanics (unified-run-view design §3.7, §8 Phase E) --
 * play/pause/scrub through an array of "frames," given a caller-
 * supplied way to draw one. Promoted out of the old, animation-specific
 * Screen 5 (`animation.js`, retired this phase, folded into `screens/
 * run-view-completed.js`) so the same component can back `completed`'s
 * own scrubber here and, design §8 Phase F, `running`'s own live,
 * growing one too -- this module knows the generic shape (an index into
 * an array, a "Generation N" label, play/pause/scrub) and nothing about
 * panels, canvases, or where frames come from; `window.fim.
 * setScrubberFrames`'s own `drawFrame` callback owns all of that.
 *
 * A singleton, not a class/factory -- there is only ever one scrubber
 * visible at a time (the same "one screen" reality every other shared
 * component in this app, `wireDemePairSelector` included, already
 * assumes), so module-level state is simpler than instance state with
 * nothing to gain from the extra generality.
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

function stopScrubber() {
    if (playIntervalId !== null) {
        clearInterval(playIntervalId);
        playIntervalId = null;
    }
    scrubberPlayButton.textContent = "Play";
}

function showCurrentFrame() {
    const frame = frames[currentIndex];
    scrubberRange.value = String(currentIndex);
    scrubberLabel.textContent =
        `Generation ${frame.generation} (frame ${currentIndex + 1} / ${frames.length})`;
    if (onFrame !== null) {
        onFrame(frame, currentIndex);
    }
}

function setCurrentIndex(index) {
    currentIndex = Math.min(Math.max(index, 0), frames.length - 1);
    showCurrentFrame();
}

function stepForward() {
    if (currentIndex >= frames.length - 1) {
        stopScrubber();
        return;
    }
    setCurrentIndex(currentIndex + 1);
}

scrubberPlayButton.addEventListener("click", () => {
    if (playIntervalId !== null) {
        stopScrubber();
        return;
    }
    if (currentIndex >= frames.length - 1) {
        setCurrentIndex(0);
    }
    scrubberPlayButton.textContent = "Pause";
    playIntervalId = setInterval(stepForward, STEP_INTERVAL_MS);
});

scrubberRange.addEventListener("input", () => {
    stopScrubber();
    setCurrentIndex(Number(scrubberRange.value));
});

/**
 * Load a fresh set of frames and (re)enable/disable the controls to
 * match -- the one entry point every caller uses (`run-view-
 * completed.js` today; `run-view-running.js`, design §8 Phase F,
 * calling it again on each new tick with a longer array is how a
 * "live, growing ceiling" scrubber works, with no change needed here).
 *
 * @param {Array<{generation: number}>} newFrames
 * @param {(frame: object, index: number) => void} drawFrame - Called
 *     with the currently-displayed frame whenever it changes (an
 *     explicit scrub, playback, or this call itself) -- owns actually
 *     drawing it; this module knows nothing about panels or canvases.
 */
window.fim.setScrubberFrames = function setScrubberFrames(newFrames, drawFrame) {
    stopScrubber();
    // `onFrame` stays null through the rest of this call -- loading a
    // frame set (typically an async `get_animation_frames` reply
    // landing well after `completed` is already on screen) must never
    // itself repaint the canvas. Whatever the caller is currently
    // showing (the run's own final state, or a chosen deme pair) has
    // to stay put until the visitor actually scrubs or presses Play;
    // otherwise this call races the deme-pair selector and the "Show
    // overview"/"Show pair" buttons and can silently overwrite
    // whichever of them last drew, depending on when the bridge call
    // happens to resolve.
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
    scrubberPlayButton.disabled = true;
    scrubberRange.disabled = true;
    scrubberRange.max = "0";
    scrubberRange.value = "0";
    scrubberLabel.textContent = "";
};
