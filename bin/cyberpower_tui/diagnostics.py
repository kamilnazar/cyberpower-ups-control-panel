"""Crash and debug logs for the control panel.

The reader already keeps its own crash log, because it is usually started by the
bar widget where stderr is discarded. The panel has the same problem for a
different reason: launched from the bar it runs inside
``omarchy-launch-terminal``, and that terminal disappears the moment the panel
exits, taking the traceback with it.

So the panel keeps its own trace, in the same state directory as the reader:

* ``crash.log`` — every traceback from the panel, always written.
* ``debug.log`` — every reader invocation the panel makes (arguments, duration,
  exit code, stderr), only when ``--debug`` or ``CYBERPOWER_UPS_DEBUG=1``.

Deliberately a near-copy of the reader's small ``Diag`` class rather than a
shared import: the reader must stay runnable on its own, and the panel must stay
a pure front end that never imports it.
"""

from __future__ import annotations

import os
import sys
import time
import traceback

LOG_MAX_BYTES = 256 * 1024


def state_dir() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "cyberpower-ups")


def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class Diag:
    """Crash traces and an opt-in debug trace, in the state directory."""

    def __init__(self) -> None:
        self.debug_enabled = False

    @staticmethod
    def path(name: str) -> str:
        return os.path.join(state_dir(), name)

    def _append(self, name: str, text: str) -> None:
        path = self.path(name)
        try:
            os.makedirs(state_dir(), exist_ok=True)
            if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
                os.replace(path, path + ".1")      # keep one older generation
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            pass                                   # logging must never raise

    def debug(self, message: str) -> None:
        if not self.debug_enabled:
            return
        self._append("debug.log", f"{_stamp()} [tui {os.getpid()}] {message}\n")

    def crash(self, error: BaseException) -> str:
        """Record a traceback; returns the file it went to."""
        text = (f"{_stamp()} [tui {os.getpid()}] crashed: "
                f"{' '.join(sys.argv[1:])}\n"
                + "".join(traceback.format_exception(type(error), error,
                                                    error.__traceback__)))
        self._append("crash.log", text + "\n")
        return self.path("crash.log")


DIAG = Diag()
