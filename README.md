# CyberPower UPS

An Omarchy (Quickshell) bar widget showing the status of a CyberPower USB UPS:
charge, estimated runtime, load, mains voltage and the current power state. Left
click opens a detail card with a self-test and an alarm-mute button; the **gear**
in the card's header opens a full-screen TUI with the rest of the UPS's controls
(alarm mode, low-battery thresholds, transfer voltages, shutdown delays), the
recorded history and an energy meter priced at your tariff.

The interesting part is that it does not need CyberPower's PowerPanel suite. A
CyberPower UPS is a standard USB HID *Power Device*, so the widget reads the
battery and power usages straight off the HID interface. PowerPanel's `pwrstat`
and Network UPS Tools are supported as fallbacks for when the direct path is not
available.

There is also an optional recorder: `cyberpower-ups watch` keeps a log of the
readings and of the events between them (mains lost, brownouts, battery), which
the panel's **History** tab draws — the same job PowerPanel's daemon does, minus
the daemon being mandatory.

## How it reads the UPS

`bin/cyberpower-ups` is a self-contained Python script (standard library only)
with three backends, tried in order unless a source is forced:

| Backend | How it works | Needs |
| ------- | ------------ | ----- |
| `hidraw` | Parses the HID report descriptor from `/dev/hidraw*` with ioctls, extracts the Power Device (usage page `0x84`) and Battery System (`0x85`) feature reports, and normalizes the values. This is the same class-based approach NUT's `cps-hid` driver takes, but written from the descriptor semantics rather than copied. | The bundled udev rule |
| `pwrstat` | Parses `pwrstat -status` from the AUR package `powerpanel` (PowerPanel Personal for Linux). | `powerpanel` + `pwrstatd` |
| `nut` | Parses `upsc <name>` from Network UPS Tools, for a UPS already owned by NUT's `usbhid-ups`. | `nut` |

All three are normalized to the same JSON document, so the widget does not care
which one answered. `bin/cyberpower-ups-tui` is a thin wrapper that runs this
reader, so the protocol lives in exactly one place.

### Why no daemon

PowerPanel keeps a `pwrstatd` daemon and talks to its clients over a Unix socket
at `/var/pwrstatd.ipc` with JSON. Reverse engineering that socket would work, but
it is unnecessary: the UPS itself is self-describing, and reading it directly
means no daemon, no root (just the udev rule), and no dependency on CyberPower's
binary. The `pwrstat` backend exists only to support machines that already run
PowerPanel.

Nothing here *requires* a daemon either: status, controls and settings are a
single process reading a HID node, and the widget just spawns it on a timer.
The one job that genuinely needs to run all the time is the history recorder
(see below) — and that is opt-in, a `systemd --user` unit, not a precondition.

## Install

Nothing here is tied to a particular user, home directory or machine: every
path is resolved relative to these files, the state directory follows
`$XDG_STATE_HOME`, and the udev rule is vendor-wide rather than device-specific.
Two things do need Python and Omarchy:

- **Python 3.8 or newer**, standard library only — no packages to install. The
  nearest thing to a requirement is `subprocess.run(capture_output=…)` (3.7) and
  PEP 604 annotations, which are never evaluated thanks to
  `from __future__ import annotations`. Only 3.14 has actually been exercised, so
  treat an older interpreter as untested rather than impossible.
- **The reader and the TUI run on any Linux box** (`bin/cyberpower-ups`,
  `bin/cyberpower-ups-tui`). Only the bar widget needs Omarchy/Quickshell, since
  it is a Quickshell component. The TUI wants an xterm-compatible terminal with
  a Nerd Font for the icons.

1. Install the folder. From a git URL, Omarchy does it for you — it clones,
   validates the manifest, and places the plugin under the `id` the manifest
   declares:

   ```bash
   omarchy plugin add <git-url> --enable
   ```

   Otherwise copy the folder to
   `~/.config/omarchy/plugins/io.github.kamil.cyberpower-ups/` (it is already
   there if you are reading this in place). A *symlink* to a checkout works for
   the shell, which loads the widget from it, but `omarchy plugin add` and
   `omarchy plugin update` refuse a plugin folder that contains one ("symlinks
   are not allowed inside a plugin folder"), so use a copy if you want the
   tooling to manage it. The widget does not care where it
   lives — it resolves `bin/` next to itself — but Omarchy installs a plugin
   under a directory named after the manifest's `id`, and the bundled systemd
   unit names that path. If you fork this and give it your own id, change it in
   `manifest.json` **and** in `BarWidget.qml`'s `moduleName` (the two must
   agree), and update the unit's `ExecStart`.

