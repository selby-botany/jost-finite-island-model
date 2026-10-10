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
    AVERAGING_MULTIPLE_MAXIMUM,
    AVERAGING_MULTIPLE_MINIMUM,
    BATCH_WIDTH,
    BURN_IN_MINIMUM_RELAXATION_TIMES,
    CAP_RELAXATION_MULTIPLE,
    CHECK_GROWTH,
    FIRST_CHECK_MINIMUM,
    FIRST_CHECK_RELAXATION_TIMES,
    FIRST_WAVE_AVERAGING_MULTIPLE,
    FRACTIONAL_BURN_IN,
    MINIMUM_EFFECTIVE_SAMPLE_SIZE,
    MINIMUM_MAX_GENERATIONS,
    REPLICATE_WAVE_MULTIPLE,
    SPECTRUM_BURN_IN_MULTIPLIER,
    START_DRIFT_ALERT_Z,
)
from fim.config.numerics import MINIMUM_WINDOW_VALUES
from fim.config.statistics import ESTIMATE_AUTO_DENOMINATOR, ESTIMATE_AUTO_FRACTION
from fim.config.storage import (
    LOG_BLOCK_GENERATIONS,
    LOG_KEY_EVERY,
    LOG_SYNC_SECONDS,
    THINNING_MINIMUM_START,
    THINNING_TRANSIENT_RELAXATION_TIMES,
)


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
        batch_width: Replicates assumed to run at once when
            `max_concurrent_replicates` is unset (at least 1).
        spectrum_burn_in_multiplier: Factor on the burn-in when an
            allele-spectrum statistic is watched (at least 1).
        log_key_every: Generations between keyframes in a sparse trajectory
            log (at least 1).
        log_sync_seconds: Seconds between the trajectory log's disk syncs
            (greater than 0).
        log_block_generations: Generations per trajectory-log block (at least
            1).
        thinning_transient_relaxation_times: Relaxation times after the burn-in
            that thinning keeps whole (at least 0).
        thinning_minimum_start: Generation before which thinning never starts
            (at least 1).
        replicate_wave_multiple: Replicate waves a batch aims for (greater
            than 0).
        averaging_multiple_minimum: Smallest matched replicate averaging
            window, in relaxation times (greater than 0).
        averaging_multiple_maximum: Largest matched replicate averaging
            window, in relaxation times (at least
            `averaging_multiple_minimum`).
        first_wave_averaging_multiple: Averaging window of the first wave of
            replicates, in relaxation times (greater than 0).
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
    batch_width: int = BATCH_WIDTH
    spectrum_burn_in_multiplier: float = SPECTRUM_BURN_IN_MULTIPLIER
    log_key_every: int = LOG_KEY_EVERY
    log_sync_seconds: float = LOG_SYNC_SECONDS
    log_block_generations: int = LOG_BLOCK_GENERATIONS
    thinning_transient_relaxation_times: float = THINNING_TRANSIENT_RELAXATION_TIMES
    thinning_minimum_start: int = THINNING_MINIMUM_START
    replicate_wave_multiple: float = REPLICATE_WAVE_MULTIPLE
    averaging_multiple_minimum: float = AVERAGING_MULTIPLE_MINIMUM
    averaging_multiple_maximum: float = AVERAGING_MULTIPLE_MAXIMUM
    first_wave_averaging_multiple: float = FIRST_WAVE_AVERAGING_MULTIPLE

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
        if self.averaging_multiple_maximum < self.averaging_multiple_minimum:
            raise ValueError(
                "expert setting averaging_multiple_maximum must be at least "
                "averaging_multiple_minimum"
            )

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


