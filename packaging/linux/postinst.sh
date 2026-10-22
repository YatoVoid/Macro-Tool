#!/bin/sh
# Let the logged-in user use /dev/uinput right away, without a reboot.
modprobe uinput 2>/dev/null || true
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --subsystem-match=misc --sysname-match=uinput 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
fi
cat <<'MSG'

Macro Tool is installed. On Wayland, hotkeys and recording read your keyboard
directly, which needs the "input" group. Run this once and log out and back in:

    sudo usermod -aG input $USER

MSG
exit 0
