# Troubleshooting

## Contents

- [A result disagrees with a published value](#a-result-disagrees-with-a-published-value)
- [A run takes very long or reaches the cap](#a-run-takes-very-long-or-reaches-the-cap)
- [A run finished in a few hundred generations](#a-run-finished-in-a-few-hundred-generations)
- [Configure rejects a value](#configure-rejects-a-value)
- [Memory warning](#memory-warning)
- [Sweep messages](#sweep-messages)
- [An item cannot be edited or deleted](#an-item-cannot-be-edited-or-deleted)
- [Closing the window](#closing-the-window)
- [If the window closes but fim keeps running](#if-the-window-closes-but-fim-keeps-running)
- [Settings were reset](#settings-were-reset)
- [Logs and diagnostics](#logs-and-diagnostics)
- [The app does not open on Linux](#the-app-does-not-open-on-linux)

## A result disagrees with a published value

Check, in order:

1. Units of N: individuals × ploidy = gene copies.
2. Migration convention: `fim`'s scalar `m` draws from the other $d-1$
   demes; a redraw-from-all-$d$ rate converts by $d/(d-1)$.
3. Estimand: population statistic, not a sample estimator; ratio of
   means across loci, not mean of ratios.
4. Equilibrium: was the burn-in long enough, and did the run converge?

See [implementation conventions](conventions.md) and the
[comparison protocol](../migration-conventions.md#3-comparison-protocol).

## A run takes very long or reaches the cap

Low migration or mutation can need tens of thousands of generations, or
more, for the initial state to decay. The derived burn-in and cap appear
beside **Run simulation** before you start.

Reaching **max generations** is a valid, non-converged result; the
report gives the precision achieved. Options: raise **max generations**,
accept a larger **precision**, add independent loci, or use a batch. A
batch can reduce elapsed time but not total computation, because each
replicate has its own burn-in. See
[why does my run take so long?](../convergence.md#why-does-my-run-take-so-long)

## A run finished in a few hundred generations

Check whether **convergence burn-in** or **max generations** in
[Run settings…](configure.md#run-settings) is a small fixed number,
perhaps from a loaded configuration. Use `auto` for both unless you have
a specific reason not to.

## Configure rejects a value

The app returns to Configure and marks the field. Press **F1** in that
field for its reference entry. A loaded YAML file with an unknown or
misspelled key is rejected, never silently ignored; compare the key with
the [configuration reference](../configuration.md).

## Memory warning

The app estimates each job's added memory before it starts and asks if
the estimate would cross the reserve in
[Background work](settings.md#background-work). While work runs, a
warning lists jobs with **Pause** buttons when available memory falls
below the reserve.

## Sweep messages

- **Another process is running this sweep:** a sweep locks its Study.
  The lock is released when the other process exits, including after a
  crash.
- **The stored sweep plan no longer matches:** a newer release changed
  what a run ID covers. Run `fim sweep replan STUDY_ID --dry-run`, then
  `fim sweep replan STUDY_ID`. See
  [sweep a parameter](../usage.md#sweep-a-parameter).
- **A point failed:** the sweep continues. Use **Retry failed (N)** on
  Home. A failure the engine reports is not retried automatically,
  because the same configuration fails the same way.

## An item cannot be edited or deleted

Examples are [read-only](organize.md#read-only-items). A deletion that
includes a read-only item is refused as a whole. A run that is executing
cannot be deleted; pause, cancel, or stop it first.

## Closing the window

When closing would lose something, `fim` asks first. That happens when a
configuration has unsaved changes, a saved run is in progress (runs
started with **Run simulation** are saved runs), or a sweep is in
progress. The dialog lists every affected document, including tabs not
on screen.

| Button | Effect |
|---|---|
| **Save and close** | Saves configurations under **Saved runs** on Home (Draft if incomplete, Planned if valid), then closes |
| **Close without saving** | Discards unsaved changes and closes |
| **Pause and close** | Shown when only a saved run is active; it pauses at a checkpoint for later **Resume** |
| **Stop and close** | Shown when only a sweep is active; the sweep stops |
| **Keep working** | Leaves the window open |

**Save and close** and **Close without saving** also pause a running
saved run or stop a running sweep. If saving fails, the dialog stays open
with the reason.

A sweep setup is not stored with a saved run. If a sweep setup was
edited, **Save and close** saves the run configuration and keeps the
dialog open with an explanation: **Keep working** retains the setup for
this session; **Close without saving** discards it.

## If the window closes but fim keeps running

If shutdown has not finished 20 seconds after the window closes, `fim`
records diagnostics and exits with status 3:

```text
fim: shutdown did not complete within 20s; forcing exit
```

Completed results remain on disk. A saved run resumes from its latest
checkpoint; work since that checkpoint may be repeated. The diagnostic
report is written to `logs/fim.log`. Report the section that begins
`shutdown deadman fired`, with your platform and launch method; see
[known issues](../../ISSUES.md#intermittent-hang-during-gui-shutdown).

To investigate, set `FIM_GUI_SHUTDOWN_TIMEOUT` to a longer timeout in
seconds, or `0` to disable the forced exit, before launching:

```console
FIM_GUI_SHUTDOWN_TIMEOUT=0 fim --graphical
```

## Settings were reset

If `preferences.json` is invalid, the app shows a banner, uses built-in
defaults, and keeps the invalid file as
`preferences.invalid-<timestamp>.json`. See
[where preferences are stored](settings.md#where-preferences-are-stored).

## Logs and diagnostics

The app writes a rotated log to `logs/fim.log` under the storage
location. A double-clicked app or `fim --graphical` accepts no logging
flags; set `FIM_LOG_LEVEL` and `FIM_LOG_OPTIONS` before launching. They
correspond to the command line's `-l` and `-L`. See the
[logging design](../fim-logging-design.md) and
[global flags](../usage.md#global-flags).

## The app does not open on Linux

The app needs WebKitGTK, a system package most desktop distributions
already include. See [installation alternatives](../../install/README.md).
