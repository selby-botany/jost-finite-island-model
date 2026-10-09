# The trajectory log

A run keeps its trajectory (every allele frequency in every deme, at every
generation) in one compact binary file, `trajectory.tlog`. This page says what
the file is, how to get the familiar `trajectory.jsonl` text from it, what
happens when a run is interrupted, and how the format works. Each section says
who it is for.

- [For everyone: what you have and what to do with it](#for-everyone-what-you-have-and-what-to-do-with-it)
- [Exporting trajectory.jsonl](#exporting-trajectoryjsonl)
- [For sysops: size, interruption and recovery](#for-sysops-size-interruption-and-recovery)
- [For a technician: inspecting a log](#for-a-technician-inspecting-a-log)
- [For developers: the format and the code](#for-developers-the-format-and-the-code)

## For everyone: what you have and what to do with it

Each run folder holds a file called `trajectory.tlog`. It is the full record
of the simulation, stored so that it is small and quick to read. The app uses
it for the scrubber, the trajectory graphs and the animation; you do not need
to do anything with it.

If you want the data as text, for a spreadsheet or another program, ask for
it:

```console
fim export results/my-run
```

That writes `trajectory.jsonl` in the same folder: one line per allele
frequency, in the format described under
[Output schemas](usage.md#output-schemas). The file can be very large (see the
next section), so the command checks that there is room first and tells you if
there is not.

## Exporting trajectory.jsonl

```console
fim export RUN [-o PATH] [--workers N] [--force]
```

- `RUN` is the run folder, or its `trajectory.tlog` (or
  `equilibrium_trajectory.tlog`, the ancestral phase of an
  [equilibrium-split](usage.md#equilibrium-split-founding) run).
- The output defaults to the log's own name with `.jsonl`, beside it. `-o`
  chooses another place, for example another disk.
- The command first checks the log against the digest in the run's
  `manifest.json` and refuses a log that has been changed or damaged.
- It then works out the exact size of the file it will write (without writing
  anything) and refuses if the disk does not have that much free space.
- The text goes to a `.partial` file that is renamed into place when it is
  complete, so a stopped export never leaves a half file that looks finished.
- It will not replace an existing file unless you pass `--force`.
- Beside the output it writes `<output>.export.json`, a small receipt with the
  SHA-256 and size of both the log and the JSON Lines file, and the number of
  generations and rows. The JSON Lines file is canonical: the same log always
  gives the same bytes and the same digest.
- `--workers` sets how many processes format the text (default: up to four).
  Nothing changes except speed; one process formats and hashes about 0.6 GB a
  second on a busy laptop.

`fim stats` and the app read the log directly; they never need the export.

## For sysops: size, interruption and recovery

**Size.** The log stores most generations as only what changed since the
previous one, with a full snapshot every 256 generations. Measured on the
shipped examples: the 295,391 generations of the Dear-Nolan low example take
7.2 MB (against 129 MB as compressed JSON Lines and tens of gigabytes as plain
text), about 24 bytes a generation at 30 loci. A run that changes a great deal
every generation (high mutation, small demes) costs more: Jost (2008) Part VI
takes about 220 bytes a generation. The text export is much larger than the
log: the Dear-Nolan low example exports to 4.8 GB (about 670 times its log) and
Part VI to 0.5 GB (about 17 times); a 400-locus, million-generation run exports
to hundreds of gigabytes, which is why the export asks first.

**Interruption.** The log is written as a sequence of *blocks* (512
generations at most, and a snapshot always starts a new one). A block counts as
saved only when it has been written completely and its checksum is correct.
Each block's checksum includes the one before it, so a damaged, missing or
reordered block invalidates everything after it, and a block that merely looks
valid after a gap is never trusted. If a run is killed, or the power fails,
the file keeps every complete block: you lose at most the block being filled
(256 generations with the default settings), not the whole run. A run
directory is only published when the run finishes, so an interrupted
`fim run` leaves no partial run folder at all; the interruption guarantees
matter to code that keeps a log open for long (the desktop app's live runs, and
the run checkpoints that are still to come).

**Durability.** A background thread writes the blocks and asks the operating
system to flush them to the disk every two seconds, and when the log is
closed. On macOS that uses the full-flush call (`F_FULLFSYNC`), because the
ordinary flush there does not reach the drive.

**Reproducibility.** For `fim run` the file is a pure function of the run: the
same configuration and seed write the same bytes, so the digest recorded in
`manifest.json` can be compared between runs. The desktop app asks for its
open block to be written every second so a live view never goes stale; that
moves block boundaries, so an app-made log can differ in bytes from a command-
line one while holding exactly the same generations (their exports are
identical).

**Software without Numba.** The compiled routines that encode and decode the
log are optional. Without Numba the same code runs as ordinary Python, which
is correct but slower.

## For a technician: inspecting a log

You do not need special tools:

```console
fim export RUN -o /tmp/trajectory.jsonl      # text, to look at or search
fim stats RUN/trajectory.tlog                # statistics of the last generation
fim stats RUN/trajectory.tlog --generation 500
```

If a log will not open:

- *"does not match its manifest"*: the file changed after the run finished
  (copying that was interrupted, a disk problem, or an edit). Restore it from a
  backup or rerun.
- *"not a trajectory log"* or *"header checksum mismatch"*: the first bytes of
  the file are damaged or it is not a log.
- A log that was cut short (a copy that did not finish) opens up to its last
  complete block; `fim export` and the app use that committed part.

## For developers: the format and the code

### File layout

All integers are little-endian.

```text
file header   "FIMTLOG1" | u16 version | u16 flags | u32 demes | u32 loci
              | u32 key_every | u16 run_id length | run_id (UTF-8)
              | varint deme_sizes[demes] | varint locus_ids[loci] | u32 crc32
block         "FTB1" | u32 payload_len | u64 first_generation
              | u64 last_generation | u32 n_records | u32 rows
              | payload (n_records records) | u32 chain_crc
record        padded-4 varint body_len | padded-4 varint rows | u8 kind
              | varint gen_delta | body
```

- `chain_crc` is the CRC-32 of the block header and payload, seeded with the
  previous block's `chain_crc` (the first block is seeded with the CRC of the
  file header's body).
- A *pair* is one (deme, locus) combination, numbered
  `deme_index * loci + locus_index`. A *frame* (`TrajectoryFrame`) holds one
  generation as flat arrays: alleles per pair, then every pair's allele ids and
  frequencies.
- `kind` 0 is a full record (a pair body for every pair); `kind` 1 is a delta
  (`n_changed`, then the changed pairs, each as the gap from the previous
  changed pair and its pair body). A full record always opens a block, so a
  reader can start decoding at any keyframe block.
- A pair body is `varint h`: `h == 1` is one allele at frequency exactly 1.0;
  otherwise `n = h >> 1` alleles follow, each as its id and either a *count*
  (`frequency = count / deme size`, used only when `float(count) / float(size)`
  reproduces the frequency bit for bit) or, when `h & 1`, the eight raw bytes
  of the float. The format is lossless for any input; compactness depends on
  frequencies being `count / size`. An unknown deme size (0) stores raw floats.
- `gen_delta` is the number of generations since the previous record (0 for
  a block's first, usually 1, more when a run is thinned).

### Modules

| Module | Role |
|---|---|
| `fim/persistence/frame.py` | `TrajectoryFrame`, `FrameLayout`, rows to frame and back |
| `fim/persistence/tlog_codec.py` | Record kernels (compiled with Numba when installed, Python otherwise) |
| `fim/persistence/tlog.py` | Header, blocks, chained checksums, `LogWriter` (thread, group-commit sync, fault hooks, checkpoint and resume), scanning and recovery |
| `fim/persistence/tlog_reader.py` | `LogReader`: random access (`frame_at`, `frames`), the resumable scan |
| `fim/persistence/tlog_export.py` | Canonical JSON Lines derivation (formatter, size pass, shards), `fim export` |
| `fim/persistence/binary_store.py` | `BinaryLogStore`, the `TrajectoryStore` the engine uses; `open_trajectory` |

The engine hands a store a frame when the store asks for frames
(`wants_frames`): Backend V builds one with a compiled call, Backends L and G
walk their dictionaries once. A store that prefers rows (the JSON Lines and
in-memory stores) keeps getting rows.

### Rules that keep it correct

- **Exactness.** A float's text in the export always comes from Python's own
  `repr`: from a table built once per deme size, or from `float.__repr__`. No
  float formatter was reimplemented, so there is nothing to prove equal.
- **Determinism.** No wall clock reaches a byte. Blocks are sealed on count
  and size by default; sealing on elapsed time is opt-in
  (`block_seconds`) because it moves block boundaries. Time only decides when
  the writer thread syncs.
- **One run per log.** The header names the run, so a store holds one run; a
  batch uses one store per replicate (a store factory).
- **The writer thread** calls only `zlib.crc32`, `os.write` and the sync call,
  which release the GIL; a Python-level formatter in a thread slows the
  compiled kernels by an order of magnitude.

### Checkpoints and resume

`LogWriter.checkpoint()` commits and syncs everything written and returns a
`LogPosition` (byte offset, chained checksum, last generation, counts, a copy
of the header). `LogWriter(..., resume=position)` checks the file against it
with one checksum comparison, restores a damaged header from the copy, cuts
everything after it and continues with a keyframe. A killed run resumed from a
checkpoint on a block and keyframe boundary leaves exactly the log of an
uninterrupted run, byte for byte; elsewhere it leaves the same generations and
the same export. fim does not write run checkpoints yet (the run lifecycle
design is not implemented); this is the log's half of that work.

### Tests

`test/persistence/test_tlog_codec.py` (round trips, with and without Numba),
`test_tlog.py` (header, blocks, corruption), `test_tlog_writer.py` (thread,
back-pressure, sync order, faults, a killed child process),
`test_tlog_sparse.py`, `test_tlog_reader.py` (exhaustive random access),
`test_tlog_export.py` and `test/cli/test_export_command.py` (byte identity
with `json.dumps` and with the JSON Lines store), `test_tlog_resume.py`
(kill and resume), `test_binary_store.py`, and `test/engine/
test_frame_identity.py` (all three backends produce identical frames).

### Design

The reasoning, measurements and the decisions behind this page are in the
project's design documents; the benchmark scripts are checked in beside them.
