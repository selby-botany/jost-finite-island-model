"""Reproduce the high-migration Dear-Nolan equilibrium case.

This example is intentionally explicit. The natural YAML configuration for
this case is not enough, because the published regime is an equilibrium
condition rather than a generic random-start simulation.

The script keeps the derivation visible:
1. compute the fixed-point identities for the biological scenario,
2. build a near-equilibrium starting state,
3. run a short validation experiment,
4. write the resulting summary to ``report.json``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TEST_PATH = ROOT / "test" / "validation" / "test_simulator_equilibrium.py"


def _load_validation_module():
    """Import the equilibrium-validation helper module."""
    spec = importlib.util.spec_from_file_location("equilibrium_validation", TEST_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def build_naive_config() -> dict[str, object]:
    """Return the configuration a user would naturally write from the docs."""
    return {
        "N": 2000,
        "d": 100,
        "m": 0.01,
        "mu": 0.001,
        "seed": 992000,
        "loci": [{"locus_id": 1, "length": 200}],
        "convergence_statistic": "G_ST",
        "convergence_window": 50,
        "convergence_tolerance": 0.01,
        "max_generations": 3000,
    }


def build_equilibrium_start(module):
    """Construct the near-equilibrium founding state used by the validation.

    The model is started at the fixed point instead of waiting for a random
    start to drift there. This is the scientific idea behind the Dear-Nolan
    high-migration equilibrium test.
    """
    within_star, between_star = module._identity_fixed_point(
        population_size=2000,
        m=0.01,
        mu=0.001,
        d=100,
    )
    return (
        module._dn2_equilibrium_start(
            within_fixed_point=within_star,
            between_fixed_point=between_star,
            d=100,
            shared_count=41,
        ),
        within_star,
        between_star,
    )


def run_validation(module, initial_frequencies):
    """Run the short equilibrium-hold validation used in the test."""
    g_values, d_values = module._run_engine_pooled(
        population_size=2000,
        m=0.01,
        mu=0.001,
        d=100,
        n_loci=1,
        horizon=30,
        replicates=5,
        seed=992000,
        initial_frequencies=initial_frequencies,
    )
    return g_values, d_values


def main() -> None:
    """Write the equilibrium summary to the example's ``report.json``."""
    module = _load_validation_module()
    naive_config = build_naive_config()
    initial_frequencies, within_star, between_star = build_equilibrium_start(module)
    g_values, d_values = run_validation(module, initial_frequencies)
    oracle_g_st, oracle_d = module._identities_to_statistics(
        within_star, between_star, 100
    )

    report = {
        "scenario": "dear_nolan_high",
        "naive_config": naive_config,
        "seed": 992000,
        "replicates": 5,
        "N": 2000,
        "d": 100,
        "m": 0.01,
        "mu": 0.001,
        "horizon": 30,
        "mean_G_ST": sum(g_values) / len(g_values),
        "mean_D": sum(d_values) / len(d_values),
        "oracle_G_ST": oracle_g_st,
        "oracle_D": oracle_d,
    }

    out_path = Path(__file__).with_name("report.json")
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
