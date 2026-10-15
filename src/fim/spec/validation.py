"""Small field checks shared by the specification types.

Each check raises `ConfigurationError` naming the field by its path, so a
message reads ``stopping.precision must be greater than 0`` wherever the bad
value was supplied. Cross-field and model-dependent rules are not repeated
here: `RunSpec` compiles to the engine's `SimulationParams`, which remains
the single place those rules live.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any, get_args

from fim.errors import ConfigurationError


def check_bool(path: str, value: object) -> None:
    """Require a real `bool` (not `0` or `1`).

    Args:
        path: Dotted field path for the message.
        value: The value to check.

    Raises:
        ConfigurationError: If `value` is not a `bool`.
    """
    if not isinstance(value, bool):
        raise ConfigurationError(f"{path} must be true or false")


def check_choice(path: str, value: object, alias: Any) -> None:
    """Require `value` to be one of the strings in a `Literal` alias.

    Args:
        path: Dotted field path for the message.
        value: The value to check.
        alias: A `Literal[...]` type; its arguments are the legal values.

    Raises:
        ConfigurationError: If `value` is not one of them.
    """
    legal = get_args(alias)
    if value not in legal or isinstance(value, bool):
        names = ", ".join(repr(item) for item in legal)
        raise ConfigurationError(f"{path} must be one of {names}")


def check_finite(path: str, value: object, *, above: float | None = None) -> None:
    """Require a finite real number, optionally strictly above a floor.

    Args:
        path: Dotted field path for the message.
        value: The value to check.
        above: Exclusive lower bound, or `None` for no bound.

    Raises:
        ConfigurationError: If `value` is not a finite number above `above`.
    """
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
    ):
        raise ConfigurationError(f"{path} must be a finite number")
    if above is not None and value <= above:
        raise ConfigurationError(f"{path} must be greater than {above:g}")


def check_int(path: str, value: object, *, minimum: int | None = None) -> None:
    """Require a whole number (not a `bool`), optionally at least `minimum`.

    Args:
        path: Dotted field path for the message.
        value: The value to check.
        minimum: Inclusive lower bound, or `None` for no bound.

    Raises:
        ConfigurationError: If `value` is not an integer at least `minimum`.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{path} must be a whole number")
    if minimum is not None and value < minimum:
        raise ConfigurationError(f"{path} must be at least {minimum}")


def check_int_or_auto(path: str, value: object, *, minimum: int) -> None:
    """Require the string ``"auto"`` or a whole number at least `minimum`.

    Args:
        path: Dotted field path for the message.
        value: The value to check.
        minimum: Inclusive lower bound for the number.

    Raises:
        ConfigurationError: If `value` is neither.
    """
    if value == "auto":
        return
    try:
        check_int(path, value, minimum=minimum)
    except ConfigurationError as error:
        raise ConfigurationError(f"{path} must be 'auto' or a whole number") from error


def check_probability(path: str, value: object) -> None:
    """Require a finite number in the closed interval from 0 to 1.

    Args:
        path: Dotted field path for the message.
        value: The value to check.

    Raises:
        ConfigurationError: If `value` is not such a number.
    """
    check_finite(path, value)
    assert isinstance(value, int | float)
    if not 0.0 <= value <= 1.0:
        raise ConfigurationError(f"{path} must be between 0 and 1")


def check_strings(path: str, values: Iterable[object]) -> None:
    """Require every item to be a non-empty string.

    Args:
        path: Dotted field path for the message.
        values: The items to check.

    Raises:
        ConfigurationError: If any item is not a non-empty string.
    """
    for item in values:
        if not isinstance(item, str) or not item:
            raise ConfigurationError(f"{path} must contain only non-empty names")
