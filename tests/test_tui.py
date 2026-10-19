#!/usr/bin/env python3
"""Tests for the terminal control panel (the cyberpower_tui package).

No UPS and no terminal are needed. The reader is replaced by a tiny stub
script, so the backend's CLI contract is exercised for real (subprocess and
all), and the drawing code is driven into a fake curses screen whose character
grid can be inspected as plain text.

Run: python3 tests/test_tui.py
"""

import copy
import curses
import os
import stat
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(HERE, "..", "bin")
sys.path.insert(0, BIN)

from cyberpower_tui import formats as F           # noqa: E402
from cyberpower_tui import widgets as W           # noqa: E402
from cyberpower_tui.app import App, EditDialog    # noqa: E402
from cyberpower_tui.backend import Backend, BackendError  # noqa: E402
from cyberpower_tui.worker import SyncWorker, Worker       # noqa: E402


# --------------------------------------------------------------------------
# a curses screen that keeps its characters in a grid
# --------------------------------------------------------------------------

class FakeScreen:
    def __init__(self, height, width):
        self.height, self.width = height, width
        self.buffer = [[" "] * width for _ in range(height)]

    def getmaxyx(self):
        return self.height, self.width

    def erase(self):
        self.buffer = [[" "] * self.width for _ in range(self.height)]

    def clear(self):
        self.erase()

    def refresh(self):
        pass

    def move(self, *args, **kwargs):
        pass

    def timeout(self, *args, **kwargs):
        pass

    def addstr(self, row, col, text, attr=0):
        if row < 0 or row >= self.height:
            return
        for offset, char in enumerate(str(text)):
            x = col + offset
            if 0 <= x < self.width:
                self.buffer[row][x] = char

    def get_wch(self):
        raise curses.error()

    def text(self):
        return "\n".join("".join(row).rstrip() for row in self.buffer)

    def lines(self):
        return self.text().splitlines()


# --------------------------------------------------------------------------
# a stand-in for the reader, plus a stub script for the backend tests
# --------------------------------------------------------------------------

class StubBackend:
    """Implements the small surface App uses, without spawning anything."""

    def __init__(self, status=None, settings=(), history=()):
        self.reader = "/stub/reader"
        self.source = "test"
        self.ups = None
        self._status = status
        self._settings = copy.deepcopy(list(settings))
        self._history = copy.deepcopy(list(history))
        self.fail_keys = set()
        self.sets = []
        self.actions = []

    def status(self):
        return self._status

    def settings(self):
        return copy.deepcopy(self._settings)

    def history(self, limit=400):
        return copy.deepcopy(self._history)

    def set_value(self, key, value):
        self.sets.append((key, value))
        if key in self.fail_keys:
            return False, "the UPS rejected that setting"
        return True, f"{key} = {value}"

    def run_action(self, key):
        self.actions.append(key)
        return True, f"{key} sent"


STATUS = {
    "model": "CPS TEST",
    "status": "fullyCharged",
    "battery": 100,
    "runtimeMin": 40,
    "loadPercent": 23,
    "loadWatts": 233,
    "inputVoltage": 230.0,
    "inputFrequency": 50.0,
    "outputVoltage": 230.0,
    "outputFrequency": 50.0,
    "batteryVoltage": 26.0,
    "testResult": "Passed",
}

SETTINGS = [
    {"key": "alarm", "label": "Audible alarm", "value": 2, "valueLabel": "enabled",
     "choices": {"1": "disabled", "2": "enabled", "3": "muted"},
     "min": 1, "max": 3, "step": 1, "offAllowed": False, "writable": True},
    {"key": "runtimeLimit", "label": "Shut down at runtime", "value": 300,
     "unit": "s", "min": 0, "max": 600, "step": 1, "offAllowed": False,
     "writable": True},
    {"key": "delayShutdown", "label": "Delay before shutdown", "value": None,
     "unit": "s", "min": 0, "max": 1966020, "step": 60, "offAllowed": True,
     "writable": True},
]

