"""Pure status/setting -> string helpers.

Nothing in here imports curses or spawns a process, so it is the part of the
UI that is cheap to unit test. The app calls these to build every row it
draws; the widgets module only knows how to paint them.
"""

from __future__ import annotations

# Human labels for the reader's status tokens.
STATUS_LABELS = {
    "online": "On utility power",
    "charging": "Charging",
    "fullyCharged": "Fully charged",
    "onBattery": "On battery",
    "lowBattery": "On battery - low",
    "replaceBattery": "Battery needs replacing",
    "overload": "Overload",
    "off": "Off",
}

# Severity drives the colour: green / yellow / red.
SEVERITY_OK = "ok"
SEVERITY_WARN = "warn"
SEVERITY_BAD = "bad"


# --------------------------------------------------------------- numbers

def num(value, digits: int = 1) -> str:
    """Render a number the way a device panel would: 236 not 236.0."""
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == int(number):
        return str(int(number))
    text = f"{number:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


# ---------------------------------------------------------------- status

def status_label(status: dict | None) -> str:
    if not status:
        return ""
    raw = status.get("status")
    if raw in (None, ""):
        return ""
    return STATUS_LABELS.get(raw, str(raw))


def severity(status: dict | None) -> str:
    """'bad', 'warn' or 'ok', for choosing a colour."""
    if not status:
        return SEVERITY_OK
    if status.get("status") in ("replaceBattery", "overload", "lowBattery"):
        return SEVERITY_BAD
    if status.get("onBattery") or status.get("status") == "onBattery":
        return SEVERITY_WARN
    return SEVERITY_OK


def battery_fraction(status: dict | None) -> float | None:
    """Charge as 0.0 .. 1.0, or None when the UPS does not report it."""
    if not status:
        return None
    percent = status.get("battery")
    if not isinstance(percent, (int, float)):
        return None
    return max(0.0, min(1.0, float(percent) / 100.0))


def reading_pairs(status: dict | None) -> list[tuple[str, str]]:
    """The status tab's (label, value) table."""
    if not status:
        return []
    rows: list[tuple[str, str]] = []

    def add(label: str, value) -> None:
        if value is None or value == "":
            return
        rows.append((label, str(value)))

    add("Status", status_label(status))

    if status.get("runtimeMin") is not None:
        add("Runtime", f"{num(status['runtimeMin'], 0)} min")

    if status.get("loadPercent") is not None:
        load = f"{num(status['loadPercent'], 0)} %"
        if status.get("loadWatts"):
            load += f" ({num(status['loadWatts'], 0)} W)"
        add("Load", load)

    if status.get("inputVoltage") is not None:
        add("Input voltage", f"{num(status['inputVoltage'])} V")
    if status.get("inputFrequency") is not None:
        add("Input frequency", f"{num(status['inputFrequency'])} Hz")
    if status.get("outputVoltage") is not None:
        add("Output voltage", f"{num(status['outputVoltage'])} V")
    if status.get("outputFrequency") is not None:
        add("Output frequency", f"{num(status['outputFrequency'])} Hz")
    if status.get("batteryVoltage") is not None:
        add("Battery voltage", f"{num(status['batteryVoltage'])} V")
    if status.get("temperature") is not None:
        add("Temperature", f"{num(status['temperature'])} °C")
    if status.get("nominalVoltage") is not None:
        add("Nominal voltage", f"{num(status['nominalVoltage'], 0)} V")
    if status.get("apparentPower") is not None:
        add("Apparent power", f"{num(status['apparentPower'], 0)} VA")

    add("Last test", status.get("testResult"))
    add("Last event", status.get("lastEvent"))
    return rows


def power_path(status: dict | None) -> str:
    """A one-line power flow, like the desktop app's diagram."""
    if not status:
        return ""
    voltage = status.get("inputVoltage")
    if status.get("onBattery") or not voltage:
        source = "Mains --"
    else:
        source = f"Mains {num(voltage)} V"
    load = ""
    if status.get("loadPercent") is not None:
        load = f"Load {num(status['loadPercent'], 0)} %"
        if status.get("loadWatts"):
            load += f" ({num(status['loadWatts'], 0)} W)"
    return f"{source}   --->   UPS   --->   {load}".rstrip()


# -------------------------------------------------------------- settings

def is_enum(setting: dict) -> bool:
    return bool(setting.get("choices"))


