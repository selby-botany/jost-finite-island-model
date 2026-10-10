"""Tests of the presentation metadata of the Expert Settings."""

from __future__ import annotations

from dataclasses import fields

from fim.config.expert import (
    EXPERT_GROUPS,
    EXPERT_SETTING_INFO,
    ExpertSettings,
    describe_expert_settings,
)


def test_every_expert_setting_has_presentation_and_nothing_else_does() -> None:
    """The info table and the settings can only change together."""
    assert set(EXPERT_SETTING_INFO) == {field.name for field in fields(ExpertSettings)}
    assert {info.group for info in EXPERT_SETTING_INFO.values()} <= set(EXPERT_GROUPS)
    for name, info in EXPERT_SETTING_INFO.items():
        assert info.label and info.help, name
        assert not info.help.endswith("  "), name


def test_the_description_lists_every_setting_grouped_with_default_and_range() -> None:
    """One entry per setting, grouped in display order, with the real default."""
    described = describe_expert_settings()
    defaults = ExpertSettings()

    first = next(d for d in described if d["group"] == "Convergence")
    assert first["name"] == "burn_in_minimum_relaxation_times"
    assert {d["name"] for d in described} == set(EXPERT_SETTING_INFO)
    groups = [d["group"] for d in described]
    assert groups == sorted(groups, key=EXPERT_GROUPS.index)
    for entry in described:
        assert entry["default"] == str(getattr(defaults, entry["name"]))
        assert entry["range"].startswith(("at least", "greater than", "whole number"))
    by_name = {d["name"]: d for d in described}
    assert by_name["batch_width"]["range"] == "whole number, at least 1"
    assert by_name["check_growth"]["range"] == "greater than 1"