def expert_template() -> str:
    """Return the commented `expert:` block `fim init` writes.

    Every Expert Setting appears with its default and its range, commented out,
    so a reader of the starter configuration sees every knob without any of
    them taking effect.

    Returns:
        YAML comment lines, ending with a newline.
    """
    lines = [
        "# Expert settings: policy constants of the convergence rule and the",
        "# trajectory log. The defaults are measured choices (doc/convergence.md);",
        "# a run that changes one is a different run. Uncomment a line to change it.",
        "# expert:",
    ]
    defaults = ExpertSettings()
    ranges = {name: (low, inclusive) for name, low, inclusive in _RANGES}
    for field_ in fields(ExpertSettings):
        low, inclusive = ranges[field_.name]
        bound = f"at least {low:g}" if inclusive else f"greater than {low:g}"
        lines.append(f"#   {field_.name}: {getattr(defaults, field_.name)}  # {bound}")
    return "\n".join(lines) + "\n"


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
    ("batch_width", 1.0, True),
    ("spectrum_burn_in_multiplier", 1.0, True),
    ("log_key_every", 1.0, True),
    ("log_sync_seconds", 0.0, False),
    ("log_block_generations", 1.0, True),
    ("thinning_transient_relaxation_times", 0.0, True),
    ("thinning_minimum_start", 1.0, True),
    ("replicate_wave_multiple", 0.0, False),
    ("averaging_multiple_minimum", 0.0, False),
    ("averaging_multiple_maximum", 0.0, False),
    ("first_wave_averaging_multiple", 0.0, False),
)
"""Each numeric field's lower bound, and whether the bound itself is allowed."""

_INTEGER_FIELDS: Final = (
    "first_check_minimum",
    "cap_minimum",
    "cap_maximum",
    "batch_width",
    "log_key_every",
    "log_block_generations",
    "thinning_minimum_start",
)
"""The fields that must be whole numbers."""


@dataclass(frozen=True, slots=True)
class ExpertSettingInfo:
    """How the app presents one Expert Setting.

    Attributes:
        group: The Settings subsection it sits in.
        label: Plain-language name.
        help: What it does and why its default is what it is, in a sentence or
            two a researcher can act on.
    """

    group: str
    label: str
    help: str


EXPERT_GROUPS: Final = ("Convergence", "Batches", "Statistics", "Storage")
"""The Settings subsections, in display order."""

