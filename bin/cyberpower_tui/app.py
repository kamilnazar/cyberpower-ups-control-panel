"""The tabbed control panel and its event loop.

The layout deliberately reads like a small desktop app rather than a shell
prompt: a title bar, a tab strip, a content pane per tab and a message line.
All state comes from ``Backend``; this module only decides what to draw and
what a keystroke means.
"""

from __future__ import annotations

import argparse
import curses
import locale
import os
import sys
import time
import traceback

from . import formats as F
from . import widgets as W
from .backend import ACTIONS, DEFAULT_READER, Backend
from .diagnostics import DIAG
from .worker import Worker

# Settings table geometry.
SETTINGS_LABEL_X = 2
SETTINGS_LABEL_W = 26
SETTINGS_VALUE_X = SETTINGS_LABEL_X + SETTINGS_LABEL_W + 2
SETTINGS_VALUE_W = 18
SETTINGS_ALLOWED_X = SETTINGS_VALUE_X + SETTINGS_VALUE_W + 2

KEY_BTAB = getattr(curses, "KEY_BTAB", -1)
ENTER_KEYS = ("\n", "\r", curses.KEY_ENTER)
BACKSPACE_KEYS = (curses.KEY_BACKSPACE, 127, 8)


def _setting_row(label: str, value: str, allowed: str, width: int) -> str:
    """Compose one settings row at fixed columns so it aligns when selected."""
    cells = [" "] * max(0, width)
    for x, text in ((SETTINGS_LABEL_X, label),
                    (SETTINGS_VALUE_X, value),
                    (SETTINGS_ALLOWED_X, allowed)):
        for offset, char in enumerate(text):
            if x + offset < len(cells):
                cells[x + offset] = char
    return "".join(cells)


class Tab:
    """One screen: it draws itself and interprets keys in the content pane."""

    title = ""

    def __init__(self, app):
        self.app = app
        self.cursor = 0
        self.offset = 0
        self.hits: list[tuple[int, int]] = []

    # -- to override -----------------------------------------------------
    def count(self) -> int:
        return 0

    def draw(self, top: int, height: int, width: int) -> None:
        raise NotImplementedError

    def handle(self, key) -> bool:
        return False

    def activate_current(self) -> None:
        return

    # -- shared ----------------------------------------------------------
    def hints(self) -> str:
        return "←/→ tabs   q quit"

    def on_show(self) -> None:
        return

    def move(self, delta: int) -> None:
        total = self.count()
        if total > 0:
            self.cursor = max(0, min(self.cursor + delta, total - 1))

    def window(self, height: int) -> tuple[int, int]:
        """Scroll so the cursor stays visible. Returns (first, stop)."""
        total = self.count()
        if self.cursor < self.offset:
            self.offset = self.cursor
        if self.cursor >= self.offset + height:
            self.offset = self.cursor - height + 1
        self.offset = max(0, min(self.offset, max(0, total - height)))
        return self.offset, min(total, self.offset + height)

    def click(self, row: int) -> bool:
        for hit_row, index in self.hits:
            if hit_row == row:
                if index == self.cursor:
                    self.activate_current()
                else:
                    self.cursor = index
                return True
        return False

    # -- key helpers -----------------------------------------------------
    @staticmethod
    def is_down(key) -> bool:
        return key == "j" or key == curses.KEY_DOWN

    @staticmethod
    def is_up(key) -> bool:
        return key == "k" or key == curses.KEY_UP


# -------------------------------------------------------------------- tabs

