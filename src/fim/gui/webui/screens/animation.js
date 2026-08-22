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
    if (panels && panels.length > 0) {
        // Same first-panel-only scope line every other screen's own
        // scatter draw already documents.
        const panel = panels[0];
        drawScatter(animationCanvas, panel.points, {
            xLabel: panel.x_label,
            yLabel: panel.y_label,
        });
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
    const result = await window.pywebview.api.get_animation_frames(outputDirectory);
    window.fim.showScreen("screen-animation");
    if (!result.ok || result.frames.length === 0) {
        animationGenerationLabel.textContent = result.message || "No frames to animate";
        playButton.disabled = true;
        scrubber.disabled = true;
        frames = [];
        return;
    }
    frames = result.frames;
    const canAnimate = frames.length >= MINIMUM_FRAMES_TO_ANIMATE;
    playButton.disabled = !canAnimate;
    scrubber.disabled = !canAnimate;
    scrubber.max = String(frames.length - 1);
    setCurrentIndex(0);
};
