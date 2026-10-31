"""Deterministic input processing: sticks, touchpads, gyro, bindings."""
from __future__ import annotations

import math

from .model import Frame, OutputFrame, Pad, Profile


def clamp(value: float) -> float:
    return max(-1.0, min(1.0, value))


def deadzone(x: float, y: float, limit: float) -> tuple[float, float]:
    """Radial deadzone with continuous remapping outside the circle."""
    length = math.hypot(x, y)
    if length <= limit:
        return 0.0, 0.0
    adjusted = min(1.0, (length - limit) / (1.0 - limit))
    scale = adjusted / length
    return clamp(x * scale), clamp(y * scale)


def pad_stick(pad: Pad, limit: float) -> tuple[float, float]:
    if not pad.down:
        return 0.0, 0.0
    return deadzone((pad.x - 0.5) * 2.0, (pad.y - 0.5) * 2.0, limit)


def pad_directions(pad: Pad, limit: float) -> set[str]:
    if not pad.down:
        return set()
    x, y = pad_stick(pad, limit)
    directions = set()
    if x > 0.3:
        directions.add("dpad_right")
    if x < -0.3:
        directions.add("dpad_left")
    if y < -0.3:
        directions.add("dpad_up")
    if y > 0.3:
        directions.add("dpad_down")
    return directions


class Mapper:
    def __init__(self):
        self.profile_id: str | None = None
        self.previous: dict[str, tuple[float, float] | None] = {
            "left": None, "right": None
        }
        self.velocity = {"left": (0.0, 0.0), "right": (0.0, 0.0)}
        self.fractional_x = 0.0
        self.fractional_y = 0.0

    def reset(self) -> None:
        self.previous = {"left": None, "right": None}
        self.velocity = {"left": (0.0, 0.0), "right": (0.0, 0.0)}
        self.fractional_x = 0.0
        self.fractional_y = 0.0

    def _mouse_pad(self, name: str, pad: Pad, profile: Profile,
                   dt: float) -> tuple[float, float]:
        prev = self.previous[name]
        vx, vy = self.velocity[name]
        delta_x = delta_y = 0.0
        if pad.down:
            if prev is not None:
                delta_x = (pad.x - prev[0]) * profile.mouse_sensitivity
                delta_y = (pad.y - prev[1]) * profile.mouse_sensitivity
                vx = delta_x / dt
                vy = delta_y / dt
            else:
                vx = vy = 0.0  # no cursor jump on finger down
            self.previous[name] = (pad.x, pad.y)
        else:
            self.previous[name] = None
            if profile.trackball:
                vx *= profile.trackball_friction ** (dt * 60.0)
                vy *= profile.trackball_friction ** (dt * 60.0)
                if abs(vx) < 4:
                    vx = 0.0
                if abs(vy) < 4:
                    vy = 0.0
                delta_x, delta_y = vx * dt, vy * dt
            else:
                vx = vy = 0.0
        self.velocity[name] = (vx, vy)
        return delta_x, delta_y

    def map(self, frame: Frame, profile: Profile, dt: float = 1 / 120) -> OutputFrame:
        if profile.id != self.profile_id:
            self.reset()
            self.profile_id = profile.id
        dt = min(max(dt, 1 / 500), 1 / 15)
        output = OutputFrame()
        output.lx, output.ly = deadzone(frame.lx, frame.ly, profile.stick_deadzone)
        # The 2015 SC doesn't have a physical right stick; SDL exposes the
        # right trackpad as right stick axes. Never leak the idle pad position.
        if not frame.legacy:
            output.rx, output.ry = deadzone(frame.rx, frame.ry, profile.stick_deadzone)
        output.lt, output.rt = frame.lt, frame.rt
        pressed = set(frame.buttons)

        for name, pad, mode in (
            ("left", frame.left_pad, profile.left_pad_mode),
            ("right", frame.right_pad, profile.right_pad_mode),
        ):
            if mode == "dpad":
                pressed.update(pad_directions(pad, profile.pad_deadzone))
            elif mode in ("left_stick", "right_stick"):
                coords = pad_stick(pad, profile.pad_deadzone)
                if mode == "left_stick":
                    output.lx, output.ly = coords
                else:
                    output.rx, output.ry = coords
            elif mode == "mouse":
                dx, dy = self._mouse_pad(name, pad, profile, dt)
                output.mouse_dx += dx
                output.mouse_dy += dy
                continue
            # Clear any stored momentum when switching away from mouse.
            self.previous[name] = None
            self.velocity[name] = (0.0, 0.0)

        active = (
            profile.gyro_mode == "always"
            or profile.gyro_mode == "right_pad_touch" and frame.right_pad.down
            or profile.gyro_mode == "left_grip" and "left_grip" in frame.buttons
        )
        if active:
            # SDL3 supplies angular velocity in rad/s (x=pitch, y=yaw).
            pitch, yaw, _ = frame.gyro
            if abs(yaw) < 0.015:
                yaw = 0.0
            if abs(pitch) < 0.015:
                pitch = 0.0
            if profile.gyro_output == "mouse":
                deg = 180.0 / math.pi
                output.mouse_dx += yaw * deg * profile.gyro_sensitivity * dt
                output.mouse_dy -= pitch * deg * profile.gyro_sensitivity * dt
            else:
                output.rx = clamp(yaw * profile.gyro_sensitivity * 0.15)
                output.ry = clamp(-pitch * profile.gyro_sensitivity * 0.15)

        if profile.invert_mouse_y:
            output.mouse_dy *= -1
        for button in pressed:
            action = profile.bindings.get(button, "none")
            if action.startswith("xbox:"):
                output.xbox.add(action[5:])
            elif action.startswith("key:"):
                output.keys.add(action[4:])
            elif action.startswith("mouse:"):
                output.mouse_buttons.add(action[6:])

        # Carry fractional pixels across frames so precise mouse motion
        # works even at high polling rates and low sensitivities.
        output.mouse_dx += self.fractional_x
        output.mouse_dy += self.fractional_y
        whole_x = math.trunc(output.mouse_dx)
        whole_y = math.trunc(output.mouse_dy)
        self.fractional_x = output.mouse_dx - whole_x
        self.fractional_y = output.mouse_dy - whole_y
        output.mouse_dx = whole_x
        output.mouse_dy = whole_y
        return output
