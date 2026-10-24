# GUI-first desktop help

## Status and scope

Implemented. This document records the design of the desktop Help screen:
its readers, content, navigation, generation pipeline, and the tests that
keep it accurate. It does not change simulation behavior. The command
reference, [`usage.md`](usage.md), remains authoritative for the `fim`
commands.

## Contents

- [Readers](#readers)
- [Problem and decision](#problem-and-decision)
- [Content](#content)
- [The Help screen](#the-help-screen)
- [Contextual help](#contextual-help)
- [Generation pipeline](#generation-pipeline)
- [Integration with existing documentation](#integration-with-existing-documentation)
- [Drift prevention and tests](#drift-prevention-and-tests)
- [Extending Help](#extending-help)
- [Metadata](#metadata)

## Readers

The readers are Lou Jost and researchers in his community: population
geneticists and conservation biologists who know the island model,
`G_ST`, `D`, identity coefficients, and their literature. A graduate
student in the same field is a secondary reader.

Consequences for the content:

- Help does not teach population genetics. It states this
  implementation's choices where a researcher could reasonably assume
  another: units of `N`, the migration convention, generation order,
  estimand definitions, deme weighting, locus aggregation, and stopping
  semantics.
- Each desktop page is a reminder or clarification, written for a reader
  who already knows the science and wants to know exactly what the
  software does.
- Scientific depth is one link away: the statistics and convergence
  references, the migration-conventions mapping, and the background
  documents are bundled, not sent to a web browser.

## Problem and decision

Help previously opened [`usage.md`](usage.md), a long command-line
reference with the desktop app described near the end, assuming the
command-line vocabulary. A desktop reader had to translate commands, keys,
and file names into screens and controls.

**Decision:** a dedicated, screen-organized desktop guide is the default
Help. Shared references remain authoritative and are linked at the point
of need. The command reference stays available under **Help >
Command-line reference** without being a prerequisite.

## Content

The desktop guide lives in [`doc/gui/`](gui/README.md), one page per
screen or concern:

| Page | Purpose |
|---|---|
| [Desktop app guide](gui/README.md) | Screens, documents and tabs, shortcuts, menus, network access |
| [Implementation conventions](gui/conventions.md) | What to check before comparing with published values |
| [Configure](gui/configure.md) | Each field's YAML key, run settings, validation |
| [Run and results](gui/run-and-results.md) | Graphs, trajectories, references, readouts, files |
| [Explore](gui/explore.md) | Closed-form predictions and sweep-point placement |
| [Sweeps](gui/sweeps.md) | Setup, recording, results, health, repair |
| [Compare runs and sweeps](gui/compare.md) | Overlays and the Welch difference panel |
| [Home and organization](gui/organize.md) | Experiments, Studies, saved runs, examples |
| [Settings](gui/settings.md) | Preferences and where they are stored |
| [Troubleshooting](gui/troubleshooting.md) | Disagreements with literature, long runs, shutdown |

Page contract:

- Controls are named exactly as displayed, in bold.
- A control that corresponds to a configuration key names the key and
  links to its entry in the [configuration reference](configuration.md),
  so a desktop configuration and a YAML file can be read side by side.
- Formulas and definitions are not restated; pages link to the
  [statistics reference](statistics.md) and [convergence](convergence.md).
- A command-line equivalent, where useful, appears in parentheses.

The desktop material formerly in `usage.md` moved to these pages.
`usage.md` keeps a short **Desktop GUI** section (launch, logging
environment variables) that links to the guide, so its existing anchor
still resolves.

## The Help screen

Help home lists every registered topic in four groups: **Desktop app**,
**Reference**, **Model and statistics background**, and **Command line**.
An introductory sentence points to the implementation conventions.

Toolbar:

| Control | Behavior |
|---|---|
| **Back** | Previous Help page; on the first page, returns to work |
| **Forward** | Next Help page |
| **Help home** | The topic list |
| **Help for &lt;screen&gt;** | The topic for the screen Help was opened from |
| Search box | Ranked, offline, section-level search |
| **Return to work** | The tab and screen Help was opened from |

A breadcrumb shows **Help › group › topic**. Help keeps its own page
history, reset each time Help is entered, so reading several pages never
fills the tab's screen history.

**Return to work** selects the origin tab if another is selected, and
uses the tab's own history when Help is its current entry, so the tab's
Back and Forward stay consistent. If the origin tab has closed, Help says
so and offers Home.

Search loads `search-index.json` on first use. All query terms must
match; a heading match outweighs a topic title or alias match, which
outweighs a body match, and desktop topics receive a small bonus. Escape
clears the search and restores the current page.

Each render takes a token, so a slow fetch never replaces a newer page.
A fetch failure shows **Try again**, **Help home**, and **Return to
work**. `window.__fimHelpRendered` names what finished rendering, so
tests wait on a fact rather than on time.

## Contextual help

- **F1** in a Configure field opens that field's configuration-reference
  entry. The `FIELD_HELP_TOPICS` map in
  [help.js](../src/fim/gui/webui/screens/help.js) is keyed by the same
  names as `FIELD_HELP` in
  [field-help.js](../src/fim/gui/webui/field-help.js); the field is found
  from its `field-<key>` id, a `label[data-field-help]`, or its fieldset's
  `legend[data-field-help]`.
- **F1** elsewhere opens the visible screen's topic (`SCREEN_HELP`); in
  Help it opens Help home. F1 is ignored while a dialog is open, because
  Help would appear behind it and dialog fields already show tooltips.
- Statistic names in tables open their statistics-reference entries, as
  before.
- The navigation rail and **Help > Desktop help** open Help home; the
  Help menu also lists the configuration, statistics, and command-line
  references.

Field tooltips are unchanged; F1 adds the reference entry behind them.

## Generation pipeline

[`doc/help-topics.json`](help-topics.json) is the registry. Each topic
has an `id`, `source`, `title`, `group`, `summary`, and `aliases`.
[`generate-help-html`](../dev/bin/generate-help-html) validates it and
writes, into `src/fim/gui/webui/help/`:

- `<id>.html` for every topic, removing fragments for unregistered ones;
- `topics.json`, the registry without source paths, for Help home;
- `search-index.json`, one entry per `h1`–`h3` section (excluding
  contents lists) with plain text capped at 1,200 characters and MathML
  removed.

Relative links resolve against the source file's own directory, as on
GitHub. A link to a registered source becomes an in-app link; any other
link opens in the system browser at the repository's `main` branch. Topic
ids `usage`, `configuration`, and `statistics` are unchanged, so existing
statistic links keep working.

Two references carry a generated note: the command-line reference points
desktop readers to the desktop guide, and the configuration reference
points to the Configure field table.

## Integration with existing documentation

- Inbound links to moved `usage.md` sections were retargeted to the
  desktop guide, including from `README.md`, `ISSUES.md`,
  `doc/run-lifecycle.md`, `doc/examples/README.md`, and the changelog.
- The CLI sweep section keeps command behavior and points to the desktop
  sweep and compare pages for screen details.
- [Desktop GUI design §11](fim-gui-design.md#11-in-app-help) summarizes
  the Help screen for maintainers.
- Equation C.63 in [convergence](convergence.md) was rewritten in TeX the
  offline MathML converter supports, so that document can be bundled.

## Drift prevention and tests

[`test_help_html.py`](../test/validation/test_help_html.py) checks:

- committed fragments and both manifests equal freshly generated output;
- every in-app link and same-page anchor resolves;
- every `doc/gui/` page is registered and the stable ids remain;
- the pre-commit hook regenerates Help for every registered source;
- `doc/gui/` links resolve relative to their own directory;
- every search entry opens an existing topic and heading;
- every `FIELD_HELP` key has an F1 target, and every F1 and screen target
  exists.

[`test_help_screen.py`](../test/gui/test_help_screen.py) drives a real
window: Help home groups, page history and **Return to work**, search,
F1 from a Configure field, recovery from a missing page, and the existing
statistic-name and external-link behavior. `check-doc-links` and
`check-webui-assets` cover Markdown links and stylesheet classes.

## Extending Help

1. Write or edit the Markdown page. New desktop pages go in `doc/gui/`.
2. Add a registry entry to `doc/help-topics.json`.
3. Add a source outside `doc/gui/` to the help trigger list in
   `dev/git-hooks/pre-commit`.
4. Run `dev/bin/generate-help-html`, then the validation tests.
5. For a new Configure field, add its key to `FIELD_HELP_TOPICS`.

## Metadata

```text
generator-name: Copilot SDK in VS Code
generator-version: unavailable
generator-model-token: unavailable (not exposed by the session)
generator-provider: unavailable
generation-date: 2026-10-24
generator-responsibility: other
```
