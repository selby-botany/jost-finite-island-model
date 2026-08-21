"""Screen 1 — model input (design doc §4.1): a tabbed form that edits
one `SimulationParams` at a time.

Every plain field maps one-to-one to a `fim.gui.config_form` entry, in
the same order `doc/configuration.md` documents those keys, grouped
into tabs the same way that document's own section headings do (design
§3.6, §4.0 #1). All six tabs are built here: **Population**,
**Migration** (the scalar-vs-named-topology `m` selector), **Mutation**
(the `mu`/`mu_b` selector, §4.0 #4), **Initial conditions** (`p_0`'s
read-only summary when loaded, §3.6/§4.0 #3), **Convergence** (the
multi-select `convergence_statistic` plus its combinator, shown only
once two or more are checked), and **Batch** (`replicate_tolerance`/
`replicate_minimum`/`replicate_confidence`, shown only once
`n_replicates` is greater than one). "Run simulation" is disabled
until `fim.model.params.SimulationParams.from_mapping` accepts the
form's current values — a rejected mapping never reaches `on_run`
(design §3.6). A validation failure always names both the offending
tab and the message in the always-visible banner (§4.0 #2: "no
click-through hunting"), flags that tab's own label with a small error
dot, and — when the field also has an inline slot — shows the same
message there too, a convenience for whoever is already on the right
tab (design §4.6, §4.7).
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping
from tkinter import ttk

from fim.gui import config_form
from fim.model.params import SimulationParams

# `n_replicates` values at or below this show only the Batch tab's
# always-visible field; above it, the adaptive-stopping fields appear
# too (§4.1: "shown only once n_replicates is greater than 1").
_SCALAR_REPLICATE_COUNT = 1
# `convergence_statistic` values at or below this hide the combinator
# (§4.1: "shown only once two or more are selected") — meaningless
# with fewer than two statistics to combine.
_SINGLE_STATISTIC_COUNT = 1


class InputScreen(ttk.Frame):
    """Screen 1: build and validate a `SimulationParams` from a tabbed form."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_run: Callable[[SimulationParams], None] | None = None,
    ) -> None:
        """Build every tab, the error banner, and the action buttons.

        Args:
            parent: The Tk container this screen is gridded into.
            on_run: Called with the validated params when "Run
                simulation" is clicked. Defaults to a no-op — Milestone
                G2 gives this a real orchestrator to call; this screen
                only ever hands it an already-validated
                `SimulationParams`.
        """
        super().__init__(parent)
        self._on_run = on_run if on_run is not None else (lambda _params: None)
        self._vars: dict[str, tk.StringVar] = {}
        self._field_errors: dict[str, ttk.Label] = {}
        self._valid_params: SimulationParams | None = None
        self._cs_vars: dict[str, tk.StringVar] = {}
        self._tab_frames: dict[str, ttk.Frame] = {}
        self._tab_labels: dict[str, str] = {}

        self._banner = ttk.Label(self, foreground="red", wraplength=480)
        self._banner.pack(fill="x", padx=4, pady=(4, 8))

        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="both", expand=True, padx=4)
        builders: dict[str, Callable[[ttk.Frame, config_form.TabSpec], None]] = {
            "migration": self._build_migration_tab,
            "mutation": self._build_mutation_tab,
            "initial_conditions": self._build_initial_conditions_tab,
            "convergence": self._build_convergence_tab,
            "batch": self._build_batch_tab,
        }
        for tab in config_form.TABS:
            frame = ttk.Frame(self._notebook)
            self._notebook.add(frame, text=tab.label)
            self._tab_frames[tab.name] = frame
            self._tab_labels[tab.name] = tab.label
            builders.get(tab.name, self._build_plain_tab)(frame, tab)

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", pady=(12, 4), padx=4)
        ttk.Button(
            buttons, text="Reset to defaults", command=self._on_reset_to_defaults
        ).pack(side="left")
        self._run_button = ttk.Button(
            buttons, text="Run simulation ▶", command=self._on_run_clicked
        )
        self._run_button.pack(side="right")

        # Prefill from the CLI's own starter config (design §3.6's
        # "Prefill") — also the first `_revalidate()`, which needs
        # `self._run_button` to already exist, hence this call sits last.
        self.set_values(config_form.starter_form_values())

    def get_values(self) -> dict[str, str]:
        """Return the form's current values, one string per exposed field."""
        values = {name: variable.get() for name, variable in self._vars.items()}
        values.update({f"cs_{name}": var.get() for name, var in self._cs_vars.items()})
        return values

    def set_values(self, values: Mapping[str, str]) -> None:
        """Replace every field's text and revalidate.

        Args:
            values: One string per exposed field, such as
                `config_form.starter_form_values()` or
                `config_form.params_to_form_values(...)` returns.
        """
        for name, variable in self._vars.items():
            variable.set(values[name])
        for name, variable in self._cs_vars.items():
            variable.set(values[f"cs_{name}"])
        self._revalidate()

    def _build_batch_tab(self, frame: ttk.Frame, tab: config_form.TabSpec) -> None:
        """Build the Batch tab: `n_replicates`, plus fields shown only once it's > 1."""
        fields = {field.name: field for field in tab.fields}
        self._build_field_row(frame, 0, fields["n_replicates"])
        n_replicates_variable = self._vars["n_replicates"]
        n_replicates_variable.trace_add(
            "write", lambda *_args: self._on_batch_field_changed()
        )

        self._batch_extra_rows = ttk.Frame(frame)
        self._batch_extra_rows.grid(row=1, column=0, columnspan=3, sticky="w")
        for row, name in enumerate(
            ("replicate_tolerance", "replicate_minimum", "replicate_confidence")
        ):
            self._build_field_row(self._batch_extra_rows, row, fields[name])

        self._update_batch_visibility()

    def _build_convergence_tab(
        self, frame: ttk.Frame, tab: config_form.TabSpec
    ) -> None:
        """Build the Convergence tab: the statistic multi-select, then plain fields."""
        checkbox_row = ttk.Frame(frame)
        checkbox_row.grid(row=0, column=0, columnspan=3, sticky="w", padx=4, pady=1)
        ttk.Label(checkbox_row, text="convergence statistic").pack(side="left")
        for name in config_form.CONVERGENCE_STATISTIC_NAMES:
            variable = tk.StringVar(self, value="false")
            variable.trace_add(
                "write", lambda *_args: self._on_convergence_statistic_changed()
            )
            self._cs_vars[name] = variable
            ttk.Checkbutton(
                checkbox_row,
                text=name,
                variable=variable,
                onvalue="true",
                offvalue="false",
            ).pack(side="left", padx=2)
        self._field_errors["convergence_statistic"] = ttk.Label(frame, foreground="red")
        self._field_errors["convergence_statistic"].grid(
            row=0, column=3, sticky="w", padx=4
        )

        fields = {field.name: field for field in tab.fields}
        self._combinator_row = ttk.Frame(frame)
        self._combinator_row.grid(row=1, column=0, columnspan=3, sticky="w")
        self._build_field_row(self._combinator_row, 0, fields["convergence_combinator"])

        self._build_field_row(frame, 2, fields["convergence_window"])
        self._build_field_row(frame, 3, fields["convergence_tolerance"])

        self._update_convergence_combinator_visibility()

    def _build_field_row(
        self, parent: ttk.Frame, row: int, field: config_form.FormField
    ) -> None:
        """Build one labeled field, its input widget, and its inline error slot."""
        variable = tk.StringVar(self)
        variable.trace_add("write", lambda *_args: self._revalidate())
        self._vars[field.name] = variable

        ttk.Label(parent, text=field.label).grid(
            row=row, column=0, sticky="w", padx=4, pady=1
        )
        widget: ttk.Entry | ttk.Combobox
        if field.kind in ("choice", "float_choice"):
            widget = ttk.Combobox(
                parent,
                textvariable=variable,
                values=field.choices,
                state="readonly",
                width=18,
            )
        else:
            widget = ttk.Entry(parent, textvariable=variable, width=20)
        widget.grid(row=row, column=1, sticky="w", padx=4, pady=1)

        error_label = ttk.Label(parent, foreground="red")
        error_label.grid(row=row, column=2, sticky="w", padx=4, pady=1)
        self._field_errors[field.name] = error_label

    def _build_initial_conditions_tab(
        self, frame: ttk.Frame, tab: config_form.TabSpec
    ) -> None:
        """Build Initial conditions: plain fields, then `p_0`'s read-only summary."""
        for row, field in enumerate(tab.fields):
            self._build_field_row(frame, row, field)

        row = len(tab.fields)
        summary_variable = tk.StringVar(self)
        summary_variable.trace_add("write", lambda *_args: self._revalidate())
        self._vars["p0_summary"] = summary_variable
        ttk.Label(frame, text="p_0").grid(row=row, column=0, sticky="w", padx=4, pady=1)
        ttk.Label(frame, textvariable=summary_variable, foreground="gray").grid(
            row=row, column=1, columnspan=2, sticky="w", padx=4, pady=1
        )

    def _build_migration_selector(self, parent: ttk.Frame, start_row: int) -> None:
        """Build the `m` scalar/named-topology/loaded-from-file selector.

        Exactly one of the three sub-rows is ever visible at a time —
        the exclusivity is the widget's own shape, not a rule the user
        discovers only after submitting more than one (§4.0 #4's
        principle, applied here to `m`). "Loaded from file" (§3.6,
        §4.0 #3) is never radio-selectable — it only ever appears
        because `set_values` loaded a matrix-shaped `m`; picking either
        radio replaces it with a fresh scalar or topology rate.
        """
        mode_variable = tk.StringVar(self, value="scalar")
        mode_variable.trace_add("write", lambda *_args: self._on_m_mode_changed())
        self._vars["m_mode"] = mode_variable

        row = start_row
        ttk.Label(parent, text="m (migration)").grid(
            row=row, column=0, sticky="w", padx=4, pady=1
        )
        mode_buttons = ttk.Frame(parent)
        mode_buttons.grid(row=row, column=1, columnspan=2, sticky="w", padx=4)
        ttk.Radiobutton(
            mode_buttons, text="Scalar rate", variable=mode_variable, value="scalar"
        ).pack(side="left")
        ttk.Radiobutton(
            mode_buttons,
            text="Named topology",
            variable=mode_variable,
            value="topology",
        ).pack(side="left")
        row += 1

        rate_variable = tk.StringVar(self)
        rate_variable.trace_add("write", lambda *_args: self._revalidate())
        self._vars["m_rate"] = rate_variable
        self._m_scalar_row = ttk.Frame(parent)
        self._m_scalar_row.grid(row=row, column=0, columnspan=3, sticky="w")
        ttk.Label(self._m_scalar_row, text="rate").grid(row=0, column=0, padx=4)
        ttk.Entry(self._m_scalar_row, textvariable=rate_variable, width=20).grid(
            row=0, column=1, padx=4
        )
        self._field_errors["m"] = ttk.Label(self._m_scalar_row, foreground="red")
        self._field_errors["m"].grid(row=0, column=2, padx=4)

        default_topology = config_form.MIGRATION_TOPOLOGIES[0]
        topology_variable = tk.StringVar(self, value=default_topology)
        topology_variable.trace_add("write", lambda *_args: self._revalidate())
        self._vars["m_topology"] = topology_variable
        topology_rate_variable = tk.StringVar(self)
        topology_rate_variable.trace_add("write", lambda *_args: self._revalidate())
        self._vars["m_topology_rate"] = topology_rate_variable
        self._m_topology_row = ttk.Frame(parent)
        self._m_topology_row.grid(row=row, column=0, columnspan=3, sticky="w")
        ttk.Label(self._m_topology_row, text="topology").grid(row=0, column=0, padx=4)
        ttk.Combobox(
            self._m_topology_row,
            textvariable=topology_variable,
            values=config_form.MIGRATION_TOPOLOGIES,
            state="readonly",
            width=10,
        ).grid(row=0, column=1, padx=4)
        ttk.Label(self._m_topology_row, text="rate").grid(row=0, column=2, padx=4)
        ttk.Entry(
            self._m_topology_row, textvariable=topology_rate_variable, width=10
        ).grid(row=0, column=3, padx=4)

        loaded_summary_variable = tk.StringVar(self)
        loaded_summary_variable.trace_add("write", lambda *_args: self._revalidate())
        self._vars["m_loaded_summary"] = loaded_summary_variable
        self._m_loaded_row = ttk.Frame(parent)
        self._m_loaded_row.grid(row=row, column=0, columnspan=3, sticky="w")
        ttk.Label(
            self._m_loaded_row, textvariable=loaded_summary_variable, foreground="gray"
        ).grid(row=0, column=0, padx=4)

        # Only the visibility toggle, not a full `_revalidate()` — this
        # runs during `__init__`, before `self._run_button` exists;
        # `_on_m_mode_changed` (wired to the trace above, so only ever
        # reached once construction has finished) is what revalidates.
        self._update_m_visibility()

    def _build_migration_tab(self, frame: ttk.Frame, tab: config_form.TabSpec) -> None:
        """Build the Migration tab: its plain fields, then the `m` selector."""
        for row, field in enumerate(tab.fields):
            self._build_field_row(frame, row, field)
        self._build_migration_selector(frame, len(tab.fields))

    def _build_mutation_selector(self, parent: ttk.Frame, start_row: int) -> None:
        """Build the `mu`/`mu_b` mutually-exclusive selector (§4.0 #4, §4.1)."""
        mode_variable = tk.StringVar(self, value="mu")
        mode_variable.trace_add("write", lambda *_args: self._on_mu_mode_changed())
        self._vars["mu_mode"] = mode_variable

        row = start_row
        ttk.Label(parent, text="mu / mu_b").grid(
            row=row, column=0, sticky="w", padx=4, pady=1
        )
        mode_buttons = ttk.Frame(parent)
        mode_buttons.grid(row=row, column=1, columnspan=2, sticky="w", padx=4)
        ttk.Radiobutton(
            mode_buttons, text="mu (per-copy)", variable=mode_variable, value="mu"
        ).pack(side="left")
        ttk.Radiobutton(
            mode_buttons, text="mu_b (per-base)", variable=mode_variable, value="mu_b"
        ).pack(side="left")
        row += 1

        mu_variable = tk.StringVar(self)
        mu_variable.trace_add("write", lambda *_args: self._revalidate())
        self._vars["mu_value"] = mu_variable
        self._mu_row = ttk.Frame(parent)
        self._mu_row.grid(row=row, column=0, columnspan=3, sticky="w")
        ttk.Label(self._mu_row, text="mu").grid(row=0, column=0, padx=4)
        ttk.Entry(self._mu_row, textvariable=mu_variable, width=20).grid(
            row=0, column=1, padx=4
        )
        self._field_errors["mu"] = ttk.Label(self._mu_row, foreground="red")
        self._field_errors["mu"].grid(row=0, column=2, padx=4)

        mu_b_variable = tk.StringVar(self)
        mu_b_variable.trace_add("write", lambda *_args: self._revalidate())
        self._vars["mu_b_value"] = mu_b_variable
        self._mu_b_row = ttk.Frame(parent)
        self._mu_b_row.grid(row=row, column=0, columnspan=3, sticky="w")
        ttk.Label(self._mu_b_row, text="mu_b").grid(row=0, column=0, padx=4)
        ttk.Entry(self._mu_b_row, textvariable=mu_b_variable, width=20).grid(
            row=0, column=1, padx=4
        )
        self._field_errors["mu_b"] = ttk.Label(self._mu_b_row, foreground="red")
        self._field_errors["mu_b"].grid(row=0, column=2, padx=4)

        self._update_mu_visibility()

    def _build_mutation_tab(self, frame: ttk.Frame, tab: config_form.TabSpec) -> None:
        """Build the Mutation tab: its plain fields, then the `mu`/`mu_b` selector."""
        for row, field in enumerate(tab.fields):
            self._build_field_row(frame, row, field)
        self._build_mutation_selector(frame, len(tab.fields))

    def _build_plain_tab(self, frame: ttk.Frame, tab: config_form.TabSpec) -> None:
        """Build a tab with nothing but plain fields, in `TabSpec.fields` order."""
        for row, field in enumerate(tab.fields):
            self._build_field_row(frame, row, field)

    def _clear_errors(self) -> None:
        """Blank the banner, every inline field error, and every tab's error dot."""
        self._banner["text"] = ""
        for label in self._field_errors.values():
            label["text"] = ""
        for name, frame in self._tab_frames.items():
            self._notebook.tab(frame, text=self._tab_labels[name])  # type: ignore[no-untyped-call]

    def _flag_tab(self, tab_name: str) -> None:
        """Mark one tab's label with a small error dot (§4.0 #2, §4.1 mockup).

        `_clear_errors` already blanked every tab back to its plain
        label this revalidation pass, so at most one tab is ever
        flagged at a time — the one holding whichever single field
        `SimulationParams.from_mapping` rejected first (§4.0 #2's own
        text describes "every tab with an invalid field," but only one
        field's error is ever known at once; see
        `fim.gui.config_form.tab_for_error`).
        """
        frame = self._tab_frames[tab_name]
        self._notebook.tab(  # type: ignore[no-untyped-call]
            frame, text=f"{self._tab_labels[tab_name]} \N{WARNING SIGN}"
        )

    def _on_batch_field_changed(self) -> None:
        """React to `n_replicates` changing: update visibility, then revalidate."""
        self._update_batch_visibility()
        self._revalidate()

    def _on_convergence_statistic_changed(self) -> None:
        """React to a checked statistic changing: update visibility, then revalidate."""
        self._update_convergence_combinator_visibility()
        self._revalidate()

    def _on_m_mode_changed(self) -> None:
        """React to the user changing `m`'s mode: update visibility, then revalidate."""
        self._update_m_visibility()
        self._revalidate()

    def _on_mu_mode_changed(self) -> None:
        """React to the user changing `mu`'s mode: update, then revalidate."""
        self._update_mu_visibility()
        self._revalidate()

    def _on_reset_to_defaults(self) -> None:
        """Restore the CLI's starter configuration (design §3.6's "Prefill")."""
        self.set_values(config_form.starter_form_values())

    def _on_run_clicked(self) -> None:
        """Invoke `on_run` with the last-validated params, if any.

        "Run simulation" is disabled whenever `_valid_params` is `None`
        (see `_revalidate`), so this only ever fires with a params object
        `SimulationParams.from_mapping` already accepted.
        """
        if self._valid_params is not None:
            self._on_run(self._valid_params)

    def _revalidate(self) -> None:
        """Re-run validation against the form's current values.

        Builds a payload via `config_form.form_values_to_payload` and
        validates it with `SimulationParams.from_mapping` — the same
        validator `fim.cli` uses (design §3.6). On success, enables "Run
        simulation" and clears every error and every tab's error dot.
        On failure, disables it; the banner (always visible, regardless
        of which tab is selected) always names the failure — prefixed
        with the offending tab's label when `config_form.tab_for_error`
        can place one, so there is no "click-through hunting" for which
        tab to check (§4.0 #2, §4.7) — and, when the field also has an
        inline slot on its own tab, the same message is shown there too
        as a convenience for whoever is already looking at it. The
        holding tab's own label also gets a small error dot
        (`_flag_tab`).
        """
        self._clear_errors()
        try:
            payload = config_form.form_values_to_payload(self.get_values())
            self._valid_params = SimulationParams.from_mapping(payload)
        except ValueError as error:
            self._valid_params = None
            self._run_button.state(["disabled"])
            message = str(error)
            field_name = config_form.field_for_error(message)
            if field_name is not None and field_name in self._field_errors:
                self._field_errors[field_name]["text"] = message
            tab_name = config_form.tab_for_error(message)
            if tab_name is not None:
                self._flag_tab(tab_name)
                self._banner["text"] = f"{self._tab_labels[tab_name]}: {message}"
            else:
                self._banner["text"] = message
            return
        self._run_button.state(["!disabled"])

    def _update_batch_visibility(self) -> None:
        """Show the adaptive-batch fields only once `n_replicates` is greater than 1."""
        try:
            n_replicates = int(self._vars["n_replicates"].get())
        except ValueError:
            n_replicates = _SCALAR_REPLICATE_COUNT
        if n_replicates > _SCALAR_REPLICATE_COUNT:
            self._batch_extra_rows.grid()
        else:
            self._batch_extra_rows.grid_remove()

    def _update_convergence_combinator_visibility(self) -> None:
        """Show `convergence_combinator` once two or more statistics are checked."""
        checked = sum(1 for var in self._cs_vars.values() if var.get() == "true")
        if checked > _SINGLE_STATISTIC_COUNT:
            self._combinator_row.grid()
        else:
            self._combinator_row.grid_remove()

    def _update_m_visibility(self) -> None:
        """Show exactly the sub-row matching the selected `m` mode."""
        self._m_scalar_row.grid_remove()
        self._m_topology_row.grid_remove()
        self._m_loaded_row.grid_remove()
        mode = self._vars["m_mode"].get()
        if mode == "scalar":
            self._m_scalar_row.grid()
        elif mode == "topology":
            self._m_topology_row.grid()
        else:
            self._m_loaded_row.grid()

    def _update_mu_visibility(self) -> None:
        """Show exactly the sub-row matching the selected `mu` mode."""
        if self._vars["mu_mode"].get() == "mu":
            self._mu_b_row.grid_remove()
            self._mu_row.grid()
        else:
            self._mu_row.grid_remove()
            self._mu_b_row.grid()
