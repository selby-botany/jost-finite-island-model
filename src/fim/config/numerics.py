"""Numerical guards and derivable constants.

Values that keep a computation finite and well conditioned, or that follow
from mathematics or IEEE double precision. They are named and documented but
are not settings: changing one would make the code wrong, not different.

See `README.md` in this directory for the table of every constant.
"""

from __future__ import annotations

from typing import Final

MAXIMUM_LAG1_CORRELATION: Final = 1.0 - 1e-9
"""A lag-1 correlation this close to 1 makes `tau_int` (below) blow up
numerically for a reason that is itself informative -- the window has not
actually decorrelated from itself at all, which is precisely "not
noise-adequate," not a division to guard around. Clamped rather than
raising, so a caller always gets a finite (very large) standard error back.

Kind: numerical guard.
"""

MINIMUM_WINDOW_VALUES: Final = 3
"""`window_statistics` needs at least this many values to define a lag-1
correlation at all (two consecutive-pair terms and a variance).

Kind: derivable.
"""

MINIMUM_CRITERION_WINDOW: Final = 2
"""`MINIMUM_CRITERION_WINDOW`.

Kind: derivable.
"""

MINIMUM_REPLICATE_COUNT: Final = 2
"""`MINIMUM_REPLICATE_COUNT`.

Kind: derivable.
"""

EXACT_SCALE_BITS: Final = 1074
"""Every finite double is an integer multiple of `2 ** -1074`.

Kind: derivable (IEEE 754).
"""

MINIMUM_DEMES: Final = 2
"""The recursion needs a between-deme identity, so at least two demes.

Kind: derivable.
"""

DEGENERACY_TOLERANCE: Final = 1e-9
"""Eigenvalues closer than this (relative to the larger) make the 2 by 2
eigenvector matrix numerically singular.

Kind: numerical guard.
"""

SINGULAR_FIXED_POINT: Final = 1e-15
"""Below these, the fixed-point system or the eigenvector matrix is singular
to double precision.

Kind: numerical guard.
"""

SINGULAR_EIGENVECTORS: Final = 1e-300
"""`SINGULAR_EIGENVECTORS`.

Kind: numerical guard.
"""

MAXIMUM_CONDITION: Final = 1e8
"""The eigenvector matrix is trusted only while it is this well conditioned
and reproduces the operator to this relative accuracy.

Kind: numerical guard.
"""

RECONSTRUCTION_TOLERANCE: Final = 1e-8
"""`RECONSTRUCTION_TOLERANCE`.

Kind: numerical guard.
"""

MINIMUM_SAMPLE_GENE_COPIES: Final = 2
"""`MINIMUM_SAMPLE_GENE_COPIES`.

Kind: derivable.
"""

DIFFERENTIATION_TOLERANCE: Final = 1e-12
"""`DIFFERENTIATION_TOLERANCE`.

Kind: numerical guard.
"""

EULER_GAMMA: Final = 0.5772156649015328606
"""Euler-Mascheroni constant gamma = -psi(1), to full double precision
(Abramowitz & Stegun 1972, table 1.1) -- the additive constant every
equilibrium Shannon-entropy formula below (`equilibrium_shannon_
entropy_isolated` and its siblings) carries, following Chao et al.
(2015) Eq. 2A.

Kind: derivable (a mathematical constant).
"""

DIGAMMA_ASYMPTOTIC_THRESHOLD: Final = 6.0
"""Threshold above which `_digamma`'s asymptotic series (Abramowitz &
Stegun 1972, formula 6.3.18 -- the same one Chao et al.'s own S2
Appendix cites) is accurate to within machine precision; below it,
the recurrence psi(x+1) = psi(x) + 1/x shifts the argument up first.

Kind: numerical guard, derivable.
"""