2. Install the udev rule so your user may open the UPS's HID node. It matches
   vendor `0764` (CyberPower) rather than one model, and grants the *active
   session's* user access, so it needs no per-user configuration:

   ```bash
   sudo udev/install.sh
   ```

   The script copies the rule through a root-owned temporary file before it
   lands in `/etc`, and it is safe to run again. If you would rather not run
   a script from this directory as root, install the rule yourself:

   ```bash
   sudo install -m 0644 udev/60-cyberpower-ups.rules /etc/udev/rules.d/
   sudo udevadm control --reload
   ```

   Then re-plug the UPS or run `sudo udevadm trigger --subsystem-match=hidraw`.
   This step is optional: without it, the widget falls back to the `pwrstat` or
   NUT backends (status only — no settings or controls).

3. Add the widget to the bar:

   ```bash
   omarchy plugin enable io.github.kamil.cyberpower-ups
   # or place it next to the other status widgets:
   omarchy bar put io.github.kamil.cyberpower-ups --after <some-other-widget>
   ```

   The shell hot-reloads `~/.config/omarchy/shell.json`, so no restart is needed.

## Settings

| Key | Default | Meaning |
| --- | ------- | ------- |
| `backend` | `Auto` | `Auto`, `USB direct`, `pwrstat`, `NUT` or `Simulate`. |
| `intervalSec` | `15` | Poll interval in seconds. |
| `showPercent` | `true` | Show the charge next to the icon. |
| `upsName` | *(empty)* | NUT UPS name; empty uses the first `upsc -l` lists. |
| `warnBattery` | `30` | Tint the icon at or below this charge while on battery. |
| `criticalBattery` | `15` | Use the urgent color at or below this charge. |
| `iconPreset` | `Auto` | `Auto`, `Battery` or `Power`. |
| `showTestControls` | `true` | Show the Self-test and Abort buttons in the popup. |
| `debug` | `false` | Append every device read and write to the debug trace. |
| `shutdownAt` | `0` | Battery charge (percent) that calls for the machine to power itself off. `0` leaves the rule off. |
| `shutdownAfterMin` | `0` | Minutes into an outage that call for the same. `0` leaves that half of the rule off. |
| `shutdownRuntimeMin` | `0` | Remaining runtime (minutes) at or below which the machine powers itself off — the load-aware rule. `0` leaves that half of the rule off. |
| `shutdownGraceSec` | `30` | How long the widget counts down before it powers the machine off. |

`Simulate` shows fixed sample data and is the quickest way to try the widget
without a UPS attached. To try an outage, write the readings you want into
`~/.local/state/cyberpower-ups/simulate.json`:

```json
{"onBattery": true, "battery": 40, "inputVoltage": 0, "runtimeMin": 5}
```

> The data source setting is called `backend` and **not** `source` on purpose.
> A bar entry that carries a setting called `source` is read as a custom QML
> module by the shell (`BarModel.js`, `customModuleType`), and the bar then
> loads the *value* as a QML file — `file://auto`, which is not there. The
> widget then renders as nothing at all, with no error anywhere.

The schema follows PowerPanel's own configuration layout, so the same words
mean the same thing in both tools:

| PowerPanel page | This plugin |
| --------------- | ----------- |
| **Serial Port** | `backend`, `upsName` — how the UPS is reached |
| **Interface** | `showPercent`, `iconPreset`, `warnBattery`, `criticalBattery`, `showTestControls` |
| **Runtime** | the `shutdown*` rules below, plus the UPS's own capacity/runtime limits and delays in the TUI |
| **Voltage** | the UPS's low/high voltage transfer in the TUI |
| **Notification** | the UPS's audible alarm in the TUI, and the widget's notifications |
| **Energy Settings** | the tariff row in the TUI's **Energy** tab, or `./cyberpower-ups energy --cost-per-kwh …` |
| **Self-Test** | the popup's self-test buttons and the reported last result |
| **Advanced** | `debug`; PowerPanel's sensitivity presets are left to the transfer thresholds above, and scheduled/hibernation actions to systemd |

