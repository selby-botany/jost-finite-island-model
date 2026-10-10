"""Statistics policy constants.

Choices about how a statistic is estimated from a run, rather than how long
the run lasts (see `fim.config.convergence` for that).

See `README.md` in this directory for the table of every constant.
"""

from __future__ import annotations

from typing import Final

ESTIMATE_AUTO_DENOMINATOR: Final = 0.01
"""Denominator below which a generation counts as degenerate for `auto`.

With `convergence_estimate: auto`, an identity statistic (`D`, `G_ST`) is
estimated as the "value of means" when its denominator (`H_T` for `G_ST`,
`1 - H_S` for `D`) is below this in more than `ESTIMATE_AUTO_FRACTION` of the
evidence-window generations: a ratio of tiny numbers is noisy and biases a mean
of values (design 6.11).

Kind: policy.
"""

ESTIMATE_AUTO_FRACTION: Final = 0.01
"""Share of window generations that may be degenerate before `auto` switches.

Kind: policy.
"""
