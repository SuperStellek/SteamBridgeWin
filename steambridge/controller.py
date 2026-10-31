"""SDL3 HIDAPI Steam Controller backend.

SDL3 decodes 2015 USB / wireless dongle / BLE packet variants, handles
the interleaved left stick + left touchpad, exposes gyro/accel, and restores
the controller's desktop lizard mode when the joystick is closed.
No Steam client, game modification, or custom Windows HID driver is required.
"""
from __future__ import annotations

import ctypes as C
from pathlib import Path
import os
import sys
import time

from .model import Frame, Pad

SDL_INIT_GAMEPAD = 0x00002000
SDL_SENSOR_ACCEL = 1
SDL_SENSOR_GYRO = 2
SDL_GAMEPAD_TYPE_STEAM = 10
SDL_HAT_UP = 0x01
SDL_HAT_RIGHT = 0x02
SDL_HAT_DOWN = 0x04
SDL_HAT_LEFT = 0x08

# Standard SDL_GamepadButton numbers, as in SDL3/SDL_gamepad.h.
BUTTON_IDS = {
    "a": 0, "b": 1, "x": 2, "y": 3, "back": 4, "steam": 5,
    "start": 6, "left_stick_click": 7, "right_pad_click": 8,
    "lb": 9, "rb": 10, "dpad_up": 11, "dpad_down": 12,
    "dpad_left": 13, "dpad_right": 14,
    "right_grip": 16, "left_grip": 17,
}
AXES = {"lx": 0, "ly": 1, "rx": 2, "ry": 3, "lt": 4, "rt": 5}


def _clamp(value: float) -> float:
    return max(-1.0, min(1.0, value))


def _normal_axis(n: int) -> float:
    return _clamp(n / (32767.0 if n >= 0 else 32768.0))


