"""Central logging configuration for both front ends (`doc/fim-logging-
design.md` §3.2).

Every module under `fim` logs via `logging.getLogger(__name__)` alone
and never touches handler or level configuration itself (`fim/__init__.
py`'s own `NullHandler` is what keeps that safe by default). This is
the one place that configuration actually happens — called once, with
already-parsed values, by each of the three real entry points:
`fim.cli.main` (from its own `-l`/`-L` arguments), `fim.launcher.main`
and `fim.gui.app.main` (from `FIM_LOG_LEVEL`/`FIM_LOG_OPTIONS`).
`configure` is idempotent: calling it more than once replaces the
`fim` logger's own handlers rather than accumulating them, so a test
(or a future caller) that calls it twice never sees doubled output.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from fim import paths

LOGGER_NAME: Final = "fim"

DEFAULT_FILE_FORMAT: Final = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DEFAULT_STREAM_FORMAT: Final = "%(levelname)s: %(message)s"
DEFAULT_MAX_BYTES: Final = 1_048_576
DEFAULT_BACKUP_COUNT: Final = 5

# `warn` is the one alias accepted beyond `logging`'s own level names —
# the short form most command-line tools already use.
_LEVEL_ALIASES: Final[Mapping[str, str]] = {"WARN": "WARNING"}

# Every key `-L`/`--log-options` (and the GUI's own `FIM_LOG_OPTIONS`)
# accepts — see `doc/fim-logging-design.md` §4.2 for what each means.
VALID_OPTION_KEYS: Final[frozenset[str]] = frozenset(
    {
        "file",
        "stream",
        "file_level",
        "stream_level",
        "format",
        "max_bytes",
        "backup_count",
    }
)


def resolve_level(level: str | int) -> int:
    """Resolve a `-l`-style level name (or an already-numeric level).

    Args:
        level: One of `logging`'s own level names, case-insensitively,
            plus the short alias `warn` for `warning` — or an int, taken
            as an already-resolved `logging` level (e.g. `logging.DEBUG`)
            and returned unchanged.

    Returns:
        The matching `logging` module-level integer constant.

    Raises:
        ValueError: If `level` is a string that names no known level.
    """
    if isinstance(level, int):
        return level
    name = level.strip().upper()
    name = _LEVEL_ALIASES.get(name, name)
    mapping = logging.getLevelNamesMapping()
    if name not in mapping or name == "NOTSET":
        valid = ", ".join(
            sorted({key.lower() for key in mapping if key != "NOTSET"} | {"warn"})
        )
        raise ValueError(f"unknown log level {level!r} (expected one of: {valid})")
    return mapping[name]


def parse_log_options(text: str | None) -> dict[str, str]:
    """Parse a `-L`/`--log-options`-style `key=value[,key=value]...` string.

    Every value is kept as its raw string; `configure` below is what
    interprets each one (an int for `max_bytes`/`backup_count`, a level
    name for `*_level`, and so on) — this function's only job is
    splitting the flag's own text and rejecting a key nothing
    recognizes, the same "a typo is a plain command-line error, not a
    silently wrong log destination" contract `doc/fim-logging-design.md`
    §4.2 documents.

    Args:
        text: The flag's raw value, or `None`/empty for no options at
            all (an empty mapping).

    Returns:
        One string value per recognized key actually present.

    Raises:
        ValueError: If an entry has no `=`, or names a key not in
            `VALID_OPTION_KEYS`.
    """
    if not text:
        return {}
    options: dict[str, str] = {}
    for raw_entry in text.split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise ValueError(
                f"invalid --log-options entry {entry!r} (expected key=value)"
            )
        raw_key, _, value = entry.partition("=")
        key = raw_key.strip()
        if key not in VALID_OPTION_KEYS:
            valid = ", ".join(sorted(VALID_OPTION_KEYS))
            raise ValueError(
                f"unknown --log-options key {key!r} (expected one of: {valid})"
            )
        options[key] = value.strip()
    return options


def configure(
    level: str | int = "warning", options: Mapping[str, str] | None = None
) -> None:
    """Configure the `fim` logger's own handlers, level, and the `warnings` bridge.

    Builds a rotating file handler (`logs/fim.log` under the resolved
    project root by default — `fim.paths.default_log_file`) and a
    stderr stream handler, each independently disable-able and
    level-overridable via `options` (`doc/fim-logging-design.md` §4.2),
    attaches whichever are active to the `fim` logger (replacing any
    handler a previous call left there), and enables `logging.
    captureWarnings` so `warnings.warn` obeys the same configuration
    (§8) instead of writing to stderr directly.

    Args:
        level: The base level (`-l`'s own value) — see `resolve_level`.
            Applies to both handlers unless `options["file_level"]`/
            `options["stream_level"]` overrides one specifically.
        options: Already-split key/value pairs (`parse_log_options`'s
            own return shape) — every key optional, every default
            documented in `doc/fim-logging-design.md` §4.2.

    Raises:
        ValueError: If `level`, `options["file_level"]`, or
            `options["stream_level"]` names an unknown level, or
            `options["max_bytes"]`/`options["backup_count"]` is not an
            integer.
    """
    resolved_options = options or {}
    base_level = resolve_level(level)
    file_level = (
        resolve_level(resolved_options["file_level"])
        if "file_level" in resolved_options
        else base_level
    )
    stream_level = (
        resolve_level(resolved_options["stream_level"])
        if "stream_level" in resolved_options
        else base_level
    )
    formatter_text = resolved_options.get("format")

    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    handlers: list[logging.Handler] = []

    if resolved_options.get("file") != "none":
        file_path = (
            Path(resolved_options["file"])
            if "file" in resolved_options
            else paths.default_log_file()
        )
        # Created eagerly, at configure() time, rather than relying on
        # `RotatingFileHandler`'s own lazy-open behavior: a directory
        # that cannot be created should fail loudly at startup, not on
        # this run's first log call, wherever in the program that
        # happens to land.
        file_path.parent.mkdir(parents=True, exist_ok=True)
        max_bytes = int(resolved_options.get("max_bytes", DEFAULT_MAX_BYTES))
        backup_count = int(resolved_options.get("backup_count", DEFAULT_BACKUP_COUNT))
        file_handler = logging.handlers.RotatingFileHandler(
            file_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(file_level)
        file_handler.setFormatter(
            logging.Formatter(formatter_text or DEFAULT_FILE_FORMAT)
        )
        handlers.append(file_handler)

    if resolved_options.get("stream") != "none":
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setLevel(stream_level)
        stream_handler.setFormatter(
            logging.Formatter(formatter_text or DEFAULT_STREAM_FORMAT)
        )
        handlers.append(stream_handler)

    for handler in handlers:
        logger.addHandler(handler)
    # The logger's own level gates every record before any handler ever
    # sees it, so it must be at least as permissive as the most verbose
    # active handler — each handler's own `setLevel` above still applies
    # its own, possibly stricter, filtering on top of that.
    effective_level = min((handler.level for handler in handlers), default=base_level)
    logger.setLevel(effective_level)

    # `logging.captureWarnings(True)` alone is not enough to route a
    # `warnings.warn` call into the handlers above: it redirects every
    # warning into a logger named exactly `py.warnings`, a sibling of
    # `fim`, not a child of it -- so it inherits no handler from `fim`
    # and (since nothing else configures it) reaches nothing at all,
    # confirmed directly against a real `warnings.warn` call before this
    # comment was written. Attaching this same handler set there too,
    # rather than to the true root logger, keeps the "only `fim`'s own
    # configuration is touched" property this module's own docstring
    # promises -- a library-style caller that configures its own root
    # logger separately is still never overridden.
    warnings_logger = logging.getLogger("py.warnings")
    for handler in list(warnings_logger.handlers):
        warnings_logger.removeHandler(handler)
    for handler in handlers:
        warnings_logger.addHandler(handler)
    warnings_logger.setLevel(effective_level)
    # `logging.captureWarnings(True)` only actually overrides `warnings.
    # showwarning` the *first* time it is called: internally it tracks
    # "already capturing" in a module-level flag and no-ops on every
    # later call while that flag is set, even if something else (pytest's
    # own per-test warnings isolation, notably) reset `showwarning` back
    # to a different function in between -- confirmed directly: a second
    # `configure()` call inside a pytest test left warnings uncaptured
    # without this toggle. Calling `False` first clears that internal
    # flag unconditionally, so the following `True` always re-establishes
    # the override for real, regardless of what changed it since the
    # last `configure()` call.
    logging.captureWarnings(False)
    logging.captureWarnings(True)
