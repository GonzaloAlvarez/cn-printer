#!/bin/bash
# Start a container-local system bus + avahi (ippeveprinter's DNS-SD init
# needs them even when advertising is off), then run the virtual printer.
set -euo pipefail
mkdir -p /run/dbus /run/avahi-daemon
rm -f /run/dbus/pid /run/avahi-daemon/pid
dbus-daemon --system --fork
avahi-daemon -D --no-chroot
for _ in $(seq 1 20); do [ -S /run/avahi-daemon/socket ] && break; sleep 0.5; done
exec /usr/sbin/ippeveprinter "$@"
