"""Tests for the desktop GUI style, link, and icon-reference checker."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHECKER = PROJECT_ROOT / "dev" / "bin" / "check-webui-assets"
VALIDATE_REPOSITORY = PROJECT_ROOT / "dev" / "bin" / "validate-repository"
SUCCESS = "All desktop GUI styles, links, and icon references are connected\n"


def _build_tree(
    root: Path,
    *,
    html: str,
    css: str = "",
    js: str = "",
    sprite: str | None = None,
) -> None:
    """Write a miniature `webui/` tree at the layout the checker expects.

    Args:
        root: Directory standing in for the repository checkout.
        html: Contents of `index.html`.
        css: Contents of `app.css`, omitted when empty.
        js: Contents of `app.js`, omitted when empty.
        sprite: Contents of `icons/fim-icons.svg`, omitted when `None`.

    Returns:
        None.
    """
    webui = root / "src" / "fim" / "gui" / "webui"
    webui.mkdir(parents=True)
    (webui / "index.html").write_text(html, encoding="utf-8")
    if css:
        (webui / "app.css").write_text(css, encoding="utf-8")
    if js:
        (webui / "app.js").write_text(js, encoding="utf-8")
    if sprite is not None:
        (webui / "icons").mkdir()
        (webui / "icons" / "fim-icons.svg").write_text(sprite, encoding="utf-8")


def _run_checker(root: Path) -> subprocess.CompletedProcess[str]:
    """Copy the checker into an isolated tree and run it there.

    The checker locates the sources it scans relative to its own
    `__file__`, so placing the copy at `dev/bin/` inside `root` is what
    points it at that tree rather than at the real repository.

    Args:
        root: Directory standing in for the repository checkout.

    Returns:
        The completed process, with output captured as text.
    """
    checker = root / "dev" / "bin" / "check-webui-assets"
    checker.parent.mkdir(parents=True)
    shutil.copy2(CHECKER, checker)
    return subprocess.run(
        [sys.executable, str(checker)],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )


def test_checker_accepts_a_consistent_tree(tmp_path: Path) -> None:
    """Styled classes, resolving links, and a real sprite symbol pass."""
    _build_tree(
        tmp_path,
        html=(
            "<!doctype html><html><head><title>T</title>"
            '<link rel="stylesheet" href="app.css"></head><body>'
            '<div class="panel" id="top">'
            '<svg><use href="icons/fim-icons.svg#icon-run"></use></svg>'
            '<a href="#top">Top</a><a href="#">Noop</a>'
            '</div><script src="app.js"></script></body></html>'
        ),
        css=".panel { color: red; }\n",
        js='"use strict";\ndocument.querySelector(".panel");\n',
        sprite='<svg><symbol id="icon-run"></symbol></svg>',
    )

    result = _run_checker(tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == SUCCESS


def test_checker_rejects_a_stylesheet_rule_nothing_references(
    tmp_path: Path,
) -> None:
    """A class with styling but no user anywhere is dead weight."""
    _build_tree(
        tmp_path,
        html="<!doctype html><html><head><title>T</title></head><body></body></html>",
        css=".nobody-uses-me { color: red; }\n",
    )

    result = _run_checker(tmp_path)

    assert result.returncode == 1
    assert "orphaned style .nobody-uses-me" in result.stderr


def test_checker_rejects_a_class_with_neither_styling_nor_a_reader(
    tmp_path: Path,
) -> None:
    """Markup that applies a class nothing acts on does nothing at all."""
    _build_tree(
        tmp_path,
        html=(
            "<!doctype html><html><head><title>T</title></head>"
            '<body><div class="inert"></div></body></html>'
        ),
    )

    result = _run_checker(tmp_path)

    assert result.returncode == 1
    assert "undefined style inert" in result.stderr


def test_checker_accepts_an_unstyled_class_a_script_reads_back(
    tmp_path: Path,
) -> None:
    """A class used purely as a behavioral hook is doing real work.

    `showScreen` in the real GUI selects `.screen` without styling it;
    such a class is justified by the script that queries it, so it must
    not be reported for having no rule behind it.
    """
    _build_tree(
        tmp_path,
        html=(
            "<!doctype html><html><head><title>T</title></head>"
            '<body><div class="screen"></div>'
            '<script src="app.js"></script></body></html>'
        ),
        js='"use strict";\ndocument.querySelectorAll(".screen");\n',
    )

    result = _run_checker(tmp_path)

    assert result.returncode == 0, result.stderr


def test_checker_rejects_a_class_a_script_applies_but_never_reads(
    tmp_path: Path,
) -> None:
    """A write-only class is as inert as an unused one in the markup."""
    _build_tree(
        tmp_path,
        html=(
            "<!doctype html><html><head><title>T</title></head>"
            '<body><script src="app.js"></script></body></html>'
        ),
        js='"use strict";\nrow.classList.add("write-only");\n',
    )

    result = _run_checker(tmp_path)

    assert result.returncode == 1
    assert "undefined style write-only" in result.stderr


def test_checker_accepts_the_allowlisted_generator_classes(
    tmp_path: Path,
) -> None:
    """Classes emitted by `generate-help-html` have a program as their user."""
    _build_tree(
        tmp_path,
        html=(
            "<!doctype html><html><head><title>T</title></head><body>"
            '<code class="language-json">{}</code>'
            '<code class="math-expr">x</code>'
            "</body></html>"
        ),
    )

    result = _run_checker(tmp_path)

    assert result.returncode == 0, result.stderr


def test_checker_rejects_a_link_to_a_file_that_is_not_there(
    tmp_path: Path,
) -> None:
    """A stylesheet or script that was renamed away is caught."""
    _build_tree(
        tmp_path,
        html=(
            "<!doctype html><html><head><title>T</title>"
            '<link rel="stylesheet" href="gone.css"></head>'
            "<body></body></html>"
        ),
    )

    result = _run_checker(tmp_path)

    assert result.returncode == 1
    assert "missing target gone.css" in result.stderr


def test_checker_rejects_a_sprite_symbol_that_is_not_in_the_sprite(
    tmp_path: Path,
) -> None:
    """A missing icon renders as nothing rather than as an error."""
    _build_tree(
        tmp_path,
        html=(
            "<!doctype html><html><head><title>T</title></head><body>"
            '<svg><use href="icons/fim-icons.svg#icon-absent"></use></svg>'
            "</body></html>"
        ),
        sprite='<svg><symbol id="icon-run"></symbol></svg>',
    )

    result = _run_checker(tmp_path)

    assert result.returncode == 1
    assert "missing sprite symbol icons/fim-icons.svg#icon-absent" in result.stderr


def test_checker_rejects_a_same_page_anchor_with_no_matching_id(
    tmp_path: Path,
) -> None:
    """A renamed heading breaks in-page Help navigation silently."""
    _build_tree(
        tmp_path,
        html=(
            "<!doctype html><html><head><title>T</title></head><body>"
            '<a href="#renamed-away">Go</a></body></html>'
        ),
    )

    result = _run_checker(tmp_path)

    assert result.returncode == 1
    assert "missing anchor #renamed-away" in result.stderr


def test_checker_does_not_mistake_a_font_file_suffix_for_a_class(
    tmp_path: Path,
) -> None:
    """`url("Lato.ttf")` inside a rule must not read as a `.ttf` class.

    The class scan strips comments and quoted strings before looking for
    selectors precisely so that a file extension inside `url()` cannot
    be reported as a stylesheet rule nobody uses.
    """
    _build_tree(
        tmp_path,
        html="<!doctype html><html><head><title>T</title></head><body></body></html>",
        css='@font-face { font-family: "Lato"; src: url("fonts/Lato.ttf"); }\n',
    )

    result = _run_checker(tmp_path)

    assert result.returncode == 0, result.stderr


def test_validate_repository_shellchecks_every_wrapper_in_bin() -> None:
    """The hand-maintained `shellcheck` path list must not fall behind.

    `dev/bin/validate-repository` names each script it checks by hand,
    on purpose -- see the comment above that list. The cost of that
    choice is that a newly added wrapper is silently left unchecked, so
    this test is what makes the list self-correcting.
    """
    listed = set(
        re.findall(
            r"^\s+(bin/[\w.-]+)\s*\\?$",
            VALIDATE_REPOSITORY.read_text("utf-8"),
            re.M,
        )
    )
    present = {
        f"bin/{path.name}"
        for path in (PROJECT_ROOT / "bin").iterdir()
        if path.is_file()
    }

    assert present - listed == set(), (
        "wrappers missing from the validate-repository shellcheck list"
    )
