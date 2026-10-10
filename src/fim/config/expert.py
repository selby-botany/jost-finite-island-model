"""Expert Settings: the policy constants a run's configuration may change.

Each field is the run-specific form of a policy constant in
`fim.config.convergence`; its default is that constant. A run's configuration
can carry an `expert:` mapping of the fields it changes. `SimulationParams`
validates the mapping here, copies it into the run's parameters (so the
manifest says exactly what ran, and the run ID changes with it), and only
non-default entries are written back out, so a run with default Expert Settings
is byte-for-byte the run it was before the mapping existed.

Numerical guards and derivable constants are not Expert Settings (design
`20261005-claude-opus-5-5-simplified-convergence-rule-design.md`, 11.1).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any, Final

from fim.config.convergence import (
    ABSOLUTE_MAX_GENERATIONS,
    BURN_IN_MINIMUM_RELAXATION_TIMES,
    CAP_RELAXATION_MULTIPLE,
    CHECK_GROWTH,
    FIRST_CHECK_MINIMUM,
    FIRST_CHECK_RELAXATION_TIMES,
    FRACTIONAL_BURN_IN,
    MINIMUM_EFFECTIVE_SAMPLE_SIZE,
    MINIMUM_MAX_GENERATIONS,
    START_DRIFT_ALERT_Z,
)
from fim.config.numerics import MINIMUM_WINDOW_VALUES
from fim.config.statistics import ESTIMATE_AUTO_DENOMINATOR, ESTIMATE_AUTO_FRACTION


@dataclass(frozen=True, slots=True)
class ExpertSettings:
    """The convergence policy constants one run uses.

    Attributes:
        burn_in_minimum_relaxation_times: Floor of the burn-in multiple `k`
            (at least 1).
        first_check_relaxation_times: First check, in relaxation times after
            the burn-in (greater than 0).
        first_check_minimum: Fewest generations after the burn-in before the
            first check (at least `MINIMUM_WINDOW_VALUES`).
        minimum_effective_sample_size: Effective-sample-size floor (at least
            10).
        check_growth: Factor by which the window grows between checks
            (greater than 1).
        fractional_burn_in: Burn-in share when no relaxation time exists
            (between 0 and 1, exclusive).
        cap_relaxation_multiple: Cap beyond the burn-in, in relaxation times
            (greater than 0).
        cap_minimum: Smallest derived `max_generations` (at least 1).
        cap_maximum: Largest derived `max_generations` (at least
            `cap_minimum`).
        start_drift_alert_z: Absolute Geweke `z` above which the report says
            the burn-in may have been too short (greater than 0).
        estimate_auto_denominator: Denominator below which a generation counts
            as degenerate for `convergence_estimate: auto` (between 0 and 1,
            exclusive).
        estimate_auto_fraction: Share of window generations that may be
            degenerate before `auto` switches to the value of means (between
            0 and 1, exclusive).
    """

    burn_in_minimum_relaxation_times: float = BURN_IN_MINIMUM_RELAXATION_TIMES
    first_check_relaxation_times: float = FIRST_CHECK_RELAXATION_TIMES
    first_check_minimum: int = FIRST_CHECK_MINIMUM
    minimum_effective_sample_size: float = MINIMUM_EFFECTIVE_SAMPLE_SIZE
    check_growth: float = CHECK_GROWTH
    fractional_burn_in: float = FRACTIONAL_BURN_IN
    cap_relaxation_multiple: float = CAP_RELAXATION_MULTIPLE
    cap_minimum: int = MINIMUM_MAX_GENERATIONS
    cap_maximum: int = ABSOLUTE_MAX_GENERATIONS
    start_drift_alert_z: float = START_DRIFT_ALERT_Z
    estimate_auto_denominator: float = ESTIMATE_AUTO_DENOMINATOR
    estimate_auto_fraction: float = ESTIMATE_AUTO_FRACTION

    def __post_init__(self) -> None:
        """Validate every field against its documented range.

        Raises:
            ValueError: Naming the first field that is out of range.
        """
        for name, low, inclusive in _RANGES:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"expert setting {name} must be a number")
            ok = math.isfinite(value) and (value >= low if inclusive else value > low)
            if not ok:
                bound = f"at least {low:g}" if inclusive else f"greater than {low:g}"
                raise ValueError(f"expert setting {name} must be {bound}")
        for name in _INTEGER_FIELDS:
            if not float(getattr(self, name)).is_integer():
                raise ValueError(f"expert setting {name} must be a whole number")
        for name in (
            "fractional_burn_in",
            "estimate_auto_denominator",
            "estimate_auto_fraction",
        ):
            if not getattr(self, name) < 1.0:
                raise ValueError(f"expert setting {name} must be below 1")
        if self.cap_maximum < self.cap_minimum:
            raise ValueError("expert setting cap_maximum must be at least cap_minimum")

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any] | None) -> ExpertSettings:
        """Build the settings a configuration's `expert:` mapping asks for.

        Args:
            mapping: The mapping, or `None` for all defaults.

        Returns:
            The settings, with every unnamed field at its default.

        Raises:
            ValueError: If `mapping` is not a mapping, names an unknown
                setting, or a value is out of range.
        """
        if mapping is None:
            return cls()
        if not isinstance(mapping, Mapping):
            raise ValueError("expert must be a mapping of setting name to value")
        known = {field.name for field in fields(cls)}
        unknown = sorted(set(mapping) - known)
        if unknown:
            raise ValueError(
                f"unknown expert setting(s): {', '.join(map(str, unknown))}; "
                f"known: {', '.join(sorted(known))}"
            )
        defaults = cls()
        values = {
            name: _typed(getattr(defaults, name), value)
            for name, value in mapping.items()
        }
        return cls(**values)

    def changes(self) -> dict[str, float | int]:
        """Return the fields that differ from their defaults, by name.

        Returns:
            An empty mapping when every setting is at its default.
        """
        defaults = ExpertSettings()
        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if getattr(self, field.name) != getattr(defaults, field.name)
        }


_RANGES: Final = (
    ("burn_in_minimum_relaxation_times", 1.0, True),
    ("first_check_relaxation_times", 0.0, False),
    ("first_check_minimum", float(MINIMUM_WINDOW_VALUES), True),
    ("minimum_effective_sample_size", 10.0, True),
    ("check_growth", 1.0, False),
    ("fractional_burn_in", 0.0, False),
    ("cap_relaxation_multiple", 0.0, False),
    ("cap_minimum", 1.0, True),
    ("cap_maximum", 1.0, True),
    ("start_drift_alert_z", 0.0, False),
    ("estimate_auto_denominator", 0.0, False),
    ("estimate_auto_fraction", 0.0, False),
)
"""Each numeric field's lower bound, and whether the bound itself is allowed."""

_INTEGER_FIELDS: Final = ("first_check_minimum", "cap_minimum", "cap_maximum")
"""The fields that must be whole numbers."""


def _typed(default: float | int, value: Any) -> Any:
    """Return `value` as the type of the setting's default, when that is exact.

    A whole number given for a decimal setting becomes a float; a float with no
    fractional part given for a whole-number setting becomes an int. Anything
    else (a fraction for a whole-number setting, text, a `bool`) is returned
    unchanged for `ExpertSettings` to refuse by name.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return value
    if isinstance(default, float):
        return float(value)
    return int(value) if float(value).is_integer() else value
