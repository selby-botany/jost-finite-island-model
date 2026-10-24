"""Scientific presentation stays separate from saved statistic identifiers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import webview

pytestmark = pytest.mark.gui

_READY = "window.__fimRunViewReady === true"


def test_catalog_symbols_and_untrusted_text(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Every catalog alias renders its symbol, without interpreting input HTML."""
    result = drive(
        window,
        ready=_READY,
        trigger="null",
        read="""(() => {
            const render = (text) => {
                const node = document.createElement("div");
                node.append(scientificText(text));
                return node;
            };
            return {
                catalog: STATISTIC_CATALOG.map((spec) => ({
                    key: spec.key,
                    expected: spec.label_html,
                    actual: render(spec.key).firstChild.outerHTML,
                    alias: render(spec.label_text).firstChild.outerHTML,
                    composed: render(plainStatisticLabel(spec.key))
                        .firstChild.outerHTML,
                })),
                safe: render("<img src=x onerror=alert(1)> H_ST").innerHTML,
                untouched: render("my_G_ST_field G_ST_NEVER").textContent,
                effective: render("HD_S and HD_T").innerHTML,
            };
        })()""",
    )
    for entry in result["catalog"]:
        expected = f"<span>{entry['expected']}</span>"
        assert entry["actual"] == expected, entry["key"]
        assert entry["alias"] == expected, entry["key"]
        assert entry["composed"] == expected, entry["key"]
    assert "<img" not in result["safe"]
    assert "&lt;img" in result["safe"]
    assert "H<sub>ST</sub>" in result["safe"]
    assert result["untouched"] == "my_G_ST_field G_ST_NEVER"
    assert "<sup>H</sup>D<sub>S</sub>" in result["effective"]
    assert "<sup>H</sup>D<sub>T</sub>" in result["effective"]