class StatusTab(Tab):
    title = "Status"

    def hints(self) -> str:
        return "r refresh   ←/→ tabs   q quit"

    def draw(self, top, height, width):
        app, screen, theme = self.app, self.app.screen, self.app.theme
        limit = top + height
        status = app.status
        if status is None:
            text = app.status_error or "reading the UPS…"
            W.put(screen, top + 1, 3, W.clip(text, width - 6),
                  theme.bad if app.status_error else theme.muted)
            return

        attr = theme.severity(F.severity(status))
        row = top + 1

        fraction = F.battery_fraction(status)
        if fraction is not None and row + 2 < limit:
            gauge_width = min(46, max(20, width - 34))
            W.battery(screen, row, 3, gauge_width, fraction, theme.muted, attr)
            x = 3 + gauge_width + 3
            W.put(screen, row, x, f"{F.num(status.get('battery'), 0):>3} %",
                  attr | curses.A_BOLD)
            W.put(screen, row + 1, x, F.status_label(status), attr)
            if status.get("runtimeMin") is not None:
                W.put(screen, row + 2, x, f"{F.num(status['runtimeMin'], 0)} min remaining",
                      theme.muted)
            row += 4

        flow = F.power_path(status)
        if flow and row < limit:
            W.put(screen, row, 3, flow, attr)
            row += 2

        if row < limit:
            rows = F.reading_pairs(status)[:(limit - row) * 2]
            W.pairs(screen, row, 3, width - 6, rows, theme)


class ControlsTab(Tab):
    title = "Controls"

    def count(self) -> int:
        return len(ACTIONS)

    def hints(self) -> str:
        return "↑/↓ select   Enter run   ←/→ tabs   r refresh   q quit"

    def draw(self, top, height, width):
        app, screen, theme = self.app, self.app.screen, self.app.theme
        W.put(screen, top, 3, "Device actions", theme.accent)
        self.hits = []

        first, stop = self.window(min(height - 8, len(ACTIONS)))
        for index in range(first, stop):
            key, label, _ = ACTIONS[index]
            row = top + 2 + (index - first)
            selected = index == self.cursor
            enabled = app.action_enabled(key)
            text = f" [ {label} ]"
            if selected:
                body = W.pad(("▸" if enabled else "·") + text, min(width - 4, 58))
                W.put(screen, row, 3, body, theme.selection if enabled else curses.A_DIM)
            else:
                W.put(screen, row, 3, (" " + text), theme.muted if not enabled else 0)
            self.hits.append((row, index))

        row = top + 2 + max(1, stop - first) + 1
        limit = top + height
        if row < limit:
            W.rule(screen, row, 3, min(width - 6, 58), theme.muted)
            row += 1
        if row < limit:
            W.put(screen, row, 3, W.clip(ACTIONS[self.cursor][2], width - 6), theme.muted)
            row += 2
        if row < limit and app.backend.source == "simulate":
            W.put(screen, row, 3, "Actions are disabled for the simulate source.", theme.warn)
        elif row < limit and app.status is None:
            W.put(screen, row, 3, "No UPS is available to run actions against.", theme.warn)

    def activate_current(self) -> None:
        key, label, _ = ACTIONS[self.cursor]
        if not self.app.action_enabled(key):
            self.app.notify(f"{label} is not available right now", "warn")
            return
        self.app.run_action(key)

    def handle(self, key) -> bool:
        if self.is_down(key):
            self.move(1)
            return True
        if self.is_up(key):
            self.move(-1)
            return True
        if key in ENTER_KEYS or key == " ":
            self.activate_current()
            return True
        return False