This Omarchy build has no settings panel for bar widgets, so the values are
set from the command line:

```sh
omarchy bar set io.github.kamil.cyberpower-ups showPercent false
omarchy bar set io.github.kamil.cyberpower-ups warnBattery 25 --json
```

Without `--json` the value is stored as text, which is right for `backend` and
`upsName`; a number or a flag needs `--json` so it is stored as one. The
widget reads both spellings either way.

### Shutting the machine down

The UPS can cut its output at any moment, and the machine then dies with the
power still on. The widget can watch for that and shut the machine down
cleanly instead. Three settings arm the rule, and any one is enough:

```sh
omarchy bar set io.github.kamil.cyberpower-ups shutdownAt 30 --json
omarchy bar set io.github.kamil.cyberpower-ups shutdownAfterMin 3 --json
omarchy bar set io.github.kamil.cyberpower-ups shutdownRuntimeMin 5 --json
```

The first calls for a shutdown when the battery reaches that charge, the
second when the outage has lasted that long, and the third when the UPS
estimates only that much runtime is left. All are off by default (`0`), and
the rule is decided by the reader, not by the widget:

```bash
./cyberpower-ups shutdown --at 30 --after 3 --runtime 5 --outage-start <unix timestamp>
```

which prints a verdict — `off`, `idle`, `armed` or `due` — and the reason
behind it. The runtime rule is the load-aware one: 30 % of a 200 W load is not
the same number of minutes as 30 % of a 50 W load, and it is the same quantity
the UPS's own `runtimeMin` (and PowerPanel's "remaining battery runtime") uses.
The widget asks for the verdict only when the rule might be due, shows the
reason in the popup, and counts down `shutdownGraceSec` before it powers the
machine off. A notification says what is happening, the popup has a
**Cancel shutdown** button, and the mains coming back cancels it by itself.
The decision is written to the history, so the log says why the machine went
down.

Three things keep it from firing by accident: the rule is off unless it is
armed, the outage has to be older than twenty seconds (a blink of the mains is
not a reason to power off), and a reading without a charge or a runtime is not
read as zero.

**Mind the UPS's own timer.** On some models — the CPS UT2200EG here — the
UPS compares its *remaining runtime* against a limit of its own (300 s on this
one) and cuts the output when the estimate falls below it, long before the
battery is empty. The charge thresholds are the firmware's as well (10 % and
20 % here, and the writes are ignored). So the rule above is the only way to
get a clean shutdown, and it has to fire inside the UPS's own window: on this
unit, five minutes of outage and about 58 % of charge.

## Control panel (TUI)

`bin/cyberpower-ups-tui` is a full-screen control panel. It is deliberately a
*front end*: it never opens USB, HID, pwrstat or NUT itself, it only runs
`bin/cyberpower-ups` and draws what comes back. The reader stays a pure
command-line program and the panel is a wrapper around it, so the UPS protocol
lives in exactly one file and the UI can be driven by a stub in tests.

The widget's popup opens it with `omarchy-launch-terminal`, and it runs the same
from a bare terminal:

```bash
./bin/cyberpower-ups-tui                  # auto-detect the backend
./bin/cyberpower-ups-tui --source hidraw  # force direct USB
```

It is laid out like a small desktop app: a title bar, six tabs and a message
line.

| Tab | What it shows |
| --- | ------------- |
| **Status** | a battery gauge, the power path (mains → UPS → load) and every reading |
| **Controls** | run a quick/deep battery test, abort one, mute the alarm |
| **Settings** | every tunable setting the UPS publishes, edited in two steps |
| **History** | the input voltage as a sparkline, then the events the recorder logged; follows the log live |
| **Energy** | cumulative, today and last-7-days kWh from the recorded load, priced at the tariff (editable in the tab) |
| **About** | where the readings come from, and the key help |

