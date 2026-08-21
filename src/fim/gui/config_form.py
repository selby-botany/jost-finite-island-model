"""Marshal `SimulationParams` to and from the tabbed model-input screen.

Every function here is a pure, Tk-free transformation between three
shapes: a `SimulationParams` instance, a `dict[str, str]` of one string
per form field (what `screens.input_screen.InputScreen` reads from and
writes to its widgets), and a `dict[str, object]` payload ready for
`fim.model.params.SimulationParams.from_mapping` — the identical
validator `fim.cli` already uses (design doc §3.6). Nothing here
duplicates a validation rule `from_mapping` already enforces: a
malformed string is only ever coerced to the right Python type before
being handed to that one validator, never re-checked against a second,
GUI-local copy of a rule.

`TABS` groups fields the same way
[configuration.md](../../../doc/configuration.md)'s own section
headings do (design §3.6, §4.0 #1) — this commit builds the first two,
**Population** and **Migration**; later commits in this milestone add
the remaining four. §3.6's cardinality rule decides what earns a live
widget here at all: O(1) and O(d)/O(loci)-sized fields do (a
comma-separated text field faithfully represents either); a `d`-by-`d`
migration matrix or an arbitrary sparse map does not (§2.3) — `m`
itself is edited only as a scalar rate or a named stepping-stone
topology (`ring`/`linear` + one shared rate), matching
[configuration.md](../../../doc/configuration.md#m)'s own two O(1)
shorthands.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

import yaml

from fim.cli import STARTER_CONFIG
from fim.model.params import SimulationParams

FieldKind = Literal["int", "float", "int_list", "choice"]

# The two `m` selector modes (design §4.1's radio between a scalar rate
# and a named topology) and the topologies `fim.model.topology` itself
# accepts — kept here, not imported from there, since the GUI only ever
# needs the two literal option strings, not the topology machinery.
MigrationMode = Literal["scalar", "topology"]
MIGRATION_TOPOLOGIES: Final[tuple[str, ...]] = ("ring", "linear")


@dataclass(frozen=True, slots=True)
class FormField:
    """One model-input screen field's config key, label, and value kind.

    Args:
        name: The exact `SimulationParams.from_mapping` config key this
            field edits — also the prefix `field_for_error` matches an
            error message against, so it must match verbatim.
        label: Human-readable text shown beside the field.
        kind: How the field's text is parsed and, for "choice", which
            values are offered. "int_list" accepts either one bare
            integer or a comma-separated list of them (§3.6's O(d)
            case: a scalar and a per-deme list are both faithfully
            representable by the same widget).
        choices: The fixed option list for a "choice" field; empty
            otherwise.
    """

    name: str
    label: str
    kind: FieldKind
    choices: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TabSpec:
    """One Screen 1 tab: a name, a display label, and its plain fields.

    `m`'s scalar-vs-topology selector is not a `FormField` — it needs
    its own composite widget (a mode radio plus one or two sub-fields,
    §4.1) — and so is marshaled by the dedicated `m_*` functions below
    instead of appearing in any `TabSpec.fields` tuple.
    """

    name: str
    label: str
    fields: tuple[FormField, ...]


POPULATION_FIELDS: Final[tuple[FormField, ...]] = (
    FormField("N", "N (gene copies/deme)", "int_list"),
    FormField("d", "d (demes)", "int"),
    FormField("seed", "seed", "int"),
    FormField("deme_weighting", "deme weighting", "choice", choices=("size", "equal")),
    FormField("max_generations", "max generations", "int"),
)

# `migrant_sampling` (G11) sits on this tab rather than a dedicated one
# of its own: it describes how many gene copies migrate each
# generation (configuration.md's own "Analysis and execution" section
# groups it with the batch/execution fields, but semantically it is a
# migration-behavior toggle, not a batch one, and design §4.1's own
# tab breakdown does not name a tab for it at all — an omission this
# implementation resolves by placing it beside `m` rather than
# inventing a seventh tab for one field).
MIGRATION_FIELDS: Final[tuple[FormField, ...]] = (
    FormField(
        "migrant_sampling",
        "migrant sampling",
        "choice",
        choices=("continuous", "stochastic"),
    ),
)

TABS: Final[tuple[TabSpec, ...]] = (
    TabSpec("population", "Population", POPULATION_FIELDS),
    TabSpec("migration", "Migration", MIGRATION_FIELDS),
)


def all_fields() -> tuple[FormField, ...]:
    """Return every plain `FormField` across every tab, in tab order."""
    return tuple(field for tab in TABS for field in tab.fields)


def field_for_error(message: str) -> str | None:
    """Return the form field name a validation error message names, if any.

    Args:
        message: A `ValueError` message raised by `form_values_to_payload`
            or `SimulationParams.from_mapping`.

    Returns:
        The matching `FormField.name`, for an inline error placement next
        to that field, or `None` when the message names no exposed field
        (an unknown-key error, an `m`/`m.topology`/`m.rate` message —
        `m` has no single `FormField` of its own — or a construct not
        yet in scope) — the caller shows those in a banner instead
        (design §4.6).
    """
    for field in all_fields():
        if field.name == "N":
            # `_parse_population_size` names a bad scalar "N ..." and a
            # bad list item "N[0] ...", "N[1] ...": both identify this
            # one field, unlike every other name below where a bare
            # prefix match risks a false positive against an unrelated
            # key that merely starts with the same letters.
            if message.startswith(("N ", "N[")):
                return field.name
            continue
        if message.startswith(f"{field.name} "):
            return field.name
    return None


def form_values_to_payload(values: Mapping[str, str]) -> dict[str, object]:
    """Coerce the form's string values into a `from_mapping`-ready payload.

    Args:
        values: One string per `all_fields()` entry, plus the `m_*`
            selector keys `m_to_payload` reads, keyed by name.

    Returns:
        A mapping ready for `SimulationParams.from_mapping`.

    Raises:
        ValueError: If a field's text does not parse as its declared
            kind. Every message begins with the field's own `name`
            (or, for `N`'s list form, `name[index]`), matching
            `SimulationParams.from_mapping`'s own wording, so
            `field_for_error` and the CLI's error text stay in
            lockstep.
    """
    payload: dict[str, object] = {}
    for field in all_fields():
        text = values[field.name].strip()
        if field.kind == "int":
            payload[field.name] = _parse_int_named(field.name, text)
        elif field.kind == "float":
            payload[field.name] = _parse_float_named(field.name, text)
        elif field.kind == "int_list":
            payload[field.name] = _parse_int_list_named(field.name, text)
        else:
            payload[field.name] = text
    payload["m"] = m_to_payload(values)
    return payload


def m_to_payload(values: Mapping[str, str]) -> float | dict[str, object]:
    """Build `m`'s payload from the selector's mode and its own sub-fields.

    Args:
        values: The full form-values mapping; only `m_mode`, `m_rate`,
            `m_topology`, and `m_topology_rate` are read.

    Returns:
        A bare scalar rate (`m_mode == "scalar"`), or a `{"topology",
        "rate"}` mapping (`m_mode == "topology"`) —
        `fim.model.params._parse_migration` accepts either verbatim.

    Raises:
        ValueError: If the active sub-field's text is not a number, or
            `m_mode` is neither `"scalar"` nor `"topology"` (a
            programming error in the caller, not a user-facing
            validation case — every real widget only ever writes one
            of the two).
    """
    mode = values["m_mode"]
    if mode == "scalar":
        return _parse_float_named("m", values["m_rate"])
    if mode == "topology":
        return {
            "topology": values["m_topology"],
            "rate": _parse_float_named("m.rate", values["m_topology_rate"]),
        }
    raise ValueError(f"unknown m selector mode: {mode!r}")


def m_from_params(params: SimulationParams) -> dict[str, str]:
    """Render `params.m` back into the selector's form-value keys.

    Args:
        params: A validated configuration.

    Returns:
        `m_mode`/`m_rate`/`m_topology`/`m_topology_rate`, covering the
        one shape this selector can represent: a scalar rate. A
        stepping-stone topology's own `{topology, rate}` sugar expands
        into a full dense matrix the moment `from_mapping` parses it
        (`fim.model.params.Migration = float | tuple[tuple[float,
        ...], ...]`) — there is no way to tell, from the matrix alone,
        which topology (or no topology at all) produced it, so a
        matrix-shaped `m` is never reconstructed into "topology" mode
        here. A later commit in this milestone (§4.0 #3, §3.6) adds the
        read-only summary badge that handles a matrix-shaped `m`
        instead; until then this falls back to an empty scalar field
        rather than guessing.
    """
    if isinstance(params.m, float):
        return {
            "m_mode": "scalar",
            "m_rate": str(params.m),
            "m_topology": MIGRATION_TOPOLOGIES[0],
            "m_topology_rate": "",
        }
    return {
        "m_mode": "scalar",
        "m_rate": "",
        "m_topology": MIGRATION_TOPOLOGIES[0],
        "m_topology_rate": "",
    }


def params_to_form_values(params: SimulationParams) -> dict[str, str]:
    """Render a validated `SimulationParams` back into the form's fields.

    Args:
        params: A validated configuration, typically loaded from YAML or
            produced by `starter_form_values`'s own round trip.

    Returns:
        One string per `all_fields()` entry, plus the `m_*` selector
        keys from `m_from_params`, suitable for
        `screens.input_screen.InputScreen.set_values`.
    """
    n_text = (
        str(params.N)
        if isinstance(params.N, int)
        else ",".join(str(value) for value in params.N)
    )
    values: dict[str, str] = {
        "N": n_text,
        "d": str(params.d),
        "seed": str(params.seed),
        "deme_weighting": params.deme_weighting,
        "max_generations": str(params.max_generations),
        "migrant_sampling": params.migrant_sampling,
    }
    values.update(m_from_params(params))
    return values


def starter_form_values() -> dict[str, str]:
    """Return the form's default values, from the CLI's own starter config.

    Returns:
        The same values `params_to_form_values` would compute for
        `fim.cli.STARTER_CONFIG` — the single source of "GUI defaults"
        design §3.6 requires, so a fresh form and `fim init` can never
        drift apart into two documented starting scenarios.
    """
    starter_params = SimulationParams.from_mapping(yaml.safe_load(STARTER_CONFIG))
    return params_to_form_values(starter_params)


def _parse_float_named(name: str, text: str) -> float:
    """Parse one float field's text, matching `_parse_float`'s wording."""
    try:
        return float(text)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error


def _parse_int_list_named(name: str, text: str) -> int | list[int]:
    """Parse a bare integer or a comma-separated list of them.

    Mirrors `fim.model.params._parse_population_size`'s own two
    accepted shapes, and its exact per-item error wording (`f"{name}
    [{index}] must be an integer"`, without the space
    `_parse_int_named` puts between name and message — matching
    `_parse_int`'s own `f"{name}[{index}]"` key format) so
    `field_for_error` recognizes either failure.
    """
    items = [item.strip() for item in text.split(",")]
    if len(items) == 1:
        return _parse_int_named(name, items[0])
    return [
        _parse_int_named(f"{name}[{index}]", item) for index, item in enumerate(items)
    ]


def _parse_int_named(name: str, text: str) -> int:
    """Parse one int field's text, matching `_parse_int`'s wording."""
    try:
        return int(text)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
