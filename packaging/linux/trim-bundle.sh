#!/bin/sh
# Drop parts of Qt the app never loads (QML, Quick, the virtual keyboard, PDF)
# and the GTK theme plugin, which drags in the build machine's GTK.
set -e
d="$1/_internal"
q="$d/PySide6/Qt"
rm -f "$q/plugins/platforminputcontexts/libqtvirtualkeyboardplugin.so" \
      "$q/plugins/imageformats/libqpdf.so" \
      "$q/plugins/platformthemes/libqgtk3.so"
rm -f "$q"/lib/libQt6VirtualKeyboard* "$q"/lib/libQt6Qml* "$q"/lib/libQt6Quick* "$q"/lib/libQt6Pdf*
rm -f "$d"/libgtk-3.so.0 "$d"/libgdk-3.so.0 "$d"/libglycin-2.so.0
# The bundled fontconfig is older than the config files on current distros and floods stderr; use the system one.
rm -f "$d/libfontconfig.so.1" "$d/libfreetype.so.6"