| Key | Action |
| --- | ------ |
| `←` `→`, `Tab`, `1`–`6` | switch tab |
| `↑` `↓`, `j` `k` | move through actions, settings and events |
| `Enter` | run an action, or start / apply a setting edit |
| `←` `→`, `+` `-` | while editing a setting: pick the new value |
| `Esc` | while editing a setting: discard the change |
| `e` | type an exact number instead of stepping |
| `o` | stage "off", where the UPS allows it; `Enter` applies it |
| `r` | re-read the status, settings and history |
| `q` | quit |

The mouse works too: click a tab to switch, click a row to select it, click it
again to run it (or to start editing a setting).

On **Settings**, editing is two steps. `Enter` starts editing the selected row
and the arrows (or `+`/`-`) then preview a new value — the preview is shown
underlined and nothing is written yet. `Enter` applies it, `Esc` (or moving to
another row) throws it away. The table is sectioned the way PowerPanel lays
its configuration out — **Notification**, **Runtime**, **Voltage** — and the
headings are not rows, so the cursor still walks from one setting to the next.
Because the arrows are free while you browse, they
keep switching tabs here as they do on every other tab, so `→` from **Settings**
lands on **History** rather than being swallowed by the table. Starting an edit
and applying it is exactly one USB write, however many arrow presses it took to
get there. Only the settings your model publishes and marks writable appear:

| Setting | HID usage | Typical range | What it does |
| ------- | --------- | ------------- | ------------ |
| Audible alarm | `AudibleAlarmControl` `0x5A` | disabled / enabled / muted | beeps when the mains fails or the battery runs low |
| Low-battery warning at | `WarningCapacityLimit` `0x8C` | 0–100 % | the charge at which the UPS says the battery is low — it only warns |
| Shut down at capacity | `RemainingCapacityLimit` `0x29` | 0–100 % | the charge at which the UPS cuts its output |
| Shut down at runtime | `RemainingTimeLimit` `0x2A` | 0–600 s (the firmware holds it to 300–600) | the runtime the UPS estimates is left when it cuts the output |
| Low voltage transfer | `LowVoltageTransfer` `0x53` | 167–175 V | the input voltage below which the UPS takes over |
| High voltage transfer | `HighVoltageTransfer` `0x54` | 287–295 V | the input voltage above which the UPS takes over |
| Delay before shutdown | `DelayBeforeShutdown` `0x57` | off, or seconds | the window the machine has to shut down cleanly |
| Delay before startup | `DelayBeforeStartup` `0x56` | off, or seconds | the window the machine has to come up first |

The panel explains the setting under the cursor as you move through the table,
so you do not have to leave it to find out what a row does. The same words come
from the reader: `config` publishes a `help` field with every setting, so any
other client can explain them the same way instead of each making one up.

Settings are read back in physical units (so a descriptor that declares the
delays as logical minutes against physical seconds still reads in seconds), and
writes are clamped to the range the descriptor declares. A negative logical
minimum is the "off" sentinel that the delays use; `o` stages it and `Enter`
applies it (`set <key> off` does the same from the CLI, in one step). Direct USB
is required to change anything; the `pwrstat` and NUT backends are status-only
here.

### Why it feels responsive

Every reading and every write is a USB round trip (a status read is around a
second on the test unit), so the panel never does one in the event loop. A
worker thread runs them instead and the loop only picks up the results, which
keeps the cursor and the keys immediate no matter what is in flight. Staging a
setting is pure UI — the arrows cost nothing and the single write happens when
you press `Enter`.

A changed setting is drawn at once and written in the background, so holding
down `→` steps as fast as you can press it while only the final value is
actually written. The reader echoes the value it stored after clamping, so the
panel never has to re-read the whole config just to show it. Half a second after
you stop stepping, one confirming read checks the device really took the value;
a few firmwares accept a write and keep the old one (this UT2200EG does it for
the warning and capacity limits), and the panel says so — *"Low-battery warning
at stayed at 20 %"* — rather than showing a value the UPS never stored.