def test_messages_render_symbols_including_collapsed_details(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Batch sentences, cautions and details render math at their DOM boundary."""
    result = drive(
        window,
        ready=_READY,
        trigger="""(() => {
            const interval = {mean: "0.2", low: "0.19", high: "0.21", sampleCount: 10};
            const messages = batchMessagesFor({
                replicates: 10, cap: 200, minimum: 10, precision: 0.02,
                confidence: 0.95, method: "interval", stopEarly: true,
                statistics: ["G_ST"],
            }, {G_ST: interval, H_ST: interval});
            setBaseRunMessages(messages);
            renderRunMessages([{severity: "warning", text: G_ST_CAUTION_TEXT}]);
        })();""",
        read="""({
            primary: document.querySelector("#run-messages > li").innerHTML,
            details: document.querySelector(".run-message-detail-list").innerHTML,
            warning: document.querySelector(".run-message-warning").innerHTML,
            raw: document.getElementById("run-messages").textContent,
        })""",
    )
    assert "G<sub>ST</sub>" in result["primary"]
    assert "H<sub>ST</sub>" in result["details"]
    assert "G<sub>ST</sub>" in result["warning"]
    assert "G_ST" not in result["raw"]
    assert "H_ST" not in result["raw"]


def test_statistic_tooltips_use_math_and_restore_titles(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Mouse and keyboard tooltips render scripts; Escape removes the bubble."""
    result = drive(
        window,
        ready=_READY,
        trigger="null",
        read="""(() => {
            const row = document.getElementById("stat-H_ST");
            applyStatRow(row, buildPointMeter("H_ST", "0.123456"));
            const link = row.querySelector("button");
            link.setAttribute("aria-describedby", "existing-description");
            const title = link.title;
            link.dispatchEvent(new MouseEvent("mouseover", {bubbles: true}));
            const tooltip = document.querySelector(".field-tooltip");
            const hover = tooltip.innerHTML;
            const suppressed = link.hasAttribute("title") && link.title === "";
            link.dispatchEvent(new MouseEvent("mouseout", {bubbles: true}));
            const restored = link.title === title && tooltip.hidden &&
                link.getAttribute("aria-describedby") === "existing-description";
            link.dispatchEvent(new FocusEvent("focusin", {bubbles: true}));
            const keyboard = tooltip.innerHTML;
            document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape"}));
            row.dispatchEvent(new MouseEvent("mouseover", {bubbles: true}));
            applyStatRow(row, buildPointMeter("H_ST", "0.234567"));
            const value = tooltip.innerHTML;
            const liveSuppressed = row.hasAttribute("title") && row.title === "";
            hideScientificTooltip();
            return {
                hover, keyboard, suppressed, restored, value, liveSuppressed,
                accessible: link.getAttribute("aria-label"),
                role: tooltip.getAttribute("role"),
            };
        })()""",
    )
    assert "H<sub>ST</sub>" in result["hover"]
    assert result["keyboard"] == result["hover"]
    assert result["suppressed"] and result["restored"]
    assert "H<sub>ST</sub>" in result["value"]
    assert "0.234567" in result["value"]
    assert result["liveSuppressed"]
    assert "H subscript ST" in result["accessible"]
    assert result["role"] == "tooltip"


def test_field_help_and_native_statistic_choices(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Field help and selected symbols use math without changing option values."""
    result = drive(
        window,
        ready=_READY,
        trigger="null",
        read="""(() => {
            const label = document.querySelector('label[for="field-deme_weighting"]');
            showFieldTooltip(label, FIELD_HELP.deme_weighting);
            const help = document.querySelector(".field-tooltip").innerHTML;
            hideFieldTooltip();
            const select = document.getElementById("compare-trajectory-statistic");
            select.value = "H_ST";
            select.dispatchEvent(new Event("change"));
            return {
                help, value: select.value,
                option: select.selectedOptions[0].textContent,
                selected: select.nextElementSibling.innerHTML,
            };
        })()""",
    )
    assert "E<sub>ST</sub>" in result["help"]
    assert "K<sub>ST</sub>" in result["help"]
    assert result["value"] == "H_ST"
    assert result["option"] == "H subscript ST"
    assert "H<sub>ST</sub>" in result["selected"]


def test_canvas_labels_have_smaller_positioned_scripts(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Canvas labels retain mathematical subscripts, superscripts and alignment."""
    result = drive(
        window,
        ready=_READY,
        trigger="null",
        read="""(() => {
            const canvas = document.createElement("canvas");
            const context = canvas.getContext("2d");
            context.font = "16px sans-serif";
            context.textAlign = "center";
            const calls = [];
            context.fillText = function(text, x, y) {
                if (text) calls.push({text, x, y, font: this.font});
            };
            fillScientificText(context, "HD_S", 100, 50);
            return {calls, font: context.font, align: context.textAlign};
        })()""",
    )
    superscript, base, subscript = result["calls"]
    assert superscript["text"] == "H" and superscript["y"] < base["y"]
    assert base["text"] == "D" and base["y"] == 50
    assert subscript["text"] == "S" and subscript["y"] > base["y"]
    assert superscript["font"].startswith("12px")
    assert subscript["font"].startswith("12px")
    assert base["font"].startswith("16px")
    assert result["align"] == "center"
    assert result["font"].startswith("16px")


def test_statistics_chooser_and_sweep_headers(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Secondary descriptions and table headings use the same catalog notation."""
    result = drive(
        window,
        ready=_READY,
        trigger="renderStatisticsChooser();",
        read="""({
            descriptions: Array.from(document.querySelectorAll(
                ".settings-statistics-description")).map((node) => node.innerHTML),
            raw: document.getElementById("settings-statistics-list").textContent,
            header: sweepHeaderRow(["H_ST mean", "Delta", "MI"]).innerHTML,
        })""",
    )
    assert any("H<sub>T</sub>" in text for text in result["descriptions"])
    assert "H_T" not in result["raw"]
    assert "G'_ST" not in result["raw"]
    assert "H<sub>ST</sub>" in result["header"]
    assert "δ<sub>G</sub>" in result["header"]
    assert "<span>I</span>" in result["header"]
