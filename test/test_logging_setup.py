"""Unit tests for `fim.logging_setup` (`doc/fim-logging-design.md` §11).

Every `configure()` call in this file passes an explicit `file=`
pointing under `tmp_path`, never the real default (`fim.paths.
default_log_file()`) — a test suite run must never create or write to
this project's own `logs/` directory as a side effect. `_isolate_fim_
logger` (autouse, below) restores the `fim` logger's own handlers,
level, and propagation after every test, so no test in this file can
leak a handler into a later, unrelated test elsewhere in the suite.
"""

from __future__ import annotations

import logging
import logging.handlers
import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest

from fim import logging_setup


@pytest.fixture(autouse=True)
def _isolate_fim_logger() -> Iterator[None]:
    """Snapshot and restore the `fim`/`py.warnings` loggers' own state.

    `configure()` touches both (`py.warnings` is where
    `logging.captureWarnings` routes every `warnings.warn` call — see
    its own docstring) — both must be restored, or a handler this file
    attaches could leak into an unrelated later test.
    """
    loggers = [
        logging.getLogger(logging_setup.LOGGER_NAME),
        logging.getLogger("py.warnings"),
    ]
    snapshots = [
        (list(logger.handlers), logger.level, logger.propagate) for logger in loggers
    ]
    try:
        yield
    finally:
        for logger, (original_handlers, original_level, original_propagate) in zip(
            loggers, snapshots, strict=True
        ):
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logger.addHandler(handler)
            logger.setLevel(original_level)
            logger.propagate = original_propagate


def test_resolve_level_accepts_every_standard_name_case_insensitively() -> None:
    """Every `logging` level name resolves, regardless of case."""
    assert logging_setup.resolve_level("debug") == logging.DEBUG
    assert logging_setup.resolve_level("INFO") == logging.INFO
    assert logging_setup.resolve_level("Warning") == logging.WARNING
    assert logging_setup.resolve_level("error") == logging.ERROR
    assert logging_setup.resolve_level("CRITICAL") == logging.CRITICAL


def test_resolve_level_accepts_the_warn_alias() -> None:
    """`warn` is accepted as the short form of `warning`."""
    assert logging_setup.resolve_level("warn") == logging.WARNING
    assert logging_setup.resolve_level("WARN") == logging.WARNING


def test_resolve_level_passes_through_an_already_numeric_level() -> None:
    """An int is returned unchanged, not re-interpreted."""
    assert logging_setup.resolve_level(logging.DEBUG) == logging.DEBUG


def test_resolve_level_rejects_an_unknown_name() -> None:
    """A typo is a plain `ValueError`, naming the input and the valid set."""
    with pytest.raises(ValueError, match="unknown log level 'verbose'"):
        logging_setup.resolve_level("verbose")


def test_resolve_level_rejects_notset_explicitly() -> None:
    """`NOTSET` is a real `logging` level name but not a valid `-l` value.

    `logging`'s own `NOTSET` means "defer to a parent logger," a
    configuration nuance this project's own `-l` flag does not expose —
    accepting it here would silently produce a `fim` logger with no
    effective level of its own.
    """
    with pytest.raises(ValueError, match="unknown log level 'notset'"):
        logging_setup.resolve_level("notset")


def test_parse_log_options_returns_an_empty_mapping_for_none_or_empty() -> None:
    """No `-L` at all parses to no options, not an error."""
    assert logging_setup.parse_log_options(None) == {}
    assert logging_setup.parse_log_options("") == {}


def test_parse_log_options_splits_every_recognized_key() -> None:
    """Every documented key round-trips through parsing untouched."""
    parsed = logging_setup.parse_log_options(
        "file=/tmp/x.log,stream=none,file_level=debug,"
        "stream_level=error,format=%(message)s,max_bytes=2048,backup_count=3"
    )
    assert parsed == {
        "file": "/tmp/x.log",
        "stream": "none",
        "file_level": "debug",
        "stream_level": "error",
        "format": "%(message)s",
        "max_bytes": "2048",
        "backup_count": "3",
    }


def test_parse_log_options_strips_surrounding_whitespace() -> None:
    """Whitespace around a key, value, or entry is not significant."""
    assert logging_setup.parse_log_options(" file = /tmp/x.log , stream = none ") == {
        "file": "/tmp/x.log",
        "stream": "none",
    }


def test_parse_log_options_rejects_an_entry_with_no_equals_sign() -> None:
    """A bare `key` with no `=value` is a plain, named `ValueError`."""
    with pytest.raises(ValueError, match="invalid --log-options entry 'debug'"):
        logging_setup.parse_log_options("debug")