class SettingsTab(Tab):
    title = "Settings"

    def count(self) -> int:
        return len(self.app.settings)

    def current(self) -> dict | None:
        settings = self.app.settings
        if 0 <= self.cursor < len(settings):
            return settings[self.cursor]
        return None

    def hints(self) -> str:
        return ("↑/↓ select   ←/→ change   Enter edit/cycle   o off   "
                "r reload   ←/→ tabs")

    def on_show(self) -> None:
        if not self.app.settings and self.app.settings_error:
            self.app.request_settings()

    def draw(self, top, height, width):
        app, screen, theme = self.app, self.app.screen, self.app.theme
        settings = app.settings
        if not settings:
            if app.settings_error:
                text, attr = app.settings_error, theme.bad
            elif app._settings_pending:
                text, attr = "reading the UPS's settings…", theme.muted
            else:
                text, attr = "This UPS does not publish any tunable settings.", theme.muted
            W.put(screen, top + 1, 3, W.clip(text, width - 6), attr)
            return

        W.put(screen, top, SETTINGS_LABEL_X, W.pad("Setting", SETTINGS_LABEL_W),
              theme.muted | curses.A_BOLD)
        W.put(screen, top, SETTINGS_VALUE_X, W.pad("Value", SETTINGS_VALUE_W),
              theme.muted | curses.A_BOLD)
        W.put(screen, top, SETTINGS_ALLOWED_X, "Range", theme.muted | curses.A_BOLD)
        self.hits = []

        first, stop = self.window(height - 3)
        for index in range(first, stop):
            setting = settings[index]
            row = top + 2 + (index - first)
            selected = index == self.cursor
            writable = setting.get("writable", True)
            label = W.pad(setting["label"], SETTINGS_LABEL_W)
            value = W.pad(F.setting_value_text(setting), SETTINGS_VALUE_W)
            allowed = F.setting_allowed_text(setting)
            if selected:
                line = _setting_row(label, value, allowed, width - 1)
                W.put(screen, row, 0, line, theme.selection if writable else curses.A_DIM)
            else:
                W.put(screen, row, SETTINGS_LABEL_X, label,
                      theme.muted if writable else curses.A_DIM)
                W.put(screen, row, SETTINGS_VALUE_X, value,
                      curses.A_DIM if not writable else 0)
                W.put(screen, row, SETTINGS_ALLOWED_X, allowed, theme.muted)
            self.hits.append((row, index))

    # -- actions ---------------------------------------------------------
    def _guard(self, setting: dict) -> bool:
        if not setting.get("writable", True):
            self.app.notify(f"{setting['label']} is read-only on this UPS", "warn")
            return False
        return True

    def activate_current(self) -> None:
        setting = self.current()
        if setting is None or not self._guard(setting):
            return
        if F.is_enum(setting):
            value = F.cycle_enum(setting, 1)
            if value is not None:
                self.app.apply_setting(setting, value)
        else:
            self.app.dialog = EditDialog(setting)

    def adjust(self, direction: int) -> None:
        setting = self.current()
        if setting is None or not self._guard(setting):
            return
        if F.is_enum(setting):
            value = F.cycle_enum(setting, direction)
        else:
            value = F.step_numeric(setting, direction)
        if value is not None:
            self.app.apply_setting(setting, F.num(value, 1))

    def turn_off(self) -> None:
        setting = self.current()
        if setting is None:
            return
        if not F.has_off(setting):
            self.app.notify(f"{setting['label']} cannot be turned off", "warn")
            return
        if self._guard(setting):
            self.app.apply_setting(setting, "off")

    def handle(self, key) -> bool:
        if self.count() == 0:
            return False
        if self.is_down(key):
            self.move(1)
            return True
        if self.is_up(key):
            self.move(-1)
            return True
        if key == curses.KEY_NPAGE:
            self.move(5)
            return True
        if key == curses.KEY_PPAGE:
            self.move(-5)
            return True
        if key in ENTER_KEYS:
            self.activate_current()
            return True
        if key == " ":
            self.activate_current()
            return True
        if key == curses.KEY_LEFT or key in ("-", "_"):
            self.adjust(-1)
            return True
        if key == curses.KEY_RIGHT or key in ("+", "="):
            self.adjust(1)
            return True
        if key in ("e", "E"):
            setting = self.current()
            if setting is not None and not F.is_enum(setting) and self._guard(setting):
                self.app.dialog = EditDialog(setting)
            return True
        if key in ("o", "O"):
            self.turn_off()
            return True
        return False


