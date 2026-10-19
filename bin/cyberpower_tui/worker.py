"""Runs the reader off the event loop's thread.

Every reading and every write is a subprocess that talks to the UPS over USB,
which costs a fraction of a second to a couple of seconds. Doing that inside the
curses loop freezes the whole interface for that long — which is exactly what it
feels like when the cursor lags behind the keyboard, or when a value takes
noticeably longer to change than the key you pressed.

So the loop only ever *submits* work; a worker thread runs it and hands the
results back through a queue that the loop drains once per frame.

Jobs are coalesced by **slot**: the latest job submitted for a slot replaces any
that is still waiting. Hammering `→` on one setting therefore writes only the
final value, and a slow status poll can never queue up behind itself.
"""

from __future__ import annotations

import queue
import threading

from .backend import BackendError


class Worker:
    """A single background thread with slot-coalesced, prioritised jobs."""

    def __init__(self, backend):
        self.backend = backend
        self._urgent: dict = {}
        self._normal: dict = {}
        self._urgent_order: list = []
        self._normal_order: list = []
        self._lock = threading.Lock()
        self._results: queue.Queue = queue.Queue()
        self._wake = threading.Event()
        self._stopping = False
        self._thread = threading.Thread(target=self._run, name="cyberpower-reader",
                                        daemon=True)
        self._thread.start()

    # ---------------------------------------------------------- submitting
    def submit(self, slot: str, function, *args, urgent: bool = False) -> None:
        """Queue `function(*args)`; replaces any waiting job for `slot`."""
        with self._lock:
            jobs, order = (self._urgent, self._urgent_order) if urgent \
                else (self._normal, self._normal_order)
            if slot not in jobs:
                order.append(slot)
            jobs[slot] = (function, args)
        self._wake.set()

    def cancel(self, slot: str) -> None:
        with self._lock:
            for jobs, order in ((self._urgent, self._urgent_order),
                                (self._normal, self._normal_order)):
                if jobs.pop(slot, None) is not None:
                    try:
                        order.remove(slot)
                    except ValueError:
                        pass

    # ------------------------------------------------------------ results
    def take_results(self) -> list[tuple[str, object, str | None]]:
        """Everything finished since the last call: (slot, result, error)."""
        results = []
        while True:
            try:
                results.append(self._results.get_nowait())
            except queue.Empty:
                return results

    def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        self._thread.join(timeout=2.0)

    # -------------------------------------------------------------- thread
    def _next(self):
        with self._lock:
            for jobs, order in ((self._urgent, self._urgent_order),
                                (self._normal, self._normal_order)):
                if order:
                    slot = order.pop(0)
                    function, args = jobs.pop(slot, (None, None))
                    if function is not None:
                        return slot, function, args
        return None

    def _run(self) -> None:
        while not self._stopping:
            job = self._next()
            if job is None:
                self._wake.wait(0.2)
                self._wake.clear()
                continue
            slot, function, args = job
            try:
                result, error = function(*args), None
            except BackendError as problem:
                result, error = None, str(problem)
            except Exception as problem:  # noqa: BLE001 - never kill the worker
                result, error = None, f"{type(problem).__name__}: {problem}"
            self._results.put((slot, result, error))


class SyncWorker:
    """A Worker that runs each job inline, so tests stay deterministic."""

    def __init__(self, backend):
        self.backend = backend
        self._results: list[tuple[str, object, str | None]] = []

    def submit(self, slot, function, *args, urgent: bool = False) -> None:
        try:
            self._results.append((slot, function(*args), None))
        except BackendError as problem:
            self._results.append((slot, None, str(problem)))

    def cancel(self, slot) -> None:
        return

    def take_results(self):
        results, self._results = self._results, []
        return results

    def stop(self) -> None:
        return