class SDLController:
    def __init__(self):
        candidates = []
        explicit = os.environ.get("STEAMBRIDGE_SDL3_DLL")
        if explicit:
            candidates.append(explicit)
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
        candidates.extend([str(base / "SDL3.dll"), "SDL3.dll"])
        last_error = None
        for filename in candidates:
            try:
                self.lib = C.CDLL(filename)
                break
            except OSError as error:
                last_error = error
        else:
            raise RuntimeError("SDL3.dll was not found. Use the packaged release, or install SDL3 runtime. "
                               + str(last_error))
        self._bind()
        self.lib.SDL_SetHint(b"SDL_JOYSTICK_HIDAPI_STEAM", b"1")
        self.lib.SDL_SetHint(b"SDL_JOYSTICK_HIDAPI", b"1")
        self.lib.SDL_SetHint(b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", b"1")
        if not self.lib.SDL_Init(SDL_INIT_GAMEPAD):
            raise RuntimeError("SDL3 initialization failed: " + self.error())
        self.gamepad = None
        self.joystick = None
        self.instance_id = None
        self.name = ""
        self.legacy = False
        self.last_scan = 0.0

    def _bind(self):
        ptr = C.c_void_p
        integer = C.c_int
        boolean = C.c_bool
        lib = self.lib

        def fn(name, restype, *args):
            method = getattr(lib, name)
            method.restype = restype
            method.argtypes = list(args)
            return method

        fn("SDL_SetHint", boolean, C.c_char_p, C.c_char_p)
        fn("SDL_Init", boolean, C.c_uint32)
        fn("SDL_Quit", None)
        fn("SDL_GetError", C.c_char_p)
        fn("SDL_free", None, ptr)
        fn("SDL_PumpEvents", None)
        fn("SDL_UpdateGamepads", None)
        fn("SDL_GetGamepads", C.POINTER(C.c_uint32), C.POINTER(integer))
        fn("SDL_GetGamepadNameForID", C.c_char_p, C.c_uint32)
        fn("SDL_GetGamepadVendorForID", C.c_uint16, C.c_uint32)
        fn("SDL_GetGamepadTypeForID", integer, C.c_uint32)
        fn("SDL_OpenGamepad", ptr, C.c_uint32)
        fn("SDL_CloseGamepad", None, ptr)
        fn("SDL_GamepadConnected", boolean, ptr)
        fn("SDL_GetGamepadJoystick", ptr, ptr)
        fn("SDL_GetJoystickProduct", C.c_uint16, ptr)
        fn("SDL_GetJoystickButton", boolean, ptr, integer)
        fn("SDL_GetGamepadButton", boolean, ptr, integer)
        fn("SDL_GetGamepadAxis", C.c_int16, ptr, integer)
        fn("SDL_GetNumGamepadTouchpads", integer, ptr)
        fn("SDL_GetGamepadTouchpadFinger", boolean, ptr, integer, integer,
           C.POINTER(boolean), C.POINTER(C.c_float), C.POINTER(C.c_float),
           C.POINTER(C.c_float))
        fn("SDL_GamepadHasSensor", boolean, ptr, integer)
        fn("SDL_SetGamepadSensorEnabled", boolean, ptr, integer, boolean)
        fn("SDL_GetGamepadSensorData", boolean, ptr, integer,
           C.POINTER(C.c_float), integer)
        fn("SDL_SendGamepadEffect", boolean, ptr, ptr, integer)

    def error(self):
        return (self.lib.SDL_GetError() or b"unknown error").decode("utf-8", "replace")

    def search(self) -> bool:
        """Single-joystick mode. Re-enumeration also handles dongle hotplug."""
        if self.gamepad and self.lib.SDL_GamepadConnected(self.gamepad):
            return True
        self.disconnect()
        count = C.c_int()
        ids = self.lib.SDL_GetGamepads(C.byref(count))
        if not ids:
            return False
        try:
            for index in range(count.value):
                instance = int(ids[index])
                vendor = int(self.lib.SDL_GetGamepadVendorForID(instance))
                name = (self.lib.SDL_GetGamepadNameForID(instance) or b"").decode("utf-8", "replace")
                kind = self.lib.SDL_GetGamepadTypeForID(instance)
                # Never loop back our own virtual Xbox device.
                if vendor != 0x28DE or ("steam" not in name.lower() and kind != SDL_GAMEPAD_TYPE_STEAM):
                    continue
                gamepad = self.lib.SDL_OpenGamepad(instance)
                if not gamepad:
                    continue
                self.gamepad = gamepad
                self.joystick = self.lib.SDL_GetGamepadJoystick(gamepad)
                self.instance_id = instance
                self.name = name
                # 2015 SC: VID 28de:1102 USB / 1142 dongle / 1105/1106 BLE.
                self.legacy = self.lib.SDL_GetJoystickProduct(self.joystick) in (0x1102, 0x1142, 0x1105, 0x1106)
                for sensor in (SDL_SENSOR_GYRO, SDL_SENSOR_ACCEL):
                    if self.lib.SDL_GamepadHasSensor(gamepad, sensor):
                        self.lib.SDL_SetGamepadSensorEnabled(gamepad, sensor, True)
                return True
        finally:
            self.lib.SDL_free(C.cast(ids, C.c_void_p))
        return False

    def _pad(self, idx: int) -> Pad:
        if self.lib.SDL_GetNumGamepadTouchpads(self.gamepad) <= idx:
            return Pad()
        down = C.c_bool()
        x, y, pressure = C.c_float(), C.c_float(), C.c_float()
        ok = self.lib.SDL_GetGamepadTouchpadFinger(self.gamepad, idx, 0,
                                                  C.byref(down), C.byref(x),
                                                  C.byref(y), C.byref(pressure))
        return Pad(bool(down.value), float(x.value), float(y.value),
                   float(pressure.value)) if ok else Pad()

    def _sensor(self, sensor: int):
        values = (C.c_float * 3)()
        if self.lib.SDL_GamepadHasSensor(self.gamepad, sensor):
            if self.lib.SDL_GetGamepadSensorData(self.gamepad, sensor, values, 3):
                return tuple(float(v) for v in values)
        return (0.0, 0.0, 0.0)

    def poll(self) -> Frame | None:
        self.lib.SDL_PumpEvents()
        self.lib.SDL_UpdateGamepads()
        now = time.monotonic()
        if not self.gamepad or not self.lib.SDL_GamepadConnected(self.gamepad):
            if now - self.last_scan >= 1.0:
                self.last_scan = now
                self.search()
            if not self.gamepad:
                return None
        buttons = {name for name, code in BUTTON_IDS.items()
                   if self.lib.SDL_GetGamepadButton(self.gamepad, code)}
        # SDL3's 2015 controller driver exposes its two grips as raw buttons
        # 12 (left) and 11 (right). SDL gamepad mapping may omit these.
        if self.legacy and self.joystick:
            if self.lib.SDL_GetJoystickButton(self.joystick, 12):
                buttons.add("left_grip")
            if self.lib.SDL_GetJoystickButton(self.joystick, 11):
                buttons.add("right_grip")
        axes = {key: _normal_axis(self.lib.SDL_GetGamepadAxis(self.gamepad, idx))
                for key, idx in AXES.items()}
        axes["lt"] = max(0.0, axes["lt"])
        axes["rt"] = max(0.0, axes["rt"])
        if axes["lt"] >= 0.95:
            buttons.add("left_trigger_click")  # threshold surrogate; not HID switch
        if axes["rt"] >= 0.95:
            buttons.add("right_trigger_click")
        left = self._pad(0)
        right = self._pad(1)
        if left.down:
            buttons.add("left_pad_touch")
        if right.down:
            buttons.add("right_pad_touch")
        if left.clicked:
            buttons.add("left_pad_click")
        if right.clicked:
            buttons.add("right_pad_click")
        return Frame(name=self.name, legacy=self.legacy, buttons=buttons, left_pad=left, right_pad=right,
                     gyro=self._sensor(SDL_SENSOR_GYRO), accel=self._sensor(SDL_SENSOR_ACCEL),
                     **axes)

    def led(self, brightness: int) -> None:
        self.lib.SDL_SetHint(b"SDL_JOYSTICK_HIDAPI_STEAM_HOME_LED",
                             str(max(0, min(100, brightness))).encode())

    def haptic_pulse(self, side: str, duration_us: int = 350) -> bool:
        """2015 SC vendor feature report; SDL HIDAPI transports USB/dongle/BLE.

        A short rumble click; not claimed for newer controllers with a
        different effect protocol.
        """
        if not self.gamepad or not self.legacy:
            return False
        packet = (C.c_ubyte * 65)()
        packet[0] = 0  # HID feature report ID
        packet[1] = 0x8F  # ID_TRIGGER_HAPTIC_PULSE
        # Original Steam Controller firmware's side numbering.
        packet[2] = 0 if side == "left" else 1
        duration_us = max(50, min(3000, duration_us))
        packet[3:5] = (duration_us & 255, duration_us >> 8)
        packet[5:9] = (0, 0, 1, 0)  # interval=0, count=1
        return bool(self.lib.SDL_SendGamepadEffect(self.gamepad, packet, 65))

    def disconnect(self) -> None:
        if self.gamepad:
            self.lib.SDL_CloseGamepad(self.gamepad)
        self.gamepad = None
        self.joystick = None
        self.instance_id = None
        self.name = ""
        self.legacy = False

    def close(self) -> None:
        self.disconnect()
        self.lib.SDL_Quit()
