"use strict";

/* The Help screen (in-app help design §4.4) -- fetches one of `dev/bin/
 * generate-help-html`'s own committed, body-only HTML fragments
 * (`help/usage.html`, `help/configuration.html`) and injects it into
 * `#help-content`. No bridge call: the fragment is a static file
 * already bundled alongside `index.html` (`packaging/fim.spec`'s own
 * whole-`webui/`-tree `datas` entry needs no change for it, design
 * §4.7), fetched the same way the page's own `app.css` already loads.
 *
 * Reachable from every screen via the native Help menu
 * (`fim.gui.app._build_menu`), including mid-run -- the first screen in
 * this app that needs a real "return to wherever I was" instead of a
 * fixed Back target (design §4.4's own departure from every existing
 * screen's fixed-edge "Back" button).
 */

const helpContent = document.getElementById("help-content");
const helpBackButton = document.getElementById("help-back-button");

let returnScreen = "screen-input";

/**
 * Show one embedded doc's rendered HTML, recording the screen shown
 * before this call so "Back" can return to it.
 *
 * @param {string} topic - `"usage"` or `"configuration"`.
 * @param {string} [anchor] - A heading id to scroll to once rendered.
 */
window.fim.showHelp = async function showHelp(topic, anchor) {
    const currentlyVisible = document.querySelector(".screen:not([hidden])");
    if (currentlyVisible !== null && currentlyVisible.id !== "screen-help") {
        returnScreen = currentlyVisible.id;
    }
    const response = await fetch(`help/${topic}.html`);
    helpContent.innerHTML = await response.text();
    window.fim.showScreen("screen-help");
    if (anchor) {
        const target = document.getElementById(anchor);
        if (target !== null) {
            target.scrollIntoView();
        }
    }
};

helpBackButton.addEventListener("click", () => {
    window.fim.showScreen(returnScreen);
});

// Every link in the fragment already carries `href="#"` plus a
// `data-fim-*` attribute (`dev/bin/generate-help-html`'s own link
// rewriting, design §4.3) -- one delegated handler routes every click,
// so no `<a>` is ever left to native navigation, which inside a
// pywebview window can otherwise carry the *whole application window*
// away from `index.html`, not open a new tab.
helpContent.addEventListener("click", (event) => {
    const link = event.target.closest("a");
    if (link === null) {
        return;
    }
    // A same-document `#anchor` link keeps its real fragment href
    // (`dev/bin/generate-help-html`'s own "left as-is" rule for that
    // one case) and carries neither attribute below -- left entirely to
    // the browser's own native in-page scroll, `preventDefault()` never
    // called for it. Only the two rewritten cases (`href="#"` plus a
    // `data-fim-*` attribute) are ever intercepted.
    if (link.dataset.fimHelp) {
        event.preventDefault();
        window.fim.showHelp(link.dataset.fimHelp, link.dataset.fimAnchor || undefined);
    } else if (link.dataset.fimExternal) {
        event.preventDefault();
        window.pywebview.api.open_external_link(link.dataset.fimExternal);
    }
});