def has_off(setting: dict) -> bool:
    return bool(setting.get("offAllowed"))


def enum_values(setting: dict) -> list[int]:
    """The selectable raw values of an enum setting, in order."""
    choices = setting.get("choices") or {}
    values = []
    for key in choices:
        try:
            values.append(int(key))
        except (TypeError, ValueError):
            continue
    return sorted(values)


def setting_value_text(setting: dict) -> str:
    """The value cell, e.g. '‹ enabled ›', '20 %' or 'off'."""
    if is_enum(setting):
        label = setting.get("valueLabel")
        return f"‹ {label} ›" if label else "‹ ? ›"
    if setting.get("value") is None:
        return str(setting.get("valueLabel") or "off")
    text = num(setting.get("value"), 0)
    if setting.get("unit"):
        text += f" {setting['unit']}"
    return text


def _group(value) -> str:
    """Add thousands separators to a plain integer (1,966,020)."""
    text = str(value)
    negative = text.startswith("-")
    digits = text[1:] if negative else text
    if digits.isdigit() and len(digits) > 4:
        return ("-" if negative else "") + f"{int(digits):,}"
    return text


def setting_allowed_text(setting: dict) -> str:
    """The range/choices cell, e.g. 'off · 60 – 3600 s'."""
    if is_enum(setting):
        choices = setting.get("choices") or {}
        return " · ".join(str(choices[key]) for key in sorted(choices, key=int))
    low = _group(num(setting.get("min"), 0))
    high = _group(num(setting.get("max"), 0))
    unit = setting.get("unit") or ""
    span = f"{low} – {high} {unit}".strip()
    if has_off(setting):
        span = f"off · {span}"
    if not setting.get("writable", True):
        span = "read-only" if not span else f"read-only · {span}"
    return span


def cycle_enum(setting: dict, direction: int) -> int | None:
    """The next/previous enum value, wrapping around."""
    values = enum_values(setting)
    if not values:
        return None
    current = setting.get("value")
    try:
        index = values.index(int(current))
    except (TypeError, ValueError):
        index = -direction
    return values[(index + direction) % len(values)]


def step_numeric(setting: dict, direction: int) -> float | None:
    """The next/previous numeric value, clamped to the declared range."""
    step = setting.get("step") or 1
    low = setting.get("min")
    high = setting.get("max")
    current = setting.get("value")
    if current is None:
        current = high if direction > 0 else (low if low is not None else 0)
    try:
        value = float(current) + direction * float(step)
    except (TypeError, ValueError):
        return None
    if low is not None:
        value = max(float(low), value)
    if high is not None:
        value = min(float(high), value)
    return value


def parse_edit(text: str) -> tuple[str, float | None]:
    """Interpret what the user typed in an edit dialog.

    Returns ("off", None) for the off sentinel or ("value", number). Raises
    ValueError for anything else, so the dialog can explain the problem.
    """
    stripped = text.strip()
    if stripped.lower() in ("off", "none"):
        return "off", None
    try:
        return "value", float(stripped)
    except ValueError:
        raise ValueError(f"{stripped!r} is not a number")


def set_local_value(setting: dict, value) -> bool:
    """Record a value the UPS has, or is about to have, so it shows at once.

    Returns False when the value does not fit the setting (an unknown enum
    choice, say), in which case the caller should leave it alone.
    """
    if is_enum(setting):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return False
        label = (setting.get("choices") or {}).get(str(number))
        if label is None:
            return False
        setting["value"] = number
        setting["valueLabel"] = label
        return True
    if value is None:
        setting["value"] = None
        setting["valueLabel"] = "off"
        return True
    try:
        setting["value"] = float(value)
    except (TypeError, ValueError):
        return False
    return True


def update_from_reply(setting: dict, message: str) -> bool:
    """Apply the reader's reply to a write.

    `set` echoes the value it actually wrote, after clamping, as
    ``"<key> = <value>"`` (or ``"<key> = off"``). Trusting that saves a second
    subprocess — a full `config` re-read — after every single keypress.
    """
    prefix = f"{setting.get('key')} = "
    if not message or not message.startswith(prefix):
        return False
    text = message[len(prefix):].strip()
    if text.lower() == "off":
        setting["value"] = None
        setting["valueLabel"] = "off"
        return True
    return set_local_value(setting, text)
