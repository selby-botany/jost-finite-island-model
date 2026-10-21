"""Apply the GUI worker's lowered operating-system priority."""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def apply_lower_priority() -> None:
    """Lower this process and its existing threads where the platform allows."""
    try:
        if sys.platform == "win32":
            kernel32 = ctypes.windll.kernel32
            process = kernel32.GetCurrentProcess()
            if not kernel32.SetPriorityClass(process, 0x00004000):
                raise OSError(ctypes.get_last_error(), "SetPriorityClass failed")
        elif sys.platform == "linux":
            if not hasattr(os, "setpriority") or not hasattr(os, "PRIO_PROCESS"):
                raise OSError("process priority is unavailable")
            thread_paths = Path("/proc/self/task").iterdir()
            for thread_path in thread_paths:
                try:
                    os.setpriority(os.PRIO_PROCESS, int(thread_path.name), 10)
                except ProcessLookupError:
                    continue
        elif hasattr(os, "setpriority") and hasattr(os, "PRIO_PROCESS"):
            os.setpriority(os.PRIO_PROCESS, 0, 10)
        else:
            raise OSError("process priority is unavailable")
    except OSError as error:
        logger.debug("could not lower GUI job priority: %s", error)


def initialize_pool_worker() -> None:
    """Lower a spawned sweep worker before it starts running sweep points."""
    apply_lower_priority()
