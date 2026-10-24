# Home and organization

Home lists everything saved in your results folder: Experiments, the
Studies inside them, their runs and batch replicates, and unfinished
saved runs. Rows summarize `report.json` or `summary.json`; batch results
are reconstructed from their saved replicates.

## Contents

- [Experiments, Studies, and runs](#experiments-studies-and-runs)
- [Names, descriptions, and documentation](#names-descriptions-and-documentation)
- [Open, clone, and delete](#open-clone-and-delete)
- [Saved runs](#saved-runs)
- [The Examples experiment](#the-examples-experiment)
- [Read-only items](#read-only-items)
- [Opening a saved example](#opening-a-saved-example)

## Experiments, Studies, and runs

- An **Experiment** groups Studies; a **Study** groups runs. A run can
  belong to more than one Study and counts once in an Experiment.
- **Default study** and a default Experiment are created when needed.
  Configure files new runs in the Study selected at its foot.
- A Study's pooled results can combine runs with different parameters;
  a "varies across members" note identifies them.
- A Study that holds a sweep has sweep actions; see
  [sweeps](sweeps.md#continue-retry-and-recompute).
- **Close** freezes a group's membership; **Reopen** reverses it. A
  group cannot be closed while one of its runs executes, and closing
  never pauses or stops a run.
- The Examples experiment appears last.

## Names, descriptions, and documentation

| | Name | Description | Documentation |
|---|---|---|---|
| Experiment | Required | One line, optional | Longer notes, optional |
| Study | Required | One line, optional | Longer notes, optional |
| Run | Optional (the folder name stands in) | One line, optional | — |

- Hover an Experiment, Study, or run name, in Home, the run view's
  title, Configure's Study choice, or a sweep screen, to see its
  description.
- Click the name, or the **ⓘ** beside it, to open the details dialog.
  **Save** keeps changes; **Cancel**, Escape, or a click outside
  discards them. An empty field clears the value.
- **Create experiment…** on Home and **New study…** in Configure take an
  optional description beside the name. Configure's **Run name** and
  **Run description** label the next run.
- The run view's title names the Experiment first, then the subject, for
  example `Ring vs island — Baseline (run-20261005-101500-000000)`,
  `… — initial conditions (p₀)` before a run, or `… — in progress`.
- Names, descriptions, and class are stored in `metadata.json`, beside
  `manifest.json`. They never change the manifest or the run ID. The
  command-line equivalents are `--description` and `--documentation` on
  `fim study create` and `fim experiment create`, and `fim run --name`
  and `--description`.

## Open, clone, and delete

- Double-click a run to open its saved result. Expand a group to see its
  runs.
- **Clone** fills Configure with the run's parameters, Run name, and Run
  description. It does not change the source run or its Study. Loaded run
  settings follow the rules in
  [loaded run settings](configure.md#loaded-run-settings).
- **Select** shows checkboxes; **Select all**, **Clear selection**, and
  **Delete selected** act on them. Confirmation names every run that a
  group deletion affects. A run that is still executing has no checkbox:
  pause, cancel, or stop it first.
- Opening a trajectory supports retained-generation analysis and
  differentiation-order ($q$) evaluation; see
  [re-analyze a trajectory](../usage.md#re-analyze-a-trajectory).

## Saved runs

**Saved runs** lists saved runs that have not completed, in any of the
[lifecycle states](../run-lifecycle.md#states) from `draft` to
`cancelled`. Each row offers only the actions its state
allows: **Watch**, **Start**, **Resume**, **Resurrect**, **Clone**,
**Edit**, **Cancel**, **Kill**, and **Delete**. A row in transition
refreshes every few seconds.

- **Resurrect** appears for a cancelled run whose latest checkpoint is
  verified and compatible. It continues exactly from that checkpoint.
- An unnamed saved run is listed by its settings, for example
  "225 diploid, d=10, m=0.01, mu=1e-05, 200 replicates". Hover the name
  for every setting, the object ID, and the run ID.
- **Watch** returns to the running view of a run this window executes.
  A run executing in another window or from the command line has no
  **Watch**.
- Completed runs move to the run list.

Saved runs are shared with `fim object`; a run created in the app can be
paused or resumed from the command line, and the reverse. See
[saved runs: pause, resume, and recover](../run-lifecycle.md).

## The Examples experiment

The **Examples** experiment holds the worked examples bundled with the
app. Each example class is a Study (a nested class is named
"Parent — Child"), holding that class's examples as runs with their saved
results. The app writes them into an `examples` folder inside your
results folder at startup, writes them again if they are removed, and
replaces them when a newer version of the app ships new versions. An
example whose result has not been saved is not listed here, but it is
still available in the **Examples…** dialog.

The examples and the questions each one addresses are described in
[worked examples](../usage.md#worked-examples).

## Read-only items

The Examples experiment, its Studies, and their runs are **read-only**.
A lock beside the name marks them in Home, the details dialog, and the
run view's title. You can open, view, compare, and clone them. You
cannot rename, describe, document, or delete them, or change their
membership; those controls are unavailable, and hovering one says why.

- **Select all** never selects a read-only item. A deletion that names
  one is refused as a whole: nothing is deleted. The command line refuses
  the same edits with an error naming the item.
- An example linked into an editable Study is different: its checkbox
  selects only that link. **Delete selected** removes the link, not the
  example or its results; the confirmation says the example is kept.
- Read-only status comes from
  [`_read_only`](../configuration.md#_read_only); only shipped examples
  use it.

## Opening a saved example

Double-click an example's row on Home, or choose **Open saved result** in
the **Examples…** dialog. The complete finished run opens: statistics,
messages, graphs, and the scrubber. No simulation runs. Large examples
can take longer to open while their compressed trajectory is
reconstructed; the trajectory is stored losslessly and needs no manual
unpacking. Batch examples include every kept replicate and the batch
summary.

**Load into Configure** gives an editable copy; running it creates a new,
editable result.
