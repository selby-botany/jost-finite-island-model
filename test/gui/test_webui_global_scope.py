"""Static-analysis guard against classic-script global-scope collisions.

`src/fim/gui/webui/*.js`/`webui/screens/*.js` are all classic, non-module
scripts sharing one global scope (`index.html` has no `type="module"` on
any `<script>` tag — established by `screens/batch-results.js`'s own
module docstring) — a `const`/`let`/`function` declared at the top level
of two different files is a `SyntaxError` ("Identifier '...' has already
been declared") that silently aborts the *second* file's entire
execution, with no error surfaced anywhere a developer would naturally
look (pywebview does not forward a page's own console errors to the
terminal by default).

A real instance of exactly this shape, found while building the
Animation screen's own deme-pair selector: `screens/animation.js` and
`screens/results.js` both declared `let currentOutputDirectory` at the
top level. Every functional symptom pointed away from the real cause —
`test/gui/test_animation_screen.py`'s own tests failed with "Screen 5
was never reached", `Api.open_run` and `showResults` both visibly
succeeded, and the whole thing looked exactly like the project's own
already-documented residual GUI-suite-stall class (`test/gui/conftest.py`'s
module docstring) until a direct `typeof window.fim.showAnimation` probe
showed `"undefined"` — the assignment had never run because the
*declaration* two lines above it, in a different file, never got the
chance to either. This test answers the same question directly, in
milliseconds, with no window and no simulation run required.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

_WEBUI_ROOT = Path(__file__).resolve().parents[2] / "src" / "fim" / "gui" / "webui"

# Column-zero `const NAME =`/`let NAME =`/`function NAME(` — this
# project's own consistent 4-space-indent style means a top-level
# (global-scope) declaration always starts at column 0; anything
# indented is inside a function/block body, not a collision candidate.
_TOP_LEVEL_DECLARATION = re.compile(
    r"^(?:const|let)\s+(\w+)\s*=|^function\s+(\w+)\s*\("
)


def _top_level_names(path: Path) -> set[str]:
    """Return every identifier `path` declares at column-zero scope."""
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _TOP_LEVEL_DECLARATION.match(line)
        if match:
            names.add(match.group(1) or match.group(2))
    return names


def test_no_top_level_identifier_is_declared_in_more_than_one_script() -> None:
    """No `const`/`let`/`function` name is declared at column 0 in two files.

    Two files sharing one name is exactly the `SyntaxError` this test
    exists to catch before a real window ever loads the page — see this
    module's own docstring for the real instance that prompted it.
    """
    js_files = sorted(_WEBUI_ROOT.glob("*.js")) + sorted(
        _WEBUI_ROOT.glob("screens/*.js")
    )
    # Sanity check on the glob itself: a typo in `_WEBUI_ROOT` should
    # fail loudly here, not pass this test vacuously with zero files
    # scanned.
    assert len(js_files) >= 10

    owners: dict[str, list[str]] = defaultdict(list)
    for path in js_files:
        for name in _top_level_names(path):
            owners[name].append(path.name)

    collisions = {name: files for name, files in owners.items() if len(files) > 1}
    assert collisions == {}
