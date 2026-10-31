"""Validated, portable controller profiles and observable input/output data."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
import os
import uuid

BUTTONS = (
    "a", "b", "x", "y", "back", "start", "steam", "lb", "rb",
    "left_stick_click", "right_pad_click", "left_grip", "right_grip",
    "left_pad_click", "left_pad_touch", "right_pad_touch",
    "left_trigger_click", "right_trigger_click",
    "dpad_up", "dpad_down", "dpad_left", "dpad_right",
)
XBOX_BUTTONS = (
    "a", "b", "x", "y", "back", "start", "guide", "lb", "rb",
    "ls", "rs", "dpad_up", "dpad_down", "dpad_left", "dpad_right",
)
KEYS = (
    "space", "shift", "ctrl", "alt", "tab", "esc", "enter", "backspace",
    "up", "down", "left", "right", "w", "a", "s", "d", "e", "f", "q", "r",
    "1", "2", "3", "4", "5", "f1", "f2", "f3", "f4", "f5",
)
ACTIONS = (
    ["none"] + ["xbox:" + button for button in XBOX_BUTTONS]
    + ["key:" + key for key in KEYS]
    + ["mouse:left", "mouse:right", "mouse:middle"]
)
PAD_MODES = ("off", "mouse", "dpad", "left_stick", "right_stick")
GYRO_MODES = ("off", "always", "right_pad_touch", "left_grip")
GYRO_OUTPUTS = ("mouse", "right_stick")

DEFAULT_BINDINGS = {
    "a": "xbox:a", "b": "xbox:b", "x": "xbox:x", "y": "xbox:y",
    "back": "xbox:back", "start": "xbox:start", "steam": "none",
    "lb": "xbox:lb", "rb": "xbox:rb",
    "left_stick_click": "xbox:ls", "right_pad_click": "xbox:rs",
    "left_grip": "key:shift", "right_grip": "key:space",
    "left_pad_click": "none", "left_pad_touch": "none", "right_pad_touch": "none",
    "left_trigger_click": "none", "right_trigger_click": "none",
    "dpad_up": "xbox:dpad_up", "dpad_down": "xbox:dpad_down",
    "dpad_left": "xbox:dpad_left", "dpad_right": "xbox:dpad_right",
}


@dataclass
class Pad:
    down: bool = False
    x: float = 0.5
    y: float = 0.5
    pressure: float = 0.0

    @property
    def clicked(self) -> bool:
        # SDL's legacy Steam driver encodes clicks by increasing pressure.
        return self.down and self.pressure >= 0.95


@dataclass
class Frame:
    name: str = ""
    legacy: bool = False
    buttons: set[str] = field(default_factory=set)
    lx: float = 0.0
    ly: float = 0.0  # SDL: screen down = positive
    rx: float = 0.0
    ry: float = 0.0
    lt: float = 0.0
    rt: float = 0.0
    left_pad: Pad = field(default_factory=Pad)
    right_pad: Pad = field(default_factory=Pad)
    gyro: tuple[float, float, float] = (0.0, 0.0, 0.0)  # rad/s
    accel: tuple[float, float, float] = (0.0, 0.0, 0.0)  # m/s^2


@dataclass
class OutputFrame:
    xbox: set[str] = field(default_factory=set)
    keys: set[str] = field(default_factory=set)
    mouse_buttons: set[str] = field(default_factory=set)
    mouse_dx: float = 0.0
    mouse_dy: float = 0.0
    lx: float = 0.0
    ly: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    lt: float = 0.0
    rt: float = 0.0


@dataclass
class Profile:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Default"
    exe: str = ""  # filename of the foreground game, blank = fallback
    bindings: dict[str, str] = field(default_factory=lambda: DEFAULT_BINDINGS.copy())
    left_pad_mode: str = "dpad"
    right_pad_mode: str = "mouse"
    gyro_mode: str = "right_pad_touch"
    gyro_output: str = "mouse"
    mouse_sensitivity: float = 900.0  # pixels per full-width swipe
    gyro_sensitivity: float = 3.5  # pixels per degree
    stick_deadzone: float = 0.12
    pad_deadzone: float = 0.20
    trackball: bool = True
    trackball_friction: float = 0.84
    invert_mouse_y: bool = False
    haptics: bool = True
    led_brightness: int = 70

    @classmethod
    def from_dict(cls, payload: dict) -> Profile:
        if not isinstance(payload, dict):
            raise ValueError("Profile must be an object.")
        values = {key: payload[key] for key in cls.__dataclass_fields__ if key in payload}
        obj = cls(**values)
        if not isinstance(obj.bindings, dict):
            raise ValueError("Bindings must be an object.")
        obj.bindings = {key: value for key, value in obj.bindings.items()
                        if key in BUTTONS and value in ACTIONS}
        obj.bindings = {**DEFAULT_BINDINGS, **obj.bindings}
        if obj.left_pad_mode not in PAD_MODES or obj.right_pad_mode not in PAD_MODES:
            raise ValueError("Unsupported trackpad mode.")
        if obj.gyro_mode not in GYRO_MODES or obj.gyro_output not in GYRO_OUTPUTS:
            raise ValueError("Unsupported gyro mode.")
        for key in ("mouse_sensitivity", "gyro_sensitivity", "stick_deadzone",
                    "pad_deadzone", "trackball_friction"):
            value = float(getattr(obj, key))
            bounds = {"mouse_sensitivity": (50, 4000), "gyro_sensitivity": (0, 30),
                      "stick_deadzone": (0, 0.9), "pad_deadzone": (0, 0.9),
                      "trackball_friction": (0, 0.99)}
            low, high = bounds[key]
            if not low <= value <= high:
                raise ValueError(f"{key} must be between {low} and {high}.")
            setattr(obj, key, value)
        if not 0 <= int(obj.led_brightness) <= 100:
            raise ValueError("LED brightness must be 0–100.")
        obj.led_brightness = int(obj.led_brightness)
        obj.name = str(obj.name).strip()[:80] or "Untitled"
        obj.exe = os.path.basename(str(obj.exe).strip()).lower()
        obj.id = str(obj.id)
        return obj


@dataclass
class Settings:
    auto_update: bool = True
    auto_switch_profiles: bool = True
    profiles: list[Profile] = field(default_factory=lambda: [Profile()])


class Store:
    def __init__(self, root: Path | None = None):
        self.root = root or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SteamBridgeWin"
        self.path = self.root / "config.json"
        self.settings = Settings()

    def load(self) -> Settings:
        if not self.path.exists():
            return self.settings
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if raw.get("schema") != 1:
            raise ValueError("Unrecognized config schema; backup your file before editing.")
        profiles = [Profile.from_dict(item) for item in raw.get("profiles", [])]
        if not profiles:
            profiles = [Profile()]
        self.settings = Settings(
            auto_update=bool(raw.get("auto_update", True)),
            auto_switch_profiles=bool(raw.get("auto_switch_profiles", True)),
            profiles=profiles,
        )
        return self.settings

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": 1, "auto_update": self.settings.auto_update,
            "auto_switch_profiles": self.settings.auto_switch_profiles,
            "profiles": [asdict(profile) for profile in self.settings.profiles],
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def profile_for_exe(self, exe: str) -> Profile:
        exe = exe.lower()
        for profile in self.settings.profiles:
            if profile.exe and profile.exe == exe:
                return profile
        return next((p for p in self.settings.profiles if not p.exe),
                    self.settings.profiles[0])
