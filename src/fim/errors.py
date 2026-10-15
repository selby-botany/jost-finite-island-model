"""Exceptions raised by `fim` for problems a caller can correct.

`FimError` is the root, so `except FimError` catches anything the package
raises on purpose. `ConfigurationError` is also a `ValueError`, matching the
Python convention that a bad argument raises `ValueError`.
"""

from __future__ import annotations

__all__ = ["ConfigurationError", "FimError"]


class FimError(Exception):
    """Root of every exception `fim` raises deliberately."""


class ConfigurationError(FimError, ValueError):
    """A specification or configuration file is invalid.

    The message names the offending field by its path, for example
    ``stopping.precision``.
    """
