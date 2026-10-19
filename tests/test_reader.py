#!/usr/bin/env python3
"""Tests for the HID report-descriptor parser in bin/cyberpower-ups.

The UPS is not needed: a report descriptor is built by hand, shaped like the
one a CyberPower CP1500PFCLCD publishes (Power Device collections, battery
usages on the Battery System page, everything in numbered feature reports),
and feature-report bodies are fed straight into the reader. This exercises the
parts that would otherwise only be testable with hardware attached.

Run: python3 tests/test_reader.py
"""

import importlib.machinery
import importlib.util
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "cyberpower-ups")

loader = importlib.machinery.SourceFileLoader("cyberpower_ups", SCRIPT)
spec = importlib.util.spec_from_loader("cyberpower_ups", loader)
ups = importlib.util.module_from_spec(spec)
loader.exec_module(ups)


# --------------------------------------------------------------------------
# a report descriptor, assembled item by item
# --------------------------------------------------------------------------


def tag(prefix, value):
    """Encode a short HID item: prefix is the tag byte with a size of 0."""
    if -128 <= value <= 127:
        return bytes([prefix | 1, value & 0xFF])
    if -32768 <= value <= 32767:
        return bytes([prefix | 2]) + int(value & 0xFFFF).to_bytes(2, "little")
    return bytes([prefix | 4]) + int(value & 0xFFFFFFFF).to_bytes(4, "little")


def u8(prefix, value):
    return bytes([prefix | 1, value & 0xFF])


def u16(prefix, value):
    return bytes([prefix | 2]) + int(value & 0xFFFF).to_bytes(2, "little")


PAGE = 0x04
USAGE = 0x08
LOG_MIN = 0x14
LOG_MAX = 0x24
UNIT = 0x64
UNIT_EXP = 0x54
REPORT_SIZE = 0x74
REPORT_ID = 0x84
REPORT_COUNT = 0x94
COLLECTION = 0xA0
END_COLLECTION = 0xC0
FEATURE = 0xB0
INPUT = 0x80


def u32(prefix, value):
    return bytes([prefix | 3]) + int(value & 0xFFFFFFFF).to_bytes(4, "little")


