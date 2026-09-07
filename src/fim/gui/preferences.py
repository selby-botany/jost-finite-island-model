"""Versioned, atomic GUI-preferences store (P1 item 4, design doc
`20260907-claude-sonnet-5-gui-preferences-persistence-design.md`).

Persists exactly the things a botanist actually asks a desktop app to
remember between launches — the View menu's display precision, the
Batch tab's worker-count override, the Progress screen's default
deme-pair selection, and the last successfully submitted model-input
form — never a run's own scientific configuration, which already has a
permanent, replayable record in its own `manifest.json`
(`fim.persistence.manifest`). Every field here is optional (`None`
means "no saved preference yet"); a caller merges a loaded
`GuiPreferences` with its own hardcoded defaults rather than this
module carrying a second copy of those defaults (`fim.gui.app`'s own
`_DEFAULT_DISPLAY_SIGNIFICANT_DIGITS` etc. stay the single source of
truth there).

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

# `default_live_deme_pair` is always exactly a (deme, deme) pair.
_DEME_PAIR_LENGTH: Final = 2


@dataclass(frozen=True, slots=True)
class GuiPreferences:
    """One loaded (or default) snapshot of the GUI's own preferences.

    Args:
        significant_digits: The View menu's display-rounding precision,
            or `None` if never saved — `Api.__init__` falls back to its
            own `_DEFAULT_DISPLAY_SIGNIFICANT_DIGITS` in that case.
        max_workers: The Batch tab's worker-count override, or `None`
            to keep using `batch_runner.default_max_workers()`'s
            computed value.
        default_live_deme_pair: The Progress screen's default selector
            state for the *next* run, or `None`. Distinct from `Api.
            _live_deme_pair`, which also starts `None` for a fresh run
            regardless of this value but can then change live, mid-run
            (`Api.set_live_deme_pair`) — that in-flight value is never
            persisted, only this default-for-a-new-run one.
        form_values: The model-input form's last successfully submitted
            values (`Api.start_run`'s own `values: dict[str, str]`
            argument, restricted to `config_form.all_fields()` names),
            or `None` if no run has ever been started. Re-validated
            through `config_form.form_values_to_payload`/
            `SimulationParams.from_mapping` on load, exactly like a
            real submission — this store never carries its own copy of
            that validation.
    """

    significant_digits: int | None = None
    max_workers: int | None = None
    default_live_deme_pair: tuple[int, int] | None = None
    form_values: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the on-disk JSON shape this preference set writes as."""
        gui: dict[str, Any] = {}
        if self.significant_digits is not None:
            gui["significant_digits"] = self.significant_digits
        if self.max_workers is not None:
            gui["max_workers"] = self.max_workers
        if self.default_live_deme_pair is not None:
            gui["default_live_deme_pair"] = list(self.default_live_deme_pair)
        result: dict[str, Any] = {"schema_version": CURRENT_SCHEMA_VERSION, "gui": gui}
        if self.form_values is not None:
            result["form"] = dict(self.form_values)
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
        deme_pair = gui.get("default_live_deme_pair")
        if deme_pair is not None:
            if not (
                isinstance(deme_pair, list) and len(deme_pair) == _DEME_PAIR_LENGTH
            ):
                raise ValueError("'default_live_deme_pair' must be a 2-element list")
            deme_pair = (int(deme_pair[0]), int(deme_pair[1]))
        form_values = data.get("form")
        if form_values is not None:
            if not isinstance(form_values, Mapping) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in form_values.items()
            ):
                raise ValueError("preferences 'form' section must be a str->str object")
            form_values = dict(form_values)
        return GuiPreferences(
            significant_digits=gui.get("significant_digits"),
            max_workers=gui.get("max_workers"),
            default_live_deme_pair=deme_pair,
            form_values=form_values,
        )

    def with_form_values(self, form_values: Mapping[str, str]) -> GuiPreferences:
        """Return a copy with `form_values` replaced — the common `start_run` update."""
        return replace(self, form_values=dict(form_values))


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