class HistoryTab(Tab):
    """What the readings did over time, from the reader's ``watch`` log.

    Two things worth looking at: the input voltage as a sparkline over the
    recorded samples (the fluctuation the UPS itself never reports), and the
    events the reader derived between them — mains lost and restored, the AVR
    boosting, the battery going low.
    """

    title = "History"
    SPARK_MAX = 48

    def count(self) -> int:
        return len(self.app.events)

    def hints(self) -> str:
        return "↑/↓ scroll   r reload   ←/→ tabs   q quit"

    def on_show(self) -> None:
        if not self.app.history and not self.app.history_error:
            self.app.request_history()

    def handle(self, key) -> bool:
        if self.is_down(key):
            self.move(1)
            return True
        if self.is_up(key):
            self.move(-1)
            return True
        return False

    def draw(self, top, height, width):
        app, screen, theme = self.app, self.app.screen, self.app.theme
        limit = top + height
        row = top + 1

        if app.history_error:
            W.put(screen, row, 3, W.clip(app.history_error, width - 6), theme.bad)
            return
        if not app.history:
            text = ("reading the recorded history…" if app.history_pending else
                    "Nothing recorded yet — run `cyberpower-ups watch`, or enable the "
                    "bundled systemd unit, to keep a log.")
            W.put(screen, row, 3, W.clip(text, width - 6), theme.muted)
            return

        volts = [record.get("inputVoltage") for record in app.samples]
        volts = [value for value in volts if isinstance(value, (int, float))]
        if volts:
            W.put(screen, row, 3, "Input voltage", theme.accent)
            summary = (f"{volts[-1]:g} V now   {min(volts):g}–{max(volts):g} V over "
                       f"{len(volts)} sample{'s' if len(volts) != 1 else ''}")
            room = width - 20 - len(summary)          # what the graph may use
            if room >= 12:
                graph_width = min(self.SPARK_MAX, room)
                W.put(screen, row, 17, W.sparkline(volts, graph_width), theme.muted)
                W.put(screen, row, 19 + graph_width, W.clip(summary, width - 20 - graph_width),
                      theme.muted)
            else:
                W.put(screen, row, 17, W.sparkline(volts, max(12, width - 20)), theme.muted)
                row += 1
                W.put(screen, row, 3, W.clip(summary, width - 6), theme.muted)
            row += 2

        events = app.events
        self.hits = []
        self.cursor = max(0, min(self.cursor, len(events) - 1))
        if row < limit:
            W.put(screen, row, 3, f"Events  ({len(events)})", theme.accent)
            row += 1
        if not events:
            if row < limit:
                W.put(screen, row, 3, "Nothing has changed since recording started.",
                      theme.muted)
            return

        first, stop = self.window(max(1, limit - row))
        for index in range(first, stop):
            record = events[index]
            at = time.strftime("%m-%d %H:%M", time.localtime(record.get("t", 0)))
            selected = index == self.cursor
            attr = theme.severity(record.get("level"))
            text = f" {at}  {record.get('text')}"
            if selected:
                W.put(screen, row, 3, W.pad("▸" + text, min(width - 5, 70)),
                      theme.selection)
            else:
                W.put(screen, row, 3, W.clip(" " + text, min(width - 5, 70)), attr)
            self.hits.append((row, index))
            row += 1


class AboutTab(Tab):
    title = "About"

    def draw(self, top, height, width):
        app, screen, theme = self.app, self.app.screen, self.app.theme
        status = app.status or {}
        limit = top + height
        row = top + 1
        W.put(screen, row, 3, "CyberPower UPS control panel", theme.accent)
        row += 2

        source = app.backend.source
        if app.backend.ups:
            source += f" · {app.backend.ups}"
        details = (
            ("Reader", app.backend.reader),
            ("Data source", source),
            ("Device", status.get("device") or "—"),
            ("Model", status.get("model") or "—"),
            ("Firmware", status.get("firmware") or "—"),
            ("History", DIAG.path("history.jsonl")),
        )
        for label, value in details:
            if row >= limit:
                return
            W.put(screen, row, 3, W.pad(label, 14), theme.muted)
            W.put(screen, row, 3 + 15, W.clip(str(value), width - 22))
            row += 1

        row += 1
        if row >= limit:
            return
        W.put(screen, row, 3, "Keys", theme.accent)
        row += 1
        for line in (
            "↑/↓ or j/k     move in the list",
            "←/→ or Tab     switch tab (1-5 jump straight there)",
            "Enter          run an action, or cycle / edit a setting",
            "+ / -          step a numeric setting up or down",
            "o              turn a setting off, where the UPS allows it",
            "r              refresh now",
            "q              quit",
        ):
            if row >= limit:
                return
            W.put(screen, row, 3, W.clip(line, width - 6), theme.muted)
            row += 1


# ------------------------------------------------------------------ dialog

