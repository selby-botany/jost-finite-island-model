"""Unit tests for `fim.gui.preferences`.

Pure filesystem tests against `tmp_path` — no GUI window, no `Api`, no
real home directory ever touched (`preferences_file_path`'s own
`home`/`environ`/`platform` overrides exist specifically so these tests
never need one).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fim.gui import preferences as preferences_module
from fim.gui.preferences import (
    CURRENT_SCHEMA_VERSION,
    GuiPreferences,
    load_preferences,
    preferences_file_path,
    save_preferences,
)


def test_round_trip_preserves_every_field(tmp_path: Path) -> None:
    """Saving then loading returns an equal `GuiPreferences`."""
    path = tmp_path / "preferences.json"
    original = GuiPreferences(
        significant_digits=5,
        form_values={"N": "500", "m": "0.01"},
    )
    save_preferences(path, original)
    loaded, warning = load_preferences(path)
    assert warning is None
    assert loaded == original


def test_missing_file_returns_defaults_with_no_warning(tmp_path: Path) -> None:
    """A first launch (no preferences file yet) is not a warning-worthy event."""
    loaded, warning = load_preferences(tmp_path / "does-not-exist.json")
    assert loaded == GuiPreferences()
    assert warning is None


def test_malformed_json_is_quarantined_and_defaults_returned(tmp_path: Path) -> None:
    """Unreadable JSON is renamed aside, never deleted, and never trusted."""
    path = tmp_path / "preferences.json"
    path.write_text("{not valid json", encoding="utf-8")
    loaded, warning = load_preferences(path)
    assert loaded == GuiPreferences()
    assert warning is not None
    assert not path.exists()
    quarantined = list(tmp_path.glob("preferences.invalid-*.json"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "{not valid json"
    assert str(quarantined[0]) in warning


def test_unrecognized_schema_version_is_quarantined(tmp_path: Path) -> None:
    """A schema_version this build does not recognize is treated as corrupt.

    Never guessed at or partially merged — the same policy `fim.
    persistence.manifest` already applies to run manifests.
    """
    path = tmp_path / "preferences.json"
    path.write_text(
        json.dumps({"schema_version": CURRENT_SCHEMA_VERSION + 1, "gui": {}}),
        encoding="utf-8",
    )
    loaded, warning = load_preferences(path)
    assert loaded == GuiPreferences()
    assert warning is not None
    assert not path.exists()


def test_malformed_form_section_is_quarantined(tmp_path: Path) -> None:
    """A non-string-map 'form' section is rejected, not silently coerced."""
    path = tmp_path / "preferences.json"
    path.write_text(
        json.dumps(
            {"schema_version": CURRENT_SCHEMA_VERSION, "gui": {}, "form": {"N": 500}}
        ),
        encoding="utf-8",
    )
    loaded, warning = load_preferences(path)
    assert loaded == GuiPreferences()
    assert warning is not None


def test_quarantine_never_overwrites_a_second_corrupt_file(tmp_path: Path) -> None:
    """Two corrupt files in a row each get their own, distinct quarantined name."""
    path = tmp_path / "preferences.json"
    path.write_text("{broken one", encoding="utf-8")
    load_preferences(path)
    path.write_text("{broken two", encoding="utf-8")
    load_preferences(path)
    quarantined = sorted(tmp_path.glob("preferences.invalid-*.json"))
    assert len(quarantined) == 2
    assert {file.read_text(encoding="utf-8") for file in quarantined} == {
        "{broken one",
        "{broken two",
    }


def test_save_creates_parent_directories(tmp_path: Path) -> None:
    """A first-ever save works even though `fim/` doesn't exist yet."""
    path = tmp_path / "fim" / "preferences.json"
    save_preferences(path, GuiPreferences(significant_digits=2))
    assert path.exists()


def test_save_is_atomic_no_temp_file_left_behind(tmp_path: Path) -> None:
    """A successful save leaves only the target file, no `.preferences-*` leftovers."""
    path = tmp_path / "preferences.json"
    save_preferences(path, GuiPreferences())
    assert [entry.name for entry in tmp_path.iterdir()] == ["preferences.json"]


def test_to_dict_omits_unset_fields() -> None:
    """A field never saved is simply absent, not a literal JSON `null` for every one."""
    data = GuiPreferences(significant_digits=4).to_dict()
    assert data["gui"] == {"significant_digits": 4}
    assert "form" not in data


def test_with_form_values_leaves_other_fields_untouched() -> None:
    """`with_form_values` updates only `form_values`."""
    original = GuiPreferences(significant_digits=7)
    updated = original.with_form_values({"N": "100"})
    assert updated.significant_digits == 7
    assert updated.form_values == {"N": "100"}


def test_preferences_file_path_macos(tmp_path: Path) -> None:
    """macOS resolves under `~/Library/Application Support/fim`."""
    path = preferences_file_path(platform="darwin", environ={}, home=tmp_path)
    assert (
        path
        == tmp_path / "Library" / "Application Support" / "fim" / "preferences.json"
    )


def test_preferences_file_path_windows_with_appdata(tmp_path: Path) -> None:
    """Windows resolves under `%APPDATA%\\fim` when `APPDATA` is set."""
    appdata = tmp_path / "Roaming"
    path = preferences_file_path(
        platform="win32", environ={"APPDATA": str(appdata)}, home=tmp_path
    )
    assert path == appdata / "fim" / "preferences.json"


def test_preferences_file_path_windows_without_appdata_falls_back(
    tmp_path: Path,
) -> None:
    """A packaged Windows launch missing `APPDATA` still resolves somewhere writable."""
    path = preferences_file_path(platform="win32", environ={}, home=tmp_path)
    assert path == tmp_path / "AppData" / "Roaming" / "fim" / "preferences.json"


def test_preferences_file_path_linux_with_xdg_config_home(tmp_path: Path) -> None:
    """Linux honors `XDG_CONFIG_HOME` when set."""
    xdg = tmp_path / "xdg-config"
    path = preferences_file_path(
        platform="linux", environ={"XDG_CONFIG_HOME": str(xdg)}, home=tmp_path
    )
    assert path == xdg / "fim" / "preferences.json"


def test_preferences_file_path_linux_without_xdg_falls_back(tmp_path: Path) -> None:
    """Linux falls back to `~/.config` when `XDG_CONFIG_HOME` is unset."""
    path = preferences_file_path(platform="linux", environ={}, home=tmp_path)
    assert path == tmp_path / ".config" / "fim" / "preferences.json"


def test_quarantine_injected_clock_produces_exact_name(tmp_path: Path) -> None:
    """An injected clock gives `_quarantine` a deterministic filename.

    `_quarantine` is private, but its effect is fully observable through
    `load_preferences`'s own public contract — this test only pins down
    the exact timestamp format via a fixed instant, matching `fim.
    paths.default_output_directory`'s own injected-clock test pattern.
    """
    path = tmp_path / "preferences.json"
    path.write_text("not json", encoding="utf-8")
    fixed = datetime(2026, 9, 7, 12, 34, 56, 789012, tzinfo=UTC)
    quarantined = preferences_module._quarantine(path, clock=lambda: fixed)
    assert quarantined.name == "preferences.invalid-20260907T123456.789012.json"
