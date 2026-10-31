"""XInput-compatible virtual controller (ViGEm) and Win32 SendInput output."""
from __future__ import annotations

import ctypes as C
import os
from .model import OutputFrame

# Win32 API: ctypes structures respect the correct 32/64-bit alignment.
ULONG_PTR = C.c_uint64 if C.sizeof(C.c_void_p) == 8 else C.c_uint32


class MOUSEINPUT(C.Structure):
    _fields_ = [("dx", C.c_long), ("dy", C.c_long),
                ("mouseData", C.c_uint32), ("dwFlags", C.c_uint32),
                ("time", C.c_uint32), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(C.Structure):
    _fields_ = [("wVk", C.c_uint16), ("wScan", C.c_uint16),
                ("dwFlags", C.c_uint32), ("time", C.c_uint32),
                ("dwExtraInfo", ULONG_PTR)]


class INPUTUNION(C.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(C.Structure):
    _fields_ = [("type", C.c_uint32), ("data", INPUTUNION)]


VK = {
    "space": 0x20, "shift": 0x10, "ctrl": 0x11, "alt": 0x12,
    "tab": 0x09, "esc": 0x1B, "enter": 0x0D, "backspace": 0x08,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    **{key: ord(key.upper()) for key in "abcdefghijklmnopqrstuvwxyz12345"},
}
MOUSE_BUTTONS = {
    "left": (0x0002, 0x0004), "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}


class KeyboardMouse:
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("Keyboard/mouse output is Windows only.")
        self.send = C.windll.user32.SendInput
        self.send.argtypes = (C.c_uint32, C.POINTER(INPUT), C.c_int)
        self.send.restype = C.c_uint32
        self.keys: set[str] = set()
        self.buttons: set[str] = set()

    def _send(self, input_: INPUT):
        if self.send(1, C.byref(input_), C.sizeof(INPUT)) != 1:
            raise OSError("SendInput failed. Check your game's input security/elevation.")

    def _key(self, name: str, down: bool):
        code = VK[name]
        self._send(INPUT(type=1, data=INPUTUNION(ki=KEYBDINPUT(
            wVk=code, wScan=0, dwFlags=0 if down else 0x0002, time=0,
            dwExtraInfo=0,
        ))))

    def _mouse_button(self, name: str, down: bool):
        flag = MOUSE_BUTTONS[name][0 if down else 1]
        self._send(INPUT(type=0, data=INPUTUNION(mi=MOUSEINPUT(
            dx=0, dy=0, mouseData=0, dwFlags=flag, time=0, dwExtraInfo=0,
        ))))

    def apply(self, frame: OutputFrame):
        for key in self.keys - frame.keys:
            self._key(key, False)
        for button in self.buttons - frame.mouse_buttons:
            self._mouse_button(button, False)
        for key in frame.keys - self.keys:
            self._key(key, True)
        for button in frame.mouse_buttons - self.buttons:
            self._mouse_button(button, True)
        self.keys = frame.keys.copy()
        self.buttons = frame.mouse_buttons.copy()
        if frame.mouse_dx or frame.mouse_dy:
            self._send(INPUT(type=0, data=INPUTUNION(mi=MOUSEINPUT(
                dx=int(frame.mouse_dx), dy=int(frame.mouse_dy),
                mouseData=0, dwFlags=0x0001, time=0, dwExtraInfo=0,
            ))))

    def release_all(self):
        self.apply(OutputFrame())


class VirtualXbox:
    def __init__(self):
        try:
            import vgamepad as vg
        except Exception as exc:
            raise RuntimeError("vgamepad unavailable; install dependencies and ViGEmBus: " + str(exc)) from exc
        self.vg = vg
        try:
            self.pad = vg.VX360Gamepad()
        except Exception as exc:
            raise RuntimeError("Could not create virtual Xbox 360 device; install ViGEmBus: " + str(exc)) from exc
        self.rumble = (0, 0)
        self.button_codes = {
            "a": vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
            "b": vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
            "x": vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
            "y": vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
            "back": vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
            "start": vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
            "lb": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
            "rb": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
            "ls": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
            "rs": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
            "dpad_up": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
            "dpad_down": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
            "dpad_left": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
            "dpad_right": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
        }
        # Some driver versions don't expose a guide button.
        guide = getattr(vg.XUSB_BUTTON, "XUSB_GAMEPAD_GUIDE", None)
        if guide is not None:
            self.button_codes["guide"] = guide

        def on_rumble(client, target, large_motor, small_motor, led, user_data):
            # The ViGEm notification thread may not call SDL functions.
            self.rumble = (int(large_motor), int(small_motor))

        self._on_rumble = on_rumble
        self.pad.register_notification(callback_function=self._on_rumble)

    def apply(self, frame: OutputFrame):
        self.pad.reset()
        for button in frame.xbox:
            if button in self.button_codes:
                self.pad.press_button(button=self.button_codes[button])
        self.pad.left_joystick_float(x_value_float=frame.lx, y_value_float=-frame.ly)
        self.pad.right_joystick_float(x_value_float=frame.rx, y_value_float=-frame.ry)
        self.pad.left_trigger_float(value_float=max(0.0, min(1.0, frame.lt)))
        self.pad.right_trigger_float(value_float=max(0.0, min(1.0, frame.rt)))
        self.pad.update()

    def release_all(self):
        self.apply(OutputFrame())
        self.rumble = (0, 0)

    def close(self):
        self.release_all()
        self.pad.unregister_notification()
