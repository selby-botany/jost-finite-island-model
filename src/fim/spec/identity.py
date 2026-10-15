"""A run's identity, from its specification."""

from __future__ import annotations

from fim.engine.identity import deterministic_run_id
from fim.spec.codec import to_params
from fim.spec.run import RunSpec


def run_identity(spec: RunSpec) -> str:
    """Return the ID of the run a specification describes.

    The ID names the science: it is the same whatever `Execution` or
    `Recording` the run uses, and different when any scientific setting
    (including the lane window of an adaptive batch) differs. It is derived
    from the resolved parameters, so a change to an automatic default gives
    a new ID, as it should.

    Args:
        spec: The run's specification.

    Returns:
        A string such as ``run-0123456789abcdef``.

    Raises:
        ConfigurationError: The engine rejects the specification.
    """
    return deterministic_run_id(to_params(spec))
