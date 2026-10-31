# SteamBridgeWin

Standalone Windows bridge for Steam Controllers and non-Steam games. Experimental 0.1.0, not hardware-verified.

Uses SDL3's Steam HIDAPI backend to decode original 2015 Steam Controller USB, dongle and BLE firmware reports. Exposes buttons, stick, grips, triggers, trackpads, gyro and accelerometer. Converts them to mouse/keyboard via Win32 SendInput and an optional virtual Xbox 360 pad via vgamepad/ViGEmBus.

## Features

- Detects controller and reconnects; editable per-game executable profiles.
- Independent left/right pad modes: mouse with momentum, D-pad, left/right stick or disabled.
- Gyro aiming with touch/grip activation, sensitivity, deadzones, customizable button mappings and LED brightness.
- Experimental haptic pulse feedback and Xbox rumble forwarding on the original 2015 controller.
- Auto-update checks newest stable GitHub Release at startup and every six hours. Opt-in automatic install/restart. Checks the Windows ZIP against its published SHA-256 checksum and installs via a detached updater process.

## Windows installation

1. Download a Windows ZIP from https://github.com/SuperStellek/SteamBridgeWin/releases when a release is published. Extract the entire ZIP to a writable directory (not Program Files) and run SteamBridgeWin.exe.
2. Install ViGEmBus for virtual Xbox output from https://github.com/nefarius/ViGEmBus/releases . Mouse/keyboard mapping still works without the driver.
3. Fully exit Steam to prevent input contention. Connect your Steam Controller using USB, official dongle or suitable BLE firmware. Configure profiles and bindings in the GUI.
4. Under Updates, enable automatic installation if wanted. Downloaded updates are verified with SHA-256 and applied after the app quits.

User profiles are stored outside the installation folder at %LOCALAPPDATA%/SteamBridgeWin/config.json; updates preserve them. Updater errors are logged to update-error.txt in that directory. A checksum proves file integrity, not publisher authenticity if a GitHub release itself was compromised.

## Source development

Install Python 3.12+, vgamepad, and SDL3.dll from https://github.com/libsdl-org/SDL/releases (place DLL next to run.py or set STEAMBRIDGE_SDL3_DLL). Install dependencies: pip install -r requirements.txt . Run: python run.py . Test: python -m unittest discover -s tests -v .

The GitHub Actions Windows build runs tests, packages the SDL3 runtime and standalone updater, and publishes a ZIP with checksum on vMAJOR.MINOR.PATCH tags.

## Known limitations

- Not Steam Input feature parity. XInput cannot carry native gyro, pads or pressure; these are converted to mapped actions.
- 2015 touchpad clicks rely on SDL's touch-pressure convention. Trigger digital clicks are approximated by analog thresholds; verify on physical hardware.
- Single controller. Full Steam Input action layers, radial menus, touch gestures and native HD haptic scripting are not implemented.
- Compatibility with newer Steam Controllers and with anti-cheat games is not guaranteed.
- Automatic updates need a writable extracted installation, and a release must be published first.

Project source is MIT-licensed. SDL3 is zlib-licensed; vgamepad and ViGEmBus are separately maintained.
