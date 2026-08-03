#!/usr/bin/env bash
# Install client_quest3_test.apk to Quest 3 via ADB
cd "$(dirname "$0")"
ADB="../tools/adb.exe"
APK="client_quest3_test.apk"

[ -f "$APK" ] || { echo "[ERROR] APK not found: $APK"; exit 1; }

echo "[1/2] Checking device..."
"$ADB" devices

echo "[2/2] Installing APK..."
if "$ADB" install -r "$APK"; then
    echo
    echo "[OK] Install success."
else
    echo
    echo "[FAIL] Install failed. Check:"
    echo "  - USB cable connected"
    echo "  - Device authorized (accept 'Allow USB debugging' dialog)"
    echo "  - Developer Mode enabled in Meta Horizon app"
    exit 1
fi
