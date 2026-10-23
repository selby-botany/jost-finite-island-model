# Saved runs: pause, resume, and recover

A *saved run* (a "Run object") stores a configuration for later execution and
supports pausing and continuation from a checkpoint, including after a
computer restart. This guide
explains what you can do with a saved run, what each state means, and how to
recover when something goes wrong.

Return to the [command reference](usage.md) or the
[project overview](../README.md).

## Contents

- [Quick start](#quick-start)
- [What a saved run is](#what-a-saved-run-is)
- [States](#states)
- [Commands](#commands)
- [Checkpoints and exact continuation](#checkpoints-and-exact-continuation)
- [Stopping a run: pause, Ctrl-C, SIGTERM, and cancel](#stopping-a-run-pause-ctrl-c-sigterm-and-cancel)
- [Recovery after a crash or power failure](#recovery-after-a-crash-or-power-failure)
- [Saved runs in the desktop app](#saved-runs-in-the-desktop-app)
- [Sweeps, Studies, and Experiments](#sweeps-studies-and-experiments)
- [What is not a saved run](#what-is-not-a-saved-run)
- [Troubleshooting](#troubleshooting)
- [For developers](#for-developers)

## Quick start

**In the desktop app:** choose **Run simulation** to create and start a Run
object automatically. While unfinished, the run appears under **Saved runs** on Home, where
**Resume**, **Edit**, **Cancel**, **Kill**, and **Delete** appear when allowed. After
completion, it appears with the completed results.
The Run screen's **Pause** button stops it at a checkpoint; after pausing,
**Run simulation** becomes **Resume run** in that same document.
Choose **Save for later** to keep a configuration without starting it.

**On the command line:**

```console
$ fim object create my-run.yaml
Created obj-20a90eac919a4ed0bd5fa6f3a5b6443a (planned)
$ fim object start obj-20a90eac919a4ed0bd5fa6f3a5b6443a
Starting obj-20a90eac919a4ed0bd5fa6f3a5b6443a
obj-20a90eac919a4ed0bd5fa6f3a5b6443a is completed at generation 300
Artifacts: /…/results/objects/obj-20a90eac919a4ed0bd5fa6f3a5b6443a
```

From a second terminal, `fim object pause OBJECT_ID` asks a running object to
stop at its next checkpoint. `fim object resume OBJECT_ID` continues it later.

## What a saved run is

A saved run is a folder under `results/objects/`, named by its object ID
(`obj-` followed by 32 hexadecimal characters). It holds:

| File | Contents |
|------|----------|
| `config.yaml` | The saved configuration |
| `run.json` | The lifecycle record: state, phase, generation, latest checkpoint, attempts, errors |
| `checkpoints/` | The latest committed checkpoint (older ones are removed) |
| `trajectory.tlog` | The trajectory log, written as the run proceeds |
| `report.json`, `manifest.json`, … | The finished run's artifacts, the same files `fim run` writes |

A batch (`n_replicates` greater than one) has one `replicate-NNN/` folder
per replicate, then `summary.json` and the batch `manifest.json`, the same
layout as a `fim run` batch.

Do not edit `run.json` by hand. Use the commands below; they apply the
lifecycle rules and keep the record consistent.

## States

| State | Meaning | What you can do next |
|-------|---------|----------------------|
| `draft` | Saved, but the configuration is incomplete or invalid | Edit it; delete it |
| `planned` | Saved, valid, and not started | Start; edit; delete |
| `running` | A command or the desktop app is executing it | Pause; cancel; kill |
| `pausing` | A pause was requested; it stops at the next safe point | Wait; cancel; kill |
| `paused` | Stopped at a checkpoint | Resume; cancel; delete |
| `interrupted` | Its owner stopped without saying so (Ctrl-C, crash, power failure) | Resume; cancel; delete |
| `failed` | An error stopped it | Resume; cancel; delete |
| `cancelling` | A cancel was requested | Wait; kill |
| `cancelled` | Stopped for good; the trajectory written so far is kept | Clone; delete |
| `completed` | Finished; every artifact is published | Clone; delete |

**Draft and Planned.** A configuration that does not validate is saved as a
`draft`, with the reasons it cannot start:

```console
$ fim object create bad.yaml
Created obj-b2498c54bc2045aaaaea90208687a75b (draft)
It cannot start until these are fixed:
  - unknown configuration key(s): island_size, n_islands
```

`fim object edit OBJECT_ID CONFIG` replaces the configuration. The object
becomes `planned` when the new configuration is valid and goes back to
`draft` when it is not. Editing is available until the run starts.

`interrupted`, `failed`, and `paused` objects resume from their latest
checkpoint. A run that stopped *before its first checkpoint* (for example, it
failed while starting) has nothing to resume from; see
[clone](#commands).

## Commands

| Command | What it does |
|---------|--------------|
| `fim object create CONFIG` | Save a configuration; `draft` if invalid, else `planned` |
| `fim object edit OBJECT_ID CONFIG` | Replace the configuration of a `draft` or `planned` object |
| `fim object list` | List every object with its state, generation, and name |
| `fim object status OBJECT_ID [--json]` | Show one object's lifecycle; `--json` prints the full record |
| `fim object start OBJECT_ID [--quiet]` | Execute a `planned` object in the foreground |
| `fim object resume OBJECT_ID [--quiet]` | Continue a `paused`, `interrupted`, or `failed` object |
| `fim object pause OBJECT_ID` | Ask a running object to checkpoint and pause |
| `fim object cancel OBJECT_ID` | Stop an object for good, keeping its partial data |
| `fim object kill OBJECT_ID` | End a running object's worker now; record it as `failed` |
| `fim object delete OBJECT_ID` | Delete an object that is not executing |
| `fim object clone OBJECT_ID` | Save the object's configuration as a new object |
| `fim object reclaim [--dry-run]` | Remove staging folders that killed commands left behind |

`start` and `resume` run in the foreground and print the final state. They
exit with status 0 when the run completes, pauses, or is cancelled; 2 when the
lifecycle refuses the request; 130 after Ctrl-C; and another nonzero status
when an error fails the run.

`clone` copies only the configuration. The new object is `planned` (or
`draft`), starts from generation 0, and records the object it was cloned from
(`source_object_id` in `run.json`). Use it to run a finished configuration
again or to retry a run that stopped before its first checkpoint.

`status` lists problems it finds, under `Problems:`, such as a damaged
artifact or a run that cannot resume and why:

```console
$ fim object status obj-30acd42ec76f4a97ac7e53ae67e36156
Object:     obj-30acd42ec76f4a97ac7e53ae67e36156
State:      paused
Revision:   23
Kind:       scalar
Run ID:     run-6dac646ec750af52
Phase:      simulating
Generation: 16650
Checkpoint: ckpt-000000016650-9be5552687f4 (generation 16650)
Attempt:    -
```

## Checkpoints and exact continuation

A checkpoint is the complete state of the simulation at one generation: the
population, the random number generator, the convergence monitor, and the
committed position in the trajectory log. A run writes one:

- at generation 0, as soon as the run is set up;
- every 1,000 generations or every 60 seconds, whichever comes first;
- whenever it pauses.

**Resuming is exact.** A run that is paused and resumed, any number of times,
produces the same trajectory, the same report, and the same convergence
decision as a run that was never stopped. A trajectory log cut off by a crash
is trimmed back to the checkpoint and continued from there.

A checkpoint's SHA-256 digest is verified before use. Incompatible
checkpoint format versions are rejected.

### Cost of a checkpoint

On an Apple M4 laptop, a
single replicate with $d=20$ and $N=225$ takes about 20–35 ms per checkpoint
and keeps about 350 KB on disk. A 16-replicate vector-engine batch with large
d takes about 135 ms per checkpoint and keeps about 19 MB. Only the latest
checkpoint is kept, so disk use does not grow with run length.

### Compatibility between versions

- Versions without saved-run support ignore `results/objects/`. They cannot
  see or resume saved runs.
- A `run.json` with a `schema_version` this version does not know is refused
  with `unsupported run.json schema_version`.
- A checkpoint from a different checkpoint format version is refused, not
  converted. Run the configuration again from generation 0 with
  `fim object clone OBJECT_ID`.

## Stopping a run: pause, Ctrl-C, SIGTERM, and cancel

| How it stops | Resulting state | Data kept |
|--------------|-----------------|-----------|
| `fim object pause` or the desktop app's **Pause** | `paused` | Everything up to a fresh checkpoint |
| SIGTERM (for example, a system shutdown or `kill PID`) | `paused` | Everything up to a fresh checkpoint |
| A second SIGTERM | `interrupted` | Up to the latest checkpoint |
| Ctrl-C (SIGINT) | `interrupted`; exits with status 130 | Up to the latest checkpoint |
| An engine error | `failed`, with the error recorded | Up to the latest checkpoint |
| `fim object cancel` | `cancelled` | The trajectory written so far |
| `fim object kill` or the desktop app's **Kill** | `failed`, recorded as killed | Up to the latest checkpoint |

A run that reaches its stopping rule completes, even if a pause or a cancel
was requested at the same moment.

**Kill** is for a run you want gone now. Cancel and pause wait for the run to
reach a safe point and write its logs, which can take minutes for a large
run. Kill ends the worker process at once instead, so nothing after the
latest checkpoint is kept. The run becomes `failed`, which you can resume
from that checkpoint or delete. In the desktop app, Kill then offers to
delete the run and all of its data. Kill refuses a run whose worker did not
record its process, rather than guess which process to end.

Plain `fim run` is different: it is not a saved run. On SIGTERM it stops,
removes its unfinished output, and exits with status 143. Rerun it to start
again.

## Recovery after a crash or power failure

Each command that reads objects
(`list`, `status`, `start`, `resume`) first checks every object recorded as
running. When no process owns it any more, the object is recorded as
`interrupted`, and `fim object resume` continues it from its latest
checkpoint.

If every final artifact was written but the last lifecycle update was lost,
the command verifies the completion receipt and records the object as
`completed` without rerunning it.

A command killed in the middle of writing can leave a hidden staging folder
(its name starts with `.`) next to the run. These folders are never mistaken
for real data. `fim object reclaim --dry-run` lists them, and
`fim object reclaim` removes the ones no running command owns.

## Saved runs in the desktop app

- **Save for later** on the Run screen keeps the form as a saved run. An
  incomplete or invalid form is saved as a Draft. Saving again while you edit
  a saved run updates it.
- **Run simulation** creates and starts a Run object. Completed runs appear
  under **Saved runs** and can be opened again. If the same configuration
  already has a completed result for the current software version, the app
  reuses that result instead of computing it again.
- **Saved runs** on Home lists unfinished saved runs. Each row offers only
  the actions its state allows: Watch, Start, Resume, Edit, Cancel, Kill,
  Delete. A row that is starting, running, pausing, or cancelling refreshes
  itself every few seconds until it settles.
- A run saved without a name is listed by its settings, such as
  "225 diploid, d=10, m=0.01, mu=1e-05, 200 replicates". Hold the pointer
  over the name to see every setting, the object ID, and the run ID.
- **Watch**, or a click on the run's name, returns to the running view of a
  saved run this window is executing. A run executing in another window or
  from the command line has no Watch; watch it there.
- **Select**, **Select all**, and **Delete selected** on Home include every
  saved run that can be deleted now. A saved run that is still executing
  has no checkbox: pause, cancel, or kill it first.
  Completed results appear in the run list instead.
- **Pause** appears on the Run screen while a saved run executes. After it
  pauses, **Run simulation** in that document resumes the same Run object.
- **Closing the window asks first** when it would lose something: a
  configuration with changes you have not saved, or work that is still
  running. The dialog offers to save the configuration (as a Draft or Planned
  run), and pauses a running saved run at a checkpoint so you can resume it
  next time. See [closing the window](usage.md#closing-the-window).
- The desktop app runs one saved run at a time. It starts single runs and
  batches, and **Clone** on a saved run makes an editable copy.

A saved run created in the desktop app can be started, paused, or resumed
from the command line, and the reverse.

## Sweeps, Studies, and Experiments

- **Sweeps.** Each point of `fim sweep run` executes as a saved run, created
  when the point first runs. Cancelling a sweep pauses the points in flight;
  `fim sweep resume STUDY_ID` resumes them from their checkpoints instead of
  starting them over. Only a completed point counts as finished.
- **Studies and Experiments.** `fim study list` and `fim experiment list`
  show a lifecycle summary for each group, for example
  `2 completed, 1 paused, 3 not started`. A run shared by two Studies counts
  once in their Experiment.
- **Close and reopen.** `fim study close STUDY_ID` (and `fim study reopen`,
  `fim experiment close`, `fim experiment reopen`, or the desktop app's
  Close/Reopen) freezes a group's membership. A group cannot be closed while
  any run in it is executing. Closing a group never pauses or stops a run.

## What is not a saved run

The command-line `fim run` command and the Python `simulate_params()` function
execute without saved-run checkpoints and publish their output when they
finish. They cannot be paused or resumed. Use a Run object for work that may
need to continue across sessions.

**Supported configurations.** Every engine backend (`lineal`, `generational`,
and `generational-vector`) supports saved runs. A configuration is refused at
start, with the reason, when:

- it asks for the JIT on the `lineal` backend;
- it asks for `generational-vector` and Numba is not installed;
- a locus exceeds the vector backend's capacity.

## Troubleshooting

**"… cannot resume; `fim object clone …` saves its configuration as a new
run".** The run stopped before its first checkpoint, so there is nothing to
resume. Clone it and start the clone.

**"another attempt is executing run object …".** Another process owns the
run: a second terminal, the desktop app, or a sweep. Pause it there, or wait
for it. A run whose owner has gone (killed, crashed) is never blocked: the
next command records it as `interrupted`.

**"config.yaml of … no longer matches its recorded configuration digest".**
The saved `config.yaml` was changed by hand after the run started. A run
never continues with a configuration it did not start with. Restore the file,
or clone the object and start the clone.

**`status` reports a damaged artifact.** A file in the object folder no longer
matches its recorded digest. Check the disk, then clone the object and run it
again.

**Disk full while running.** The run fails at its next write and keeps its
latest checkpoint. Free space, then `fim object resume OBJECT_ID`.

**Hidden folders beginning with `.` next to runs.** These are leftovers from
killed commands. Run `fim object reclaim`.

## For developers

The [developer guide](developer.md#run-objects-and-checkpoints) maps the
modules. The state machine is in `fim.persistence.run_lifecycle`, ownership
and recovery in `fim.persistence.run_ownership`, checkpoints in
`fim.persistence.run_checkpoint`, and execution in `fim.run_service`, which
the command line, the desktop app, and sweeps all share.
