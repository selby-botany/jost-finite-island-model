"""Run and replicate identity: IDs and per-replicate parameters."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from fim.model.params import SimulationParams


def deterministic_run_id(params: SimulationParams) -> str:
    """Return a stable run ID derived only from replayable parameters.

    Used whenever a run is started without a caller-supplied `run_id`
    (see `fim`'s own docstring above). The id is not random — it is a
    short hash of the run's own configuration, written out in a
    fixed, unambiguous order (`sort_keys=True`) so the exact same
    configuration always produces the exact same id, on any machine,
    every time. That means two people who independently run the
    identical configuration end up with matching run ids without ever
    having to coordinate — a convenient side effect of this project's
    own emphasis on determinism (see this module's own docstring, above)
    rather than something engineered for its own sake.
    """
    canonical = json.dumps(
        params.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"run-{hashlib.sha256(canonical).hexdigest()[:16]}"


def params_for_replicate(
    params: SimulationParams, replicate_index: int, window: int | None = None
) -> SimulationParams:
    """Return the configuration of one replicate of a batch.

    The replicate is an independent single run with its own seed. Its
    `precision_method` is reset (a single run has no interval across
    replicates, so `planned_replicates` would be refused) and its averaging
    window, when the batch assigned one, is written into the parameters, so
    the replicate's manifest says exactly what it ran.

    Args:
        params: The batch's configuration.
        replicate_index: The replicate's zero-based index.
        window: The averaging window the batch assigned, in generations, or
            `None` to keep the batch's own setting.

    Returns:
        The replicate's configuration.
    """
    return replace(
        params,
        seed=params.seed + replicate_index,
        n_replicates=1,
        precision_method="interval",
        replicate_averaging_window=window or params.replicate_averaging_window,
    )


def derive_replicate_run_id(
    params: SimulationParams, replicate_index: int, run_id: str | None
) -> str:
    """Return the run ID of one replicate of a batch.

    Args:
        params: The batch's configuration.
        replicate_index: The replicate's zero-based index.
        run_id: The batch's ID, or `None` to derive one from the parameters.

    Returns:
        `<run_id>-rNNN` for a named batch. Otherwise the deterministic ID of
        the replicate's configuration *with the batch's own averaging-window
        setting*, not the window it was assigned: every backend learns that
        window at a different moment, and the same replicate must get the same
        ID whichever one runs it.
    """
    if run_id is not None:
        return f"{run_id}-r{replicate_index + 1:03}"
    return deterministic_run_id(params_for_replicate(params, replicate_index))
