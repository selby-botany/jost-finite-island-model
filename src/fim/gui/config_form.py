"""Marshal `SimulationParams` to and from the tabbed model-input screen.

Every function here is a pure, Tk-free transformation between three
shapes: a `SimulationParams` instance, a `dict[str, str]` of one string
per form field (what `screens.input_screen.InputScreen` reads from and
writes to its widgets), and a `dict[str, object]` payload ready for
`fim.model.params.SimulationParams.from_mapping` — the identical
validator `fim.cli` already uses (`doc/fim-gui-design.md` §6.1).
Nothing here duplicates a validation rule `from_mapping` already
enforces: a malformed string is only ever coerced to the right Python
type before being handed to that one validator, never re-checked
against a second, GUI-local copy of a rule.

`TABS` groups fields the same way
[configuration.md](../../doc/configuration.md)'s own section
headings do. The cardinality rule (`doc/fim-gui-design.md` §6.1)
decides what earns a live widget here at all: O(1) and O(d)/O(loci)-
sized fields do (a comma-separated text field faithfully represents
either); a `d`-by-`d` migration matrix, a `loci` list with custom
`locus_id`s, and a `d`-by-locus explicit `p_0` now do too, each edited
cell by cell (botanist GUI design doc
`20260907-claude-sonnet-5-botanist-gui-redesign.md` §4.4 —
`m_to_payload`/`m_from_params`'s own `"matrix"` mode,
`loci_to_payload`/`loci_from_params`'s own `"custom"` mode, and
`initial_conditions_to_payload`/`initial_conditions_from_params`'s own
`"explicit_p0"` mode, respectively); a genuinely per-locus `mu` still
does not, and instead raises a clear `ValueError` from
`params_to_form_values` (the same "edit the YAML file directly" pattern
this form has always used for a construct it cannot represent at all)
— see `doc/fim-gui-design.md` §6.2.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

import yaml

from fim.cli import STARTER_CONFIG
from fim.model.params import SimulationParams

FieldKind = Literal[
    "int", "float", "int_list", "choice", "optional_float", "float_choice"
]

# The two `m` selector modes (a radio between a scalar rate
# and a named topology) and the topologies `fim.model.topology` itself
# accepts — kept here, not imported from there, since the GUI only ever
# needs the two literal option strings, not the topology machinery.
MigrationMode = Literal["scalar", "topology", "matrix"]
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
# migration-behavior toggle, not a batch one, and no tab breakdown
# names a tab for it at all — an omission this
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

# `mu`/`mu_b` (§4.0 #4) and `loci` (botanist GUI design doc
# `20260907-claude-sonnet-5-botanist-gui-redesign.md` §4.4 -- a mode
# selector between the simple comma-list shorthand and a real per-locus
# grid for custom IDs, `loci_to_payload`/`loci_from_params` below) are
# both composite, not plain `FormField`s — so this tab's only plain
# field is `mutation_model` itself.
MUTATION_FIELDS: Final[tuple[FormField, ...]] = (
    FormField(
        "mutation_model",
        "mutation model",
        "choice",
        choices=("infinite_alleles", "finite_alleles"),
    ),
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


# Composite fields' own config keys, mapped to the tab holding their
# selector — `m`/`mu`/`mu_b`/`convergence_statistic` are never plain
# `FormField`s (see `TabSpec`'s own docstring), so `tab_for_field`
# cannot find them by walking `TABS` the way it does every other name.
_COMPOSITE_FIELD_TABS: Final[Mapping[str, str]] = {
    "m": "migration",
    "m.topology": "migration",
    "m.rate": "migration",
    "mu": "mutation",
    "mu_b": "mutation",
    "convergence_statistic": "convergence",
    "equilibrium": "initial_conditions",
    "equilibrium_convergence_window": "initial_conditions",
    "equilibrium_convergence_tolerance": "initial_conditions",
    "equilibrium_max_generations": "initial_conditions",
    "p_0": "initial_conditions",
    "loci": "mutation",
    "locus": "mutation",
    "locus_lengths": "mutation",
    "sigma_band_multiplier": "convergence",
    "sigma_band_window": "convergence",
}


def tab_for_field(name: str) -> str | None:
    """Return the `TabSpec.name` holding the given config-key field, if any.

    Args:
        name: A `FormField.name`, or one of the composite fields'
            config keys (`m`, `mu`, `mu_b`, `convergence_statistic`).

    Returns:
        The tab's `TabSpec.name` (`config_form.TABS`'s own identifier,
        not its display `label`), or `None` if `name` names no field
        this form exposes at all.
    """
    if name in _COMPOSITE_FIELD_TABS:
        return _COMPOSITE_FIELD_TABS[name]
    for tab in TABS:
        if any(field.name == name for field in tab.fields):
            return tab.name
    return None


def tab_for_error(message: str) -> str | None:
    """Return the tab holding the field a validation error message names.

    Args:
        message: A `ValueError` message raised by `form_values_to_payload`
            or `SimulationParams.from_mapping`.

    Returns:
        The offending field's tab (§4.0 #2: "every tab with an invalid
        field shows a small error dot" — in practice, the one tab
        holding whichever single field `SimulationParams.from_mapping`
        happened to reject first, since it stops validating at the
        first failure rather than collecting every field's own error
        independently), or `None` when the message names no field this
        form can place on any tab (an unknown-key error, for instance)
        — the caller shows those in the banner alone.
    """
    plain_field = field_for_error(message)
    if plain_field is not None:
        return tab_for_field(plain_field)
    # Longer, more specific prefixes first: bare "m" is itself a prefix
    # of "m.topology", "m.rate", "mu", and "mu_b", so checking it first
    # would misroute every one of those to Migration instead of their
    # own tab.
    for name in (
        "m.topology",
        "m.rate",
        "mu_b",
        "mu",
        "m",
        "convergence_statistic",
        "equilibrium",
        "p_0",
        "loci",
        "locus",
    ):
        if message.startswith(name):
            return tab_for_field(name)
    return None


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
        yet in scope) — the caller shows those in a banner instead.
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
    # The three equilibrium-split fields (§4.3): each a plain,
    # single-widget leaf like any other field — unlike `m`/`mu_b`,
    # which span several alternate representations with no one single
    # widget — but excluded from `all_fields()`/`TABS` itself (not from
    # `INITIAL_CONDITIONS_FIELDS`) because their presence in the
    # payload is conditional on `initial_conditions_mode`
    # (`initial_conditions_to_payload`), unlike every ordinary
    # `FormField` `form_values_to_payload`'s main loop includes
    # unconditionally.
    for name in (
        "equilibrium_convergence_window",
        "equilibrium_convergence_tolerance",
        "equilibrium_max_generations",
    ):
        if message.startswith(f"{name} "):
            return name
    # `sigma_band_multiplier`/`sigma_band_window`: the identical
    # "excluded from `all_fields()` because its own presence in the
    # payload is conditional" shape the three equilibrium fields above
    # already have (`sigma_band_to_payload`'s own toggle), not a new
    # pattern.
    for name in ("sigma_band_multiplier", "sigma_band_window"):
        if message.startswith(f"{name} "):
            return name
    # `locus_lengths` (loci_mode "lengths"): excluded from `all_fields()`
    # for the identical reason the three equilibrium fields above are —
    # its presence in the payload is conditional on `loci_mode`
    # (`loci_to_payload`) — but it is still one real, single-widget
    # field, so its own `int_list`-shaped errors (`_parse_int_list_
    # named`'s own bare-scalar and per-item forms) are still routed
    # directly to it, the same as any `all_fields()` entry of that kind.
    if message.startswith(("locus_lengths ", "locus_lengths[")):
        return "locus_lengths"
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
            kind (every message begins with the field's own `name`, or
            `name[index]` for `N`'s list form, matching
            `SimulationParams.from_mapping`'s own wording so
            `field_for_error` and the CLI's error text stay in
            lockstep), or if `values` is simply missing a key this
            function or one of the mode dispatchers below it
            (`m_to_payload`, `loci_to_payload`, ...) expects. The
            second case is deliberate, not merely tolerated: `Api.
            get_initial_form`'s own re-validation of a *saved* form
            relies on catching exactly `ValueError` to discard a form
            that no longer matches the current field set (a field added
            since it was saved — confirmed live against a real
            `preferences.json` predating `loci_mode`) and fall back to
            starter values, the same schema-drift shape already fixed
            once in `STARTER_CONFIG` (`n_replicates`, CHANGELOG
            "Fixed" 2026-09-08). A bare `KeyError` would defeat that
            fallback silently — the caller's `except ValueError` simply
            never fires, and the whole bridge call surfaces to the user
            as nothing happening at all (`ISSUES.md` would be the right
            place for this if it were only mitigated rather than fixed
            at the source).
    """
    try:
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
                payload[field.name] = _parse_int_list_named(field.name, text)
            else:
                payload[field.name] = text
        payload["m"] = m_to_payload(values)
        payload.update(mu_to_payload(values))
        payload.update(initial_conditions_to_payload(values))
        payload.update(loci_to_payload(values))
        payload["convergence_statistic"] = convergence_statistic_to_payload(values)
        payload.update(sigma_band_to_payload(values))
    except KeyError as error:
        raise ValueError(f"missing field: {error}") from error
    return payload