def build_descriptor():
    """A descriptor shaped like a CyberPower CP1500PFCLCD's."""
    d = b""
    d += u16(PAGE, 0x84) + u8(USAGE, 0x04) + u8(COLLECTION, 0x01)  # ~ UPS, Application
    d += u8(USAGE, 0x24) + u8(COLLECTION, 0x00)  # ~ PowerSummary, Physical
    # Battery charge / runtime / status flags: report 8
    d += u8(REPORT_ID, 0x08)
    d += u16(PAGE, 0x85)
    d += u8(USAGE, 0x66) + u8(LOG_MIN, 0) + u8(LOG_MAX, 100)
    d += u8(REPORT_SIZE, 8) + u8(REPORT_COUNT, 1) + u8(FEATURE, 0x02)
    d += u8(USAGE, 0x68) + u16(LOG_MAX, 32767)
    d += u8(REPORT_SIZE, 16) + u8(REPORT_COUNT, 1) + u8(FEATURE, 0x02)
    d += u8(USAGE, 0xD0) + u8(USAGE, 0x44) + u8(USAGE, 0x45) + u8(USAGE, 0x46)
    d += u8(USAGE, 0x42) + u8(USAGE, 0x4B)
    d += u8(LOG_MIN, 0) + u8(LOG_MAX, 1) + u8(REPORT_SIZE, 1) + u8(REPORT_COUNT, 6)
    d += u8(FEATURE, 0x02)
    d += u8(REPORT_COUNT, 2) + u8(FEATURE, 0x03)  # padding
    # Report 8 is declared a second time as input reports. The UT2200EG does
    # just this: it pushes the values it would otherwise have to be asked for,
    # so a listener can have them without making a request. Same layout, only
    # the main item differs.
    d += u8(USAGE, 0x66) + u8(LOG_MIN, 0) + u8(LOG_MAX, 100)
    d += u8(REPORT_SIZE, 8) + u8(REPORT_COUNT, 1) + u8(INPUT, 0x02)
    d += u8(USAGE, 0x68) + u16(LOG_MAX, 32767)
    d += u8(REPORT_SIZE, 16) + u8(REPORT_COUNT, 1) + u8(INPUT, 0x02)
    d += u8(USAGE, 0xD0) + u8(USAGE, 0x44) + u8(USAGE, 0x45) + u8(USAGE, 0x46)
    d += u8(USAGE, 0x42) + u8(USAGE, 0x4B)
    d += u8(LOG_MIN, 0) + u8(LOG_MAX, 1) + u8(REPORT_SIZE, 1) + u8(REPORT_COUNT, 6)
    d += u8(INPUT, 0x02)
    d += u8(REPORT_COUNT, 2) + u8(INPUT, 0x03)  # padding
    d += bytes([END_COLLECTION])
    # Input voltage / frequency: report 0x10
    d += u16(PAGE, 0x84)
    d += u8(USAGE, 0x1A) + u8(COLLECTION, 0x00)
    d += u8(REPORT_ID, 0x10)
    d += u8(USAGE, 0x30) + u8(LOG_MIN, 0) + u16(LOG_MAX, 511)
    d += u32(UNIT, 0x00F0D121) + u8(UNIT_EXP, 0x07)  # volts
    d += u8(REPORT_SIZE, 16) + u8(REPORT_COUNT, 1) + u8(FEATURE, 0x02)
    d += u8(USAGE, 0x32) + u8(LOG_MAX, 100)
    d += u32(UNIT, 0x0000F001) + u8(UNIT_EXP, 0x0F)  # 0.1 Hz
    d += u8(REPORT_SIZE, 16) + u8(REPORT_COUNT, 1) + u8(FEATURE, 0x02)
    d += bytes([END_COLLECTION])
    # Output voltage / load: report 0x12
    d += u8(USAGE, 0x1C) + u8(COLLECTION, 0x00)
    d += u8(REPORT_ID, 0x12)
    d += u8(USAGE, 0x30) + u8(LOG_MIN, 0) + u16(LOG_MAX, 511)
    d += u32(UNIT, 0x00F0D121) + u8(UNIT_EXP, 0x07)
    d += u8(REPORT_SIZE, 16) + u8(REPORT_COUNT, 1) + u8(FEATURE, 0x02)
    d += u8(USAGE, 0x35) + u8(LOG_MAX, 100)
    d += (
        u32(UNIT, 0x00000000)
        + u8(UNIT_EXP, 0x00)
        + u8(REPORT_SIZE, 8)
        + u8(REPORT_COUNT, 1)
    )
    d += u8(FEATURE, 0x02)
    d += bytes([END_COLLECTION])
    # Battery voltage: report 0x13
    d += u16(PAGE, 0x84) + u8(USAGE, 0x12) + u8(COLLECTION, 0x00)
    d += u8(REPORT_ID, 0x13)
    d += u8(USAGE, 0x30) + u8(LOG_MIN, 0) + u16(LOG_MAX, 511)
    d += u32(UNIT, 0x00F0D121) + u8(UNIT_EXP, 0x07)
    d += u8(REPORT_SIZE, 16) + u8(REPORT_COUNT, 1) + u8(FEATURE, 0x02)
    d += bytes([END_COLLECTION])
    # Configurable settings: alarm, transfer voltages: report 0x20
    d += u8(USAGE, 0x24) + u8(COLLECTION, 0x00)  # PowerSummary
    d += u8(REPORT_ID, 0x20)
    d += u8(USAGE, 0x5A) + u8(LOG_MIN, 1) + u8(LOG_MAX, 3)
    d += u8(REPORT_SIZE, 8) + u8(REPORT_COUNT, 1) + u8(FEATURE, 0x02)
    d += u8(USAGE, 0x53) + u16(LOG_MIN, 167) + u16(LOG_MAX, 175)
    d += u32(UNIT, 0x00F0D121) + u8(UNIT_EXP, 0x07)
    d += u8(REPORT_SIZE, 16) + u8(REPORT_COUNT, 1) + u8(FEATURE, 0x02)
    d += u8(USAGE, 0x54) + u16(LOG_MIN, 287) + u16(LOG_MAX, 295)
    d += u32(UNIT, 0x00F0D121) + u8(UNIT_EXP, 0x07)
    d += u8(REPORT_SIZE, 16) + u8(REPORT_COUNT, 1) + u8(FEATURE, 0x02)
    d += bytes([END_COLLECTION])
    # Battery settings: report 0x21
    d += u16(PAGE, 0x85)
    d += u8(REPORT_ID, 0x21)
    d += u8(USAGE, 0x8C) + u8(LOG_MIN, 0) + u8(LOG_MAX, 100)
    d += u8(REPORT_SIZE, 8) + u8(REPORT_COUNT, 1) + u8(FEATURE, 0x02)
    d += u8(USAGE, 0x29) + u8(LOG_MIN, 0) + u8(LOG_MAX, 100)
    d += u8(REPORT_SIZE, 8) + u8(REPORT_COUNT, 1) + u8(FEATURE, 0x02)
    d += bytes([END_COLLECTION])  # ~ UPS
    return d


