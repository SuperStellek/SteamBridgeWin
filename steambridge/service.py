"""Background 120 Hz controller bridge and automatic per-game profile selection."""
from __future__ import annotations

import ctypes as C
import os
from pathlib import Path
import queue
import threading
import time

from .controller import SDLController
from .mapper import Mapper
from .model import OutputFrame, Profile, Store
from .outputs import KeyboardMouse, VirtualXbox


def foreground_executable() -> str:
    """Only queries the foreground window's filename. No process injection."""
    if os.name != "nt":
        return ""
    from ctypes import wintypes as W
    user32 = C.windll.user32
    kernel32 = C.windll.kernel32
    user32.GetForegroundWindow.restype = W.HWND
    user32.GetWindowThreadProcessId.argtypes = (W.HWND, C.POINTER(W.DWORD))
    user32.GetWindowThreadProcessId.restype = W.DWORD
    kernel32.OpenProcess.argtypes = (W.DWORD, W.BOOL, W.DWORD)
    kernel32.OpenProcess.restype = W.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (
        W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD))
    kernel32.QueryFullProcessImageNameW.restype = W.BOOL
    kernel32.CloseHandle.argtypes = (W.HANDLE,)
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    pid = W.DWORD()
    user32.GetWindowThreadProcessId(hwnd, C.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if not handle:
        return ""
    try:
        buf = C.create_unicode_buffer(32768)
        size = W.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, C.byref(size)):
            return Path(buf.value).name.lower()
    finally:
        kernel32.CloseHandle(handle)
    return ""


class BridgeService:
    def __init__(self, store: Store):
        self.store = store
        self.messages: queue.Queue[dict] = queue.Queue(maxsize=80)
        self.commands: queue.Queue[tuple] = queue.Queue()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.active_profile_id = store.settings.profiles[0].id
        self.manual_profile_id = self.active_profile_id
        self.paused = False

    def emit(self, **payload):
        try:
            self.messages.put_nowait(payload)
        except queue.Full:
            pass

    def command(self, name: str, value=None):
        self.commands.put((name, value))

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="SteamBridge-HID", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3.0)

    def _selected(self, exe: str) -> Profile:
        profiles = self.store.settings.profiles
        if self.store.settings.auto_switch_profiles:
            return self.store.profile_for_exe(exe)
        return next((p for p in profiles if p.id == self.manual_profile_id), profiles[0])

    def _run(self):
        device = keyboard = xbox = None
        mapper = Mapper()
        try:
            device = SDLController()
            keyboard = KeyboardMouse()
            try:
                xbox = VirtualXbox()
                self.emit(kind="log", message="Virtual Xbox 360 controller connected.")
            except Exception as exc:
                self.emit(kind="warning", message=str(exc) + " Keyboard/mouse mappings still work.")
            self.emit(kind="log", message="SDL3 ready. Scanning for a Steam Controller.")
            was_connected = False
            prev_buttons = set()
            prev_profile_id = None
            exe = ""
            next_exe = 0.0
            next_status = 0.0
            next_haptic = 0.0
            next_led = None
            last_time = time.monotonic()
            while not self.stop_event.is_set():
                now = time.monotonic()
                dt = now - last_time
                last_time = now
                try:
                    while True:
                        name, value = self.commands.get_nowait()
                        if name == "pause":
                            self.paused = bool(value)
                        elif name == "manual_profile":
                            self.manual_profile_id = str(value)
                        elif name == "pulse" and device.gamepad:
                            device.haptic_pulse(str(value))
                except queue.Empty:
                    pass
                if now >= next_exe:
                    next_exe = now + 0.7
                    exe = foreground_executable()
                profile = self._selected(exe)
                self.active_profile_id = profile.id
                if profile.id != prev_profile_id:
                    if keyboard:
                        keyboard.release_all()
                    if xbox:
                        xbox.release_all()
                    mapper.reset()
                    prev_profile_id = profile.id
                    next_led = None
                    self.emit(kind="profile", name=profile.name, exe=exe)
                frame = device.poll()
                connected = frame is not None
                if connected != was_connected:
                    was_connected = connected
                    if not connected:
                        if keyboard:
                            keyboard.release_all()
                        if xbox:
                            xbox.release_all()
                        mapper.reset()
                        prev_buttons.clear()
                        self.emit(kind="connection", connected=False, name="")
                    else:
                        self.emit(kind="connection", connected=True, name=frame.name)
                        next_led = None
                if connected and next_led != profile.led_brightness:
                    device.led(profile.led_brightness)
                    next_led = profile.led_brightness
                if self.paused or not connected:
                    if self.paused:
                        if keyboard:
                            keyboard.release_all()
                        if xbox:
                            xbox.release_all()
                    prev_buttons.clear()
                else:
                    mapped = mapper.map(frame, profile, dt)
                    keyboard.apply(mapped)
                    if xbox:
                        xbox.apply(mapped)
                    # On-pad clicks: optional local haptic feedback.
                    if profile.haptics:
                        for pad_name, key in (("left", "left_pad_click"), ("right", "right_pad_click")):
                            if key in frame.buttons and key not in prev_buttons:
                                device.haptic_pulse(pad_name, 250)
                        # Translate game rumble to the SC's two linear resonant actuators.
                        if xbox and now >= next_haptic:
                            strong, weak = xbox.rumble
                            if strong > 10:
                                device.haptic_pulse("left", 150 + strong * 2)
                            if weak > 10:
                                device.haptic_pulse("right", 150 + weak * 2)
                            next_haptic = now + 0.06
                    prev_buttons = frame.buttons.copy()
                if now >= next_status:
                    next_status = now + 0.15
                    self.emit(kind="state", connected=connected, profile=profile.name,
                              exe=exe, paused=self.paused, virtual_xbox=xbox is not None,
                              buttons=sorted(frame.buttons) if frame else [],
                              gyro=tuple(round(v, 2) for v in frame.gyro) if frame else (0, 0, 0))
                self.stop_event.wait(max(0, 1 / 120 - (time.monotonic() - now)))
        except Exception as exc:
            self.emit(kind="error", message=f"Controller loop stopped: {exc}")
        finally:
            try:
                if keyboard:
                    keyboard.release_all()
                if xbox:
                    xbox.close()
                if device:
                    device.close()
            except Exception as exc:
                self.emit(kind="warning", message="Shutdown: " + str(exc))
            self.emit(kind="stopped")