def m_to_payload(
    values: Mapping[str, str],
) -> float | dict[str, object] | list[list[float]]:
    """Build `m`'s payload from the selector's mode and its own sub-fields.

    Args:
        values: The full form-values mapping; only `m_mode`, `m_rate`,
            `m_topology`, and `m_topology_rate` are read.

    Returns:
        A bare scalar rate (`m_mode == "scalar"`), a `{"topology",
        "rate"}` mapping (`m_mode == "topology"`), or a dense
        `list[list[float]]` matrix (`m_mode == "matrix"`) —
        `fim.model.params._parse_migration` accepts any of the three
        verbatim.

    Raises:
        ValueError: If the active sub-field's text is not a number, if
            `m_matrix_json` (mode `"matrix"`) is not valid JSON or not a
            list of lists of numbers (the grid editor's own JS keeps
            this field in sync with the visible cells on every change,
            so a malformed value here means the grid itself was never
            actually rendered — a programming error to surface loudly,
            not a validation message a user would recognize as their
            own mistake), or `m_mode` is none of the three (a
            programming error in the caller, not a user-facing
            validation case).
    """
    mode = values["m_mode"]
    if mode == "scalar":
        return _parse_float_named("m", values["m_rate"])
    if mode == "topology":
        return {
            "topology": values["m_topology"],
            "rate": _parse_float_named("m.rate", values["m_topology_rate"]),
        }
    if mode == "matrix":
        return _parse_m_matrix_json(values["m_matrix_json"])
    raise ValueError(f"unknown m selector mode: {mode!r}")


