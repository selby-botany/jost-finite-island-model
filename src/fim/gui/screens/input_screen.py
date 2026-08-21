"""Screen 1 — model input (design doc §4.1): a tabbed form that edits
one `SimulationParams` at a time.

Every plain field maps one-to-one to a `fim.gui.config_form` entry, in
the same order `doc/configuration.md` documents those keys, grouped
into tabs the same way that document's own section headings do (design
§3.6, §4.0 #1). This commit builds the **Population** and
**Migration** tabs — including Migration's scalar-vs-named-topology
`m` selector (§4.0 #4's exclusivity-as-shape principle, applied to `m`
rather than `mu`/`mu_b`, which arrives in the next commit). "Run
simulation" is disabled until `fim.model.params.SimulationParams.
from_mapping` accepts the form's current values — a rejected mapping
never reaches `on_run` (design §3.6). A validation failure is shown
beside the field `fim.gui.config_form.field_for_error` names, or in a
banner otherwise (design §4.6); routing the banner-shown failure to
the tab that actually holds the field is a later commit in this
milestone (§4.0 #2, §7.3's fifth bullet) — until then, every field
error is at least visible on whichever tab happens to already be
selected, or in the banner.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping
from tkinter import ttk

from fim.gui import config_form
from fim.model.params import SimulationParams


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

        self._banner = ttk.Label(self, foreground="red", wraplength=480)
        self._banner.pack(fill="x", padx=4, pady=(4, 8))

        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="both", expand=True, padx=4)
        for tab in config_form.TABS:
            frame = ttk.Frame(self._notebook)
            self._notebook.add(frame, text=tab.label)
            for row, field in enumerate(tab.fields):
                self._build_field_row(frame, row, field)
            if tab.name == "migration":
                self._build_migration_selector(frame, len(tab.fields))

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
        return {name: variable.get() for name, variable in self._vars.items()}

    def set_values(self, values: Mapping[str, str]) -> None:
        """Replace every field's text and revalidate.

        Args:
            values: One string per exposed field, such as
                `config_form.starter_form_values()` or
                `config_form.params_to_form_values(...)` returns.
        """
        for name, variable in self._vars.items():
            variable.set(values[name])
        self._revalidate()

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
        if field.kind == "choice":
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

    def _build_migration_selector(self, parent: ttk.Frame, start_row: int) -> None:
        """Build the `m` scalar-vs-named-topology radio selector (§4.0 #4, §4.1).

        Exactly one of the "scalar rate" and "named topology" sub-rows
        is ever visible at a time — the exclusivity is the widget's own
        shape, not a rule the user discovers only after submitting both.
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

        # Only the visibility toggle, not a full `_revalidate()` — this
        # runs during `__init__`, before `self._run_button` exists;
        # `_on_m_mode_changed` (wired to the trace above, so only ever
        # reached once construction has finished) is what revalidates.
        self._update_m_visibility()

    def _clear_errors(self) -> None:
        """Blank the banner and every inline field error label."""
        self._banner["text"] = ""
        for label in self._field_errors.values():
            label["text"] = ""

    def _on_m_mode_changed(self) -> None:
        """React to the user changing `m`'s mode: update visibility, then revalidate."""
        self._update_m_visibility()
        self._revalidate()

    def _update_m_visibility(self) -> None:
        """Show exactly the sub-row matching the selected `m` mode."""
        if self._vars["m_mode"].get() == "scalar":
            self._m_topology_row.grid_remove()
            self._m_scalar_row.grid()
        else:
            self._m_scalar_row.grid_remove()
            self._m_topology_row.grid()

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
        simulation" and clears every error. On failure, disables it and
        shows the message beside the field `config_form.field_for_error`
        names, or in the banner when it names none (design §4.6).
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
            else:
                self._banner["text"] = message
            return
        self._run_button.state(["!disabled"])
