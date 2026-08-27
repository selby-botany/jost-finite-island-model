"""Shared identifier and frequency parsing for config, state, and row layers.

Several different parts of this project read the same two small value
shapes back out of loosely typed data (a YAML config file, a JSON
trajectory row, or a `dict` built by hand in a test): an "integer-like
identifier" (an allele ID or a deme index — always a whole number, but
one that might have arrived as a string, since mapping keys are always
strings under both JSON and YAML) and a "trajectory-row-style
probability" (an allele frequency, always strictly greater than zero
and at most one — see `fim.model.state.ModelState.from_rows` and
`fim.persistence.store` for why a persisted row's own frequency can
never legitimately be zero). This module is the single place both
value shapes are parsed, so every caller enforces exactly the same
rules on exactly the same kind of value.

Regression fix for S5/S6: before this module existed, three call sites
parsed the same two value shapes independently, and had already
drifted apart once (`fim.model.params`'s config parser rejected a
truncated float or a negative identifier that `fim.model.state.
ModelState`'s own constructor and `from_rows` silently accepted). One
shared rule, used everywhere the same value shape is parsed, cannot
drift piecemeal the way three separately maintained copies did.
"""

from __future__ import annotations

import math
from typing import Any


def parse_bounded_frequency(message: str, raw_value: Any) -> float:
    """Parse one trajectory-row-style probability, strictly within ``(0, 1]``.

    This is the row-schema contract used by both `fim.model.state.
    ModelState.from_rows` and `fim.persistence.store.normalize_row`:
    every persisted row is a nonzero frequency (a zero-frequency allele
    is simply never written — see the sparse representation), so unlike
    a config's ``p_0``, which legitimately accepts an explicit ``0.0``
    that is then filtered out, a row's frequency has no zero case to
    accept in the first place.

    Args:
        message: The exact error message to raise for any invalid input;
            callers keep their own wording since the same value shape is
            parsed in more than one context.
        raw_value: The raw row value to parse.

    Returns:
        The parsed frequency.

    Raises:
        ValueError: If `raw_value` is a boolean, not numeric, non-finite,
            or outside ``(0, 1]``.
    """
    if isinstance(raw_value, bool) or not isinstance(raw_value, int | float):
        raise ValueError(message)
    value = float(raw_value)
    if not math.isfinite(value) or not 0.0 < value <= 1.0:
        raise ValueError(message)
    return value


def parse_integer_identifier(message: str, raw_value: Any) -> int:
    """Parse an allele or deme identifier without silently truncating it.

    Accepts a native integer or a numeric string (mapping keys are
    always strings under JSON, and often written that way by hand in
    YAML too), so this accepts a native integer or a numeric string in
    addition. It never falls through to plain ``int()`` coercion on a
    float, which truncates a non-integral value (``1.9`` to ``1``)
    instead of rejecting it. Callers that require the result to be
    non-negative (every current one does) check that separately, with
    their own message, since this function alone cannot tell an allele
    ID from a deme ID from the message text.

    Args:
        message: The exact error message to raise for any invalid input;
            callers keep their own wording since the same identifier
            shape is parsed in more than one context.
        raw_value: The raw mapping key or value to parse.

    Returns:
        The parsed identifier.

    Raises:
        ValueError: If ``raw_value`` is a boolean, a non-integral float, a
            non-numeric string, or any other non-integer type.
    """
    if isinstance(raw_value, bool):
        raise ValueError(message)
    if isinstance(raw_value, int):
        return raw_value
    if isinstance(raw_value, float):
        if not raw_value.is_integer():
            raise ValueError(message)
        return int(raw_value)
    if isinstance(raw_value, str):
        try:
            return int(raw_value)
        except ValueError as error:
            raise ValueError(message) from error
    raise ValueError(message)