def _parse_m_matrix_json(text: str) -> list[list[float]]:
    """Parse the migration-matrix grid editor's own serialized JSON value.

    Args:
        text: `m_matrix_json`'s own current value — a JSON array of
            equal-length arrays of numbers, written by `webui/screens/
            migration-matrix.js` from the grid's own live cell values.

    Raises:
        ValueError: If `text` is not valid JSON, or is not a nonempty
            list of nonempty lists of numbers — the exact shape `fim.
            model.params._parse_migration` itself expects for a dense
            matrix, checked here only well enough to give a clear error
            for a malformed *shape* (this function's own concern); a
            well-shaped matrix with the wrong dimensions for the
            configured `d`, or rows that do not each sum to 1, is still
            `from_mapping`'s own concern to reject, exactly like a
            hand-authored YAML matrix already is.
    """
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"m matrix must be valid JSON: {error}") from error
    if not isinstance(parsed, list) or not parsed:
        raise ValueError("m matrix must be a nonempty list of rows")
    matrix: list[list[float]] = []
    for row in parsed:
        if (
            not isinstance(row, list)
            or not row
            or not all(isinstance(value, int | float) for value in row)
        ):
            raise ValueError("m matrix must be a nonempty list of nonempty number rows")
        matrix.append([float(value) for value in row])
    return matrix


