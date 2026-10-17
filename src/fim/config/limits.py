"""Run-time limits.

Sizes beyond which a computation is refused or skipped because its cost
grows too fast.

See `README.md` in this directory for the table of every constant.
"""

from __future__ import annotations

from typing import Final

MAXIMUM_RECURSION_DEMES: Final = 24
"""Largest `d` for which the `d² by d²` eigenvalue route is used.

A 24-deme system is a 576 by 576 eigenproblem, well under a second. The
cost grows as `d⁶`, so larger explicit migration matrices must be given
explicit convergence values.

Kind: policy (run time).
"""

MINIMUM_EQUILIBRIUM_N: Final = 1
"""Smallest population size the closed-form equilibrium formulas accept.

Kind: numerical guard.
"""

MINIMUM_EQUILIBRIUM_DEMES: Final = 2
"""Smallest deme count the closed-form equilibrium formulas accept.

Kind: numerical guard.
"""
