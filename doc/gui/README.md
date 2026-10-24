# Desktop app guide

This guide covers the `fim` desktop app (`fim-gui`). It assumes you know
the finite island model and differentiation statistics. It records what
this implementation does, which conventions it uses, and where each
control lives. For the model itself, start with the
[introduction to the finite island model](../finite-island-model-introduction.md).

In the app, open this guide with **Help** in the left rail or
**Help > Desktop help**. Press **F1** for help on the focused field or
the current screen.

## Contents

- [Before you compare with the literature](#before-you-compare-with-the-literature)
- [Screens](#screens)
- [Open documents](#open-documents)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Menus](#menus)
- [Network access and reproducibility](#network-access-and-reproducibility)
- [All desktop topics](#all-desktop-topics)

## Before you compare with the literature

Read [implementation conventions](conventions.md) before you compare a
result with a published value. Four conventions most often explain an
apparent disagreement:

- `N` counts **individuals** per deme; the engine uses
  $N \times \text{ploidy}$ gene copies.
- The scalar `m` is the fraction of a deme's parents drawn from the
  **other** $d-1$ demes, not a redraw from all $d$ demes.
- A generation is migrate, then drift, then mutate.
- Generation 0 is a Dirichlet prior (or explicit $p_0$), not a sampled
  population.

## Screens

The left rail is available on every screen. Run and Results share one
run view.

| Rail item | Purpose | Help |
|---|---|---|
| Home | Experiments, Studies, runs, saved runs, and the Examples experiment | [Home and organization](organize.md) |
| Configure | Model parameters, structure, stopping rule, and sweep setup | [Configure](configure.md) |
| Explore | Closed-form equilibrium and identity-recovery predictions; no simulation | [Explore](explore.md) |
| Run, Results | Live and completed run view, batch summaries, playback | [Run and results](run-and-results.md) |
| Compare | Overlay two or more completed runs | [Compare runs and sweeps](compare.md) |
| Help | This guide and the bundled references | — |

The parameter strip at the top shows N, d, m, and μ for the selected
document. Select a value to open Configure. A **run settings** item
appears when a loaded configuration's run settings differ from your
Settings; see [loaded run settings](configure.md#loaded-run-settings).
**Back** and **Forward** move through screen history for the selected
document. **Settings** opens [preferences](settings.md).

## Open documents

The tab bar below the title lists open configurations, runs, and
studies. Each tab keeps its own screen and history. Switching tabs does
not stop work in another tab.

- Opening a result that is already open selects its existing tab. A new
  configuration always starts as a separate draft.
- Open tabs are held in memory for this window; they are not restored
  after a restart. Saved results are unaffected.
- Close a tab with its **×**, **File > Close tab**, or **Delete** while
  the tab has focus. A configuration with unsaved changes asks whether
  to save, discard, or keep it. An active run, saved run, or sweep
  offers the appropriate pause or stop action. Closing a tab never
  deletes saved results.
- Right-click a tab for **Close other tabs** and **Close all results**.
  **Close all results** closes completed run and sweep-result tabs, not
  configurations or saved runs. Unsaved or active tabs use the usual
  close dialog one at a time: **Keep** skips that tab; Escape cancels
  the rest of the bulk close.
- With more than 12 tabs open, a notice offers **Close all results**.
  This is a reminder, not a limit.

## Keyboard shortcuts

| Action | macOS | Windows and Linux |
|---|---|---|
| New configuration | Command+N | Control+N |
| Close selected tab | Command+W | Control+W |
| Next tab | Control+Tab or Command+Shift+] | Control+Tab |
| Previous tab | Control+Shift+Tab or Command+Shift+[ | Control+Shift+Tab |
| Help for the focused field or screen | F1 | F1 |

## Menus

- **File:** **New configuration**, **Close tab**, **Next tab**,
  **Previous tab**, **Close other tabs**, **Close all results**,
  **Open configuration…**, **Save configuration…**, **Load example…**,
  **Open run…**, **Reveal output folder**, and **Quit fim**.
- **Run:** **Run simulation** and **Cancel run**.
- **Help:** **Desktop help** (this guide), **Configuration reference**,
  **Statistics reference**, **Command-line reference**, **Documentation
  on GitHub**, **Check for updates**, and **About fim**.

**Quit fim** and closing the window use the same
[close-window guard](troubleshooting.md#closing-the-window).

## Network access and reproducibility

The app uses the same simulation code and `results/` folder as the
`fim` command line. A run started in the app produces the same
`report.json` and `trajectory.tlog`, byte for byte, as the same
configuration and seed run from the terminal; see
[reproduce a run](../usage.md#reproduce-a-run).

The only network access is **Help > Check for updates**, which is
explicit. Help pages are bundled with the app and work offline. Links
to source code and to documents that are not bundled open on GitHub in
your browser.

## All desktop topics

- [Implementation conventions](conventions.md)
- [Configure](configure.md)
- [Run and results](run-and-results.md)
- [Explore](explore.md)
- [Sweeps](sweeps.md)
- [Compare runs and sweeps](compare.md)
- [Home and organization](organize.md)
- [Settings and saved preferences](settings.md)
- [Troubleshooting](troubleshooting.md)

Reference documents:
[configuration](../configuration.md),
[statistics](../statistics.md),
[convergence](../convergence.md),
[saved runs](../run-lifecycle.md),
[migration conventions](../migration-conventions.md), and the
[command-line reference](../usage.md).