def m_from_params(params: SimulationParams) -> dict[str, str]:
    """Render `params.m` back into the selector's form-value keys.

    Args:
        params: A validated configuration.

    Returns:
        `m_mode`/`m_rate`/`m_topology`/`m_topology_rate`/
        `m_matrix_json`. A scalar `params.m` renders as `"scalar"`
        mode. A matrix-shaped `params.m` — a full matrix, a sparse
        neighbor map, or a stepping-stone topology, all already
        expanded to one dense matrix by the time `from_mapping` parses
        it (`fim.model.params.Migration = float | tuple[tuple[float,
        ...], ...]`) — renders as `"matrix"` mode with the actual dense
        values, editable cell by cell (botanist GUI design doc
        `20260907-claude-sonnet-5-botanist-gui-redesign.md` §4.4,
        replacing an earlier, read-only `"loaded"` mode that could only
        show a size summary: there is still no way to tell, from the
        matrix alone, which topology — or none at all — produced it,
        but that no longer matters once every cell is directly
        editable rather than frozen behind a badge).
    """
    if isinstance(params.m, float):
        return {
            "m_mode": "scalar",
            "m_rate": str(params.m),
            "m_topology": MIGRATION_TOPOLOGIES[0],
            "m_topology_rate": "",
            "m_matrix_json": "",
        }
    return {
        "m_mode": "matrix",
        "m_rate": "",
        "m_topology": MIGRATION_TOPOLOGIES[0],
        "m_topology_rate": "",
        "m_matrix_json": json.dumps([list(row) for row in params.m]),
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


# The two initial-conditions modes (botanist GUI design doc §4.3):
# `"dirichlet"` (the library default — `initial_allele_count`/
# `initial_concentration`, both always present, plain `FormField`s
# already on `INITIAL_CONDITIONS_FIELDS`), `"equilibrium_split"`
# (`fim.model.initial.EquilibriumSplitInitialCondition`, triggered by
# its own three `equilibrium_*` fields' joint presence), `"explicit_p0"`
# (a real `p_0` grid, triggered by `initial_frequencies` — see
# `initial_conditions_to_payload`, below), and `"fixed_per_deme"`
# (botanist GUI design doc `20260907-claude-sonnet-5-botanist-gui-
# redesign.md` §4.3 -- a GUI-only convenience that expands directly to
# an explicit `p_0` at submit time, never stored or round-tripped as
# its own construct: `SimulationParams` itself has no concept of "fixed
# per deme", only the `p_0` it expands to). `SimulationParams` rejects
# combining `initial_frequencies` with any equilibrium field
# (`_validate_equilibrium_split_config`'s own docstring), so
# `"equilibrium_split"` and the two `p_0`-producing modes are genuinely
# mutually exclusive at that level too — unlike `initial_allele_count`/
# `initial_concentration`, which stay set and simply go unused whenever
# another mode is active (`generate_initial_state`'s own dispatch
# order) — only the *payload inclusion* of the equilibrium fields and
# `p_0` is mode-gated here.
InitialConditionsMode = Literal[
    "dirichlet", "equilibrium_split", "explicit_p0", "fixed_per_deme"
]

# The three "fixed per deme" sub-choices (§4.3): every deme starts fixed
# for exactly one allele, differing only in which allele. All three
# apply identically to every locus (the design gives no per-locus
# variant of this mode).
FixedPerDemeChoice = Literal["all_different", "all_same", "all_but_one"]

# `initial_conditions_from_params` always renders some
# `fixed_per_deme_choice` value, even when the loaded configuration is
# not in `"fixed_per_deme"` mode at all (so the radio group always has
# a defined checked option the moment a user switches to that mode) —
# there is no way to recover "the choice that was last selected" from a
# `SimulationParams` alone, since the mode never round-trips (its own
# docstring, above), so this fixed default is used universally. "All
# the same" is the design's own "no differentiation" starting baseline.
_DEFAULT_FIXED_PER_DEME_CHOICE: Final[FixedPerDemeChoice] = "all_same"


def initial_conditions_to_payload(values: Mapping[str, str]) -> dict[str, object]:
    """Build the `equilibrium_*`/`p_0` payload keys from the selector's mode.

    Args:
        values: The full form-values mapping; only
            `initial_conditions_mode`, `equilibrium_convergence_window`,
            `equilibrium_convergence_tolerance`,
            `equilibrium_max_generations`, `p0_json`,
            `fixed_per_deme_choice`, `d`, and (via `loci_to_payload`)
            the loci selector's own keys are read.

    Returns:
        An empty mapping in `"dirichlet"` mode (the three equilibrium
        fields and `p_0` are simply absent from the payload, exactly
        like an unset `replicate_tolerance`'s own `None`-by-omission
        convention); the three equilibrium fields, parsed to their
        declared types, in `"equilibrium_split"` mode; `{"p_0": ...}`
        in `"explicit_p0"` mode; or `{"p_0": ...}` expanded from `d`,
        the currently-configured loci count, and
        `fixed_per_deme_choice` in `"fixed_per_deme"` mode.

    Raises:
        ValueError: If `"equilibrium_split"` mode is selected and any
            of the three fields' text does not parse as its declared
            type (every message begins with the field's own name,
            matching `SimulationParams.from_mapping`'s own wording —
            `field_for_error` locates each of the three individually,
            the same as any other plain `FormField`), if
            `"explicit_p0"` mode is selected and `p0_json` is not valid
            JSON in the expected shape, if `"fixed_per_deme"` mode is
            selected and `d` does not parse as an integer or
            `fixed_per_deme_choice` is none of the three sub-choices,
            or `initial_conditions_mode` is none of the four.
    """
    mode = values.get("initial_conditions_mode", "dirichlet")
    if mode == "equilibrium_split":
        return {
            "equilibrium_convergence_window": _parse_int_named(
                "equilibrium_convergence_window",
                values["equilibrium_convergence_window"].strip(),
            ),
            "equilibrium_convergence_tolerance": _parse_float_named(
                "equilibrium_convergence_tolerance",
                values["equilibrium_convergence_tolerance"].strip(),
            ),
            "equilibrium_max_generations": _parse_int_named(
                "equilibrium_max_generations",
                values["equilibrium_max_generations"].strip(),
            ),
        }
    if mode == "explicit_p0":
        return {"p_0": _parse_p0_json(values["p0_json"])}
    if mode == "fixed_per_deme":
        d = _parse_int_named("d", values["d"].strip())
        loci_payload = loci_to_payload(values)
        loci_count = (
            len(loci_payload["loci"])  # type: ignore[arg-type]
            if "loci" in loci_payload
            else loci_payload["n_loci"]
        )
        assert isinstance(loci_count, int)
        return {
            "p_0": _fixed_per_deme_p0(d, loci_count, values["fixed_per_deme_choice"])
        }
    if mode != "dirichlet":
        raise ValueError(f"unknown initial_conditions selector mode: {mode!r}")
    return {}


def _fixed_per_deme_p0(
    d: int, loci_count: int, choice: str
) -> list[list[dict[str, float]]]:
    """Expand a "fixed per deme" sub-choice into an explicit `p_0` (§4.3).

    Args:
        d: The configured deme count.
        loci_count: The configured locus count — every locus gets the
            identical per-deme fixation pattern; the design gives no
            per-locus variant of this mode.
        choice: One of `"all_different"` (deme *i* fixed for allele
            *i*), `"all_same"` (every deme fixed for allele 0 — the "no
            differentiation" starting baseline), or `"all_but_one"`
            (every deme but the last fixed for allele 0, the last fixed
            for allele 1).

    Returns:
        `_parse_p0_json`'s own return shape — a list of `d` demes, each
        a list of `loci_count` identical `{"<allele_id>": 1.0}`
        mappings — ready to hand `SimulationParams.from_mapping` as
        `p_0` verbatim.

    Raises:
        ValueError: If `choice` is none of the three — a clear
            programming error (a JS bug sending an unrecognized radio
            value), not a silent default, matching `m_to_payload`'s/
            `loci_to_payload`'s own "unknown ... mode" guards.
    """
    if choice == "all_different":
        return [[{str(deme): 1.0} for _ in range(loci_count)] for deme in range(d)]
    if choice == "all_same":
        return [[{"0": 1.0} for _ in range(loci_count)] for _ in range(d)]
    if choice == "all_but_one":
        return [
            [{"0": 1.0} for _ in range(loci_count)]
            if deme < d - 1
            else [{"1": 1.0} for _ in range(loci_count)]
            for deme in range(d)
        ]
    raise ValueError(f"unknown fixed_per_deme selector choice: {choice!r}")


def _parse_p0_json(text: str) -> list[list[dict[str, float]]]:
    """Parse the `p_0` grid editor's own serialized JSON value.

    Args:
        text: `p0_json`'s own current value — a JSON array of arrays of
            `{alleleId: frequency}` mappings (one outer entry per deme,
            one inner entry per locus), written by `webui/screens/
            p0-grid.js` from the grid's own live cell values. The exact
            raw shape `fim.model.params._parse_initial_frequencies`
            itself accepts for a hand-authored YAML `p_0` — allele IDs
            stay as JSON's own string mapping keys, parsed to integers
            only by that function, not here.

    Raises:
        ValueError: If `text` is not valid JSON, or is not a list of
            lists of `{alleleId: frequency}` mappings — checked here
            only well enough to give a clear error for a malformed
            *shape* (this function's own concern, with the identical
            per-index wording `_parse_initial_frequencies`'s own
            messages use); the deme count, per-deme locus count, and
            each locus's own frequencies summing to 1 all stay that
            function's concern to reject, exactly like a hand-authored
            YAML `p_0` already is.
    """
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"p_0 must be valid JSON: {error}") from error
    if not isinstance(parsed, list):
        raise ValueError("p_0 must be a list of demes")
    demes: list[list[dict[str, float]]] = []
    for deme_index, raw_deme in enumerate(parsed):
        if not isinstance(raw_deme, list):
            raise ValueError(f"p_0[{deme_index}] must be a list of loci")
        loci: list[dict[str, float]] = []
        for locus_index, raw_locus in enumerate(raw_deme):
            if not isinstance(raw_locus, dict) or not all(
                isinstance(frequency, int | float) and not isinstance(frequency, bool)
                for frequency in raw_locus.values()
            ):
                raise ValueError(
                    f"p_0[{deme_index}][{locus_index}] must be a mapping of "
                    "allele ID to frequency"
                )
            loci.append(dict(raw_locus))
        demes.append(loci)
    return demes