EXPERT_SETTING_INFO: Final[dict[str, ExpertSettingInfo]] = {
    "burn_in_minimum_relaxation_times": ExpertSettingInfo(
        "Convergence",
        "Burn-in floor (relaxation times)",
        "The burn-in is at least this many relaxation times, however loose the "
        "precision. 5 leaves under 1% of the starting state.",
    ),
    "first_check_relaxation_times": ExpertSettingInfo(
        "Convergence",
        "First check (relaxation times)",
        "How long after the burn-in the first precision check comes. Checks "
        "are cheap; this only avoids judging a window that is too short.",
    ),
    "first_check_minimum": ExpertSettingInfo(
        "Convergence",
        "First check (generations, at least)",
        "The first check never comes sooner than this many generations after "
        "the burn-in, whatever the relaxation time.",
    ),
    "minimum_effective_sample_size": ExpertSettingInfo(
        "Convergence",
        "Effective sample size needed",
        "A window must hold this many independent values before its standard "
        "error is trusted. 50 keeps the error bar honest.",
    ),
    "check_growth": ExpertSettingInfo(
        "Convergence",
        "Window growth between checks",
        "After a failed check the window must grow by this factor before the "
        "next one. 2 doubles it, so a run overshoots its precision by at most "
        "a factor of 2 in length.",
    ),
    "fractional_burn_in": ExpertSettingInfo(
        "Convergence",
        "Burn-in share when unknown",
        "A model with no relaxation time discards this share of the run as "
        "burn-in at each check.",
    ),
    "cap_relaxation_multiple": ExpertSettingInfo(
        "Convergence",
        "Cap beyond the burn-in (relaxation times)",
        "The derived generation cap is the burn-in plus this many relaxation "
        "times (never below the minimum cap).",
    ),
    "cap_minimum": ExpertSettingInfo(
        "Convergence",
        "Smallest derived cap",
        "A derived generation cap is never below this, so a fast model still "
        "has room to average a noisy statistic.",
    ),
    "cap_maximum": ExpertSettingInfo(
        "Convergence",
        "Largest derived cap",
        "A derived generation cap is never above this, so a nearly isolated "
        "system stays finite.",
    ),
    "start_drift_alert_z": ExpertSettingInfo(
        "Convergence",
        "Burn-in alert (absolute z)",
        "When the start of the averaging window differs from its end by more "
        "than this many standard errors, the report warns that the burn-in "
        "may have been too short.",
    ),
    "spectrum_burn_in_multiplier": ExpertSettingInfo(
        "Convergence",
        "Burn-in multiplier for allele statistics",
        "Lengthens the burn-in when an allele-spectrum statistic (E_ST, K_ST, "
        "A_CGD, Delta, MI) is watched, because the relaxation time is derived "
        "for the identity statistics. 1 changes nothing.",
    ),
    "batch_width": ExpertSettingInfo(
        "Batches",
        "Replicates assumed at once",
        "The first wave of a batch, which measures the noise the later "
        "replicate windows are matched from. A number, not your processor "
        "count, so a configuration gives the same results on every machine.",
    ),
    "replicate_wave_multiple": ExpertSettingInfo(
        "Batches",
        "Replicate waves aimed for",
        "The matched replicate window is sized for this many waves of "
        "replicates (never fewer than the replicate minimum).",
    ),
    "first_wave_averaging_multiple": ExpertSettingInfo(
        "Batches",
        "First-wave window (relaxation times)",
        "How long the first wave of replicates averages, before anything is "
        "known about the noise.",
    ),
    "averaging_multiple_minimum": ExpertSettingInfo(
        "Batches",
        "Shortest replicate window (relaxation times)",
        "A matched replicate window is never shorter than this; below it a "
        "replicate's own average is barely better than a snapshot.",
    ),
    "averaging_multiple_maximum": ExpertSettingInfo(
        "Batches",
        "Longest replicate window (relaxation times)",
        "A matched replicate window is never longer than this; more "
        "replicates then serve better than a longer window.",
    ),
    "estimate_auto_denominator": ExpertSettingInfo(
        "Statistics",
        "Degenerate denominator",
        "For the automatic expected-value form: a generation whose "
        "denominator (H_T for G_ST, 1 - H_S for D) is below this is "
        "degenerate.",
    ),
    "estimate_auto_fraction": ExpertSettingInfo(
        "Statistics",
        "Degenerate share allowed",
        "For the automatic expected-value form: the value of means is used "
        "when more than this share of the window is degenerate.",
    ),
    "log_key_every": ExpertSettingInfo(
        "Storage",
        "Keyframe interval (generations)",
        "The trajectory log stores every deme in full this often and only the "
        "changes between. Smaller makes the log larger and seeking faster.",
    ),
    "log_block_generations": ExpertSettingInfo(
        "Storage",
        "Log block size (generations)",
        "Generations per saved block of the trajectory log. Larger writes more "
        "efficiently; smaller loses less when a run is cut off.",
    ),
    "thinning_transient_relaxation_times": ExpertSettingInfo(
        "Storage",
        "Thinning: transient kept whole (relaxation times)",
        "With thinning on, the run is kept in full until this many "
        "relaxation times after the burn-in, so the transient is not thinned.",
    ),
    "thinning_minimum_start": ExpertSettingInfo(
        "Storage",
        "Thinning: earliest start (generations)",
        "Thinning never starts before this generation, so a shorter run keeps "
        "every generation even with thinning on.",
    ),
    "log_sync_seconds": ExpertSettingInfo(
        "Storage",
        "Disk sync period (seconds)",
        "How often the trajectory log is flushed to disk: the most a power "
        "loss can take.",
    ),
}
"""Presentation of every Expert Setting, by name (a test checks the keys)."""


def describe_expert_settings() -> list[dict[str, str]]:
    """Return every Expert Setting as the app lists it, in display order.

    Returns:
        One mapping per setting: `name`, `group`, `label`, `help`, `default`
        (text) and `range` (text), grouped by `EXPERT_GROUPS` and, within a
        group, in field order.
    """
    defaults = ExpertSettings()
    ranges = {name: (low, inclusive) for name, low, inclusive in _RANGES}
    described: list[dict[str, str]] = []
    for group in EXPERT_GROUPS:
        for field_ in fields(ExpertSettings):
            info = EXPERT_SETTING_INFO[field_.name]
            if info.group != group:
                continue
            low, inclusive = ranges[field_.name]
            bound = f"at least {low:g}" if inclusive else f"greater than {low:g}"
            if field_.name in _INTEGER_FIELDS:
                bound = f"whole number, {bound}"
            described.append(
                {
                    "name": field_.name,
                    "group": group,
                    "label": info.label,
                    "help": info.help,
                    "default": str(getattr(defaults, field_.name)),
                    "range": bound,
                }
            )
    return described


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
