"use strict";

/* Screen 5: animated trajectory (design doc §3.8, §4.5) -- a Canvas-redraw
 * player. `Api.get_animation_frames(outputDirectory)` loads every sampled
 * frame's already-2-D points up front, in one bridge call; play, pause,
 * and scrub are then pure client-side JavaScript, driven by `setInterval`
 * -- zero further Python calls, and zero further rendering calls of any
 * kind, during playback.
 *
 * Reached only from Screen 3 (`results.js`'s own "Animate" button click):
 * design §4.5's "Reached only from Screen 3 or Screen 6, never as a
 * standalone entry point" resolves to just Screen 3 in practice here,
 * since Screen 6 (`open-run.js`) always routes through `fim.showResults`
 * first (design §4.6's own "opening a run re-renders Screen 3... Screen 3
 * is what actually offers Animate), so "Back" always returns there.
 *
 * Screens 3/4's own "Compare demes directly" choice -- the default
 * pairwise-grid-or-first-pair view (unified-run-view design §3.6)
 * versus a different explicit raw deme pair -- extends here too
 * (`app.js`'s shared `wireDemePairSelector`), across the whole
 * animated trajectory rather than one static state: picking a pair fires
 * exactly one more bridge call (`get_animation_deme_pair_frames`, the
 * whole sampled set for that one pair, all at once), after which
 * play/pause/scrub stay exactly as call-free as the default view already
 * was. "Show overview" needs no bridge call at all -- the default view's
 * own frames are kept on hand (`defaultFrames`) for exactly that.
 */

// A watchable cadence: fast enough to read as motion rather than a
// slideshow, slow enough that individual frames (up to
// `GUI_ANIMATION_MAX_FRAMES` of them) do not blur past unreadably --
// the same constant the Tk-era `AnimationScreen` used.
const STEP_INTERVAL_MS = 150;

// A single-frame trajectory has nothing to play -- Play stays disabled
// and scrubbing has nowhere to go, the same rule Screen 3's own
// "Animate" button already applies before this screen is ever reached
// (`generationCount <= 1`), checked again here since `Api.get_
// animation_frames`' own frame count is not always identical to
// `generationCount` (frame sampling, `GUI_ANIMATION_MAX_FRAMES`).
const MINIMUM_FRAMES_TO_ANIMATE = 2;

const animationCanvas = document.getElementById("animation-canvas");
const animationGenerationLabel = document.getElementById(
    "animation-generation-label"
);
const playButton = document.getElementById("animation-play-button");
const scrubber = document.getElementById("animation-scrubber");
const backButton = document.getElementById("animation-back-button");
const animationDemePairSelector = document.getElementById(
    "animation-deme-pair-selector"
);
const animationXDeme = document.getElementById("animation-x-deme");
const animationYDeme = document.getElementById("animation-y-deme");
const animationShowPairButton = document.getElementById(
    "animation-show-pair-button"
);
const animationShowOverviewButton = document.getElementById(
    "animation-show-overview-button"
);

let animationOutputDirectory = null;
// The default view's own frames (`panels_from_points`' own automatic
// dispatch: the pairwise grid for `d <= scatter.PAIRWISE_MAX_DEMES`, one
// Deme-1-vs-Deme-2 panel above it -- unified-run-view design §3.6) --
// kept separately from `frames` (whichever set is
// actually being played/scrubbed right now) so "Show overview" can
// switch straight back with no further bridge call, the same "no second
// bridge call needed for that direction" guarantee Screens 3/4's own
// identical selector already gives (`app.js`'s `wireDemePairSelector`).
let defaultFrames = [];
let frames = [];
let currentIndex = 0;
let playIntervalId = null;

function stopPlaying() {
    if (playIntervalId !== null) {
        clearInterval(playIntervalId);
        playIntervalId = null;
    }
    playButton.textContent = "Play";
}