def fake_device(reports, product=0x0501):
    device = ups.UpsDevice("/dev/fake", 0x0764, product, "CyberPower CP1500PFCLCD")
    fields, bits, numbered = ups.parse_report_descriptor(build_descriptor())
    device.fields, device.report_bits, device.uses_report_ids = fields, bits, numbered
    device.descriptor = build_descriptor()
    device._reports = reports
    device._read_report = lambda rid, rtype, fd=None: device._reports.get(rid, b"")
    return device


def test_descriptor_fields():
    fields, bits, numbered = ups.parse_report_descriptor(build_descriptor())
    assert numbered, "report ids are used"
    by_usage = {}
    for field in fields:
        if field.report_type == ups.REPORT_FEATURE:
            by_usage.setdefault(field.usage, []).append(field)

    charge = by_usage[ups.U_REMAINING_CAPACITY][0]
    assert (charge.report_id, charge.offset, charge.size) == (8, 0, 8), charge.__dict__
    assert (charge.log_min, charge.log_max) == (0, 100)

    runtime = by_usage[ups.U_RUN_TIME_TO_EMPTY][0]
    assert (runtime.offset, runtime.size) == (8, 16)

    # Status flags share one byte after the runtime word, one bit each.
    offsets = {
        usage: by_usage[usage][0].offset
        for usage in (
            ups.U_AC_PRESENT,
            ups.U_CHARGING,
            ups.U_DISCHARGING,
            ups.U_FULLY_CHARGED,
            ups.U_BELOW_CAPACITY_LIMIT,
            ups.U_NEED_REPLACEMENT,
        )
    }
    assert offsets == {
        ups.U_AC_PRESENT: 24,
        ups.U_CHARGING: 25,
        ups.U_DISCHARGING: 26,
        ups.U_FULLY_CHARGED: 27,
        ups.U_BELOW_CAPACITY_LIMIT: 28,
        ups.U_NEED_REPLACEMENT: 29,
    }, offsets

    assert bits[(8, ups.REPORT_FEATURE)] == 32, bits
    assert bits[(0x10, ups.REPORT_FEATURE)] == 32, bits
    assert bits[(0x12, ups.REPORT_FEATURE)] == 24, bits


def test_extraction():
    assert ups.extract(bytes.fromhex("e600"), 0, 16) == 0x00E6
    assert ups.extract(bytes.fromhex("e600"), 8, 16) == 0x00E6 >> 8
    assert ups.extract(bytes.fromhex("09"), 0, 4) == 0x9
    assert ups.extract(bytes.fromhex("09"), 4, 4) == 0x0


def test_readings():
    # 100%, 3600 s runtime, ACPresent + FullyCharged set
    reports = {
        0x08: bytes.fromhex("64") + (3600).to_bytes(2, "little") + bytes.fromhex("09")
    }
    reports[0x10] = (230).to_bytes(2, "little") + (500).to_bytes(2, "little")
    reports[0x12] = (230).to_bytes(2, "little") + bytes([14])
    reports[0x13] = (136).to_bytes(2, "little")

    data = ups.read_hidraw(fake_device(reports))
    assert data["source"] == "hidraw"
    assert data["battery"] == 100, data
    assert data["runtimeMin"] == 60, data
    assert data["inputVoltage"] == 230.0, data
    # 0.1 Hz unit exponent: 500 -> 50.0
    assert data["inputFrequency"] == 50.0, data
    assert data["outputVoltage"] == 230.0, data
    assert data["loadPercent"] == 14, data
    assert data["batteryVoltage"] == 13.6, data
    assert data["online"] is True and data["onBattery"] is False, data
    assert data["fullyCharged"] is True, data
    assert data["status"] == "fullyCharged", data


