"""Static checks on the configuration snippets in the documentation."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fim.gui.presets import list_presets

ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted(
    path
    for path in (ROOT / "doc").rglob("*.md")
    # Design documents describe historical output; only the reference and
    # worked-example documents are executable instructions.
    if path.name in {"usage.md", "configuration.md", "convergence.md", "README.md"}
)
YAML_BLOCK = re.compile(r"```(?:yaml|yml)\n(.*?)\n```", re.S)
WORKED_EXAMPLE_BLOCK = re.compile(
    r"<!-- worked-example-config: (?P<relative_path>"
    r"examples/[a-z0-9-]+/config\.yaml) -->\n"
    r"```yaml\n(?P<configuration>.*?)\n```",
    re.S,
)
PLOIDY_WORDS = ("haploid", "diploid", "triploid", "tetraploid")


@pytest.mark.parametrize("path", DOCS, ids=lambda path: str(path.relative_to(ROOT)))
def test_every_configuration_snippet_that_sets_n_also_sets_a_ploidy_word(
    path: Path,
) -> None:
    """`N` counts individuals, so a snippet that sets it must say the ploidy.

    A snippet copied into a file must run, and a number is refused
    (`SimulationParams.from_mapping`), so the ploidy is checked as a word.
    """
    for block in YAML_BLOCK.findall(path.read_text(encoding="utf-8")):
        if not re.search(r"^N:", block, re.M):
            continue
        match = re.search(r"^ploidy:\s*(\w+)", block, re.M)
        assert match is not None, (
            f"{path.name}: snippet sets N but not ploidy:\n{block}"
        )
        assert match.group(1) in PLOIDY_WORDS, block


def test_worked_example_configs_are_the_source_of_usage_yaml() -> None:
    """Every inline worked-example config matches its canonical YAML file."""
    usage = (ROOT / "doc" / "usage.md").read_text(encoding="utf-8")
    references: set[Path] = set()

    for match in WORKED_EXAMPLE_BLOCK.finditer(usage):
        config_path = ROOT / "doc" / match.group("relative_path")
        assert config_path not in references
        references.add(config_path)
        assert (config_path.parent / "README.md").is_file()
        assert match.group("configuration") == config_path.read_text(
            encoding="utf-8"
        ).removesuffix("\n")

    assert len(references) == 14


# Worked examples that deliberately pin a run length: a one-generation
# statistics check, and two engine-timing workloads.
PINNED_CONVERGENCE_EXAMPLES = {
    "literature-distance-statistics-from-an-explicit-founder-split",
    "a-large-d-batch-under-generational-vector",
    "a-long-locus-batch-under-the-generational-engine",
}


def test_worked_examples_use_derived_convergence_unless_deliberately_pinned() -> None:
    """A pinned window or generation cap makes a run stop long before it settles.

    Every worked example lets `convergence_window` and `max_generations` be
    derived from the model, except the few that name a reason to pin them.
    Pinning `convergence_window: 10` was what made the hub example stop at
    generation 19 with a meaningless result.
    """
    webui = ROOT / "src" / "fim" / "gui" / "webui"
    pinned = set()
    for preset in list_presets(webui):
        keys = re.findall(
            r"^(convergence_window|max_generations):\s*(\S+)",
            preset.yaml_text,
            re.M,
        )
        if any(value != "auto" for _, value in keys):
            pinned.add(preset.preset_id)

    assert pinned == PINNED_CONVERGENCE_EXAMPLES