def initial_conditions_from_params(params: SimulationParams) -> dict[str, str]:
    """Render `params`'s starting-frequency fields into the selector's form-value keys.

    Args:
        params: A validated configuration.

    Returns:
        `initial_conditions_mode`/`equilibrium_convergence_window`/
        `equilibrium_convergence_tolerance`/`equilibrium_max_generations`/
        `p0_json`/`fixed_per_deme_choice`. An explicit `p_0` (`params.
        initial_frequencies is not None`) renders as `"explicit_p0"`
        mode with every deme/locus's own real allele-frequency mapping
        (mutually exclusive with the equilibrium fields at the
        `SimulationParams` level, so checking it first is unambiguous)
        — `"fixed_per_deme"` never round-trips back from a `params`
        object at all (its own module-level docstring, above), so a
        `p_0` this shape happens to match still renders as
        `"explicit_p0"`, real values in a real editable grid, not a
        rejected re-run; otherwise the three equilibrium fields render
        as empty strings in `"dirichlet"` mode (`params.
        equilibrium_convergence_window is None`, guaranteed to mean all
        three are `None` together by `SimulationParams`'s own
        all-or-none validation) rather than `"None"` — an empty field,
        not a placeholder value the user would otherwise have to notice
        and clear. `fixed_per_deme_choice` is always
        `_DEFAULT_FIXED_PER_DEME_CHOICE`, regardless of mode.
    """
    if params.initial_frequencies is not None:
        return {
            "initial_conditions_mode": "explicit_p0",
            "equilibrium_convergence_window": "",
            "equilibrium_convergence_tolerance": "",
            "equilibrium_max_generations": "",
            "p0_json": json.dumps(
                [[dict(locus) for locus in deme] for deme in params.initial_frequencies]
            ),
            "fixed_per_deme_choice": _DEFAULT_FIXED_PER_DEME_CHOICE,
        }
    if params.equilibrium_convergence_window is None:
        return {
            "initial_conditions_mode": "dirichlet",
            "equilibrium_convergence_window": "",
            "equilibrium_convergence_tolerance": "",
            "equilibrium_max_generations": "",
            "p0_json": "",
            "fixed_per_deme_choice": _DEFAULT_FIXED_PER_DEME_CHOICE,
        }
    return {
        "initial_conditions_mode": "equilibrium_split",
        "equilibrium_convergence_window": str(params.equilibrium_convergence_window),
        "equilibrium_convergence_tolerance": str(
            params.equilibrium_convergence_tolerance
        ),
        "equilibrium_max_generations": str(params.equilibrium_max_generations),
        "p0_json": "",
        "fixed_per_deme_choice": _DEFAULT_FIXED_PER_DEME_CHOICE,
    }