function drawCurrentFrame() {
    const frame = frames[currentIndex];
    animationGenerationLabel.textContent =
        `Generation ${frame.generation} (frame ${currentIndex + 1} / ${frames.length})`;
    scrubber.value = String(currentIndex);
    const panels = frame.panels;
    if (panels && panels.length === 1) {
        drawScatter(animationCanvas, panels[0]);
    } else if (panels && panels.length > 1) {
        // Same "draw every panel" rule `results.js`'s own `showResults`
        // documents (visualization-and-config-editors design §3.1) --
        // supersedes the prior first-panel-only scope line.
        drawScatterGrid(animationCanvas, panels);
    }
}

function setCurrentIndex(index) {
    currentIndex = Math.min(Math.max(index, 0), frames.length - 1);
    drawCurrentFrame();
}

function stepForward() {
    if (currentIndex >= frames.length - 1) {
        stopPlaying();
        return;
    }
    setCurrentIndex(currentIndex + 1);
}

playButton.addEventListener("click", () => {
    if (playIntervalId !== null) {
        stopPlaying();
        return;
    }
    if (currentIndex >= frames.length - 1) {
        setCurrentIndex(0);
    }
    playButton.textContent = "Pause";
    playIntervalId = setInterval(stepForward, STEP_INTERVAL_MS);
});

scrubber.addEventListener("input", () => {
    stopPlaying();
    setCurrentIndex(Number(scrubber.value));
});

backButton.addEventListener("click", () => {
    stopPlaying();
    window.fim.showScreen("screen-results");
});

window.fim.showAnimation = async function showAnimation(outputDirectory) {
    stopPlaying();
    animationOutputDirectory = outputDirectory;
    const result = await window.pywebview.api.get_animation_frames(outputDirectory);
    window.fim.showScreen("screen-animation");
    if (!result.ok || result.frames.length === 0) {
        animationGenerationLabel.textContent = result.message || "No frames to animate";
        playButton.disabled = true;
        scrubber.disabled = true;
        defaultFrames = [];
        frames = [];
        animationDemePairSelector.hidden = true;
        return;
    }
    defaultFrames = result.frames;
    frames = defaultFrames;
    const canAnimate = frames.length >= MINIMUM_FRAMES_TO_ANIMATE;
    playButton.disabled = !canAnimate;
    scrubber.disabled = !canAnimate;
    scrubber.max = String(frames.length - 1);
    setCurrentIndex(0);
    // A fresh run/replicate every call -- never left showing a stale
    // pair selection (or a stale X/Y choice) from whichever animation
    // was open before this one, the same reason `resetInputForm`
    // (design §4.5) exists rather than trusting a screen to still be in
    // a sane state from its own last use.
    window.fim.wireDemePairSelector({
        xSelect: animationXDeme,
        ySelect: animationYDeme,
        showPairButton: animationShowPairButton,
        showOverviewButton: animationShowOverviewButton,
        container: animationDemePairSelector,
        demeCount: result.demeCount,
        onShowPair: async (x, y) => {
            if (animationOutputDirectory === null) {
                return;
            }
            const pairResult =
                await window.pywebview.api.get_animation_deme_pair_frames(
                    animationOutputDirectory,
                    x,
                    y
                );
            if (!pairResult.ok) {
                return;
            }
            stopPlaying();
            // `pre_render_frames` samples identically both times (same
            // trajectory, same `max_frames` default -- `Api.get_
            // animation_deme_pair_frames`'s own docstring), so this set
            // is always the same length as `defaultFrames`; `currentIndex`
            // carries over unchanged, redrawing the same generation
            // under the new view rather than jumping back to frame 0.
            frames = pairResult.frames.map((frame) => ({
                generation: frame.generation,
                panels: [frame.panel],
            }));
            drawCurrentFrame();
        },
        onShowOverview: () => {
            stopPlaying();
            frames = defaultFrames;
            drawCurrentFrame();
        },
    });
};
