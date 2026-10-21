# GUI constants

The `config/` scripts hold every named constant the desktop GUI's drawing and
layout code uses. `index.html` loads them before any other script, in the
order below. They are classic scripts that share one global scope, so each
top-level `const` is visible everywhere. `test_webui_global_scope.py` checks
that no name is declared twice and that every file here is loaded first.

| File | What it holds |
| --- | --- |
| `math.js` | `TAU` (a full turn, 2π) and `QUARTER_TURN` |
| `palette.js` | Every hard-coded color: chart greys, the Okabe-Ito data colors, statistic and compare-run colors, heat-map ramps, scatter colors |
| `canvas.js` | Shared drawing vocabulary: fonts, tick length and label gaps, dash patterns, line widths, tick-selection tables |
| `plots.js` | Per-plot geometry, one section per plot: scatter, heat map, trajectory, supplemental graphs, Explore, sweep results |
| `behavior.js` | Timing, layout and interaction values: graph stage, zoom, scrubber, debounce delays, number display precision, thresholds |
| `model-defaults.js` | Model vocabulary and the starting contents of the input grids; several mirror a Python constant |

## Adding a constant

- Put it in the file whose subject it belongs to, in that file's section for
  the plot or screen that uses it. Start a new section if none fits.
- Give it a comment that says what it controls and why it has this value,
  not only what the number is.
- Name a color by its role (`CHART_AXIS_COLOR`) rather than its appearance.
- Colors that must follow the light or dark theme are not constants: read the
  `--fim-*` custom property at draw time.
- Constants may be shared across scripts. Remove unused declarations;
  ESLint reports constants with no consumers.
- When a value mirrors Python, name the corresponding Python constant.
  The declarations are maintained separately.

See the [GUI design](../../../../../doc/fim-gui-design.md) for script
loading and drawing boundaries, and the
[Python constants reference](../../../config/README.md) for model defaults.
