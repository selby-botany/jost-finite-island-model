"""Apply the GUI worker's lowered operating-system priority."""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)
Priority = Literal["normal", "lower", "lowest"]


def apply_priority(priority: Priority) -> None:
    """Apply a best-effort child-process priority before starting work."""
    if priority == "normal":
        return
    try:
        if sys.platform == "win32":
            kernel32 = ctypes.windll.kernel32
            process = kernel32.GetCurrentProcess()
            priority_class = 0x00004000 if priority == "lower" else 0x00000040
            if not kernel32.SetPriorityClass(process, priority_class):
                raise OSError(ctypes.get_last_error(), "SetPriorityClass failed")
        elif sys.platform == "linux":
            if not hasattr(os, "setpriority") or not hasattr(os, "PRIO_PROCESS"):
                raise OSError("process priority is unavailable")
            nice_value = 10 if priority == "lower" else 19
            thread_paths = Path("/proc/self/task").iterdir()
            for thread_path in thread_paths:
                try:
                    os.setpriority(os.PRIO_PROCESS, int(thread_path.name), nice_value)
                except ProcessLookupError:
                    continue
        elif sys.platform == "darwin" and priority == "lowest":
            process_selector = getattr(os, "PRIO_DARWIN_PROCESS", 4)
            background_priority = getattr(os, "PRIO_DARWIN_BG", 0x1000)
            os.setpriority(process_selector, 0, background_priority)
        elif hasattr(os, "setpriority") and hasattr(os, "PRIO_PROCESS"):
            nice_value = 10 if priority == "lower" else 19
            os.setpriority(os.PRIO_PROCESS, 0, nice_value)
        else:
            raise OSError("process priority is unavailable")
    except OSError as error:
        logger.debug("could not apply GUI job priority %s: %s", priority, error)


def apply_lower_priority() -> None:
    """Retain the historical lower-priority entry point."""
    apply_priority("lower")


def initialize_pool_worker(priority: Priority = "lower") -> None:
    """Apply a spawned sweep worker's priority before it starts its point."""
    apply_priority(priority)