def test_on_battery_and_low():
    reports = {
        0x08: bytes.fromhex("0f") + (900).to_bytes(2, "little") + bytes.fromhex("14")
    }
    # 15% charge, 900 s, Discharging + BelowRemainingCapacityLimit
    reports[0x10] = (0).to_bytes(2, "little") + (0).to_bytes(2, "little")
    reports[0x12] = (0).to_bytes(2, "little") + bytes([40])
    reports[0x13] = (132).to_bytes(2, "little")

    data = ups.read_hidraw(fake_device(reports))
    assert data["onBattery"] is True, data
    assert data["lowBattery"] is True, data
    assert data["status"] == "lowBattery", data
    assert data["runtimeMin"] == 15, data
    assert data["inputVoltage"] == 0.0, data


def test_pwrstat_parse():
    sample = """
The UPS information shows as following:

\tProperties:
\t\tModel Name................... CP1500PFCLCD
\t\tFirmware Number.............. BFH8102-6O1.5
\t\tRating Voltage............... 230 V
\t\tRating Power................. 900 Watt(1500 VA)

\tCurrent UPS status:
\t\tState........................ Normal
\t\tPower Supply by.............. Utility Power
\t\tUtility Voltage.............. 231 V
\t\tOutput Voltage............... 230 V
\t\tUtility Frequency............ 50.0 Hz
\t\tBattery Capacity............. 100 %
\t\tRemaining Runtime............ 62 min.
\t\tLoad......................... 96 Watt(14 %)
\t\tTest Result.................. Passed at 2026/10/12 21:00:00
\t\tLast Power Event............. Blackout at 2026/10/12 20:00:00
"""
    # Exercise the parsing half of the pwrstat backend without the binary.
    raw = ups.parse_pwrstat_status(sample)
    assert raw["model"] == "CP1500PFCLCD", raw
    assert raw["battery"] == "100 %", raw
    assert raw["loadRaw"] == "96 Watt(14 %)", raw
    assert raw["testResult"] == "Passed at 2026/10/12 21:00:00", raw

    data = ups.normalize_pwrstat(raw)
    assert data["battery"] == 100, data
    assert data["runtimeMin"] == 62, data
    assert data["loadPercent"] == 14 and data["loadWatts"] == 96, data
    assert data["inputVoltage"] == 231.0, data
    assert data["status"] == "online", data


def test_control_actions():
    """The CLI's control actions must map to the right usage/value.

    UpsDevice.find is stubbed out so this exercises the dispatch table without
    writing anything to a real UPS.
    """
    import contextlib
    import io

    calls = []

    class FakeDevice:
        def command(self, *usages, value):
            calls.append((usages, value))
            return True

    original_find = ups.UpsDevice.find
    original_which = ups._which
    ups.UpsDevice.find = staticmethod(lambda *a, **k: FakeDevice())
    ups._which = lambda name: None
    try:
        expected = {
            "quick": (ups.U_TEST, 1),
            "test": (ups.U_TEST, 1),
            "deep": (ups.U_TEST, 2),
            "abort": (ups.U_TEST, 3),
            "mute": (ups.U_AUDIBLE_ALARM, 3),
        }
        for action, (usage, value) in expected.items():
            calls.clear()
            with contextlib.redirect_stdout(io.StringIO()):
                code = ups.main([action, "--source", "hidraw"])
            assert code == 0, (action, code)
            assert calls == [((usage,), value)], (action, calls)
    finally:
        ups.UpsDevice.find = staticmethod(original_find)
        ups._which = original_which


