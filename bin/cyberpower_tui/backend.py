"""The command-line reader, wrapped as a plain Python object.

This is the *only* module in the package that knows how to invoke the reader.
Everything above it deals in dictionaries and lists, so the UI never parses
text and never learns about hidraw, pwrstat or NUT.

The reader's contract, as consumed here:

* ``status``  -> one JSON object (see the reader's module docstring)
* ``config``  -> ``{"settings": [...]}``, one entry per tunable setting
* ``set``     -> a short human line, exit status 0 accepted / 1 rejected
* actions    -> a short human line, exit status 0 sent / non-zero failed
"""

from __future__ import annotations

import json
import os
import subprocess
import time

from .diagnostics import DIAG

#: Default to the reader that sits next to this package.
DEFAULT_READER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cyberpower-ups"
)

#: Device actions, in menu order: (key the reader understands, label, blurb).
ACTIONS = (
    (
        "quick",
        "Run a quick battery test",
        "Switches the UPS to battery for a few seconds; the unit beeps.",
    ),
    (
        "deep",
        "Run a deep battery test",
        "Discharges the battery further; can take a minute or more.",
    ),
    (
        "abort",
        "Abort a running battery test",
        "Stops a test in progress and returns to normal operation.",
    ),
    ("mute", "Mute the alarm", "Silences the alarm until the next power event."),
)


class BackendError(Exception):
    """The reader could not answer: missing device, bad JSON or a timeout."""


class Backend:
    """Runs the reader and normalises its output."""

    def __init__(
        self,
        reader: str = DEFAULT_READER,
        source: str = "auto",
        ups: str | None = None,
        debug: bool = False,
    ):
        self.reader = reader
        self.source = source
        self.ups = ups
        self.debug = debug

    # ------------------------------------------------------------ plumbing

    def _argv(self, *args: str) -> list[str]:
        argv = [self.reader, *args, "--source", self.source]
        if self.ups:
            argv += ["--ups", self.ups]
        if self.debug:
            argv.append("--debug")  # let the reader trace its own reads too
        return argv

    def _run(self, *args: str, timeout: float = 15.0) -> subprocess.CompletedProcess:
        argv = self._argv(*args)
        started = time.monotonic()
        try:
            result = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout
            )
        except FileNotFoundError as error:
            DIAG.debug(f"{' '.join(argv)} -> reader not found")
            raise BackendError(f"reader not found: {self.reader}") from error
        except subprocess.TimeoutExpired:
            DIAG.debug(f"{' '.join(argv)} -> timed out after {timeout:g}s")
            raise BackendError(f"the reader timed out after {timeout:g}s")
        except OSError as error:
            DIAG.debug(f"{' '.join(argv)} -> {error}")
            raise BackendError(str(error)) from error
        note = ""
        if result.returncode != 0:
            note = f" stderr={((result.stderr or '').strip() or '(empty)')!r}"
        DIAG.debug(
            f"{' '.join(argv)} -> rc={result.returncode} "
            f"{(time.monotonic() - started) * 1000:.0f} ms{note}"
        )
        return result

    @staticmethod
    def _error_text(result: subprocess.CompletedProcess) -> str:
        lines = [
            line
            for line in (result.stderr or result.stdout).strip().splitlines()
            if not line.startswith("traceback written to")
        ]
        return lines[-1] if lines else f"exit {result.returncode}"

    # --------------------------------------------------------------- reads

    def status(self) -> dict:
        """The UPS's current readings. Raises BackendError on failure."""
        result = self._run("status", timeout=10.0)
        if result.returncode != 0:
            raise BackendError(self._error_text(result))
        try:
            return json.loads(result.stdout)
        except ValueError as error:
            raise BackendError(f"the reader returned invalid JSON: {error}") from error

    def settings(self) -> list[dict]:
        """The UPS's tunable settings. Raises BackendError on failure."""
        result = self._run("config", timeout=15.0)
        if result.returncode != 0:
            raise BackendError(self._error_text(result))
        try:
            return list(json.loads(result.stdout).get("settings", []))
        except ValueError as error:
            raise BackendError(f"the reader returned invalid JSON: {error}") from error

    def history(self, limit: int = 400) -> list[dict]:
        """Recorded samples and events, oldest first. Empty when nothing recorded.

        A missing or unreadable history is not an error: the panel just has
        nothing to show until something runs `cyberpower-ups watch`.
        """
        result = self._run("history", "--json", "--limit", str(limit), timeout=10.0)
        if result.returncode != 0:
            raise BackendError(self._error_text(result))
        records = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue  # a torn line is not fatal
            if isinstance(record, dict) and record.get("kind"):
                records.append(record)
        return records

    def energy(self) -> dict:
        """The energy meter: kWh and cost for the total, today and the week."""
        return self._energy_from(self._run("energy", "--json", timeout=15.0))

    def set_energy(self, cost_per_kwh: float) -> dict:
        """Write the tariff, and return the re-priced summary."""
        result = self._run(
            "energy", "--json", "--cost-per-kwh", f"{cost_per_kwh:g}", timeout=15.0
        )
        return self._energy_from(result)

    @staticmethod
    def _energy_from(result: subprocess.CompletedProcess) -> dict:
        if result.returncode != 0:
            raise BackendError(Backend._error_text(result))
        try:
            summary = json.loads(result.stdout)
        except ValueError as error:
            raise BackendError(f"the reader returned invalid JSON: {error}") from error
        if not isinstance(summary, dict):
            raise BackendError("the reader returned no energy summary")
        return summary

    # -------------------------------------------------------------- writes

    def set_value(self, key: str, value) -> tuple[bool, str]:
        """Write one setting. Returns (accepted, reader's message)."""
        result = self._run("set", str(key), str(value), timeout=15.0)
        message = (result.stdout or result.stderr).strip()
        return result.returncode == 0, message or "done"

    def run_action(self, key: str) -> tuple[bool, str]:
        """Fire a device action. Returns (sent, reader's message)."""
        result = self._run(key, timeout=25.0)
        message = (result.stdout or result.stderr).strip()
        return result.returncode == 0, message or "done"