The panel is a package (`bin/cyberpower_tui/`) with a small, testable split:
`backend` is the only part that runs the reader, `formats` is pure
status/setting → text, `widgets` holds the curses drawing, `worker` runs the
reader off the event loop, `diagnostics` keeps the logs, and `app` is the tabs
plus the loop.

## Command line

The reader doubles as a diagnostic tool. Run it from the plugin's `bin/`
directory:

```bash
./cyberpower-ups status                 # JSON, auto-detected backend
./cyberpower-ups status --pretty
./cyberpower-ups status --source simulate
./cyberpower-ups probe                  # is a UPS there, and can I read it?
./cyberpower-ups dump                   # descriptor + every feature report + JSON
./cyberpower-ups quick                  # start the quick battery test
./cyberpower-ups deep                   # start the deep battery test
./cyberpower-ups abort                  # abort a running battery test
./cyberpower-ups mute                   # silence the alarm
./cyberpower-ups config                 # every tunable setting, as JSON
./cyberpower-ups set capacityLimit 20   # write one setting
./cyberpower-ups set delayShutdown off  # write the delays' "off" sentinel
./cyberpower-ups watch                  # record a sample every 60 s (Ctrl-C to stop)
./cyberpower-ups watch --listen         # ...and react to what the UPS pushes itself
./cyberpower-ups watch --once           # one sample, for a systemd timer
./cyberpower-ups history                # the last 40 recorded lines
./cyberpower-ups history --events       # only the events
./cyberpower-ups history --since 120 --json
./cyberpower-ups energy                 # kWh for the total, today and the week
./cyberpower-ups energy --cost-per-kwh 0.75 --currency EUR   # price it
./cyberpower-ups energy --json          # the same summary as one document
```

`dump` and `probe` are the tools to reach for when a model misbehaves: they
print the parsed descriptor layout and the raw reports, which is exactly what is
needed to add a quirk.

## History

A reading is a snapshot; PowerPanel's log comes from its `pwrstatd` daemon
writing `/var/log/pwrstatd.log`. This does the same job without making a daemon
mandatory: `watch` samples the UPS and appends to
`~/.local/state/cyberpower-ups/history.jsonl`, and the **History** tab reads it
back. While that tab is on screen it stats the file once a second and re-reads
it only when the recorder actually appended, so a new sample or event shows up
within about a second without spending a process per tick.

Two kinds of line, both with a `t` timestamp, in time order:

```json
{"t":1792408044,"kind":"sample","online":true,"inputVoltage":231.0,"loadPercent":14,...}
{"t":1792408100,"kind":"event","event":"mainsLost","text":"Mains power lost (input 0 V)","level":"bad"}
```

Samples are the raw series — input and output voltage, load, charge, runtime —
kept in the same shape as a reading so consecutive lines compare directly.
Events are the *transitions* between them, which is what a log is actually read
for:

| Event | Trigger |
| ----- | ------- |
| `mainsLost` / `mainsRestored` | the mains input stopped / came back |
| `onBattery` / `onBatteryEnd` | the load moved onto / off the battery |
| `lowBattery` | charge crossed the UPS's own low-battery limit |
| `boost` | automatic voltage regulation kicked in (input sag) |
| `overload` | output over its rating |
| `voltageHigh` / `voltageLow` / `voltageNormal` | input left / re-entered the band around nominal (`--band`, 10 % by default) |
| `testResult` | a self-test finished |
| `replaceBattery`, `deviceLost`, `deviceBack`, `readerError` | as it says |

To record continuously, enable the bundled user unit — it is not installed for
you, and the widget never needs it:

```bash
# from this plugin's directory
mkdir -p ~/.config/systemd/user
cp systemd/cyberpower-ups-watch.service ~/.config/systemd/user/
systemctl --user enable cyberpower-ups-watch.service
journalctl --user -u cyberpower-ups-watch -f      # events as they happen
```

The unit is enabled into `graphical-session.target`, so the recorder starts
with the desktop and stops with it — no `--now` needed, and it is not running
on a bare TTY login. Do not add `Wants=graphical-session.target` to it: that
target is what uwsm uses to decide whether a compositor session is already
running, and pulling it in from a unit of your own makes uwsm refuse to start
the compositor, which leaves you at a black screen after login.

