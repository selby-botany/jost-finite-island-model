"use strict";

/* Walking-skeleton bootstrap (Milestone W1, design doc §7.2). Every real
 * screen (§4, from Milestone W2 onward) replaces `#app`'s contents once
 * pywebview's bridge is confirmed live -- this file's only job right now
 * is proving that confirmation, using the two-step await-then-read-back
 * pattern every bridge call in this application uses (see
 * src/fim/gui/app.py's own module docstring for why a direct
 * `evaluate_js("window.pywebview.api.foo()")` call cannot be used
 * instead): an `async` handler awaits the `js_api` call and writes the
 * result into the DOM itself, rather than returning a value through
 * `evaluate_js`. */

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
