"""Versioned, atomic GUI-preferences store (P1 item 4, design doc
`20260907-claude-sonnet-5-gui-preferences-persistence-design.md`).

Persists exactly the two things the remediation item names as confirmed
gaps — the View menu's display precision and the model-input form's
last successfully submitted values (including the Batch tab's own
`max_workers` field, which is already one of those values;
`start_run`'s own docstring: "the Batch tab's own `max_workers`
field... parsed here directly") — never a run's own scientific
configuration, which already has a permanent, replayable record in its
own `manifest.json` (`fim.persistence.manifest`). Both fields are
optional (`None` means "no saved preference yet"); a caller merges a
loaded `GuiPreferences` with its own hardcoded default rather than this
module carrying a second copy of it (`fim.gui.app`'s own `_DEFAULT_
DISPLAY_SIGNIFICANT_DIGITS` stays the single source of truth there).

A third, optional field — `named_presets` — was added later (botanist
GUI design doc `20260907-claude-sonnet-5-botanist-gui-redesign.md` §12:
"User-saved presets... live in the same preferences store, alongside...
the last-submitted form snapshot"), a user's own named form-value
snapshots, distinct from the built-in worked-example presets `fim.gui.
presets` reads from the bundled usage guide — those are read-only and
ship with the app; these are created, renamed by overwrite, and deleted
entirely by the user, at any time, with no relationship to any run's
own scientific record either. Purely additive to the on-disk shape
(schema_version does not change): an older file with no `"presets"` key
loads exactly as it already did, with `named_presets` simply `None`.

A fourth, optional field — `dark_mode_override` — follows the same §12
precedent again: "Dark-mode override (§11.2), when the user has
explicitly chosen one rather than following the OS, is a new, small
addition to that same preferences file — one more scalar value,
following the exact precedent... `significant_digits`." `None` means
"follow the OS," the app's own default (§11.2: "the app follows the
OS-level light/dark preference by default"), not merely "unset" —
there is no third stored value for "system" distinct from absence, the
same way `significant_digits: None` already means "use the hardcoded
default," not a fourth digit count. Purely additive again: an older
file with no `"dark_mode_override"` key loads exactly as it already
did, with the field simply `None`.

Deliberately excludes a "default deme pair for the next run": `Api.
_start_scalar_run`/`_start_batch_run` reset `_live_deme_pair` to `None`
at the start of every run on purpose ("a fresh run never inherits a
previous run's own live pair selection... never left showing stale
state from whichever screen used it last") — persisting a value that
would then need to override that reset contradicts a documented,
deliberate design choice already in `app.py`, not an oversight this
store should paper over.

The on-disk shape is one small JSON document,
`{"schema_version": 1, "gui": {...}, "form": {...}}`, written with the
same mkstemp-then-`os.replace` atomic idiom `fim.gui.store.
write_progress_sidecar` already uses — this module is that idiom's
second caller, not a second implementation of it. A file this module
cannot parse, or whose `schema_version` it does not recognize, is never
silently ignored or partially trusted: it is renamed aside with a
timestamp (never deleted — always a recoverable copy) and `load_
preferences` returns fresh defaults plus a human-readable warning the
caller is expected to actually show, not just log
(`fim.gui.app.Api.get_startup_warnings`).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

# Bumped whenever `GuiPreferences`'s on-disk shape changes incompatibly.
# A file whose own `schema_version` does not match this is treated
# exactly like a corrupt file (quarantined, defaults returned) rather
# than guessed at — the same policy `fim.persistence.manifest.
# CURRENT_SCHEMA_VERSION` already uses for run manifests.
CURRENT_SCHEMA_VERSION: Final = 1

# Injectable so a test can supply a fixed instant for the quarantine
# filename, matching `fim.paths.default_output_directory`'s own `Clock`
# parameter — a real caller never supplies this.
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class GuiPreferences:
    """One loaded (or default) snapshot of the GUI's own preferences.

    Args:
        significant_digits: The View menu's display-rounding precision,
            or `None` if never saved — `Api.__init__` falls back to its
            own `_DEFAULT_DISPLAY_SIGNIFICANT_DIGITS` in that case.
        form_values: The model-input form's last successfully submitted
            values (`Api.start_run`'s own `values: dict[str, str]`
            argument, which already includes the Batch tab's
            `max_workers` field for a batch run), or `None` if no run
            has ever been started. Re-validated through `config_form.
            form_values_to_payload`/`SimulationParams.from_mapping` on
            load, exactly like a real submission — this store never
            carries its own copy of that validation.
        named_presets: The user's own saved configurations, name ->
            form values (the identical shape `form_values` above uses),
            or `None` if none have ever been saved. Re-validated on load
            exactly like `form_values` — see `with_named_preset`'s own
            docstring for how a name is added or overwritten.
        dark_mode_override: `"light"`, `"dark"`, or `None` to follow the
            OS-level preference (the app's own default) — never a
            stored `"system"` string, since absence already means that.
        welcome_dismissed: Whether the first-launch welcome panel
            (botanist GUI design doc `20260907-claude-sonnet-5-botanist-
            gui-redesign.md` §10) has already been shown and dismissed —
            `False` by default, distinct from `form_values is None`
            ("no run has ever completed"): a user who dismisses the
            panel via "Start from scratch" without ever running anything
            must not see it again on the next launch either.
    """

    significant_digits: int | None = None
    form_values: dict[str, str] | None = None
    named_presets: dict[str, dict[str, str]] | None = None
    dark_mode_override: str | None = None
    welcome_dismissed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return the on-disk JSON shape this preference set writes as."""
        gui: dict[str, Any] = {}
        if self.significant_digits is not None:
            gui["significant_digits"] = self.significant_digits
        if self.dark_mode_override is not None:
            gui["dark_mode_override"] = self.dark_mode_override
        if self.welcome_dismissed:
            gui["welcome_dismissed"] = True
        result: dict[str, Any] = {"schema_version": CURRENT_SCHEMA_VERSION, "gui": gui}
        if self.form_values is not None:
            result["form"] = dict(self.form_values)
        if self.named_presets is not None:
            result["presets"] = {
                name: dict(values) for name, values in self.named_presets.items()
            }
        return result

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> GuiPreferences:
        """Parse the on-disk JSON shape `to_dict` writes.

        Raises:
            ValueError: `data["schema_version"]` is absent or does not
                equal `CURRENT_SCHEMA_VERSION`, or a field has the wrong
                shape — either way, the caller (`load_preferences`)
                treats this identically to malformed JSON: quarantine
                and fall back to defaults, never a partial, guessed-at
                merge of an unrecognized shape.
        """
        found_version = data.get("schema_version")
        if found_version != CURRENT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported preferences schema_version: {found_version!r}"
            )
        gui = data.get("gui", {})
        if not isinstance(gui, Mapping):
            raise ValueError("preferences 'gui' section must be an object")
        dark_mode_override = gui.get("dark_mode_override")
        if dark_mode_override is not None and dark_mode_override not in (
            "light",
            "dark",
        ):
            raise ValueError(
                "preferences 'gui.dark_mode_override' must be 'light' or 'dark'"
            )
        form_values = data.get("form")
        if form_values is not None:
            if not isinstance(form_values, Mapping) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in form_values.items()
            ):
                raise ValueError("preferences 'form' section must be a str->str object")
            form_values = dict(form_values)
        named_presets = data.get("presets")
        if named_presets is not None:
            if not isinstance(named_presets, Mapping) or not all(
                isinstance(name, str)
                and isinstance(values, Mapping)
                and all(
                    isinstance(key, str) and isinstance(value, str)
                    for key, value in values.items()
                )
                for name, values in named_presets.items()
            ):
                raise ValueError(
                    "preferences 'presets' section must be a str->(str->str) object"
                )
            named_presets = {
                name: dict(values) for name, values in named_presets.items()
            }
        return GuiPreferences(
            significant_digits=gui.get("significant_digits"),
            form_values=form_values,
            named_presets=named_presets,
            dark_mode_override=dark_mode_override,
            welcome_dismissed=bool(gui.get("welcome_dismissed", False)),
        )

    def with_form_values(self, form_values: Mapping[str, str]) -> GuiPreferences:
        """Return a copy with `form_values` replaced — the common `start_run` update."""
        return replace(self, form_values=dict(form_values))

    def with_named_preset(
        self, name: str, form_values: Mapping[str, str]
    ) -> GuiPreferences:
        """Return a copy with one named preset added, or overwritten by the same name.

        Args:
            name: The preset's own display name — also its unique key;
                saving under a name that already exists silently
                overwrites it (the same "the newest save wins, no
                separate rename/overwrite prompt" convention a plain
                file save already uses).
            form_values: The current form's own values to remember —
                the identical shape `with_form_values` already takes.
        """
        updated = dict(self.named_presets) if self.named_presets is not None else {}
        updated[name] = dict(form_values)
        return replace(self, named_presets=updated)

    def without_named_preset(self, name: str) -> GuiPreferences:
        """Return a copy with one named preset removed, if it existed.

        A `name` that does not exist is a silent no-op, not an error —
        the GUI's own delete affordance only ever offers a name it just
        listed, so this can only race a preference file edited by hand
        or by a second launch, not a real user-facing mistake worth
        surfacing.
        """
        if self.named_presets is None or name not in self.named_presets:
            return self
        updated = dict(self.named_presets)
        del updated[name]
        return replace(self, named_presets=updated)

    def with_significant_digits(self, significant_digits: int) -> GuiPreferences:
        """Return a copy with `significant_digits` replaced.

        The `set_significant_digits` bridge method's own update.
        """
        return replace(self, significant_digits=significant_digits)

    def with_dark_mode_override(self, dark_mode_override: str | None) -> GuiPreferences:
        """Return a copy with `dark_mode_override` replaced.

        The `set_dark_mode_override` bridge method's own update.
        `None` returns to following the OS-level preference — a real,
        first-class choice (design §11.2 does not require an override to
        stay set forever), not merely "clear an error."
        """
        return replace(self, dark_mode_override=dark_mode_override)

    def with_welcome_dismissed(self) -> GuiPreferences:
        """Return a copy with `welcome_dismissed` set.

        One-directional on purpose — nothing ever needs to show the
        first-launch welcome panel a second time, so there is no
        `without_welcome_dismissed`/parameterized setter the way
        `dark_mode_override` needs one to support returning to "follow
        the OS."
        """
        return replace(self, welcome_dismissed=True)


