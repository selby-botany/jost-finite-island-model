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
headings do (design §3.6, §4.0 #1). §3.6's cardinality rule decides
what earns a live widget here at all: O(1) and O(d)/O(loci)-sized
fields do (a comma-separated text field faithfully represents either);
a `d`-by-`d` migration matrix, an arbitrary sparse map, a per-locus
`p_0`, and — narrower cases the design doc does not work through in
the same detail — a genuinely per-locus `mu` or a `loci` list with
custom `locus_id`s do not (§2.3). `m` and `p_0` get the read-only
"loaded from file" badge treatment §4.0 #3 describes when a loaded
configuration actually uses one; `mu`-per-locus and custom-ID `loci`
instead raise a clear `ValueError` from `params_to_form_values` (the
same "edit the YAML file directly" pattern this form has always used
for a construct it cannot represent at all, load-only badge or not) —
narrower, later-discovered edge cases than the two the design doc's
own worked examples cover, not a deliberate scope reduction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

import yaml

from fim.cli import STARTER_CONFIG
from fim.model.params import SimulationParams

FieldKind = Literal[
    "int", "float", "int_list", "choice", "optional_float", "float_choice"
]

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
        kind: How the field's text is parsed and, for "choice"/
            "float_choice", which values are offered. "int_list"
            accepts either one bare integer or a comma-separated list
            of them (§3.6's O(d)/O(loci) case: a scalar and a per-
            deme/per-locus list are both faithfully representable by
            the same widget). "optional_float" treats an empty string
            as `None`, matching a field whose `SimulationParams`
            default is `None` (`replicate_tolerance`). "float_choice"
            is "choice" restricted to a fixed set of numbers rather
            than tokens (`replicate_confidence`) — `from_mapping`
            requires an actual `float`, not its string spelling.
        choices: The fixed option list for a "choice"/"float_choice"
            field; empty otherwise.
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

# `mu`/`mu_b` (§4.0 #4) and `p_0`'s read-only summary (§3.6, §4.0 #3)
# are composite, not plain `FormField`s — see the `mu_*`/`p0_*`
# functions below — so this tab's only plain fields are
# `mutation_model` and the locus-lengths comma list, whose item count
# *is* `n_loci` (§3.6: one length per locus, the same O(loci)
# cardinality-rule shape `N` already uses for O(d)).
MUTATION_FIELDS: Final[tuple[FormField, ...]] = (
    FormField(
        "mutation_model",
        "mutation model",
        "choice",
        choices=("infinite_alleles", "finite_alleles"),
    ),
    FormField("locus_lengths", "locus length(s)", "int_list"),
)

INITIAL_CONDITIONS_FIELDS: Final[tuple[FormField, ...]] = (
    FormField("initial_allele_count", "initial allele count", "int"),
    FormField("initial_concentration", "concentration", "float"),
)

# `convergence_statistic`'s multi-select is composite — see the
# `convergence_statistic_*` functions below. `convergence_combinator`
# is a plain field (its marshaling is trivial, just a "choice"); only
# its *visibility* is conditional (shown once two or more statistics
# are checked, §4.1) — the screen's own concern, not this module's.
CONVERGENCE_FIELDS: Final[tuple[FormField, ...]] = (
    FormField("convergence_combinator", "combinator", "choice", choices=("any", "all")),
    FormField("convergence_window", "convergence window", "int"),
    FormField("convergence_tolerance", "tolerance", "float"),
)

# `replicate_tolerance`/`replicate_minimum`/`replicate_confidence` are
# shown only once `n_replicates` is greater than 1 (§4.1) — a
# visibility rule the screen applies, not a different marshaling
# shape, so they stay plain `FormField`s. `replicate_tolerance` is
# `float | None`; an empty field means "unset", matching
# `SimulationParams`'s own default.
BATCH_FIELDS: Final[tuple[FormField, ...]] = (
    FormField("n_replicates", "n_replicates", "int"),
    FormField("replicate_tolerance", "replicate tolerance", "optional_float"),
    FormField("replicate_minimum", "replicate minimum", "int"),
    FormField(
        "replicate_confidence",
        "replicate confidence",
        "float_choice",
        choices=("0.9", "0.95", "0.99"),
    ),
)

CONVERGENCE_STATISTIC_NAMES: Final[tuple[str, ...]] = (
    "D",
    "G_ST",
    "E_ST",
    "K_ST",
    "H_S",
    "H_T",
)

TABS: Final[tuple[TabSpec, ...]] = (
    TabSpec("population", "Population", POPULATION_FIELDS),
    TabSpec("migration", "Migration", MIGRATION_FIELDS),
    TabSpec("mutation", "Mutation", MUTATION_FIELDS),
    TabSpec("initial_conditions", "Initial conditions", INITIAL_CONDITIONS_FIELDS),
    TabSpec("convergence", "Convergence", CONVERGENCE_FIELDS),
    TabSpec("batch", "Batch", BATCH_FIELDS),
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
        if field.kind == "int_list":
            # An "int_list" field's own parser (`_parse_int_list_named`)
            # names a bad scalar "<name> ..." and a bad list item
            # "<name>[0] ...", "<name>[1] ...": both identify this one
            # field, unlike every other name below where a bare prefix
            # match risks a false positive against an unrelated key that
            # merely starts with the same letters.
            if message.startswith((f"{field.name} ", f"{field.name}[")):
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
        elif field.kind in ("float", "float_choice"):
            payload[field.name] = _parse_float_named(field.name, text)
        elif field.kind == "optional_float":
            payload[field.name] = (
                None if not text else _parse_float_named(field.name, text)
            )
        elif field.kind == "int_list":
            n_loci_field = field.name == "locus_lengths"
            parsed = _parse_int_list_named(field.name, text)
            payload[field.name] = parsed
            if n_loci_field:
                payload["n_loci"] = 1 if isinstance(parsed, int) else len(parsed)
        else:
            payload[field.name] = text
    payload["m"] = m_to_payload(values)
    payload.update(mu_to_payload(values))
    payload["convergence_statistic"] = convergence_statistic_to_payload(values)
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
        ValueError: If the active sub-field's text is not a number, if
            `m_mode == "loaded"` (a loaded matrix/sparse map has no
            editable representation here at all — §3.6, §4.0 #3; the
            screen itself is responsible for re-submitting a loaded,
            untouched `m` from the `SimulationParams` it was loaded
            from, rather than asking this function to reconstruct a
            matrix from a summary string), or `m_mode` is none of the
            three (a programming error in the caller, not a
            user-facing validation case).
    """
    mode = values["m_mode"]
    if mode == "scalar":
        return _parse_float_named("m", values["m_rate"])
    if mode == "topology":
        return {
            "topology": values["m_topology"],
            "rate": _parse_float_named("m.rate", values["m_topology_rate"]),
        }
    if mode == "loaded":
        raise ValueError(
            "m uses a loaded migration matrix or sparse map; it cannot be "
            "edited here — load a different configuration, or switch to a "
            "scalar rate or named topology, to change it"
        )
    raise ValueError(f"unknown m selector mode: {mode!r}")


def m_from_params(params: SimulationParams) -> dict[str, str]:
    """Render `params.m` back into the selector's form-value keys.

    Args:
        params: A validated configuration.

    Returns:
        `m_mode`/`m_rate`/`m_topology`/`m_topology_rate`/
        `m_loaded_summary`. A scalar `params.m` renders as `"scalar"`
        mode. A matrix-shaped `params.m` renders as `"loaded"` mode
        with a read-only summary (§3.6, §4.0 #3) — a stepping-stone
        topology's own `{topology, rate}` sugar expands into a full
        dense matrix the moment `from_mapping` parses it
        (`fim.model.params.Migration = float | tuple[tuple[float,
        ...], ...]`), so there is no way to tell, from the matrix
        alone, which topology (or none at all, an explicit or sparse-
        map matrix) produced it — "loaded" is the only honest
        representation for any matrix-shaped `m`, not only a sparse-
        map or explicitly-authored one.
    """
    if isinstance(params.m, float):
        return {
            "m_mode": "scalar",
            "m_rate": str(params.m),
            "m_topology": MIGRATION_TOPOLOGIES[0],
            "m_topology_rate": "",
            "m_loaded_summary": "",
        }
    size = len(params.m)
    return {
        "m_mode": "loaded",
        "m_rate": "",
        "m_topology": MIGRATION_TOPOLOGIES[0],
        "m_topology_rate": "",
        "m_loaded_summary": (
            f"{size}\N{MULTIPLICATION SIGN}{size} migration matrix "
            "(loaded from file — edit via Load YAML…)"
        ),
    }


def mu_to_payload(values: Mapping[str, str]) -> dict[str, object]:
    """Build `mu`'s or `mu_b`'s payload key from the selector's mode.

    Args:
        values: The full form-values mapping; only `mu_mode`,
            `mu_value`, and `mu_b_value` are read.

    Returns:
        `{"mu": <rate>}` or `{"mu_b": <rate>}` — never both, matching
        `SimulationParams.from_mapping`'s own mutual-exclusivity rule
        (§4.0 #4: the exclusivity is this selector's shape, not a
        validation message discovered after submitting both).

    Raises:
        ValueError: If the active sub-field's text is not a number, or
            `mu_mode` is neither `"mu"` nor `"mu_b"` (a programming
            error in the caller).
    """
    mode = values["mu_mode"]
    if mode == "mu":
        return {"mu": _parse_float_named("mu", values["mu_value"])}
    if mode == "mu_b":
        return {"mu_b": _parse_float_named("mu_b", values["mu_b_value"])}
    raise ValueError(f"unknown mu selector mode: {mode!r}")


def mu_from_params(params: SimulationParams) -> dict[str, str]:
    """Render `params.mu` back into the mu/mu_b selector's form-value keys.

    Args:
        params: A validated configuration.

    Returns:
        `mu_mode`/`mu_value`/`mu_b_value`. `SimulationParams.
        __post_init__` collapses `mu` back to a scalar whenever every
        locus's rate happens to be equal — whether it came from a
        scalar `mu`, a per-locus `mu` list whose values all matched, or
        `mu_b` with equal-length loci — so a scalar `params.mu` is
        always representable here as `mu_mode="mu"`, exactly
        reproducing the value actually used regardless of how the
        loaded config originally spelled it.

    Raises:
        ValueError: If `params.mu` is a genuinely per-locus tuple
            (unequal rates across loci) — narrower than the design
            doc's own worked "loaded" badge examples (m, p_0); this
            form has no per-locus mu editor, load-only or otherwise,
            so the message says to edit the YAML file directly, the
            same pattern this form already uses for every other
            construct it cannot represent at all.
    """
    if not isinstance(params.mu, float):
        raise ValueError(
            "this configuration uses a per-locus mu; edit the YAML file "
            "directly — the form only edits a single shared mu or mu_b"
        )
    return {"mu_mode": "mu", "mu_value": str(params.mu), "mu_b_value": ""}


def convergence_statistic_to_payload(values: Mapping[str, str]) -> str | list[str]:
    """Build `convergence_statistic`'s payload from the multi-select checkboxes.

    Args:
        values: The full form-values mapping; only the `cs_<NAME>` keys
            (one per `CONVERGENCE_STATISTIC_NAMES` entry, `"true"` or
            `"false"`) are read.

    Returns:
        The one checked name as a bare string (`from_mapping`'s own
        single-statistic shape), or every checked name as a list, in
        `CONVERGENCE_STATISTIC_NAMES` order, once two or more are
        checked.
    """
    checked = [
        name for name in CONVERGENCE_STATISTIC_NAMES if values[f"cs_{name}"] == "true"
    ]
    return checked[0] if len(checked) == 1 else checked


def convergence_statistic_from_params(params: SimulationParams) -> dict[str, str]:
    """Render `params.convergence_statistic` back into the checkbox keys."""
    watched = set(params.convergence_statistics)
    return {
        f"cs_{name}": "true" if name in watched else "false"
        for name in CONVERGENCE_STATISTIC_NAMES
    }


def p0_summary_from_params(params: SimulationParams) -> str:
    """Return the Initial conditions tab's read-only `p_0` summary.

    Returns:
        A description naming the deme and locus counts when
        `params.initial_frequencies` is set (§2.3: `p_0` is genuinely
        unbounded and load-only, unlike every other field this
        revision brings into scope — there is no editable widget for
        it at all, load-only badge or not), or `""` otherwise.
    """
    if params.initial_frequencies is None:
        return ""
    return (
        f"initial frequencies loaded for {params.d} deme(s), "
        f"{len(params.loci)} locus/loci (loaded from file — edit via "
        "Load YAML…)"
    )


def params_to_form_values(params: SimulationParams) -> dict[str, str]:
    """Render a validated `SimulationParams` back into the form's fields.

    Args:
        params: A validated configuration, typically loaded from YAML or
            produced by `starter_form_values`'s own round trip.

    Returns:
        One string per `all_fields()` entry, plus every composite
        field's own keys (`m_*`, `mu_*`, `cs_*`, `p0_summary`),
        suitable for `screens.input_screen.InputScreen.set_values`.

    Raises:
        ValueError: If `params` uses a construct this form cannot
            represent at all — a per-locus `mu` (`mu_from_params`), or
            a `loci` list with custom, non-default-position
            `locus_id`s (narrower than the design doc's own worked
            "loaded" badge examples; §2.3 names an explicit `loci` list
            with custom ordering as load-only, and this form's one
            `locus_lengths` field cannot express a custom ID either).
    """
    if any(locus.locus_id != index + 1 for index, locus in enumerate(params.loci)):
        raise ValueError(
            "this configuration uses custom locus IDs; edit the YAML file "
            "directly — the form only edits locus lengths, in position order"
        )
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
        "mutation_model": params.mutation_model,
        "locus_lengths": ",".join(str(locus.length) for locus in params.loci),
        "initial_allele_count": str(params.initial_allele_count),
        "initial_concentration": str(params.initial_concentration),
        "p0_summary": p0_summary_from_params(params),
        "convergence_combinator": params.convergence_combinator,
        "convergence_window": str(params.convergence_window),
        "convergence_tolerance": str(params.convergence_tolerance),
        "n_replicates": str(params.n_replicates),
        "replicate_tolerance": (
            ""
            if params.replicate_tolerance is None
            else str(params.replicate_tolerance)
        ),
        "replicate_minimum": str(params.replicate_minimum),
        "replicate_confidence": str(params.replicate_confidence),
    }
    values.update(m_from_params(params))
    values.update(mu_from_params(params))
    values.update(convergence_statistic_from_params(params))
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