# The two loci modes (botanist GUI design doc §4.4): `"lengths"` (the
# library default — a comma-separated `locus_lengths` list, sequential
# 1-based IDs implied) and `"custom"` (a real per-locus grid, editable
# `locus_id` and `length` cell by cell — `configuration.md`'s own
# explicit `loci: [{locus_id, length}, ...]` list, submitted directly
# rather than through the `n_loci`/`locus_lengths` shorthand). Replaces
# `params_to_form_values`'s earlier flat rejection of any non-default
# `locus_id` ordering with a real, editable representation — the same
# "loaded badge to real editor" upgrade `m_from_params`'s own `"matrix"`
# mode already made for a loaded migration matrix.
LociMode = Literal["lengths", "custom"]


def loci_to_payload(values: Mapping[str, str]) -> dict[str, object]:
    """Build `loci`'s (or `n_loci`/`locus_lengths`'s) payload from the selector's mode.

    Args:
        values: The full form-values mapping; only `loci_mode`,
            `locus_lengths`, and `loci_json` are read.

    Returns:
        `{"n_loci": ..., "locus_lengths": ...}` (`loci_mode ==
        "lengths"`, mirroring the `int_list` cardinality rule `N`
        already uses) or `{"loci": [...]}` (`loci_mode == "custom"`) —
        `fim.model.params.SimulationParams.from_mapping` accepts either
        shape verbatim, and the two are mutually exclusive in the
        payload, exactly like a hand-authored YAML file only ever uses
        one or the other.

    Raises:
        ValueError: If the active sub-field's text does not parse (a
            malformed `locus_lengths` list, or `loci_json` that is not
            valid JSON shaped as a list of `{locus_id, length}`
            mappings), or `loci_mode` is neither of the two.
    """
    mode = values["loci_mode"]
    if mode == "lengths":
        parsed = _parse_int_list_named("locus_lengths", values["locus_lengths"].strip())
        return {
            "n_loci": 1 if isinstance(parsed, int) else len(parsed),
            "locus_lengths": parsed,
        }
    if mode == "custom":
        return {"loci": _parse_loci_json(values["loci_json"])}
    raise ValueError(f"unknown loci selector mode: {mode!r}")