If you installed the plugin somewhere other than
`~/.config/omarchy/plugins/io.github.kamil.cyberpower-ups/`, edit the unit's
`ExecStart=` to point at your copy — it is the one place in the project that
names its own location — and the `ConditionPathExists=` line above it, which
names the same path and stops the unit from retrying a path that is not there. If this machine talks to the UPS through pwrstat or NUT
rather than hidraw, add `--source pwrstat` or `--ups NAME`; `--listen` will then
step aside and poll instead, since only the HID endpoint has reports to read.

If you would rather not keep a process around, use a timer instead: the same
command with `watch --once` (it compares against the last recorded sample, so
events are still detected between runs).

The file is append-only and capped: past 2 MB the oldest half is dropped, never
the newest, so the recording keeps going without growing without bound.

## Energy and cost

PowerPanel keeps an energy meter — a cumulative and an average consumption in
kWh, priced at what it calls *Cost per kWh*. The widget already records a load
figure with every sample, so the same meter can be integrated from the history
instead of needing a second recorder:

```bash
./cyberpower-ups energy
# Total        2.204 kWh   208 W average   10.6 h measured   2436 min unrecorded
# Today        0.386 kWh   209 W average   1.8 h measured
# Last 7 days  2.204 kWh   208 W average   10.6 h measured   2436 min unrecorded
# Tariff       not set (use --cost-per-kwh to price the energy)
```

`Total` covers every sample the history still holds, `Today` since local
midnight and `Last 7 days` the rolling week. The energy is a trapezoid
integration between consecutive samples, so it is the *measured* figure and
not an estimate from the nominal load. A gap longer than five minutes is
reported as unrecorded rather than bridged: the recorder was down, and a
single reading spread over an hour of silence would invent energy.

The tariff lives in `~/.local/state/cyberpower-ups/energy.json` and survives
between runs; the currency defaults to the locale's.

```bash
./cyberpower-ups energy --cost-per-kwh 0.75 --currency EUR
./cyberpower-ups energy --json | jq .today
```

The TUI's **Energy** tab shows the same three scopes and a **Cost per kWh**
row: `Enter` (or `e`) opens the same editor the Settings tab uses, so the
tariff no longer needs a shell command. The currency is still set with
`--currency`, since it is a unit rather than a tariff. The tab re-reads the
scopes when the recorder appends. Nothing is added to the sample file and nothing extra
runs: with the recorder off there is simply no energy to report, which is
honest about the coverage.

### Why there is no battery-health percentage

PowerPanel's *Battery Health Indicator* is an estimate, and on this UPS it
cannot be measured: the UT2200EG exposes no `FullChargeCapacity`,
`DesignCapacity`, `CycleCount` or `ManufactureDate` usage at all (see
`./cyberpower-ups dump`), so any percentage would be invented. What the
hardware does report is `BatteryNeedsReplacing`, which this reader already
parses as `replaceBattery` and the widget already shows as "Battery needs
replacing". A health number would need a baseline over many full discharges
to the same load, which the history alone cannot supply.

### Why `--listen` exists

A plain `watch` is a sampler, and a sampler has a blind spot: an outage that
begins *and* ends between two ticks leaves no trace at all, and the UPS keeps no
log to back-fill from (this one returns `null` for `LastPowerEvent`). At the 60 s
default that blind spot is a minute wide.

`--listen` closes most of it, because the UPS is chattier than you would expect.
The UT2200EG volunteers input reports on its USB interrupt endpoint unprompted,
forever — its report descriptor declares the same usages twice, once as *feature*
reports (which a poll asks for) and once as *input* reports (which the device
sends on its own). Those pushes carry exactly the flags the events are built
from: mains present, charging, discharging, below-capacity-limit, boost,
overload. Measured on this unit, that group arrives every 11.2 s on average and
12.1 s at worst.

So `--listen` reads the endpoint and takes a full reading only when one of those
flags actually moves. A status change is therefore recorded in about ten seconds
instead of up to a minute, and it costs nothing — the listening side makes no USB
request at all; the device was going to send those reports regardless.

