#!/usr/bin/env bash
# Install the udev rule that lets the active user open a CyberPower UPS's
# hidraw node. Needs root; run it with sudo (or pkexec from a GUI).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
rule="60-cyberpower-ups.rules"
target="/etc/udev/rules.d/$rule"

if [[ $EUID -ne 0 ]]; then
  echo "This script installs a rules file into /etc, so it needs root." >&2
  echo "Re-run it: sudo $0" >&2
  exit 1
fi

# The rule is copied to a root-owned temporary file first, and installed from
# there. A udev rule can run commands, so what lands in /etc should be a
# snapshot of the file rather than a symlink into a directory the user owns.
staging="$(mktemp /tmp/cyberpower-ups-rules.XXXXXX)"
trap 'rm -f "$staging"' EXIT
install -m 0644 "$here/$rule" "$staging"
install -m 0644 "$staging" "$target"

udevadm control --reload
udevadm trigger --subsystem-match=hidraw

echo "Installed $target and reloaded udev."
echo "Re-plug the UPS (or just wait a moment) and check with:"
echo "  udevadm info -q all -n /dev/hidraw0 | grep -i uaccess"
echo "  # the node number is yours, not always 0 -- find it with:"
echo "  ls /dev/hidraw*"