def test_config_and_set():
    """read_config / set_setting drive the right usage, with the right value."""
    reports = {
        0x20: bytes([2]) + (170).to_bytes(2, "little") + (290).to_bytes(2, "little"),
        0x21: bytes([30]) + bytes([10]),
    }
    device = fake_device(reports)

    settings = {entry["key"]: entry for entry in ups.read_config(device)["settings"]}

    assert settings["alarm"]["value"] == 2
    assert settings["alarm"]["valueLabel"] == "enabled"
    assert settings["alarm"]["choices"] == {
        "1": "disabled",
        "2": "enabled",
        "3": "muted",
    }
    assert settings["warningLimit"]["value"] == 30
    assert settings["capacityLimit"]["value"] == 10

    low = settings["lowVoltageTransfer"]
    assert (low["value"], low["min"], low["max"]) == (170, 167, 175), low
    assert low["writable"] is True
    assert settings["warningLimit"]["offAllowed"] is False
    assert settings["alarm"]["offAllowed"] is False

    calls = []
    device.command = lambda *usages, value: (calls.append((usages, value)), True)[1]

    # Out-of-range values are clamped to what the descriptor declares.
    ok, applied = ups.set_setting("highVoltageTransfer", 300, device)
    assert (ok, applied) == (True, 295), (ok, applied)
    assert calls == [((ups.U_HIGH_VOLTAGE_TRANSFER,), 295)], calls

    ok, applied = ups.set_setting("alarm", "3", device)
    assert (ok, applied) == (True, 3), (ok, applied)

    for key, value in (("alarm", 9), ("nope", 1)):
        try:
            ups.set_setting(key, value, device)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{key}={value} should have been rejected")


def test_set_off_sentinel():
    """The delay fields' "off" sentinel round-trips through set_setting.

    A negative logical minimum means "off" (delay disabled). Writing the
    string "off" must land the sentinel itself, not a clamped zero.
    """
    field = ups.Field(
        size=16, log_min=-1, log_max=32767, phy_min=-60, phy_max=1966020, has_phy=1
    )
    calls = []

    class FakeDevice:
        def field_for(self, *usages):
            return field

        def command(self, *usages, value):
            calls.append((usages, value))
            return True

    ok, applied = ups.set_setting("delayShutdown", "off", FakeDevice())
    assert (ok, applied) == (True, -60), (ok, applied)
    assert calls == [((ups.U_DELAY_BEFORE_SHUTDOWN,), -1)], calls

    # A setting without that sentinel refuses to be turned off.
    plain = ups.Field(size=8, log_min=1, log_max=3, phy_min=1, phy_max=3, has_phy=1)

    class PlainDevice:
        def field_for(self, *usages):
            return plain

        def command(self, *usages, value):
            raise AssertionError("must not write")

    try:
        ups.set_setting("alarm", "off", PlainDevice())
    except ValueError:
        pass
    else:
        raise AssertionError('alarm="off" should have been rejected')


def test_physical_mapping():
    """A logical/physical range is converted both ways, sentinel included."""
    field = ups.Field(
        size=16, log_min=-1, log_max=32767, phy_min=-60, phy_max=1966020, has_phy=1
    )
    assert ups._logical_value(field, 0xFFFF) == -1  # all-ones is signed -1
    assert ups.to_physical(field, 0xFFFF) == -60.0
    assert ups.to_physical(field, 10) == 600.0  # logical minutes -> seconds
    assert ups._physical_to_logical(field, 600) == 10
    assert ups._physical_to_logical(field, 0) == 0


# --------------------------------------------------------------------------
# listening: reacting to the reports the UPS pushes by itself
# --------------------------------------------------------------------------


#: the status byte, as the device would push it: 100% charge, 3600 s runtime
def pushed_body(flags_byte):
    return bytes.fromhex("64") + (3600).to_bytes(2, "little") + bytes([flags_byte])


#: the same, framed the way it arrives on the interrupt endpoint: id byte first
def pushed_record(flags_byte):
    return bytes([8]) + pushed_body(flags_byte)


def test_listen_reports_comes_from_the_descriptor():
    """Which reports to wait for is read off the device, not hardcoded."""
    assert ups.listen_reports(fake_device({})) == (8,)


def test_pushed_flags_need_a_complete_burst():
    """A burst without the flag reports says nothing, rather than "all clear"."""
    device = fake_device({})
    needed = ups.listen_reports(device)
    assert ups.pushed_flags(device, {}, needed) is None
    assert ups.pushed_flags(device, {0x20: b"\x00\x00"}, needed) is None

    flags = ups.pushed_flags(device, {8: pushed_body(0x09)}, needed)
    assert flags is not None
    seen = dict(zip(ups.LISTEN_FLAGS, flags))
    assert seen[ups.U_AC_PRESENT] == 1 and seen[ups.U_FULLY_CHARGED] == 1, seen
    assert seen[ups.U_DISCHARGING] == 0 and seen[ups.U_BOOST] == 0, seen