def test_parse_log_options_rejects_an_unknown_key() -> None:
    """A typo'd key is rejected outright, never silently ignored."""
    with pytest.raises(ValueError, match="unknown --log-options key 'leveel'"):
        logging_setup.parse_log_options("leveel=debug")


def test_configure_attaches_a_file_and_a_stream_handler_by_default(
    tmp_path: Path,
) -> None:
    """The default configuration is both handlers, at the requested level."""
    log_file = tmp_path / "fim.log"
    logging_setup.configure("info", {"file": str(log_file)})

    logger = logging.getLogger(logging_setup.LOGGER_NAME)
    assert len(logger.handlers) == 2
    kinds = {type(handler) for handler in logger.handlers}
    assert logging.handlers.RotatingFileHandler in kinds
    assert logging.StreamHandler in kinds
    assert logger.level == logging.INFO
    assert log_file.is_file()


def test_configure_is_idempotent_and_never_doubles_handlers(tmp_path: Path) -> None:
    """Calling `configure()` twice replaces handlers, never accumulates them."""
    log_file = tmp_path / "fim.log"
    logging_setup.configure("info", {"file": str(log_file)})
    logging_setup.configure("debug", {"file": str(log_file)})

    logger = logging.getLogger(logging_setup.LOGGER_NAME)
    assert len(logger.handlers) == 2
    assert logger.level == logging.DEBUG


def test_configure_file_none_disables_the_file_handler(tmp_path: Path) -> None:
    """`file=none` leaves only the stream handler active."""
    logging_setup.configure("info", {"file": "none"})

    logger = logging.getLogger(logging_setup.LOGGER_NAME)
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.StreamHandler)


def test_configure_stream_none_disables_the_stream_handler(tmp_path: Path) -> None:
    """`stream=none` leaves only the file handler active."""
    log_file = tmp_path / "fim.log"
    logging_setup.configure("info", {"file": str(log_file), "stream": "none"})

    logger = logging.getLogger(logging_setup.LOGGER_NAME)
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.handlers.RotatingFileHandler)


def test_configure_file_level_and_stream_level_override_independently(
    tmp_path: Path,
) -> None:
    """`file_level`/`stream_level` override `-l`'s value per handler."""
    log_file = tmp_path / "fim.log"
    logging_setup.configure(
        "warning",
        {"file": str(log_file), "file_level": "debug", "stream_level": "error"},
    )

    logger = logging.getLogger(logging_setup.LOGGER_NAME)
    levels = {type(handler): handler.level for handler in logger.handlers}
    assert levels[logging.handlers.RotatingFileHandler] == logging.DEBUG
    assert levels[logging.StreamHandler] == logging.ERROR
    # The logger's own level must be at least as permissive as the most
    # verbose active handler, or DEBUG records would never reach the
    # file handler at all (§3.2's own documented reasoning).
    assert logger.level == logging.DEBUG


def test_configure_rejects_a_non_integer_max_bytes(tmp_path: Path) -> None:
    """A malformed `max_bytes` is a plain `ValueError`, not a crash mid-write."""
    with pytest.raises(ValueError, match="invalid literal"):
        logging_setup.configure(
            "info", {"file": str(tmp_path / "fim.log"), "max_bytes": "not-a-number"}
        )


def test_configure_creates_the_log_directory_if_missing(tmp_path: Path) -> None:
    """A missing parent directory is created, not treated as an error."""
    log_file = tmp_path / "nested" / "logs" / "fim.log"
    logging_setup.configure("info", {"file": str(log_file)})

    assert log_file.parent.is_dir()


def test_configure_applies_a_custom_format_to_both_handlers(tmp_path: Path) -> None:
    """`format=` overrides the default formatter on every active handler."""
    log_file = tmp_path / "fim.log"
    logging_setup.configure(
        "info", {"file": str(log_file), "format": "CUSTOM:%(message)s"}
    )
    logging.getLogger("fim.test_logging_setup").info("hello")

    assert "CUSTOM:hello" in log_file.read_text(encoding="utf-8")


def test_configure_enables_warnings_capture_through_the_file_handler(
    tmp_path: Path,
) -> None:
    """A real `warnings.warn` call reaches the configured file handler.

    Confirms the `logging.captureWarnings` bridge is actually active,
    end to end, rather than only asserting the function was called.
    """
    log_file = tmp_path / "fim.log"
    logging_setup.configure("warning", {"file": str(log_file)})

    with warnings.catch_warnings():
        warnings.simplefilter("always")
        warnings.warn("a captured test warning", UserWarning, stacklevel=1)

    assert "a captured test warning" in log_file.read_text(encoding="utf-8")