def load_preferences(path: Path) -> tuple[GuiPreferences, str | None]:
    """Load `path`, quarantining and defaulting on any unreadable content.

    Args:
        path: Usually `preferences_file_path()`'s own return value.

    Returns:
        A `(preferences, warning)` pair. `warning` is `None` on a clean
        load (including a `path` that simply does not exist yet — a
        normal first launch, not a warning-worthy event); otherwise a
        human-readable message naming the quarantined file, meant to
        reach the user via `Api.get_startup_warnings`, not just a log
        line (`fim.gui.app`'s own comment on why `Api.__init__` has no
        inline-error surface to return one through instead).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return GuiPreferences(), None
    try:
        return GuiPreferences.from_dict(json.loads(text)), None
    except (json.JSONDecodeError, ValueError) as error:
        quarantined = _quarantine(path)
        warning = (
            f"could not read saved preferences ({error}); starting from "
            f"defaults. The unreadable file was kept at: {quarantined}"
        )
        logger.warning("preferences: %s", warning)
        return GuiPreferences(), warning


def preferences_file_path(
    *,
    platform: str = sys.platform,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the platform-appropriate `preferences.json` path.

    Args:
        platform: Defaults to `sys.platform`; overridable so a test can
            exercise all three branches on any one host, exactly like
            `fim.paths.default_output_directory`'s injectable `clock`.
        environ: Defaults to `os.environ`; overridable for the same
            reason (`XDG_CONFIG_HOME`, below).
        home: Defaults to `Path.home()`; overridable so a test never
            touches a real home directory.

    Returns:
        `~/Library/Application Support/fim/preferences.json` on macOS,
        `%APPDATA%\\fim\\preferences.json` on Windows (falling back to
        `home / "AppData" / "Roaming"` if `APPDATA` is unset — the same
        defensive fallback `os.environ.get` already needs, since a
        packaged Windows build's own launch environment is not
        guaranteed to set every variable a normal interactive shell
        would), and `$XDG_CONFIG_HOME/fim/preferences.json` (or
        `~/.config/fim/preferences.json` if that variable is unset) on
        Linux and everywhere else. Mirrors `fim.paths.project_root`'s
        own three-case OS split rather than introducing a second,
        differently structured convention for "where does per-platform
        state live" (this module's own top docstring).
    """
    resolved_environ = os.environ if environ is None else environ
    resolved_home = Path.home() if home is None else home
    if platform == "darwin":
        base = resolved_home / "Library" / "Application Support"
    elif platform == "win32":
        appdata = resolved_environ.get("APPDATA")
        base = Path(appdata) if appdata else resolved_home / "AppData" / "Roaming"
    else:
        xdg_config_home = resolved_environ.get("XDG_CONFIG_HOME")
        base = Path(xdg_config_home) if xdg_config_home else resolved_home / ".config"
    return base / "fim" / "preferences.json"


def save_preferences(path: Path, preferences: GuiPreferences) -> None:
    """Atomically write `preferences` to `path`, creating parent directories as needed.

    Same mkstemp-then-`os.replace` idiom as `fim.gui.store.
    write_progress_sidecar` — a concurrent reader always sees either the
    previous complete file or the new one, never a torn write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(preferences.to_dict(), indent=2, sort_keys=True)
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".preferences-")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
            temp_file.write(payload)
        temp_path.replace(path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    logger.debug("wrote preferences: %s", path)


def _quarantine(path: Path, *, clock: Clock = lambda: datetime.now(UTC)) -> Path:
    """Rename an unreadable preferences file aside, timestamped; return the new path.

    Never overwrites an existing quarantined file: a two-part name
    (`.invalid-<timestamp>`) plus microsecond precision makes a
    same-second collision as unlikely in practice as `fim.paths.
    default_output_directory`'s own microsecond-stamped run
    directories, and this path is reached only once per corrupt file
    per process anyway.
    """
    stamp = clock().strftime("%Y%m%dT%H%M%S.%f")
    quarantined = path.with_name(f"{path.stem}.invalid-{stamp}{path.suffix}")
    path.replace(quarantined)
    return quarantined