`--interval` keeps its meaning for everything else: it is still how often a full
reading is taken, which is what keeps the sample series regular and what catches
the values the device *never* pushes (a battery that needs replacing, the input
frequency, the voltage band). So there is no reason to lower it.

What is left of the blind spot is the device's own cadence: a dip shorter than
the push interval is invisible to any tool, PowerPanel included, because the
information never leaves the UPS. Nothing on the host side can beat that.
`--listen` needs the hidraw backend; with `pwrstat` or NUT it steps aside and
polls, since those have no endpoint to read. A model that declares no pushed
status flags does the same.

## When something goes wrong

Both programs keep their traces in `~/.local/state/cyberpower-ups/`
(`$XDG_STATE_HOME` is honoured), because neither one is usually watched: the bar
widget reads the reader's stdout and only shows its stderr when the reader
fails, and the panel runs in a terminal that closes the moment it exits.

| File | Written when | Holds |
| ---- | ------------ | ----- |
| `crash.log` | any unhandled error, always | a timestamp, the command line, the full traceback |
| `debug.log` | only when asked for | every device read and write, with timings |
| `history.jsonl` | when `watch` runs (see [History](#history)) | the recorded samples and events |

Turn the debug trace on with `--debug`, with `CYBERPOWER_UPS_DEBUG=1`, or with
the widget's **Write a debug trace** setting (the panel passes it down to the
reader, so both halves land in one file). It records the descriptor it parsed,
each feature report with its bytes and round-trip time, every feature write, and
each reader invocation the panel made:

```
2026-10-19 16:34:37 [395433] descriptor 1059 B -> 71 fields, report ids [...]
2026-10-19 16:34:37 [395433] report 0x3e 4 B 79 ms 28050000
2026-10-19 16:34:37 [395433] wrote usage 0x0058 value 1 -> report 0x3f 01000000
2026-10-19 16:34:37 [tui 394105] .../cyberpower-ups status --source hidraw --debug -> rc=0 1011 ms
```

A crash prints the traceback to stderr as before and adds one line saying where
the copy went, so nothing is lost when the terminal goes away:

```
ValueError: unknown setting 'bogus'
traceback written to /home/kamil/.local/state/cyberpower-ups/crash.log
```

Both files are size-capped (256 KB, one older generation kept as `.log.1`), and
a log that cannot be written is ignored rather than allowed to break a poll.

## Tests

```bash
python3 tests/test_reader.py   # the HID/backend side
python3 tests/test_tui.py      # the control panel
```

The Python is formatted with [black](https://black.readthedocs.io/), pinned to
88 columns in `pyproject.toml`:

```bash
black bin tests
```

The reader tests build a report descriptor shaped like a `CP1500PFCLCD`'s, feed
it synthetic feature reports, and check the parser, the extraction, the unit
handling, the pwrstat text parsing, the control-action dispatch and the
`config`/`set` conversion (including clamping, the `off` sentinel and the
logical↔physical mapping). They need neither a UPS nor a daemon.

The TUI tests point the backend at a stub reader script (so the CLI contract is
exercised for real, subprocess and all), check the pure formatting helpers, and
render every tab into a fake curses screen whose character grid can be inspected
as text — including the edit dialog, tab/row mouse clicks, the sparkline, and the
way a short terminal keeps its footer. They also cover the parts that make the
panel feel quick: the worker's slot coalescing and priority, a write showing up
before the reader has answered, a superseded reply being ignored, a rejected
write rolling back, and a write the device ignored being reported after the
confirming read.

The reader side also covers the history: that consecutive readings produce the
right events and only those, that a band breach is one line rather than one per
sample, that an unreadable line is skipped instead of fatal, and that a file
over its cap loses its oldest half and never its newest.

The two logs are tested the same way: that a crash writes a traceback, that the
debug trace is completely silent until it is asked for, that a log which cannot
be written does not break a poll, and that the panel traces each reader call it
makes.

## Hardware testing status

Validated against a **CyberPower UT2200EG** (`0764:0501`), reporting:

```
battery 100%   runtime 35 min   load 18-20% (206-321 W)
input/output 232-236 V @ 49.9 Hz   nominal 230 V
battery voltage 26.2 V   test result "No test initiated"
```

Two hardware-only issues were found and fixed during that first run:

- `HIDIOCGRDESCSIZE` mutates its buffer in place and returns `0`, so the
  descriptor size has to be read back out of the buffer rather than from the
  ioctl's return value. The synthetic tests never touched this path.
- The UT2200EG reports battery voltage under the **Power Summary** collection
  next to `RemainingCapacity` instead of under a `Battery` collection, so
  `read_hidraw()` falls back to the Power Summary's `Voltage` usage.

The control panel has been driven against the same unit in a pty: every tab
renders, the settings table reads the live values, the alarm and the delays'
"off" sentinel round-trip through a real write, and the editor opens and cancels
cleanly. Measured in that pty, a settings change appears on screen about **3 ms**
after the key, with the status poll running every 2 s (it used to block for
around 640 ms per keypress).

That unit also has a firmware quirk worth knowing: it *accepts* a write to
`WarningCapacityLimit` and `RemainingCapacityLimit` and then keeps the old
value, so those two read back unchanged. The confirming read catches this and
says `stayed at 20 %` instead of leaving a stale value on screen.

Still untested on real hardware: the deep self-test, the low-battery path, and
the pwrstat and NUT backends. If values look wrong on another model, run
`./cyberpower-ups dump` and open an issue with that output — the quirk fixes are
all in one place in `read_hidraw()`.

The recorder and the outage path have both been exercised against the unit for
real, including a deliberate mains cut. The full trailing edge was logged, with
the battery genuinely draining behind it:

```
17:01:27  233.0 V in   233.0 V out   15 % load   battery 100 %
17:02:28  0.0 V in     230.0 V out   14 % load   battery  93 %
17:02:28        Mains power lost (input 0 V)
17:02:28        Running on battery
17:03:30  234.0 V in   234.0 V out   15 % load   battery  92 %
17:03:30        Mains power restored (input 234 V)
17:03:30        Back on mains power
```

Worth noting: the UPS itself reports `lastEvent: null` throughout, so it keeps
no record of this at all — the log is the only one that exists. `watch` was also
run at one-second and sixty-second intervals, `watch --once` recorded single
samples, and the panel's **History** tab drew the sparkline and event list from
what was on disk.

The debug trace and the crash log were both exercised on the real device (a
panel run with `--debug`, and a forced panel exception), confirming that a crash
launched from the bar leaves a traceback behind even though its terminal closes.

`--listen` was measured rather than assumed. Sitting on the interrupt endpoint
alone for 60 s, asking for nothing, produced 107 unsolicited reports across 13
report ids: the status group every 11.2 s on average (12.1 s at worst) and a
settings-only group every 5.3 s. The pushed copies decode to the same flags a
feature read returns — that is what `test_pushed_flags_agree_with_the_feature_reports`
pins down; only the numbers differ, and only because the pushed ones were
captured a few seconds earlier.

## Scope

- Control covers the battery test, the audible alarm and the UPS's own tunable
  settings (the TUI), all over HID feature reports. Monitoring is covered too:
  the widget, the panel, the optional `watch` recorder and the energy meter
  derived from it. Automation covers the shutdown rules above (charge, outage
  length and remaining runtime) and nothing more: scheduled actions,
  hibernation windows and PowerPanel's "energy saving" schedules are OS work
  this plugin deliberately leaves to systemd.
- CyberPower UPSes report through usages the HID Power Device class defines.
  Non-conforming models fall back to `pwrstat` or NUT.

### If you want automatic shutdown

The UPS itself only reports state; something has to act on it. This widget
can: the `shutdownAt`, `shutdownAfterMin` and `shutdownRuntimeMin` settings
above shut the machine down cleanly when the charge, the clock or the
remaining runtime says so, and the decision is written to the history. NUT is
the other way: run `usbhid-ups` against the UPS and let `upsmon` handle
shutdowns, and point this widget's `backend` at `NUT` for display. The reader
exposes the same values, so a small systemd unit could just as well read its
JSON and act.

## License

MIT. NUT (`drivers/cps-hid.c`, `drivers/libhid.c`) was consulted as a
specification reference for the CyberPower usage table and HID unit handling;
those files are GPL-licensed and no code was copied from them.
