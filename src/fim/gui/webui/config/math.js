"use strict";

/* Mathematical constants shared by every drawing routine.
 *
 * Part of the `config/` modules: classic scripts that `index.html` loads
 * before any other script, so each top-level `const` here is visible to
 * the rest of the GUI. They hold values only -- no logic and no
 * dependency on anything declared later -- so their order among
 * themselves does not matter.
 */

/* One full turn in radians (2π), the way ℏ stands for h/2π. Canvas
 * `arc()` takes angles in radians, so a complete circle is
 * `arc(x, y, r, 0, TAU)`.
 */
const TAU = 2 * Math.PI;

/* A quarter turn in radians (π/2). A y-axis title is drawn by rotating
 * the canvas `-QUARTER_TURN`, so the text reads upward along the axis.
 */
const QUARTER_TURN = Math.PI / 2;