STUB_BODY = """
import json, sys
command = sys.argv[1] if len(sys.argv) > 1 else "status"
if command == "status":
    print(json.dumps({"model": "STUB", "battery": 42}))
elif command == "config":
    print(json.dumps({"settings": [{"key": "alarm"}]}))
elif command == "set":
    print("%s = %s" % (sys.argv[2], sys.argv[3]))
elif command == "history":
    print('{"t": 1, "kind": "sample", "inputVoltage": 231.0}')
    print('{"t": 2, "kind": "event", "text": "Mains power lost", "level": "bad"}')
    print("not json at all")
else:
    print("sent " + command)
"""


#: A short recording: alternating samples and the two events between them.
HISTORY = [
    {"t": 1792400000, "kind": "sample", "inputVoltage": 229.0, "outputVoltage": 229.0,
     "loadPercent": 14, "battery": 100, "runtimeMin": 60},
    {"t": 1792400060, "kind": "sample", "inputVoltage": 231.0, "outputVoltage": 231.0,
     "loadPercent": 15, "battery": 100, "runtimeMin": 59},
    {"t": 1792400120, "kind": "event", "event": "mainsLost",
     "text": "Mains power lost (input 0 V)", "level": "bad"},
    {"t": 1792400180, "kind": "sample", "inputVoltage": 236.0, "outputVoltage": 231.0,
     "loadPercent": 16, "battery": 99, "runtimeMin": 50},
    {"t": 1792400240, "kind": "event", "event": "mainsRestored",
     "text": "Mains power restored (input 231 V)", "level": "info"},
]