class EditDialog:
    """The little modal used to type a new value for a numeric setting."""

    def __init__(self, setting: dict):
        self.setting = setting
        self.buffer = "" if setting.get("value") is None else F.num(setting["value"], 1)
        self.cursor = len(self.buffer)
        self.note = F.setting_allowed_text(setting)

    # -- input -----------------------------------------------------------
    def _insert(self, char: str) -> None:
        self.buffer = self.buffer[:self.cursor] + char + self.buffer[self.cursor:]
        self.cursor += 1

    def _backspace(self) -> None:
        if self.cursor > 0:
            self.buffer = self.buffer[:self.cursor - 1] + self.buffer[self.cursor:]
            self.cursor -= 1

    def key(self, app, key) -> None:
        if key in ENTER_KEYS:
            self._apply(app)
            return
        if key in (27, "\x1b"):
            app.close_dialog()
            return
        if isinstance(key, int):
            if key in BACKSPACE_KEYS:
                self._backspace()
            elif key == curses.KEY_DC:
                if self.cursor < len(self.buffer):
                    self.buffer = self.buffer[:self.cursor] + self.buffer[self.cursor + 1:]
            elif key == curses.KEY_LEFT:
                self.cursor = max(0, self.cursor - 1)
            elif key == curses.KEY_RIGHT:
                self.cursor = min(len(self.buffer), self.cursor + 1)
            elif key == curses.KEY_HOME:
                self.cursor = 0
            elif key == curses.KEY_END:
                self.cursor = len(self.buffer)
            return
        if key in ("\x7f", "\b"):
            self._backspace()
        elif isinstance(key, str) and key.isprintable():
            self._insert(key)

    def _apply(self, app) -> None:
        try:
            kind, _ = F.parse_edit(self.buffer)
        except ValueError as error:
            self.note = str(error)
            return
        if kind == "off" and not F.has_off(self.setting):
            self.note = "this setting cannot be turned off"
            return
        value = "off" if kind == "off" else self.buffer.strip()
        app.close_dialog()
        app.apply_setting(self.setting, value)

    # -- drawing ---------------------------------------------------------
    def draw(self, app) -> None:
        screen, theme = app.screen, app.theme
        height, width = screen.getmaxyx()
        label = self.setting["label"]
        box_width = min(width - 4, max(len(label) + 10, 54))
        box_height = 8
        top = max(1, (height - box_height) // 2)
        left = max(1, (width - box_width) // 2)

        W.fill(screen, top + 1, left + 1, box_height - 2, box_width - 2)
        W.box(screen, top, left, box_height, box_width, theme.accent)
        W.put(screen, top, left + 2, " Edit setting ", theme.accent)
        W.put(screen, top + 1, left + 2, W.clip(label, box_width - 4))
        field_width = box_width - 6
        W.put(screen, top + 3, left + 3, "┌" + "─" * (field_width - 2) + "┐", theme.muted)
        W.put(screen, top + 4, left + 3, "│", theme.muted)
        W.put(screen, top + 4, left + 4, W.pad(self.buffer, field_width - 2))
        W.put(screen, top + 4, left + 3 + field_width - 1, "│", theme.muted)
        W.put(screen, top + 5, left + 3, "└" + "─" * (field_width - 2) + "┘", theme.muted)
        W.put(screen, top + 6, left + 2, W.clip(self.note, box_width - 4), theme.muted)

        try:
            curses.curs_set(1)
        except curses.error:
            pass
        cursor_x = min(left + 4 + self.cursor, left + 3 + field_width - 2)
        try:
            screen.move(top + 4, cursor_x)
        except curses.error:
            pass


# --------------------------------------------------------------------- app

class App:
    def __init__(self, screen, backend: Backend, interval: float = 5.0, worker=None):
        self.screen = screen
        self.backend = backend
        self.interval = interval
        self.theme = W.Theme(screen)
        self.worker = worker if worker is not None else Worker(backend)

        self.status: dict | None = None
        self.status_error = ""
        self.settings: list[dict] = []
        self.settings_error = ""
        self.history: list[dict] = []
        self.history_error = ""

        self.message = ""
        self.message_kind = "info"
        self.dialog: EditDialog | None = None

        self.tabs = [StatusTab(self), ControlsTab(self), SettingsTab(self),
                     HistoryTab(self), AboutTab(self)]
        self.tab_index = 0
        self.running = True
        self._tab_hits: list[tuple[int, int, int]] = []

        # Async bookkeeping: what is outstanding, and the last value each
        # setting had before the user started editing it (for rollback).
        self._status_requested = 0.0
        self._status_pending = False
        self._settings_pending = False
        self._history_pending = False
        self._last_input = 0.0
        self._set_sequence: dict[str, int] = {}
        self._set_base: dict[str, tuple] = {}
        self._confirm_at: float | None = None
        self._wanted: dict[str, object] = {}

    # ------------------------------------------------------------- state
    @property
    def tab(self) -> Tab:
        return self.tabs[self.tab_index]

    def notify(self, text: str, kind: str = "info") -> None:
        self.message = text or "done"
        self.message_kind = kind

    def setting(self, key: str) -> dict | None:
        return next((item for item in self.settings if item.get("key") == key), None)

    @property
    def history_pending(self) -> bool:
        return self._history_pending

    @property
    def samples(self) -> list[dict]:
        return [record for record in self.history if record.get("kind") == "sample"]

    @property
    def events(self) -> list[dict]:
        """The recorded events, newest first — a log reads that way round."""
        return [record for record in reversed(self.history)
                if record.get("kind") == "event"]

    # ------------------------------------------------------------ reads
    def request_status(self, force: bool = False) -> None:
        """Start a status read. Ignored when one is already outstanding."""
        if self._status_pending and not force:
            return
        self._status_pending = True
        self._status_requested = time.monotonic()
        self.worker.submit("status", self.backend.status)

    def request_settings(self, force: bool = False) -> None:
        if self._settings_pending and not force:
            return
        self._settings_pending = True
        self.worker.submit("settings", self.backend.settings)

    def request_history(self, force: bool = False) -> None:
        if self._history_pending and not force:
            return
        self._history_pending = True
        self.worker.submit("history", self.backend.history)

    def _maybe_poll(self) -> None:
        """Timers that run when a key is not waiting: confirm writes, then poll."""
        now = time.monotonic()
        if self._confirm_at is not None and now >= self._confirm_at:
            self._confirm_at = None
            if self.dialog is None:
                self.request_settings(force=True)
        if now - self._last_input < 1.0:
            return
        if now - self._status_requested >= self.interval:
            self.request_status()

    # ----------------------------------------------------------- writes
    def run_action(self, key: str) -> None:
        self.notify("sending…")
        self.worker.submit(f"action:{key}",
                           lambda key=key: self.backend.run_action(key), urgent=True)

    def apply_setting(self, setting: dict, value) -> None:
        """Show the new value now, write it in the background.

        Updating the local copy first is what makes repeated presses add up:
        each press steps from what is on screen, not from the last value the
        UPS confirmed.
        """
        key = setting["key"]
        self._set_base.setdefault(key, (setting.get("value"), setting.get("valueLabel")))
        if str(value).strip().lower() == "off":
            setting["value"] = None
            setting["valueLabel"] = "off"
        else:
            F.set_local_value(setting, value)

        self._set_sequence[key] = self._set_sequence.get(key, 0) + 1
        sequence = self._set_sequence[key]
        self._wanted[key] = setting.get("value")
        self._confirm_at = None          # any new write defers the check below

        def job(sequence=sequence, key=key, value=value):
            ok, message = self.backend.set_value(key, value)
            return sequence, key, ok, message

        self.worker.submit(f"set:{key}", job, urgent=True)

    def close_dialog(self) -> None:
        self.dialog = None
        try:
            curses.curs_set(0)
        except curses.error:
            pass

    # ------------------------------------------------------------ results
    def _drain(self) -> None:
        for slot, result, error in self.worker.take_results():
            if slot == "status":
                self._status_pending = False
                if error is not None:
                    self.status, self.status_error = None, error
                else:
                    self.status, self.status_error = result, ""
            elif slot == "settings":
                self._settings_pending = False
                if error is not None:
                    self.settings, self.settings_error = [], error
                else:
                    self.settings, self.settings_error = list(result), ""
                    self._verify_writes()
            elif slot == "history":
                self._history_pending = False
                if error is not None:
                    self.history, self.history_error = [], error
                else:
                    self.history, self.history_error = list(result), ""
            elif slot.startswith("set:"):
                self._finish_set(result, error)
            elif slot.startswith("action:"):
                self._finish_action(slot[len("action:"):], result, error)

    def _verify_writes(self) -> None:
        """After a confirming re-read, say so if the UPS ignored a write.

        Some firmwares accept a feature write and simply keep the old value
        (the UT2200EG does this for the warning/capacity limits). Silently
        showing the value we asked for would then be a lie.
        """
        if not self._wanted or self.dialog is not None:
            return
        for key, wanted in list(self._wanted.items()):
            setting = self.setting(key)
            del self._wanted[key]
            if setting is not None and setting.get("value") != wanted:
                self.notify(f"{setting['label']} stayed at "
                            f"{F.setting_value_text(setting)}", "warn")

    def _finish_set(self, result, error) -> None:
        if error is not None:
            self.notify(error, "error")
            self.request_settings(force=True)
            return
        sequence, key, ok, message = result
        if sequence != self._set_sequence.get(key):
            return                       # a newer write supersedes this reply
        base = self._set_base.pop(key, None)
        if ok:
            setting = self.setting(key)
            if setting is not None:
                F.update_from_reply(setting, message)
                self._wanted[key] = setting.get("value")
            self.notify(message, "ok")
            # Confirm against the device once the user stops stepping, so a
            # write the firmware quietly drops does not stay on screen.
            self._confirm_at = time.monotonic() + 0.5
        else:
            setting = self.setting(key)
            if setting is not None and base is not None:
                setting["value"], setting["valueLabel"] = base
            self._wanted.pop(key, None)
            self.notify(message or "the UPS rejected that setting", "error")
            self.request_settings(force=True)

    def _finish_action(self, key: str, result, error) -> None:
        if error is not None:
            self.notify(error, "error")
        else:
            ok, message = result
            self.notify(message, "ok" if ok else "error")
        self.request_status(force=True)

    def action_enabled(self, key: str) -> bool:
        if self.backend.source == "simulate":
            return False
        return self.status is not None

    # ------------------------------------------------------------ drawing
    def draw(self) -> None:
        screen = self.screen
        screen.erase()
        height, width = screen.getmaxyx()
        if height < 14 or width < 56:
            W.put(screen, 0, 0, "Terminal too small — enlarge to at least 56x14.",
                  curses.A_BOLD)
            screen.refresh()
            return

        self._draw_title(width)
        self._draw_tabs(width)
        W.rule(screen, 2, 0, width, self.theme.muted)

        top = 3
        self.tab.draw(top, height - 4 - top + 1, width)

        W.rule(screen, height - 3, 0, width, self.theme.muted)
        W.put(screen, height - 2, 2, W.clip(self.message, width - 4),
              self.theme.severity(self.message_kind))
        W.put(screen, height - 1, 2, W.clip(self.tab.hints(), width - 4), self.theme.muted)

        if self.dialog is not None:
            self.dialog.draw(self)
        else:
            try:
                curses.curs_set(0)
            except curses.error:
                pass
        screen.refresh()

    def _draw_title(self, width: int) -> None:
        W.put(self.screen, 0, 0, " CyberPower UPS ", self.theme.accent)
        model = ""
        if self.status:
            model = str(self.status.get("model") or "")
        if not model:
            model = "no UPS detected"
        W.put(self.screen, 0, max(2, width - len(model) - 2), model, self.theme.muted)

    def _draw_tabs(self, width: int) -> None:
        x = 2
        self._tab_hits = []
        for index, tab in enumerate(self.tabs):
            label = f" {tab.title} "
            attr = self.theme.selection if index == self.tab_index else self.theme.muted
            W.put(self.screen, 1, x, label, attr)
            self._tab_hits.append((x, x + len(label) - 1, index))
            x += len(label) + 2

    # -------------------------------------------------------------- input
    def select_tab(self, index: int) -> None:
        if not 0 <= index < len(self.tabs) or index == self.tab_index:
            return
        self.tab_index = index
        self.tab.on_show()

    def handle(self, key) -> None:
        if key in ("q", "Q"):
            self.running = False
            return
        if key == curses.KEY_RESIZE:
            self.screen.clear()
            return
        if key == curses.KEY_MOUSE:
            self._handle_mouse()
            return
        if key == "\t":
            self.select_tab((self.tab_index + 1) % len(self.tabs))
            return
        if key == KEY_BTAB:
            self.select_tab((self.tab_index - 1) % len(self.tabs))
            return
        if isinstance(key, str) and key in "12345":
            self.select_tab(int(key) - 1)
            return
        if key in ("r", "R"):
            self.notify("refreshing…")
            self.request_status(force=True)
            if self.tab_index == 2 or not self.settings:
                self.request_settings(force=True)
            if self.tab_index == 3 or not self.history:
                self.request_history(force=True)
            return
        if self.tab.handle(key):
            return
        # Tabs are reachable with the arrows wherever the tab did not use them.
        if key == curses.KEY_LEFT:
            self.select_tab((self.tab_index - 1) % len(self.tabs))
        elif key == curses.KEY_RIGHT:
            self.select_tab((self.tab_index + 1) % len(self.tabs))

    def _handle_mouse(self) -> None:
        try:
            _, mouse_x, mouse_y, _, _ = curses.getmouse()
        except curses.error:
            return
        if mouse_y == 1:
            for start, end, index in self._tab_hits:
                if start <= mouse_x <= end:
                    self.select_tab(index)
                    return
        self.tab.click(mouse_y)

    # --------------------------------------------------------------- loop
    def loop(self) -> None:
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS)
            curses.mouseinterval(0)
        except (curses.error, AttributeError):
            pass
        try:
            # Default ESCDELAY is ~1s, which makes a lone Escape (cancel the
            # editor) feel broken. 50 ms is plenty to tell it from a sequence.
            curses.set_escdelay(50)
        except (curses.error, AttributeError):
            pass
        self.screen.timeout(100)

        try:
            # Kick the first reads off and draw immediately; the loop stays
            # responsive while the reader does its USB round trips.
            self.request_status()
            self.request_settings()
            while self.running:
                self._drain()
                self.draw()
                try:
                    key = self.screen.get_wch()
                except curses.error:
                    key = None
                except KeyboardInterrupt:
                    break
                if key is None:
                    self._maybe_poll()
                    continue
                self._last_input = time.monotonic()
                if self.dialog is not None:
                    self.dialog.key(self, key)
                else:
                    self.handle(key)
        finally:
            self.worker.stop()
            try:
                curses.curs_set(1)
            except curses.error:
                pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="cyberpower-ups-tui",
        description="Interactive control panel for a CyberPower UPS")
    parser.add_argument("--source", default="auto",
                        choices=["auto", "hidraw", "pwrstat", "nut", "simulate"])
    parser.add_argument("--ups", default=None, help="NUT UPS name")
    parser.add_argument("--interval", type=float, default=5.0,
                        help="seconds between automatic status polls")
    parser.add_argument("--reader", default=DEFAULT_READER, help=argparse.SUPPRESS)
    parser.add_argument("--debug", action="store_true",
                        help="append every reader call to the debug trace")
    args = parser.parse_args(argv)

    DIAG.debug_enabled = args.debug or bool(os.environ.get("CYBERPOWER_UPS_DEBUG"))

    if not os.path.exists(args.reader):
        print(f"reader not found: {args.reader}", file=sys.stderr)
        return 2

    locale.setlocale(locale.LC_ALL, "")
    backend = Backend(args.reader, args.source, args.ups, debug=args.debug)

    def run(screen):
        App(screen, backend, args.interval).loop()

    try:
        curses.wrapper(run)
    except KeyboardInterrupt:
        pass
    except Exception as error:
        # curses.wrapper() has already restored the terminal, so the traceback
        # would print to a window that is about to close. Keep a copy.
        path = DIAG.crash(error)
        traceback.print_exception(type(error), error, error.__traceback__)
        print(f"traceback written to {path}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
