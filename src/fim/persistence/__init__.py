"""Public interfaces for trajectory storage and run manifests.

The package exports the binary and in-memory trajectory stores, the row schema
and store protocol, and helpers for reading and writing run manifests.
"""

from fim.persistence.binary_store import BinaryLogStore
from fim.persistence.manifest import RunManifest, read_manifest, write_manifest
from fim.persistence.store import (
    InMemoryTrajectoryStore,
    TrajectoryRow,
    TrajectoryStore,
)

__all__ = [
    "BinaryLogStore",
    "InMemoryTrajectoryStore",
    "RunManifest",
    "TrajectoryRow",
    "TrajectoryStore",
    "read_manifest",
    "write_manifest",
]