def _parse_loci_json(text: str) -> list[dict[str, int]]:
    """Parse the per-locus grid editor's own serialized JSON value.

    Args:
        text: `loci_json`'s own current value — a JSON array of
            `{"locus_id": <int>, "length": <int>}` objects, written by
            `webui/screens/loci-grid.js` from the grid's own live rows.

    Raises:
        ValueError: If `text` is not valid JSON, or is not a nonempty
            list of mappings each carrying exactly `locus_id` and
            `length` integer keys — checked here only well enough to
            give a clear error for a malformed *shape* (this function's
            own concern); duplicate locus IDs, a non-positive length, or
            any other semantic rule stays `from_mapping`'s own concern
            to reject, exactly like a hand-authored YAML `loci` list.
    """
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"loci must be valid JSON: {error}") from error
    if not isinstance(parsed, list) or not parsed:
        raise ValueError("loci must be a nonempty list of rows")
    loci: list[dict[str, int]] = []
    for row in parsed:
        if (
            not isinstance(row, dict)
            or set(row) != {"locus_id", "length"}
            or not all(isinstance(value, int) for value in row.values())
        ):
            raise ValueError(
                "loci must be a nonempty list of {locus_id, length} integer rows"
            )
        loci.append({"locus_id": row["locus_id"], "length": row["length"]})
    return loci


def loci_from_params(params: SimulationParams) -> dict[str, str]:
    """Render `params.loci` back into the selector's form-value keys.

    Args:
        params: A validated configuration.

    Returns:
        `loci_mode`/`locus_lengths`/`loci_json`. Sequential, 1-based
        default `locus_id`s (`1, 2, 3, ...`, in position order) render
        as `"lengths"` mode with the existing comma-list; any other
        ordering — custom IDs, non-sequential IDs, or IDs not starting
        at 1 — renders as `"custom"` mode with every locus's own real
        `(locus_id, length)` pair.
    """
    if all(locus.locus_id == index + 1 for index, locus in enumerate(params.loci)):
        return {
            "loci_mode": "lengths",
            "locus_lengths": ",".join(str(locus.length) for locus in params.loci),
            "loci_json": "",
        }
    return {
        "loci_mode": "custom",
        "locus_lengths": "",
        "loci_json": json.dumps(
            [
                {"locus_id": locus.locus_id, "length": locus.length}
                for locus in params.loci
            ]
        ),
    }


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


# GUI-layer starting values for the sigma-band toggle's own two fields,
# offered the first time it is ever checked (`sigma-band-selector`'s own
# JS-side seed listener) — never a `SimulationParams`-level default
# (neither field has one: `sigma_band_multiplier`/`.sigma_band_window`
# are `None` unless a caller states both explicitly, the same
# `equilibrium_*` precedent `sigma_band_from_params`'s own docstring,
# below, names). `"2.0"` matches botanist GUI design doc `20260907-
# claude-sonnet-5-botanist-gui-redesign.md` §7.2's own "A configurable
# sigma multiplier (2-sigma or 3-sigma)" ordering; `"100"` is that
# section's own literal suggested window size.
_DEFAULT_SIGMA_BAND_MULTIPLIER: Final = "2.0"
_DEFAULT_SIGMA_BAND_WINDOW: Final = "100"


def sigma_band_to_payload(values: Mapping[str, str]) -> dict[str, object]:
    """Build the `sigma_band_*` payload keys from the toggle's own checked state.

    Args:
        values: The full form-values mapping; only `sigma_band_enabled`,
            `sigma_band_multiplier`, and `sigma_band_window` are read.

    Returns:
        An empty mapping when the toggle is unchecked — both real
        fields simply absent from the payload, the identical "set
        together or not at all" shape `SimulationParams` itself already
        enforces for this exact pair, and the same by-omission
        convention `replicate_tolerance`'s own `"optional_float"` kind
        already uses for a single optional field. `{"sigma_band_
        multiplier": ..., "sigma_band_window": ...}`, parsed to their
        declared types, when checked.

    Raises:
        ValueError: If the toggle is checked and either field's text
            does not parse as its declared type (`field_for_error`
            locates each of the two individually, the identical
            treatment the three equilibrium-split fields already get
            for the same "conditionally present" reason). This only
            coerces text into the right Python type — `SimulationParams.
            __post_init__` still enforces the closed multiplier set
            (`{2.0, 3.0}`) and the minimum window size (`>= 2`), the
            same "GUI coerces, the model validates" division every
            other field here already follows.
    """
    if values.get("sigma_band_enabled") != "true":
        return {}
    return {
        "sigma_band_multiplier": _parse_float_named(
            "sigma_band_multiplier", values["sigma_band_multiplier"].strip()
        ),
        "sigma_band_window": _parse_int_named(
            "sigma_band_window", values["sigma_band_window"].strip()
        ),
    }