def test_pushed_flags_refuse_a_truncated_report():
    """A short body would read as cleared flags, so it is not believed."""
    device = fake_device({})
    needed = ups.listen_reports(device)
    body = pushed_body(0x09)
    assert ups.pushed_flags(device, {8: body}, needed) is not None
    assert ups.pushed_flags(device, {8: body[:3]}, needed) is None
    assert ups.pushed_flags(device, {8: body[:1]}, needed) is None


def test_a_write_is_refused_when_the_report_cannot_be_read():
    """A body that was not read back would zero every other field."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "hidraw")
        with open(path, "wb"):
            pass
        device = fake_device({})
        device.path = path  # the node opens; the read fails

        def failing(report_id, report_type, fd=None):
            raise OSError("no such device")

        device._read_report = failing
        try:
            device.command(ups.U_AUDIBLE_ALARM, value=3)
        except ups.NoDevice as error:
            assert "nothing was written" in str(error), error
        else:
            raise AssertionError("the write should have been refused")


def test_nut_high_battery_is_not_a_low_battery():
    """NUT's HB means the battery has recovered; it is not a warning."""
    with tempfile.TemporaryDirectory() as tmp:
        upsc = os.path.join(tmp, "upsc")
        with open(upsc, "w", encoding="utf-8") as handle:
            handle.write(
                "#!/bin/sh\n"
                "printf 'ups.status: OL HB\\n'\n"
                "printf 'battery.charge: 100\\n'\n"
            )
        os.chmod(upsc, 0o755)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = tmp + os.pathsep + old_path
        try:
            data = ups.read_nut("ups1")
        finally:
            os.environ["PATH"] = old_path
    assert data["status"] == "online", data
    assert data["lowBattery"] is False, data


