"""Exceptions raised by `fim` for problems a caller can correct.

`FimError` is the root, so `except FimError` catches anything the package
raises on purpose. `ConfigurationError` is also a `ValueError`, matching the
Python convention that a bad argument raises `ValueError`.
"""

from __future__ import annotations

__all__ = [
    "ConfigurationError",
    "FimError",
    "StoreError",
    "UnsupportedError",
]


class FimError(Exception):
    """Root of every exception `fim` raises deliberately."""


class ConfigurationError(FimError, ValueError):
    """A specification or configuration file is invalid.

    The message names the offending field by its path, for example
    ``stopping.precision``.
    """


class UnsupportedError(FimError, ValueError):
    """The specification is valid, but no available engine can run it.

    For example a backend that needs `numba` without it installed, or a model
    too large for the machine.
    """


class StoreError(FimError):
    """A stored trajectory, checkpoint, or saved group cannot be used.

    Raised for damaged or closed stores and for attempts to change a
    read-only run.
    """
