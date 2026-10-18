"""A worked example runs as the default batch, or says why it is one run.

The engine's default is `n_replicates: 200`, an adaptively stopped batch with a
confidence interval, and a single run happens only when asked for. A shipped
example that sets `n_replicates: 1` is therefore the exception and has to say
why, in a form a reader and this test can find: the pin line carries a comment
starting `single run:`, and the README has a "Why a single run" section.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "doc" / "examples"
_PIN = re.compile(r"^n_replicates:\s*1\s*(#\s*(?P<comment>.*))?$", re.MULTILINE)


def _pinned() -> list[str]:
    """Return the IDs of the examples that set `n_replicates: 1`."""
    return sorted(
        config.parent.name
        for config in EXAMPLES.glob("*/config.yaml")
        if _PIN.search(config.read_text(encoding="utf-8"))
    )


def test_the_single_run_examples_are_the_ones_whose_subject_is_one_run() -> None:
    """Exactly three examples are single runs, each about a within-run idea."""
    assert _pinned() == [
        "literature-distance-statistics-from-an-explicit-founder-split",
        "several-convergence-statistics",
        "within-run-sigma-band",
    ]


@pytest.mark.parametrize("example", _pinned())
def test_a_single_run_pin_states_its_reason_in_the_config_and_the_readme(
    example: str,
) -> None:
    """The pin line says `single run: <reason>` and the README has the section."""
    directory = EXAMPLES / example
    match = _PIN.search((directory / "config.yaml").read_text(encoding="utf-8"))

    assert match is not None
    comment = match.group("comment") or ""
    assert comment.startswith("single run:"), comment
    assert len(comment) > len("single run: ") + 20, "the reason is missing"
    readme = (directory / "README.md").read_text(encoding="utf-8")
    assert "\n## Why a single run\n" in readme


@pytest.mark.parametrize(
    "example", sorted(p.parent.name for p in EXAMPLES.glob("*/config.yaml"))
)
def test_a_batch_example_does_not_carry_a_stale_single_run_explanation(
    example: str,
) -> None:
    """An example that is not pinned to one run has no "Why a single run" section."""
    if example in _pinned():
        return
    readme = (EXAMPLES / example / "README.md").read_text(encoding="utf-8")

    assert "## Why a single run" not in readme
