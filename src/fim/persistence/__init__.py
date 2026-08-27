"""Incremental trajectory and manifest persistence.

This package is where a run's results actually get written to (and
read back from) disk. A run persists two kinds of file:

- A "trajectory" — every generation's own allele frequencies, written
  one generation at a time as it happens (`fim.persistence.
  jsonl_store.JSONLTrajectoryStore`), so a run's history survives even
  if it is interrupted partway through and so it can later be re-
  analyzed at any earlier generation (see `fim.reanalyze`). `fim.
  persistence.store` defines the row schema and store interface both
  the real file-backed store and an in-memory test double implement.
- A "manifest" — the run's own bookkeeping recorded once, at
  completion: its parameters, how it stopped, and a checksum of its
  trajectory file (`fim.persistence.manifest`), used to detect if the
  trajectory file has since been edited, corrupted, or replaced.

`fim.persistence.report` holds a small helper, `write_report`, for
writing other JSON result files (`report.json`, a batch's own
`summary.json`) in the same deterministic, byte-reproducible format as
the manifest — not itself re-exported here, since it is used directly
by `fim.engine` and `fim.cli` rather than through this package's own
top-level API.
"""

from fim.persistence.jsonl_store import JSONLTrajectoryStore
from fim.persistence.manifest import RunManifest, read_manifest, write_manifest
from fim.persistence.store import (
    InMemoryTrajectoryStore,
    TrajectoryRow,
    TrajectoryStore,
)

__all__ = [
    "InMemoryTrajectoryStore",
    "JSONLTrajectoryStore",
    "RunManifest",
    "TrajectoryRow",
    "TrajectoryStore",
    "read_manifest",
    "write_manifest",
]
