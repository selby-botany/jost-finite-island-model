"""The configuration reference names every setting where a search finds it.

`doc/configuration.md` gives each setting a `###` heading, and a settings
index near the top lists them all with links. A heading that hid a key
behind markup (`n<sub>replicates</sub>`) could not be found by searching
for `n_replicates`, so headings spell each key literally and the index must
list every one.
"""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_anchor_for() -> Callable[[str], str]:
    """Load `dev/lib/docslug.py`'s `anchor_for`, the slugger the link checker uses."""
    spec = importlib.util.spec_from_file_location(
        "docslug", ROOT / "dev" / "lib" / "docslug.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    anchor: Callable[[str], str] = module.anchor_for
    return anchor


anchor_for = _load_anchor_for()
_REFERENCE = ROOT / "doc" / "configuration.md"
_SETTING_SECTIONS = {
    "Required model keys",
    "Loci",
    "Mutation model",
    "Initial conditions",
    "Convergence",
    "Analysis and execution",
    "Engine backend and JIT",
    "Run labels",
    "Internal attributes",
}


def _headings() -> list[tuple[str, str]]:
    """Return `(section, heading text)` for every `###` heading, outside code."""
    section = ""
    found: list[tuple[str, str]] = []
    in_code = False
    for line in _REFERENCE.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_code = not in_code
        if in_code:
            continue
        if line.startswith("## "):
            section = line[3:]
        elif line.startswith("### ") and section in _SETTING_SECTIONS:
            found.append((section, line[4:]))
    return found


def _index_rows() -> dict[str, str]:
    """Return `{setting: anchor}` from the settings index table."""
    rows = {}
    for line in _REFERENCE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\| \[`([^`]+)`\]\(#([^)]+)\) \|", line)
        if match:
            rows[match.group(1)] = match.group(2)
    return rows


def test_every_setting_heading_is_in_the_index_with_its_anchor() -> None:
    """Each key in a heading has an index row linking to that heading."""
    index = _index_rows()
    expected = {}
    for _section, heading in _headings():
        for key in heading.split(","):
            expected[key.strip().strip("`")] = anchor_for(heading)

    assert expected
    assert index == expected


def test_setting_headings_spell_the_key_without_markup() -> None:
    """No setting heading hides a key behind a subscript or other tag."""
    for _section, heading in _headings():
        assert "<" not in heading, heading


def test_the_index_is_alphabetical() -> None:
    """The index reads in alphabetical order, ignoring case."""
    keys = list(_index_rows())

    assert keys == sorted(keys, key=str.lower)
