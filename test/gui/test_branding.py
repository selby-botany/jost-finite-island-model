"""Static guards for the locally bundled Selby scientific visual identity."""

from pathlib import Path

_PROJECT_ROOT = Path(__file__).parents[2]
_WEBUI = _PROJECT_ROOT / "src" / "fim" / "gui" / "webui"


def test_lato_identity_font_is_local_and_licensed() -> None:
    """Every declared Lato face and its license ship inside the web UI."""
    css = (_WEBUI / "app.css").read_text(encoding="utf-8")

    for filename in (
        "Lato-Light.ttf",
        "Lato-Regular.ttf",
        "Lato-Bold.ttf",
        "Lato-Black.ttf",
    ):
        assert f'url("fonts/{filename}")' in css
        assert (_WEBUI / "fonts" / filename).stat().st_size > 0

    license_text = (_WEBUI / "fonts" / "OFL.txt").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE" in license_text


def test_navigation_uses_one_svg_icon_language_instead_of_emoji() -> None:
    """All seven destinations use the bundled monochrome SVG symbols."""
    html = (_WEBUI / "index.html").read_text(encoding="utf-8")
    icons = (_WEBUI / "icons" / "fim-icons.svg").read_text(encoding="utf-8")

    assert html.count('class="rail-icon"') == 7
    assert html.count('href="icons/fim-icons.svg#icon-') == 12
    for icon_id in (
        "home",
        "configure",
        "explore",
        "run",
        "results",
        "compare",
        "help",
    ):
        assert f'id="icon-{icon_id}"' in icons

    for emoji in ("🏠", "⚙", "🔮", "▶", "📊", "⇄", "❓"):
        assert emoji not in html


def test_home_and_about_carry_restrained_selby_identity() -> None:
    """Identity surfaces use the orchid mark without entering data views."""
    html = (_WEBUI / "index.html").read_text(encoding="utf-8")

    assert 'class="rail-brand"' in html
    assert 'class="home-identity"' in html
    assert "<h1>Finite Island Model</h1>" in html
    assert html.count("assets/selby-orchid-logo.jpeg") == 3
    assert html.count("Marie Selby Botanical Gardens") >= 3


def test_reserved_branding_is_canonical_at_the_repository_root() -> None:
    """The web UI links to, rather than duplicates, the reserved orchid mark."""
    reserved_logo = _PROJECT_ROOT / "branding" / "selby-orchid-logo.jpeg"
    reserved_policy = _PROJECT_ROOT / "branding" / "README.md"
    webui_logo = _WEBUI / "assets" / "selby-orchid-logo.jpeg"
    webui_policy = _WEBUI / "assets" / "BRANDING.md"

    assert reserved_logo.is_file()
    assert webui_logo.is_symlink()
    assert webui_logo.samefile(reserved_logo)
    assert webui_policy.is_symlink()
    assert webui_policy.samefile(reserved_policy)
    assert not (_WEBUI / "branding").exists()


def test_license_excludes_the_identified_branding_assets_from_agpl() -> None:
    """The repository license and branding policy state the asset boundary."""
    license_text = (_PROJECT_ROOT / "LICENSE.md").read_text(encoding="utf-8")
    policy_text = (_PROJECT_ROOT / "branding" / "README.md").read_text(encoding="utf-8")

    assert "Except for the files and assets expressly identified as branding" in (
        license_text
    )
    assert "`branding/` are not licensed under the AGPL" in license_text
    assert "All rights reserved" in policy_text
    assert "`selby-orchid-logo.jpeg`" in policy_text