def test_the_history_trim_waits_for_another_writer():
    """The trim must not drop a line another writer is appending."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "history.jsonl")
        old_limit = ups.HISTORY_MAX_BYTES
        ups.HISTORY_MAX_BYTES = 400
        try:
            with open(path, "w", encoding="utf-8") as handle:
                for index in range(40):
                    handle.write(json.dumps({"t": index, "kind": "sample"}) + "\n")

            done = []
            with open(path, "a", encoding="utf-8") as writer:
                ups._lock_history(writer)

                def trim():
                    ups._trim_history(path)
                    done.append(True)

                thread = threading.Thread(target=trim)
                thread.start()
                time.sleep(0.2)
                assert not done, "the trim rewrote the file under a writer"
                writer.write(json.dumps({"t": 9999, "kind": "event"}) + "\n")
                ups._unlock_history(writer)
                thread.join(5)
            assert done, "the trim never finished"
            with open(path, encoding="utf-8") as handle:
                records = [json.loads(line) for line in handle if line.strip()]
        finally:
            ups.HISTORY_MAX_BYTES = old_limit
    assert any(record.get("t") == 9999 for record in records), records[-3:]


def test_pushed_flags_agree_with_the_feature_reports():
    """The pushed copy decodes to what a poll would have read."""
    body = pushed_body(0x0D)  # AC present + discharging
    device = fake_device({8: body})
    pushed = ups.pushed_flags(device, {8: body}, ups.listen_reports(device))
    feature = device.flags_of({8: body}, *ups.LISTEN_FLAGS)
    assert pushed == tuple(int(feature.get(usage, 0)) for usage in ups.LISTEN_FLAGS)
    assert dict(zip(ups.LISTEN_FLAGS, pushed))[ups.U_DISCHARGING] == 1


def test_listen_records_only_when_a_pushed_flag_moves():
    """The loop wakes a full reading on a change, and stays quiet otherwise.

    The device is a FIFO here, so bursts can be fed in by hand; the readings a
    full read would produce are stubbed, and follow whatever the burst said.
    """
    import threading

    original = {
        "read_hidraw": ups.read_hidraw,
        "LISTEN_QUIET": ups.LISTEN_QUIET,
        "LISTEN_TICK": ups.LISTEN_TICK,
    }
    with temp_state(), tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "pushed")
        os.mkfifo(path)
        device = fake_device({})
        device.path = path

        state = {"discharging": False}
        readings = []

        def fake_read_hidraw(device=None):
            readings.append(state["discharging"])
            return reading(
                online=not state["discharging"],
                onBattery=state["discharging"],
                status="onBattery" if state["discharging"] else "fullyCharged",
            )

        failures = []

        def run():
            try:
                ups._listen_loop(ups.Recorder(), 60.0, device)
            except BaseException as error:  # the loop only ever leaves by raising
                failures.append(error)

        ups.read_hidraw = fake_read_hidraw
        ups.LISTEN_QUIET = 0.2  # keep the test quick
        ups.LISTEN_TICK = 0.05
        try:
            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            time.sleep(0.3)  # the reader opens, then the first reading
            writer = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
            os.write(writer, pushed_record(0x09))  # mains up, fully charged
            time.sleep(0.5)
            state["discharging"] = True
            os.write(writer, pushed_record(0x0D))  # ...now on battery
            time.sleep(0.5)
            os.write(writer, pushed_record(0x0D))  # a repeat changes nothing
            time.sleep(0.5)
            os.close(writer)  # unplugged
            thread.join(timeout=5)
        finally:
            ups.read_hidraw = original["read_hidraw"]
            ups.LISTEN_QUIET = original["LISTEN_QUIET"]
            ups.LISTEN_TICK = original["LISTEN_TICK"]

        assert not thread.is_alive(), "the loop should stop at end of file"
        assert len(failures) == 1 and isinstance(failures[0], ups.NoDevice), failures

        # one reading at the start, one when the flag moved, and no more
        assert readings == [False, True], readings
        records = ups.read_history()
        assert [record["kind"] for record in records].count("sample") == 2, records
        assert [record["event"] for record in records if record["kind"] == "event"] == [
            "mainsLost",
            "onBattery",
        ], records


def test_listen_falls_back_to_polling_without_an_endpoint():
    """pwrstat and NUT have no interrupt endpoint, so listening is not offered."""
    calls = []
    original_poll = ups.poll_loop
    try:
        ups.poll_loop = (
            lambda rec, interval, source, ups_name: calls.append(source) or 0
        )
        assert ups.watch(60.0, "pwrstat", None, False, listen=True) == 0
        assert calls == ["pwrstat"], calls
    finally:
        ups.poll_loop = original_poll


# --------------------------------------------------------------------------
# diagnostics: the crash log and the opt-in debug trace
# --------------------------------------------------------------------------


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
            ups.DIAG.debug_enabled = False


def test_crash_log_records_the_traceback():
    with temp_state() as directory:
        try:
            raise ValueError("boom")
        except ValueError as error:
            path = ups.Diag().crash(error)
        assert path == os.path.join(directory, "crash.log")
        text = open(path, encoding="utf-8").read()
        assert "Traceback" in text and "boom" in text


def test_debug_trace_is_silent_until_enabled():
    with temp_state() as directory:
        log = os.path.join(directory, "debug.log")
        diag = ups.Diag()
        diag.debug("not written")
        assert not os.path.exists(log)
        diag.debug_enabled = True
        diag.debug("a report was read")
        assert "a report was read" in open(log, encoding="utf-8").read()


def test_logs_rotate_once_and_never_raise():
    with temp_state() as directory:
        diag = ups.Diag()
        path = os.path.join(directory, "debug.log")
        diag._append("debug.log", "x" * (ups.LOG_MAX_BYTES + 1))
        diag._append("debug.log", "second\n")
        assert open(path + ".1", encoding="utf-8").read().startswith("x")
        assert open(path, encoding="utf-8").read() == "second\n"
        # a log that cannot be written must never take the reader down with it
        os.environ["XDG_STATE_HOME"] = "/proc/definitely/not/writable"
        diag._append("debug.log", "nowhere\n")


def test_main_traces_a_run_and_echoes_with_debug():
    with temp_state() as directory:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            assert ups.main(["status", "--source", "simulate", "--debug"]) == 0
        log = open(os.path.join(directory, "debug.log"), encoding="utf-8").read()
        assert "simulate" in log, log
        assert "debug: status --source simulate --debug" in err.getvalue()


# --------------------------------------------------------------------------
# history: samples, and the events between them
# --------------------------------------------------------------------------


def reading(**overrides):
    base = dict(ups.SIMULATED)
    base["updated"] = int(time.time())
    base.update(overrides)
    return base


def test_events_need_a_previous_sample():
    assert ups.events_between(None, reading()) == []


def test_events_are_quiet_when_nothing_changes():
    assert ups.events_between(reading(), reading()) == []


def test_events_report_mains_and_battery():
    after = reading(online=False, onBattery=True, status="onBattery", inputVoltage=0.0)
    events = ups.events_between(reading(), after)
    assert [event["event"] for event in events] == ["mainsLost", "onBattery"], events
    assert "Mains power lost" in events[0]["text"]
    assert [event["level"] for event in events] == ["bad", "warn"], events

    back = ups.events_between(after, reading())
    assert [event["event"] for event in back] == ["mainsRestored", "onBatteryEnd"]


def test_events_report_regulation_and_overload():
    before = reading()
    assert [
        event["event"] for event in ups.events_between(before, reading(boost=True))
    ] == ["boost"]
    assert [
        event["event"] for event in ups.events_between(before, reading(overload=True))
    ] == ["overload"]
    assert [
        event["event"]
        for event in ups.events_between(before, reading(replaceBattery=True))
    ] == ["replaceBattery"]


def test_events_report_voltage_out_of_band():
    before = reading()  # 231 V against 230 V nominal
    high = ups.events_between(before, reading(inputVoltage=260.0))
    assert [event["event"] for event in high] == ["voltageHigh"], high
    assert "above 10% of nominal" in high[0]["text"], high

    assert [
        event["event"]
        for event in ups.events_between(before, reading(inputVoltage=180.0))
    ] == ["voltageLow"]
    # staying out of band is one line, not one per sample...
    assert (
        ups.events_between(reading(inputVoltage=260.0), reading(inputVoltage=258.0))
        == []
    )
    # ...and coming back is another
    normal = ups.events_between(
        reading(inputVoltage=260.0), reading(inputVoltage=231.0)
    )
    assert [event["event"] for event in normal] == ["voltageNormal"]

    # a narrower band on the same reading counts as out of range
    tight = ups.events_between(before, reading(inputVoltage=245.0), band=5.0)
    assert [event["event"] for event in tight] == ["voltageHigh"], tight


def test_events_report_a_finished_test():
    events = ups.events_between(
        reading(testResult="No test initiated"), reading(testResult="Passed")
    )
    assert [event["event"] for event in events] == ["testResult"]
    assert "Passed" in events[0]["text"]


def test_sample_record_keeps_only_the_reading_fields():
    record = ups.sample_record(reading(updated=1792400000))
    assert record["kind"] == "sample" and record["t"] == 1792400000
    assert record["inputVoltage"] == 231.0 and record["battery"] == 100
    assert "device" not in record and "model" not in record


def test_history_appends_reads_back_and_filters():
    with temp_state():
        ups.append_history(
            [
                ups.sample_record(reading(updated=100)),
                ups._event("x", "hello", "info", int(time.time())),
            ]
        )
        records = ups.read_history()
        assert len(records) == 2 and records[1]["text"] == "hello"
        assert ups.select_history(records, None, None, events_only=True) == [records[1]]
        assert ups.select_history(records, 1, None, events_only=False) == [records[1]]
        assert ups.select_history(records, None, 60, events_only=False) == records[-1:]
        assert ups.select_history(records, 0, None, events_only=False) == records


def test_history_survives_damage_and_keeps_the_newest():
    with temp_state():
        path = ups.history_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('not json\n{"t": 1, "kind": "event", "text": "old"}\n')
        assert [record["text"] for record in ups.read_history()] == ["old"]

        original = ups.HISTORY_MAX_BYTES
        ups.HISTORY_MAX_BYTES = 120  # smaller than one sample
        try:
            for index in range(6):
                ups.append_history([ups.sample_record(reading(updated=100 + index))])
        finally:
            ups.HISTORY_MAX_BYTES = original
        kept = ups.read_history()
        assert 0 < len(kept) < 6, kept
        assert kept[-1]["t"] == 105 and kept[-1]["kind"] == "sample"


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
