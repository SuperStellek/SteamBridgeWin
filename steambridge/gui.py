"""Responsive Tkinter desktop GUI: controller status, full profile editor, updater."""
from __future__ import annotations

import copy
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import __version__
from .model import ACTIONS, BUTTONS, GYRO_MODES, GYRO_OUTPUTS, PAD_MODES, Profile, Store
from .service import BridgeService
from .updater import UpdateManager


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("SteamBridgeWin " + __version__)
        self.root.geometry("940x760")
        self.root.minsize(780, 570)
        self.store = Store()
        try:
            self.store.load()
        except Exception as exc:
            messagebox.showerror("Configuration", "Unable to load configuration:\n" + str(exc))
        self.service = BridgeService(self.store)
        self.updater = UpdateManager(self.store)
        self.ui_messages = queue.Queue()
        self.pending_update = None
        self.autoinstall_requested = False
        self.updating = False
        self.current_profile = self.store.settings.profiles[0]
        self._build()
        self.select_profile(self.current_profile)
        self.service.start()
        self.root.after(100, self.tick)
        self.root.after(4000, self.check_updates_if_enabled)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build(self):
        container = ttk.Frame(self.root, padding=14)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="SteamBridgeWin", font=("Segoe UI", 22, "bold")).pack(anchor="w")
        ttk.Label(container, text="Steam Controller → Xbox 360 + mouse / keyboard · no Steam required").pack(anchor="w")
        self.status = tk.StringVar(value="Initializing controller...")
        self.active = tk.StringVar(value="Profile: Default")
        self.inputs = tk.StringVar(value="Buttons: —")
        self.motion = tk.StringVar(value="Gyro: —")
        for value in (self.status, self.active, self.inputs, self.motion):
            ttk.Label(container, textvariable=value).pack(anchor="w", pady=2)
        bar = ttk.Frame(container)
        bar.pack(fill="x", pady=(10, 6))
        self.pause_btn = ttk.Button(bar, text="Pause bridge", command=self.toggle_pause)
        self.pause_btn.pack(side="left")
        ttk.Button(bar, text="Test left haptic", command=lambda: self.service.command("pulse", "left")).pack(side="left", padx=6)
        ttk.Button(bar, text="Test right haptic", command=lambda: self.service.command("pulse", "right")).pack(side="left")
        ttk.Label(bar, text="Auto-profile: foreground .exe").pack(side="right")
        ttk.Separator(container).pack(fill="x", pady=6)

        controls = ttk.Frame(container)
        controls.pack(fill="both", expand=True)
        sidebar = ttk.Frame(controls, width=195)
        sidebar.pack(side="left", fill="y", padx=(0, 12))
        sidebar.pack_propagate(False)
        ttk.Label(sidebar, text="Profiles", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.profiles_list = tk.Listbox(sidebar, exportselection=False, height=16)
        self.profiles_list.pack(fill="both", expand=True, pady=6)
        self.profiles_list.bind("<<ListboxSelect>>", self.on_profile_select)
        ttk.Button(sidebar, text="New profile", command=self.new_profile).pack(fill="x", pady=2)
        ttk.Button(sidebar, text="Duplicate", command=self.duplicate_profile).pack(fill="x", pady=2)
        ttk.Button(sidebar, text="Delete", command=self.delete_profile).pack(fill="x", pady=2)
        self.refresh_profiles()

        editor_container = ttk.Frame(controls)
        editor_container.pack(side="left", fill="both", expand=True)
        notebook = ttk.Notebook(editor_container)
        notebook.pack(fill="both", expand=True)
        general = ttk.Frame(notebook, padding=12)
        bindings = ttk.Frame(notebook, padding=12)
        updates = ttk.Frame(notebook, padding=12)
        notebook.add(general, text="Mapping & motion")
        notebook.add(bindings, text="Buttons")
        notebook.add(updates, text="Updates")
        self.variables = {}
        fields = [
            ("Profile name", "name", "text", None),
            ("Game executable (e.g. game.exe, blank=default)", "exe", "text", None),
            ("Left touchpad", "left_pad_mode", "choice", PAD_MODES),
            ("Right touchpad", "right_pad_mode", "choice", PAD_MODES),
            ("Gyroscope activation", "gyro_mode", "choice", GYRO_MODES),
            ("Gyroscope output", "gyro_output", "choice", GYRO_OUTPUTS),
            ("Mouse sensitivity (pixels / pad width)", "mouse_sensitivity", "text", None),
            ("Gyro sensitivity (pixels / degree)", "gyro_sensitivity", "text", None),
            ("Stick deadzone (0–0.9)", "stick_deadzone", "text", None),
            ("Pad deadzone (0–0.9)", "pad_deadzone", "text", None),
            ("Trackball friction (0–0.99)", "trackball_friction", "text", None),
            ("LED brightness (0–100)", "led_brightness", "text", None),
        ]
        for label, key, kind, options in fields:
            row = ttk.Frame(general)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=40).pack(side="left")
            variable = tk.StringVar()
            widget = (ttk.Combobox(row, textvariable=variable, values=options, state="readonly")
                      if kind == "choice" else ttk.Entry(row, textvariable=variable))
            widget.pack(side="left", fill="x", expand=True)
            self.variables[key] = variable
        self.booleans = {}
        for label, key in [
            ("Trackball / momentum scrolling", "trackball"),
            ("Invert mouse Y", "invert_mouse_y"),
            ("Haptic feedback / game rumble", "haptics"),
        ]:
            value = tk.BooleanVar()
            ttk.Checkbutton(general, text=label, variable=value).pack(anchor="w", pady=3)
            self.booleans[key] = value
        ttk.Button(general, text="Save profile", command=self.save_profile).pack(anchor="e", pady=8)

        canvas = tk.Canvas(bindings, highlightthickness=0)
        scroll = ttk.Scrollbar(bindings, orient="vertical", command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        canvas.configure(yscrollcommand=scroll.set)
        inner = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        self.binding_variables = {}
        for key in BUTTONS:
            line = ttk.Frame(inner)
            line.pack(fill="x", pady=2)
            ttk.Label(line, text=key.replace("_", " ").title(), width=24).pack(side="left")
            value = tk.StringVar()
            ttk.Combobox(line, textvariable=value, values=ACTIONS,
                         state="readonly", width=22).pack(side="left")
            self.binding_variables[key] = value
        ttk.Button(bindings, text="Save all button mappings", command=self.save_profile).pack(pady=4)

        self.autoupdate = tk.BooleanVar(value=self.store.settings.auto_update)
        self.autoswitch = tk.BooleanVar(value=self.store.settings.auto_switch_profiles)
        ttk.Checkbutton(updates, text="Check for updates automatically on launch and periodically",
                        variable=self.autoupdate, command=self.update_preferences).pack(anchor="w", pady=6)
        ttk.Checkbutton(updates, text="Automatically install verified updates and restart",
                        variable=self.autoinstall_value(), command=self.update_preferences).pack(anchor="w", pady=6)
        ttk.Checkbutton(updates, text="Switch profiles based on foreground game",
                        variable=self.autoswitch, command=self.update_preferences).pack(anchor="w", pady=6)
        ttk.Button(updates, text="Check for updates now", command=self.check_updates).pack(anchor="w", pady=12)
        self.update_label = tk.StringVar(value="Installed version: " + __version__)
        ttk.Label(updates, textvariable=self.update_label, wraplength=540).pack(anchor="w", pady=6)
        ttk.Label(updates, text="Updates come from this project's GitHub Releases and must pass SHA-256 verification. "
                  "User profiles are stored separately in AppData.",
                  wraplength=540).pack(anchor="w", pady=8)
        self.logvar = tk.StringVar(value="Ready")
        ttk.Label(container, textvariable=self.logvar, wraplength=800).pack(anchor="w", pady=8)

    def autoinstall_value(self):
        if not hasattr(self, "autoinstall"):
            self.autoinstall = tk.BooleanVar(value=False)
        return self.autoinstall

    def update_preferences(self):
        self.store.settings.auto_update = self.autoupdate.get()
        self.store.settings.auto_switch_profiles = self.autoswitch.get()
        self.store.save()

    def refresh_profiles(self):
        self.profiles_list.delete(0, "end")
        for profile in self.store.settings.profiles:
            self.profiles_list.insert("end", profile.name + (" · " + profile.exe if profile.exe else " · default"))

    def select_profile(self, profile):
        self.current_profile = profile
        self.refresh_profiles()
        index = self.store.settings.profiles.index(profile)
        self.profiles_list.selection_clear(0, "end")
        self.profiles_list.selection_set(index)
        for key, variable in self.variables.items():
            variable.set(str(getattr(profile, key)))
        for key, variable in self.booleans.items():
            variable.set(bool(getattr(profile, key)))
        for key, variable in self.binding_variables.items():
            variable.set(profile.bindings.get(key, "none"))
        self.service.command("manual_profile", profile.id)

    def on_profile_select(self, event=None):
        indices = self.profiles_list.curselection()
        if indices:
            profile = self.store.settings.profiles[indices[0]]
            if profile is not self.current_profile:
                self.select_profile(profile)

    def save_profile(self):
        profile = self.current_profile
        fields = {key: variable.get() for key, variable in self.variables.items()}
        fields.update({key: variable.get() for key, variable in self.booleans.items()})
        fields["bindings"] = {key: var.get() for key, var in self.binding_variables.items()}
        fields["id"] = profile.id
        try:
            updated = Profile.from_dict(fields)
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Invalid profile", str(exc))
            return
        index = self.store.settings.profiles.index(profile)
        self.store.settings.profiles[index] = updated
        self.store.save()
        self.select_profile(updated)
        self.logvar.set("Profile saved: " + updated.name)

    def new_profile(self):
        profile = Profile(name="New profile")
        self.store.settings.profiles.append(profile)
        self.store.save()
        self.select_profile(profile)

    def duplicate_profile(self):
        profile = copy.deepcopy(self.current_profile)
        import uuid
        profile.id = str(uuid.uuid4())
        profile.name += " copy"
        self.store.settings.profiles.append(profile)
        self.store.save()
        self.select_profile(profile)

    def delete_profile(self):
        if len(self.store.settings.profiles) == 1:
            messagebox.showwarning("Profiles", "At least one profile is required.")
            return
        self.store.settings.profiles.remove(self.current_profile)
        self.store.save()
        self.select_profile(self.store.settings.profiles[0])

    def toggle_pause(self):
        self.service.command("pause", not self.service.paused)
        self.pause_btn.configure(text="Resume bridge" if not self.service.paused else "Pause bridge")

    def check_updates_if_enabled(self):
        if self.store.settings.auto_update:
            self.check_updates()
        self.root.after(6 * 60 * 60 * 1000, self.check_updates_if_enabled)

    def check_updates(self):
        if self.updating:
            return
        self.updating = True
        self.update_label.set("Checking GitHub Releases...")
        def worker():
            try:
                available = self.updater.check()
                self.ui_messages.put(("update", available))
            except Exception as exc:
                self.ui_messages.put(("update_error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def _start_install(self, update):
        self.updating = True
        self.update_label.set("Downloading and verifying update " + update.version + "...")
        def worker():
            try:
                package, digest = self.updater.download(update)
                self.ui_messages.put(("downloaded", (package, digest)))
            except Exception as exc:
                self.ui_messages.put(("update_error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def tick(self):
        while True:
            try:
                entry = self.service.messages.get_nowait()
            except queue.Empty:
                break
            kind = entry.get("kind")
            if kind == "connection":
                self.status.set("Connected: " + entry["name"] if entry["connected"] else "Controller disconnected — scanning...")
            elif kind == "profile":
                self.active.set("Active profile: " + entry["name"] + " (foreground: " + entry["exe"] + ")")
            elif kind == "state":
                self.inputs.set("Buttons: " + ", ".join(entry["buttons"])[:180] if entry["buttons"] else "Buttons: —")
                self.motion.set("Gyro (rad/s): " + str(entry["gyro"]))
                if entry["paused"]:
                    self.status.set("Bridge paused")
            elif kind in ("log", "warning", "error"):
                self.logvar.set(entry["message"])
                if kind == "error":
                    self.status.set("Error — see message below")
        while True:
            try:
                kind, value = self.ui_messages.get_nowait()
            except queue.Empty:
                break
            if kind == "update":
                self.updating = False
                if value is None:
                    self.update_label.set("Up to date · v" + __version__)
                else:
                    self.pending_update = value
                    self.update_label.set("Update available: v" + value.version)
                    if self.autoinstall.get() and self.updater.can_install():
                        self._start_install(value)
                    elif self.updater.can_install() and messagebox.askyesno(
                        "Update available", "Install SteamBridgeWin v" + value.version + " and restart?"
                    ):
                        self._start_install(value)
            elif kind == "downloaded":
                package, digest = value
                self.update_label.set("Verified update ready. Closing to install...")
                try:
                    self.service.stop()
                    self.updater.install_on_exit(package, digest)
                    self.root.destroy()
                    return
                except Exception as exc:
                    self.update_label.set("Could not start updater: " + str(exc))
                    self.service.start()
                self.updating = False
            elif kind == "update_error":
                self.updating = False
                self.update_label.set("Update check/install failed: " + value)
        self.root.after(100, self.tick)

    def close(self):
        self.service.stop()
        self.root.destroy()


def main():
    App().root.mainloop()


if __name__ == "__main__":
    main()
