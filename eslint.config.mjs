/* ESLint configuration for the desktop GUI's browser JavaScript.
 *
 * Why this file looks unusual: the GUI's scripts are *classic* scripts,
 * not ES modules. `index.html` loads them with plain `<script src=...>`
 * tags, so every top-level `function`/`const`/`let` in any one of them
 * is visible to all the others at run time. ESLint has no "these files
 * share one global scope" mode, so without help it flags every
 * cross-file call as `no-undef` -- roughly forty false positives, which
 * would make the rule useless and invite someone to switch it off.
 *
 * Rather than hand-maintain a list of shared names that would silently
 * drift the first time somebody added a function, this config reads the
 * scripts and derives that list. A declaration is "shared" when it
 * starts at column zero, which is exactly the run-time rule: the source
 * is indented four spaces per level, so column zero means top level
 * means global. `no-undef` then still does its real job of catching
 * misspelled browser APIs and genuinely undeclared names.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const WEBUI = fileURLToPath(new URL("src/fim/gui/webui/", import.meta.url));

/* Browser and DOM names the GUI actually uses. Listed explicitly rather
 * than pulled from the `globals` package so that linting needs exactly
 * one npm download (see `bin/eslint`) and so that adding a new platform
 * API is a deliberate, reviewable act.
 */
const BROWSER_GLOBALS = [
    "AbortController",
    "Blob",
    "CSS",
    "CustomEvent",
    "DOMParser",
    "Element",
    "Event",
    "FormData",
    "HTMLElement",
    "Image",
    "IntersectionObserver",
    "MutationObserver",
    "Node",
    "NodeList",
    "RadioNodeList",
    "ResizeObserver",
    "URL",
    "URLSearchParams",
    "alert",
    "cancelAnimationFrame",
    "clearInterval",
    "clearTimeout",
    "confirm",
    "console",
    "devicePixelRatio",
    "document",
    "fetch",
    "getComputedStyle",
    "localStorage",
    "location",
    "navigator",
    "performance",
    "queueMicrotask",
    "requestAnimationFrame",
    "screen",
    "sessionStorage",
    "setInterval",
    "setTimeout",
    "structuredClone",
    "window",
];

/* Every `.js` file under the webui tree, relative to `WEBUI`. */
function webuiScripts(directory = WEBUI, prefix = "") {
    const found = [];
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
        const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
        if (entry.isDirectory()) {
            found.push(...webuiScripts(join(directory, entry.name), relative));
        } else if (entry.name.endsWith(".js")) {
            found.push(relative);
        }
    }
    return found;
}

/* Where each column-zero declaration lives, keyed by declared name --
 * see the header comment for why column zero is the right test.
 */
function sharedDeclarations() {
    const declaration =
        /^(?:async\s+)?(?:function\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)/;
    const sources = new Map();
    const declaredIn = new Map();
    for (const relative of webuiScripts()) {
        const source = readFileSync(join(WEBUI, relative), "utf8");
        sources.set(relative, source);
        for (const line of source.split("\n")) {
            const match = declaration.exec(line);
            if (match && !declaredIn.has(match[1])) {
                declaredIn.set(match[1], relative);
            }
        }
    }
    return { sources, declaredIn };
}

const { sources, declaredIn } = sharedDeclarations();

/* Shared names that some *other* script mentions. `no-unused-vars` sees
 * one file at a time, so a helper defined in `meters.js` and called only
 * from `screens/open-run.js` reads as dead code to it. Exempting exactly
 * the names with a reader elsewhere silences that false positive while
 * still letting the rule flag a top-level function nobody calls at all.
 */
function consumedAcrossFiles() {
    const consumed = [];
    for (const [name, home] of declaredIn) {
        const reference = new RegExp(`\\b${name}\\b`);
        for (const [relative, source] of sources) {
            if (relative !== home && reference.test(source)) {
                consumed.push(name);
                break;
            }
        }
    }
    return consumed;
}

const globals = Object.fromEntries([
    ...BROWSER_GLOBALS.map((name) => [name, "readonly"]),
    ...[...declaredIn.keys()].map((name) => [name, "writable"]),
]);

const unusedVarsIgnorePattern = `^(?:_|${consumedAcrossFiles().join("|")})`;

export default [
    {
        files: ["src/fim/gui/webui/**/*.js"],
        languageOptions: {
            ecmaVersion: 2023,
            sourceType: "script",
            globals,
        },
        linterOptions: {
            reportUnusedDisableDirectives: "error",
        },
        rules: {
            /* Correctness: these catch code that is simply wrong. */
            "no-undef": "error",
            "no-unused-vars": [
                "error",
                { args: "none", varsIgnorePattern: unusedVarsIgnorePattern },
            ],
            "no-dupe-args": "error",
            "no-dupe-keys": "error",
            "no-dupe-else-if": "error",
            "no-duplicate-case": "error",
            "no-func-assign": "error",
            "no-self-assign": "error",
            "no-self-compare": "error",
            "no-sparse-arrays": "error",
            "no-template-curly-in-string": "error",
            "no-unreachable": "error",
            "no-unsafe-negation": "error",
            "use-isnan": "error",
            "valid-typeof": "error",

            /* Style choices the existing source already follows, kept as
             * errors so the tree stays internally consistent.
             */
            "eqeqeq": ["error", "smart"],
            "no-var": "error",
            "prefer-const": "error",
            "curly": ["error", "all"],
            "no-throw-literal": "error",
            "no-useless-concat": "error",
            "no-useless-return": "error",
            "prefer-template": "error",
        },
    },
];