def sigma_band_from_params(params: SimulationParams) -> dict[str, str]:
    """Render `params`'s own sigma-band fields into the toggle's form-value keys.

    Args:
        params: A validated configuration.

    Returns:
        `sigma_band_enabled`/`sigma_band_multiplier`/`sigma_band_window`.
        `sigma_band_enabled` is `"true"` exactly when `params.sigma_
        band_multiplier is not None` (`SimulationParams`'s own
        all-or-none validation guarantees `sigma_band_window` agrees
        whenever it does) — the real fields then render `params`'s own
        values; otherwise both render this module's own suggested
        starting values (`_DEFAULT_SIGMA_BAND_MULTIPLIER`/`_WINDOW`)
        rather than an empty string, so the toggle's own revealed
        fields already hold a sensible starting point the first time a
        user checks it, mirroring `initial_conditions_from_params`'s
        own `fixed_per_deme_choice` precedent (a field that never
        round-trips a "the user's own last choice" value, so it always
        renders one fixed default instead).
    """
    if params.sigma_band_multiplier is None:
        return {
            "sigma_band_enabled": "false",
            "sigma_band_multiplier": _DEFAULT_SIGMA_BAND_MULTIPLIER,
            "sigma_band_window": _DEFAULT_SIGMA_BAND_WINDOW,
        }
    return {
        "sigma_band_enabled": "true",
        "sigma_band_multiplier": str(params.sigma_band_multiplier),
        "sigma_band_window": str(params.sigma_band_window),
    }


def params_to_form_values(params: SimulationParams) -> dict[str, str]:
    """Render a validated `SimulationParams` back into the form's fields.

    Args:
        params: A validated configuration, typically loaded from YAML or
            produced by `starter_form_values`'s own round trip.

    Returns:
        One string per `all_fields()` entry, plus every composite
        field's own keys (`m_*`, `mu_*`, `cs_*`, `p0_json`),
        suitable for `screens.input_screen.InputScreen.set_values`.

    Raises:
        ValueError: If `params` uses a construct this form cannot
            represent at all — a genuinely per-locus `mu`
            (`mu_from_params`'s own docstring; custom locus IDs alone no
            longer trigger this, `loci_from_params` below now renders
            those as a real, editable grid instead).
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
        "mutation_model": params.mutation_model,
        "initial_allele_count": str(params.initial_allele_count),
        "initial_concentration": str(params.initial_concentration),
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
    values.update(initial_conditions_from_params(params))
    values.update(loci_from_params(params))
    values.update(convergence_statistic_from_params(params))
    values.update(sigma_band_from_params(params))
    return values


def starter_form_values() -> dict[str, str]:
    """Return the form's default values, from the CLI's own starter config.

    Returns:
        The same values `params_to_form_values` would compute for
        `fim.cli.STARTER_CONFIG` — the single source of "GUI defaults",
        so a fresh form and `fim init` can never
        drift apart into two documented starting scenarios.
    """
    starter_params = SimulationParams.from_mapping(yaml.safe_load(STARTER_CONFIG))
    return params_to_form_values(starter_params)


# `configuration.md`'s own section order (§3.6: "same key order as
# STARTER_CONFIG"), covering every key `form_values_to_payload` can
# ever produce. Not every payload has every key — `mu` and `mu_b` are
# mutually exclusive (only one is ever present) — so
# `payload_to_yaml_text` filters this down to whichever keys are
# actually present, in this order.
_YAML_KEY_ORDER: Final[tuple[str, ...]] = (
    "N",
    "d",
    "m",
    "mu",
    "mu_b",
    "seed",
    "n_loci",
    "locus_lengths",
    "loci",
    "mutation_model",
    "initial_allele_count",
    "initial_concentration",
    "equilibrium_convergence_window",
    "equilibrium_convergence_tolerance",
    "equilibrium_max_generations",
    "p_0",
    "deme_weighting",
    "convergence_statistic",
    "convergence_combinator",
    "convergence_window",
    "convergence_tolerance",
    "max_generations",
    "n_replicates",
    "replicate_tolerance",
    "replicate_minimum",
    "replicate_confidence",
    "migrant_sampling",
)


def payload_to_yaml_text(payload: Mapping[str, object]) -> str:
    """Serialize a validated payload as an `fim run`/`fim init`-compatible YAML doc.

    Args:
        payload: A payload already accepted by `SimulationParams.from_mapping`
            (typically `form_values_to_payload`'s own return value).

    Returns:
        YAML text in `configuration.md`'s own key order (§3.6) — a
        valid `fim run`/`fim init --force` replacement. Any key not in
        `_YAML_KEY_ORDER` (none exist today; a defensive fallback
        against this list drifting out of sync with a future field) is
        appended afterward rather than silently dropped.
    """
    ordered = {name: payload[name] for name in _YAML_KEY_ORDER if name in payload}
    ordered.update({key: value for key, value in payload.items() if key not in ordered})
    return yaml.safe_dump(ordered, sort_keys=False)


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
