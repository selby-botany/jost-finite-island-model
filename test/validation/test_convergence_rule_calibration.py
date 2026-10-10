"""Seeded calibration of the burn-in-then-average convergence rule.

The rule stops a run when the average of each watched statistic is known to
the requested precision, and reports that average with a standard error.
These tests check the two promises with fixed seeds, so a commit always gives
the same result:

- the average lands within `precision` of its target, and
- the standard error is honest: at confidence 0.95 about one run in twenty
  misses its target by more than 1.96 standard errors.

Form 1 (mean of the per-generation values) is compared with the mean over the
seeds, because it estimates the expected value of the statistic, which for a
few loci differs slightly from the analytic ratio. Form 2 (value of the means)
is compared with the analytic D of the identity recursion. The number of
misses a regime may have is a binomial bound, written in the evidence file
with the observed count: `convergence-rule-calibration-evidence.json`
(`dev/bin/calibrate-convergence-defaults`).

The same evidence file records how fast the allele-spectrum statistics settle
next to `D`, which keeps `spectrum_burn_in_multiplier` at 1, and the
Dear-Nolan low scenario, whose seeded run is the committed example report.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from fim.config.convergence import SPECTRUM_BURN_IN_MULTIPLIER
from fim.convergence.defaults import derive_convergence_defaults

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "test" / "validation" / "convergence-rule-calibration-evidence.json"
DEAR_NOLAN_LOW_REPORT = ROOT / "doc" / "examples" / "dear-nolan-low" / "report.json"

RUNS = 12
"""Seeded runs per regime."""
LOCI = 4
"""Loci pooled in each run."""
NOMINAL_MISS_RATE = 0.05
"""Misses expected at confidence 0.95, per run."""
TAIL_PROBABILITY = 0.02
"""Chance, at the nominal rate, that a regime exceeds its bound."""
REGIMES = ("fast", "golden-part-vi", "ring")


def _calibrate_module() -> ModuleType:
    """Import the calibration script, which holds the regimes and the runs."""
    path = ROOT / "dev" / "bin" / "calibrate-convergence-defaults"
    spec = importlib.util.spec_from_loader(
        "calibrate_convergence_defaults",
        loader=importlib.machinery.SourceFileLoader(
            "calibrate_convergence_defaults", str(path)
        ),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _evidence() -> dict[str, Any]:
    """Return the versioned calibration evidence."""
    data: dict[str, Any] = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    return data


def max_misses(runs: int, rate: float, tail: float) -> int:
    """Return the most misses a regime may show at the nominal rate.

    The smallest bound `b` such that, if every run misses independently at
    `rate`, more than `b` misses happen with probability at most `tail`.

    Args:
        runs: Runs in the regime.
        rate: Probability that one run misses.
        tail: Largest acceptable probability of exceeding the bound.

    Returns:
        The bound.
    """
    cumulative = 0.0
    for misses in range(runs + 1):
        cumulative += (
            math.comb(runs, misses) * rate**misses * (1 - rate) ** (runs - misses)
        )
        if 1.0 - cumulative <= tail:
            return misses
    return runs


MISS_BOUND = max_misses(RUNS, NOMINAL_MISS_RATE, TAIL_PROBABILITY)


def test_miss_bound_is_the_binomial_tail() -> None:
    """Twelve runs at one miss in twenty: three or more misses is a 2% event."""
    assert MISS_BOUND == 2


def test_evidence_records_the_bound_and_stays_within_it() -> None:
    """The evidence file states the bound the tests use and every count obeys it."""
    evidence = _evidence()
    assert evidence["binomial"] == {
        "runs": RUNS,
        "nominal_miss_rate": NOMINAL_MISS_RATE,
        "tail_probability": TAIL_PROBABILITY,
        "max_misses": MISS_BOUND,
    }
    assert sorted(evidence["rule"]) == sorted(REGIMES)
    for regime, entry in evidence["rule"].items():
        assert entry["runs"] == RUNS, regime
        assert entry["loci"] == LOCI, regime
        assert entry["not_converged"] == 0, regime
        for key in (
            "form_1_misses_precision",
            "form_1_misses_error_bar",
            "form_2_misses_precision",
            "form_2_misses_error_bar",
        ):
            assert entry[key] <= MISS_BOUND, (regime, key)


def test_spectrum_statistics_settle_with_the_identity_statistics() -> None:
    """The allele-spectrum statistics need no longer burn-in than `D`.

    The default `spectrum_burn_in_multiplier` is 1 because, in each measured
    regime, every allele-spectrum statistic settles within the burn-in `D`
    needs (and far inside the five relaxation times the burn-in never drops
    below).
    """
    evidence = _evidence()
    assert evidence["spectrum_burn_in_multiplier"]["chosen"] == (
        SPECTRUM_BURN_IN_MULTIPLIER
    )
    assert evidence["spectrum_burn_in_multiplier"]["chosen"] == 1.0
    assert evidence["spectrum"]
    for regime, entry in evidence["spectrum"].items():
        settled = entry["settled_after_relaxation_times"]
        for name in ("K_ST", "A_CGD", "Delta", "MI", "E_ST"):
            assert settled[name] is not None, (regime, name)
            assert settled[name] <= max(settled["D"], 5.0), (regime, name)


def test_derived_burn_in_covers_the_settling_time() -> None:
    """The derived burn-in is at least five relaxation times for these regimes."""
    module = _calibrate_module()
    for regime in REGIMES:
        d, n, migration, mu = module.REGIMES[regime]
        derived = derive_convergence_defaults(
            deme_sizes=[n] * d,
            migration=migration,
            mutation_rates=[mu] * LOCI,
            precision=module.PRECISION,
        )
        assert derived.burn_in >= 5 * derived.relaxation_time - 1, regime


def test_dear_nolan_low_report_matches_its_analytic_value() -> None:
    """Dear-Nolan low: the committed seeded run is honest about what it knows.

    The run reaches its cap without meeting the precision (D's effective
    sample size is far below the floor), reports that, and its average lies
    within two standard errors of the analytic D.
    """
    module = _calibrate_module()
    report = json.loads(DEAR_NOLAN_LOW_REPORT.read_text(encoding="utf-8"))
    window = report["window_statistics"]["D"]
    expected = module.analytic_d("dear-nolan-low")

    assert report["converged"] is False
    assert report["reason"] == "hit the cap"
    assert window["noise_adequate"] is False
    assert abs(window["mean"] - expected) <= 2.0 * window["standard_error"]

    entry = _evidence()["dear_nolan_low"]
    assert entry["analytic_D"] == pytest.approx(expected, abs=1e-4)
    assert entry["example_D_mean"] == pytest.approx(window["mean"], abs=1e-4)
    assert entry["example_D_standard_error"] == pytest.approx(
        window["standard_error"], abs=1e-4
    )


@pytest.mark.slow
def test_dear_nolan_low_never_stops_inside_its_burn_in() -> None:
    """A run whose cap lies inside its burn-in ends at the cap, unconverged.

    Regression for the reported failure: the old fixed defaults stopped
    this scenario after about 100 generations with D near 0.4, in the
    transient. The burn-in here is 104,338 generations; a run capped at 5,000
    must not report a converged average.
    """
    module = _calibrate_module()
    params = module.regime_params(
        "dear-nolan-low", loci=2, seed=910000, max_generations=5000
    )
    assert params.convergence_burn_in > params.max_generations
    report = module.run_reports(params)[0]

    assert report["converged"] is False
    assert report["generation"] == 5000
    assert report["reason"] == "hit the cap"
    assert not report.get("window_statistics")


@pytest.mark.slow
@pytest.mark.statistical
@pytest.mark.parametrize("regime", REGIMES)
def test_rule_lands_on_target_and_reports_an_honest_error_bar(regime: str) -> None:
    """Twelve seeded runs: few averages miss, and none misses the cap.

    Counts, per form: runs whose average is farther than `precision` from its
    target, and runs farther than 1.96 of their own standard errors. Each is
    held to the binomial bound the evidence file records.
    """
    module = _calibrate_module()
    first_seed = _evidence()["rule"][regime]["first_seed"]
    rows = [
        module.run_seed((regime, first_seed + index, LOCI, module.PRECISION))
        for index in range(RUNS)
    ]
    verdict = module.rule_verdict(regime, rows, module.PRECISION)

    assert verdict["not_converged"] == 0
    for key in (
        "form_1_misses_precision",
        "form_1_misses_error_bar",
        "form_2_misses_precision",
        "form_2_misses_error_bar",
    ):
        assert verdict[key] <= MISS_BOUND, (regime, key, verdict[key])