def _script(directory, body, name="reader"):
    path = os.path.join(directory, name)
    with open(path, "w") as handle:
        handle.write("#!/usr/bin/env python3\n" + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


# --------------------------------------------------------------------------
# formats: the pure text helpers
# --------------------------------------------------------------------------

def test_reading_pairs():
    pairs = dict(F.reading_pairs(STATUS))
    assert pairs["Status"] == "Fully charged", pairs
    assert pairs["Runtime"] == "40 min", pairs
    assert pairs["Load"] == "23 % (233 W)", pairs
    assert pairs["Input voltage"] == "230 V", pairs
    assert pairs["Last test"] == "Passed", pairs
    assert F.reading_pairs(None) == []


def test_severity():
    assert F.severity({"status": "online"}) == "ok"
    assert F.severity({"status": "onBattery"}) == "warn"
    assert F.severity({"onBattery": True}) == "warn"
    assert F.severity({"status": "overload"}) == "bad"
    assert F.severity({"status": "replaceBattery"}) == "bad"


def test_power_path():
    online = F.power_path({"inputVoltage": 230, "loadPercent": 23})
    assert "Mains 230 V" in online and "Load 23 %" in online
    on_battery = F.power_path({"onBattery": True, "loadPercent": 40})
    assert "Mains --" in on_battery, on_battery


def test_battery_fraction():
    assert F.battery_fraction({"battery": 100}) == 1.0
    assert F.battery_fraction({"battery": 50}) == 0.5
    assert F.battery_fraction({}) is None
    assert F.battery_fraction(None) is None


def test_num():
    assert F.num(230.0) == "230"
    assert F.num(50.1) == "50.1"
    assert F.num(None) == ""


def test_setting_value_text():
    assert F.setting_value_text(SETTINGS[0]) == "‹ enabled ›"
    assert F.setting_value_text(SETTINGS[1]) == "300 s"
    assert F.setting_value_text(SETTINGS[2]) == "off"


def test_setting_allowed_text():
    assert F.setting_allowed_text(SETTINGS[0]) == "disabled · enabled · muted"
    assert F.setting_allowed_text(SETTINGS[1]) == "0 – 600 s"
    # The "off" sentinel is advertised, and big numbers are grouped.
    assert F.setting_allowed_text(SETTINGS[2]) == "off · 0 – 1,966,020 s"


def test_cycle_enum_wraps():
    assert F.cycle_enum(SETTINGS[0], 1) == 3          # enabled -> muted
    assert F.cycle_enum(SETTINGS[0], -1) == 1         # enabled -> disabled
    muted = dict(SETTINGS[0], value=3, valueLabel="muted")
    assert F.cycle_enum(muted, 1) == 1                # wrapped back around


def test_step_numeric_clamps():
    assert F.step_numeric(SETTINGS[1], 1) == 301
    assert F.step_numeric(SETTINGS[1], -1) == 299
    top = dict(SETTINGS[1], value=600)
    assert F.step_numeric(top, 1) == 600              # clamped to max
    floor = dict(SETTINGS[1], value=0)
    assert F.step_numeric(floor, -1) == 0             # clamped to min


def test_parse_edit():
    assert F.parse_edit("450") == ("value", 450.0)
    assert F.parse_edit(" 12.5 ") == ("value", 12.5)
    assert F.parse_edit("off") == ("off", None)
    try:
        F.parse_edit("banana")
    except ValueError:
        pass
    else:
        raise AssertionError("a non-number should raise")


def test_clip_and_pad():
    assert W.clip("hello", 10) == "hello"
    assert W.clip("hello world", 8) == "hello w…"
    assert len(W.clip("x" * 100, 10)) == 10
    assert W.clip("anything", 0) == ""
    assert W.pad("hi", 5) == "hi   "
    assert len(W.pad("toolong", 4)) == 4


def test_set_local_value():
    numeric = dict(SETTINGS[1])
    assert F.set_local_value(numeric, 450) is True
    assert numeric["value"] == 450.0

    enum = dict(SETTINGS[0])
    assert F.set_local_value(enum, 3) is True
    assert (enum["value"], enum["valueLabel"]) == (3, "muted")
    assert F.set_local_value(enum, 9) is False      # not a choice
    assert enum["value"] == 3                       # left untouched

    off = dict(SETTINGS[2])
    assert F.set_local_value(off, None) is True
    assert (off["value"], off["valueLabel"]) == (None, "off")


def test_update_from_reply():
    numeric = dict(SETTINGS[1])
    assert F.update_from_reply(numeric, "runtimeLimit = 600") is True
    assert numeric["value"] == 600.0

    enum = dict(SETTINGS[0])
    assert F.update_from_reply(enum, "alarm = 3") is True
    assert (enum["value"], enum["valueLabel"]) == (3, "muted")

    off = dict(SETTINGS[2])
    assert F.update_from_reply(off, "delayShutdown = off") is True
    assert off["value"] is None

    assert F.update_from_reply(numeric, "the UPS rejected that setting") is False
    assert F.update_from_reply(numeric, "somethingElse = 1") is False


# --------------------------------------------------------------------------
# the worker: the UI never blocks on the reader
# --------------------------------------------------------------------------

def _wait_for(worker, count, timeout=5.0):
    deadline = time.monotonic() + timeout
    collected = []
    while len(collected) < count and time.monotonic() < deadline:
        collected.extend(worker.take_results())
        if len(collected) < count:
            time.sleep(0.005)
    return collected


def test_worker_runs_jobs_off_thread():
    worker = Worker(object())
    try:
        worker.submit("job", lambda: 6 * 7)
        results = _wait_for(worker, 1)
        assert results == [("job", 42, None)], results
    finally:
        worker.stop()


def test_worker_coalesces_waiting_jobs():
    """Pressing → ten times must not mean ten writes: the last one wins."""
    worker = Worker(object())
    try:
        release = threading.Event()
        worker.submit("blocker", lambda: release.wait(2.0))
        ran = []
        for value in (1, 2, 3):
            worker.submit("x", lambda value=value: ran.append(value))
        release.set()
        results = _wait_for(worker, 2)
        assert ran == [3], ran
        assert sorted(slot for slot, _, _ in results) == ["blocker", "x"], results
    finally:
        worker.stop()


def test_worker_runs_urgent_jobs_first():
    worker = Worker(object())
    try:
        release = threading.Event()
        worker.submit("blocker", lambda: release.wait(2.0))
        ran = []
        worker.submit("read", lambda: ran.append("read"))
        worker.submit("write", lambda: ran.append("write"), urgent=True)
        release.set()
        _wait_for(worker, 3)
        assert ran == ["write", "read"], ran
    finally:
        worker.stop()


def test_worker_reports_errors_instead_of_dying():
    worker = Worker(object())

    def boom():
        raise RuntimeError("nope")

    try:
        worker.submit("boom", boom)
        worker.submit("after", lambda: "still alive")
        results = dict((slot, (result, error)) for slot, result, error in _wait_for(worker, 2))
        assert results["boom"][0] is None and "nope" in results["boom"][1], results
        assert results["after"] == ("still alive", None), results
    finally:
        worker.stop()


def test_worker_cancel_drops_a_waiting_job():
    worker = Worker(object())
    try:
        release = threading.Event()
        worker.submit("blocker", lambda: release.wait(2.0))
        ran = []
        worker.submit("x", lambda: ran.append("x"))
        worker.cancel("x")
        release.set()
        results = _wait_for(worker, 1)
        assert ran == [], ran
        assert [slot for slot, _, _ in results] == ["blocker"], results
    finally:
        worker.stop()


# --------------------------------------------------------------------------
# backend: it really does shell out to the reader
# --------------------------------------------------------------------------

def test_backend_reads_the_reader():
    with tempfile.TemporaryDirectory() as tmp:
        backend = Backend(_script(tmp, STUB_BODY), source="auto")
        assert backend.status() == {"model": "STUB", "battery": 42}
        assert backend.settings() == [{"key": "alarm"}]


def test_backend_writes_and_actions():
    with tempfile.TemporaryDirectory() as tmp:
        backend = Backend(_script(tmp, STUB_BODY), source="auto")
        assert backend.set_value("alarm", 3) == (True, "alarm = 3")
        ok, message = backend.run_action("quick")
        assert ok and message == "sent quick", (ok, message)


def test_backend_missing_reader():
    try:
        Backend("/does/not/exist/reader").status()
    except BackendError as error:
        assert "not found" in str(error), error
    else:
        raise AssertionError("a missing reader should raise BackendError")


def test_backend_reports_reader_failure():
    with tempfile.TemporaryDirectory() as tmp:
        body = 'import sys\nsys.stderr.write("no UPS on hidraw\\n")\nsys.exit(2)\n'
        backend = Backend(_script(tmp, body), source="auto")
        for call in (backend.status, backend.settings):
            try:
                call()
            except BackendError as error:
                assert "no UPS on hidraw" in str(error), error
            else:
                raise AssertionError("a failing reader should raise BackendError")


def test_backend_history_reads_jsonl_and_skips_damage():
    with tempfile.TemporaryDirectory() as tmp:
        backend = Backend(_script(tmp, STUB_BODY), source="auto")
        records = backend.history(limit=10)
        assert [record["kind"] for record in records] == ["sample", "event"], records
        assert records[1]["text"] == "Mains power lost"


def test_backend_error_text_hides_the_crash_hint():
    import subprocess
    result = subprocess.CompletedProcess(
        ["reader"], 1, "",
        "ValueError: boom\n"
        "traceback written to /home/u/.local/state/cyberpower-ups/crash.log\n")
    assert Backend._error_text(result) == "ValueError: boom"
    empty = subprocess.CompletedProcess(["reader"], 3, "", "")
    assert Backend._error_text(empty) == "exit 3"


def test_backend_rejects_garbage_json():
    with tempfile.TemporaryDirectory() as tmp:
        backend = Backend(_script(tmp, 'print("not json")\n'), source="auto")
        try:
            backend.status()
        except BackendError as error:
            assert "invalid JSON" in str(error), error
        else:
            raise AssertionError("invalid JSON should raise BackendError")


# --------------------------------------------------------------------------
# diagnostics: the panel's own crash log and its trace of reader calls
# --------------------------------------------------------------------------

import contextlib      # noqa: E402
from cyberpower_tui import diagnostics as D   # noqa: E402


@contextlib.contextmanager
def temp_state():
    """Point the logs at a scratch directory for the length of a test."""
    with tempfile.TemporaryDirectory() as tmp:
        previous = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = tmp
        try:
            yield os.path.join(tmp, "cyberpower-ups")
        finally:
            if previous is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = previous
            D.DIAG.debug_enabled = False


def test_panel_crash_log_records_the_traceback():
    with temp_state() as directory:
        try:
            raise RuntimeError("panel exploded")
        except RuntimeError as error:
            path = D.Diag().crash(error)
        assert path == os.path.join(directory, "crash.log")
        text = open(path, encoding="utf-8").read()
        assert "[tui" in text and "Traceback" in text and "panel exploded" in text


def test_panel_debug_trace_is_silent_until_enabled():
    with temp_state() as directory:
        log = os.path.join(directory, "debug.log")
        D.DIAG.debug("nothing here")
        assert not os.path.exists(log)
        D.DIAG.debug_enabled = True
        D.DIAG.debug("reader called")
        assert "reader called" in open(log, encoding="utf-8").read()


def test_panel_traces_each_reader_call():
    with temp_state() as directory:
        D.DIAG.debug_enabled = True
        with tempfile.TemporaryDirectory() as tmp:
            backend = Backend(_script(tmp, STUB_BODY), source="auto", debug=True)
            backend.status()
            backend.set_value("alarm", 3)
        log = open(os.path.join(directory, "debug.log"), encoding="utf-8").read()
        # --debug is passed down, so the reader traces its own reads as well
        assert "status --source auto --debug -> rc=0" in log, log
        assert "set alarm 3 --source auto --debug -> rc=0" in log, log


def test_panel_trace_records_a_failing_call():
    with temp_state() as directory:
        D.DIAG.debug_enabled = True
        with tempfile.TemporaryDirectory() as tmp:
            body = 'import sys\nsys.stderr.write("no UPS\\n")\nsys.exit(2)\n'
            backend = Backend(_script(tmp, body), source="auto")
            try:
                backend.status()
            except BackendError:
                pass
        log = open(os.path.join(directory, "debug.log"), encoding="utf-8").read()
        assert "rc=2" in log and "stderr='no UPS'" in log, log


# --------------------------------------------------------------------------
# the app: draw each tab into the fake screen
# --------------------------------------------------------------------------

def _app(width=96, height=28, status=STATUS, settings=SETTINGS):
    screen = FakeScreen(height, width)
    backend = StubBackend(status=status, settings=settings)
    app = App(screen, backend, worker=SyncWorker(backend))
    app.status = status
    app.settings = copy.deepcopy(list(settings))
    app.message = "ready"
    return screen, app, backend


def test_status_tab_renders():
    screen, app, _ = _app()
    app.tab_index = 0
    app.draw()
    text = screen.text()
    assert "CPS TEST" in text, text
    assert "100 %" in text, text
    assert "Fully charged" in text, text
    assert "Mains 230 V" in text, text
    assert "Runtime" in text and "40 min" in text, text
    assert "r refresh" in text, text


def test_status_tab_handles_no_ups():
    screen = FakeScreen(20, 80)
    backend = StubBackend()
    app = App(screen, backend, worker=SyncWorker(backend))
    app.status = None
    app.status_error = "no CyberPower UPS found"
    app.tab_index = 0
    app.draw()
    assert "no CyberPower UPS found" in screen.text()


def test_controls_tab_lists_actions_and_runs_them():
    screen, app, backend = _app()
    app.tab_index = 1
    app.draw()
    assert "Run a quick battery test" in screen.text()

    tab = app.tabs[1]
    tab.cursor = 0
    tab.activate_current()
    assert backend.actions == ["quick"], backend.actions

    tab.cursor = 2
    tab.activate_current()
    assert backend.actions == ["quick", "abort"], backend.actions


def test_controls_tab_blocks_simulate():
    screen, app, backend = _app()
    app.backend.source = "simulate"
    app.tab_index = 1
    app.draw()
    assert "simulate" in screen.text()
    app.tabs[1].activate_current()
    assert backend.actions == [], backend.actions


def test_settings_tab_renders():
    screen, app, _ = _app()
    app.tab_index = 2
    app.draw()
    text = screen.text()
    assert "Audible alarm" in text, text
    assert "‹ enabled ›" in text, text
    assert "disabled · enabled · muted" in text, text
    assert "off · 0 – 1,966,020 s" in text, text


def test_settings_enum_cycles_and_numeric_steps():
    screen, app, backend = _app()
    tab = app.tabs[2]

    tab.cursor = 0                                  # enum: Enter cycles
    tab.activate_current()
    assert backend.sets[-1] == ("alarm", 3), backend.sets
    assert app.setting("alarm")["valueLabel"] == "muted", app.setting("alarm")

    tab.cursor = 1                                  # numeric: arrows step
    tab.adjust(1)
    assert backend.sets[-1] == ("runtimeLimit", "301"), backend.sets
    # 300 -> 301 -> 300. Each press builds on what is on screen, not on the
    # value the UPS last confirmed, so quick presses add up.
    tab.adjust(-1)
    assert backend.sets[-1] == ("runtimeLimit", "300"), backend.sets

    app._drain()                                    # apply the confirmations
    assert app.setting("runtimeLimit")["value"] == 300, app.setting("runtimeLimit")


def test_setting_write_is_optimistic():
    """A write shows up in the UI before the reader has even answered."""
    screen, app, backend = _app()
    tab = app.tabs[2]
    tab.cursor = 1                                  # runtimeLimit, 300 s

    tab.adjust(1)
    assert app.setting("runtimeLimit")["value"] == 301      # already on screen
    assert backend.sets == [("runtimeLimit", "301")]

    app._drain()
    assert app.setting("runtimeLimit")["value"] == 301
    assert app.message == "runtimeLimit = 301", app.message


def test_setting_write_rolls_back_on_rejection():
    screen = FakeScreen(28, 96)
    backend = StubBackend(status=STATUS, settings=SETTINGS)
    backend.fail_keys = {"runtimeLimit"}
    app = App(screen, backend, worker=SyncWorker(backend))
    app.settings = copy.deepcopy(list(SETTINGS))
    tab = app.tabs[2]
    tab.cursor = 1
    before = app.setting("runtimeLimit")["value"]

    tab.adjust(1)
    assert app.setting("runtimeLimit")["value"] == before + 1

    app._drain()
    assert app.setting("runtimeLimit")["value"] == before, app.setting("runtimeLimit")
    assert "rejected" in app.message, app.message


def test_stale_write_reply_is_ignored():
    """A reply for a value the user has already moved past must not win."""
    screen, app, backend = _app()
    tab = app.tabs[2]
    tab.cursor = 1

    tab.adjust(1)                                   # sequence 1: 301
    tab.adjust(1)                                   # sequence 2: 302
    app._drain()                                    # both replies are queued
    assert app.setting("runtimeLimit")["value"] == 302, app.setting("runtimeLimit")


def test_confirmation_reports_a_write_the_ups_ignored():
    """Some firmwares accept a write and keep the old value; say so."""
    screen, app, backend = _app()                   # the stub never changes
    tab = app.tabs[2]
    tab.cursor = 1

    tab.adjust(1)
    app._drain()
    assert app.setting("runtimeLimit")["value"] == 301
    assert app._confirm_at is not None

    app._confirm_at = 0.0                           # pretend the pause elapsed
    app._maybe_poll()
    app._drain()
    assert app.setting("runtimeLimit")["value"] == 300, app.setting("runtimeLimit")
    assert "stayed at 300 s" in app.message, app.message


def test_confirmation_is_quiet_when_the_write_sticks():
    screen = FakeScreen(28, 96)
    backend = StubBackend(status=STATUS, settings=SETTINGS)
    app = App(screen, backend, worker=SyncWorker(backend))
    app.settings = copy.deepcopy(list(SETTINGS))
    tab = app.tabs[2]
    tab.cursor = 1

    tab.adjust(1)
    app._drain()
    backend._settings[1]["value"] = 301             # the UPS took the write

    app._confirm_at = 0.0
    app._maybe_poll()
    app._drain()
    assert app.message == "runtimeLimit = 301", app.message
    assert app.setting("runtimeLimit")["value"] == 301


def test_settings_turn_off_only_where_allowed():
    screen, app, backend = _app()
    tab = app.tabs[2]

    tab.cursor = 1                                  # no sentinel: refused
    tab.turn_off()
    assert backend.sets == [], backend.sets

    tab.cursor = 2                                  # sentinel: written as "off"
    tab.turn_off()
    assert backend.sets == [("delayShutdown", "off")], backend.sets


def test_settings_enter_opens_editor_for_numeric():
    screen, app, backend = _app()
    tab = app.tabs[2]
    tab.cursor = 1
    tab.activate_current()
    assert app.dialog is not None
    app.close_dialog()


def test_about_tab_renders():
    screen, app, _ = _app()
    app.tab_index = 4
    app.draw()
    text = screen.text()
    assert "CyberPower UPS control panel" in text, text
    assert "/stub/reader" in text, text
    assert "switch tab" in text, text
    assert "history.jsonl" in text, text


def test_sparkline():
    assert W.sparkline([], 10) == ""
    assert W.sparkline([230, 230, 230], 10) == "▅▅▅"       # flat draws a level line
    assert len(W.sparkline(range(100), 12)) == 12          # squeezed to width
    assert W.sparkline([1, 2, 3], 3) == "▁▅█"              # scaled to its own range


def test_history_tab_renders_the_voltage_and_the_events():
    screen, app, _ = _app()
    app.history = copy.deepcopy(HISTORY)
    app.tab_index = 3
    app.draw()
    text = screen.text()
    assert "Input voltage" in text, text
    assert "Events  (2)" in text, text
    assert "229–236 V over 3 samples" in text, text
    # a log reads newest first
    assert text.index("Mains power restored") < text.index("Mains power lost"), text


def test_history_tab_when_nothing_is_recorded():
    screen, app, _ = _app()
    app.history = []
    app.history_error = ""
    app.tab_index = 3
    app.draw()
    assert "Nothing recorded yet" in screen.text(), screen.text()

    app.history_error = "reader not found"
    app.draw()
    assert "reader not found" in screen.text(), screen.text()


def test_history_tab_splits_samples_and_events():
    screen, app, _ = _app()
    app.history = copy.deepcopy(HISTORY)
    assert len(app.samples) == 3
    assert [record["event"] for record in app.events] == ["mainsRestored", "mainsLost"]


def test_history_tab_survives_a_shrinking_log():
    screen, app, _ = _app()
    tab = app.tabs[3]
    app.tab_index = 3
    app.history = copy.deepcopy(HISTORY)
    app.draw()
    assert tab.count() == 2
    tab.cursor = 1

    # the file was trimmed under us and no events are left
    app.history = [record for record in HISTORY if record["kind"] == "sample"]
    app.draw()
    assert tab.hits == [] and tab.cursor == 0

    # ...and a click cannot select a row that is no longer there
    app.history = copy.deepcopy(HISTORY)
    app.draw()
    assert tab.count() == 2 and tab.cursor == 0


def test_history_is_requested_once_and_applied():
    screen = FakeScreen(28, 96)
    backend = StubBackend(status=STATUS, settings=SETTINGS, history=HISTORY)
    app = App(screen, backend, worker=SyncWorker(backend))
    app.draw()

    app.select_tab(3)                                  # on_show asks for the log
    assert app.history_pending is True
    app._drain()
    assert app.history_pending is False
    assert [record["kind"] for record in app.history] == ["sample", "sample", "event",
                                                          "sample", "event"]
    app.draw()
    assert "Mains power lost" in screen.text()


def test_history_failure_is_shown_not_hidden():
    screen = FakeScreen(28, 96)
    backend = StubBackend(status=STATUS, settings=SETTINGS)
    backend.history = lambda limit=400: (_ for _ in ()).throw(BackendError("no history"))
    app = App(screen, backend, worker=SyncWorker(backend))
    app.tab_index = 3

    app.request_history()
    app._drain()
    app.draw()
    assert "no history" in screen.text(), screen.text()


def test_small_terminal_keeps_the_footer():
    screen, app, _ = _app(width=60, height=14)
    app.tab_index = 0
    app.draw()
    lines = screen.lines()
    assert lines[-1].strip().startswith("r refresh"), lines
    assert lines[-2].strip() == "ready", lines
    # The busy status tab must not spill into the footer rows.
    assert "min remaining" not in "\n".join(lines[-2:]), lines


def test_tiny_terminal_shows_a_hint():
    screen, app, _ = _app(width=40, height=9)
    app.tab_index = 0
    app.draw()
    assert "too small" in screen.text()


def test_tab_switch_and_mouse():
    screen, app, _ = _app()
    app.handle("\t")
    assert app.tab_index == 1
    app.handle("3")
    assert app.tab_index == 2
    # On the Settings tab the arrows belong to the values, so switch tabs
    # elsewhere before checking that the arrows still change tabs.
    app.handle("4")
    assert app.tab_index == 3
    app.handle(curses.KEY_LEFT)
    assert app.tab_index == 2


def test_mouse_clicks_tabs_and_rows():
    screen, app, _ = _app()
    app.tab_index = 0
    app.draw()

    original = curses.getmouse
    try:
        # Click the "Controls" label in the tab strip (row 1).
        start, _, index = app._tab_hits[1]
        curses.getmouse = lambda: (0, start + 1, 1, 0, 0)
        app._handle_mouse()
        assert index == 1 and app.tab_index == 1, app.tab_index

        # The fifth tab's label is new, and its box must match the label it
        # drew, not the old four-tab layout.
        start, end, index = app._tab_hits[3]
        curses.getmouse = lambda: (0, (start + end) // 2, 1, 0, 0)
        app._handle_mouse()
        assert index == 3 and app.tab_index == 3, app.tab_index
        assert screen.lines()[1][start:end + 1].strip() == "History", screen.lines()[1]

        # Click a settings row: first selects it, a second click activates it.
        app.tab_index = 2
        app.draw()
        tab = app.tabs[2]
        row, row_index = tab.hits[1]
        curses.getmouse = lambda: (0, 8, row, 0, 0)
        app._handle_mouse()
        assert tab.cursor == row_index == 1, tab.cursor
        app._handle_mouse()
        assert app.dialog is not None
    finally:
        curses.getmouse = original


# --------------------------------------------------------------------------
# the edit dialog
# --------------------------------------------------------------------------

def test_edit_dialog_typing_and_applying():
    screen, app, backend = _app()
    dialog = EditDialog(copy.deepcopy(SETTINGS[1]))
    assert dialog.buffer == "300", dialog.buffer

    dialog.key(app, curses.KEY_BACKSPACE)
    dialog.key(app, curses.KEY_BACKSPACE)
    dialog.key(app, curses.KEY_BACKSPACE)
    assert dialog.buffer == "", dialog.buffer
    for char in "450":
        dialog.key(app, char)
    assert dialog.buffer == "450", dialog.buffer

    app.dialog = dialog
    dialog.key(app, "\n")
    assert backend.sets == [("runtimeLimit", "450")], backend.sets
    assert app.dialog is None


def test_edit_dialog_off_and_cancel():
    screen, app, backend = _app()

    dialog = EditDialog(copy.deepcopy(SETTINGS[2]))   # allows off
    dialog.key(app, curses.KEY_BACKSPACE)
    for char in "off":
        dialog.key(app, char)
    app.dialog = dialog
    dialog.key(app, "\n")
    assert backend.sets == [("delayShutdown", "off")], backend.sets

    dialog = EditDialog(copy.deepcopy(SETTINGS[1]))   # Esc just closes
    app.dialog = dialog
    dialog.key(app, "\x1b")
    assert app.dialog is None
    assert backend.sets == [("delayShutdown", "off")], backend.sets


def test_edit_dialog_keeps_bad_input():
    screen, app, backend = _app()
    dialog = EditDialog(copy.deepcopy(SETTINGS[1]))
    dialog.buffer = "banana"
    app.dialog = dialog
    dialog.key(app, "\n")
    assert app.dialog is dialog
    assert "not a number" in dialog.note, dialog.note
    assert backend.sets == []

    dialog = EditDialog(copy.deepcopy(SETTINGS[1]))
    dialog.buffer = "off"
    app.dialog = dialog
    dialog.key(app, "\n")
    assert app.dialog is dialog
    assert "cannot be turned off" in dialog.note, dialog.note


def main():
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"ok   {name}")
        except AssertionError as error:
            failures += 1
            print(f"FAIL {name}: {error}")
    print("all tests passed" if failures == 0 else f"{failures} test(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
