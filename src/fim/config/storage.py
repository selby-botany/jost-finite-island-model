"""Trajectory-log policy constants.

How the binary trajectory log (`fim.persistence.tlog`) groups and syncs what it
writes. None of them changes a simulated number; they change the log's size,
how fast it can be read back, and how much of a run a crash can lose.

See `README.md` in this directory for the table of every constant.
"""

from __future__ import annotations

from typing import Final

LOG_KEY_EVERY: Final = 256
"""Generations between keyframes in a sparse trajectory log.

A keyframe stores every deme's allele frequencies in full; the generations
between keyframes store only what changed. A smaller interval makes the log
larger and seeking to a generation faster; a larger one the reverse.

Kind: policy.
"""

LOG_SYNC_SECONDS: Final = 2.0
"""Seconds between the log's group-commit syncs to disk.

Bounds how much of a running trajectory a power loss can take: at most this
many seconds of written blocks. A shorter interval costs more disk syncs.

Kind: policy.
"""

LOG_BLOCK_GENERATIONS: Final = 512
"""Generations after which the open block of the log is sealed and written.

A block is the unit that is checksummed and written, and the unit a reader can
skip to. Larger blocks compress and write more efficiently; smaller ones lose
less when a run is cut off and make a live view fresher.

Kind: policy.
"""
