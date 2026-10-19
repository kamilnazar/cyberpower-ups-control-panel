"""A terminal control panel for a CyberPower UPS.

The panel is a *front end* and nothing more. It never opens USB, HID, pwrstat
or NUT itself; every reading, setting and action comes from running the
companion command-line reader (``bin/cyberpower-ups``) and parsing what it
prints. Keeping that boundary means the UPS protocol lives in exactly one
file, and this package is pure presentation that can be driven by a stub
reader in tests.

Modules:
    backend   the only place that spawns the reader (the "wrapper" around the CLI)
    formats   pure status/setting -> string helpers (no curses, no subprocess)
    widgets   small curses drawing primitives and a colour theme
    app       the tabbed application and its event loop

Run it as ``cyberpower-ups-tui`` or ``python3 -m cyberpower_tui``.
"""

#: Kept in step with ``manifest.json``'s ``version``; that file is the source
#: of truth, this is only so the package can report itself.
__version__ = "0.2.0"
