"""
Tkinter GUI for ipasideloader.

Two tabs: "Sideload" (the main workflow) and "Settings" (anisette server,
default signing backend, credential storage location). Kept deliberately
plain/native -- standard ttk widgets, no theming hacks -- since the goal
is a working cross-platform tool, not a styled one.
"""
from __future__ import annotations

import asyncio
import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from ..config import CREDS_DIR
from ..device.manager import list_connected_devices
from ..errors import SideloaderError
from ..pipeline import SideloadOptions, run_sideload
from ..signing.manager import SigningManager

SETTINGS_PATH = CREDS_DIR / "gui_settings.json"


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2))


class SideloadTab(ttk.Frame):
    def __init__(self, master: tk.Widget, settings: dict):
        super().__init__(master, padding=12)
        self.settings = settings

        self.ipa_path_var = tk.StringVar()
        self.profile_path_var = tk.StringVar()
        self.p12_path_var = tk.StringVar()
        self.p12_password_var = tk.StringVar()
        self.identity_var = tk.StringVar()
        self.backend_var = tk.StringVar(value="auto")
        self.install_var = tk.BooleanVar(value=True)
        self.output_path_var = tk.StringVar()

        self._build_layout()
        self._refresh_backends()

    def _build_layout(self) -> None:
        row = 0

        ttk.Label(self, text="IPA file:").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(self, textvariable=self.ipa_path_var, width=50).grid(row=row, column=1, sticky="we", padx=6)
        ttk.Button(self, text="Browse...", command=self._pick_ipa).grid(row=row, column=2)
        row += 1

        ttk.Label(self, text="Provisioning profile:").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(self, textvariable=self.profile_path_var, width=50).grid(row=row, column=1, sticky="we", padx=6)
        ttk.Button(self, text="Browse...", command=self._pick_profile).grid(row=row, column=2)
        row += 1

        ttk.Label(self, text="Signing certificate (.p12):").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(self, textvariable=self.p12_path_var, width=50).grid(row=row, column=1, sticky="we", padx=6)
        ttk.Button(self, text="Browse...", command=self._pick_p12).grid(row=row, column=2)
        row += 1

        ttk.Label(self, text=".p12 password:").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(self, textvariable=self.p12_password_var, show="*", width=50).grid(
            row=row, column=1, sticky="we", padx=6
        )
        row += 1

        ttk.Label(self, text="Keychain identity (macOS):").grid(row=row, column=0, sticky="w", pady=4)
        self.identity_combo = ttk.Combobox(self, textvariable=self.identity_var, width=47)
        self.identity_combo.grid(row=row, column=1, sticky="we", padx=6)
        ttk.Button(self, text="Refresh", command=self._refresh_identities).grid(row=row, column=2)
        row += 1

        ttk.Label(self, text="Signing backend:").grid(row=row, column=0, sticky="w", pady=4)
        self.backend_combo = ttk.Combobox(
            self, textvariable=self.backend_var, values=["auto", "codesign", "zsign", "ldid"],
            state="readonly", width=47,
        )
        self.backend_combo.grid(row=row, column=1, sticky="we", padx=6)
        row += 1

        self.backend_status_label = ttk.Label(self, text="", foreground="#666666")
        self.backend_status_label.grid(row=row, column=1, sticky="w")
        row += 1

        ttk.Checkbutton(self, text="Install to connected device", variable=self.install_var).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=4
        )
        row += 1

        ttk.Label(self, text="Save signed .ipa to (optional):").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(self, textvariable=self.output_path_var, width=50).grid(row=row, column=1, sticky="we", padx=6)
        ttk.Button(self, text="Browse...", command=self._pick_output).grid(row=row, column=2)
        row += 1

        self.run_button = ttk.Button(self, text="Sign & Install", command=self._on_run)
        self.run_button.grid(row=row, column=0, columnspan=3, pady=10)
        row += 1

        self.progress_text = tk.Text(self, height=10, width=70, state="disabled")
        self.progress_text.grid(row=row, column=0, columnspan=3, sticky="we", pady=4)

        self.columnconfigure(1, weight=1)

    # -- file pickers ---------------------------------------------------

    def _pick_ipa(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("IPA files", "*.ipa")])
        if path:
            self.ipa_path_var.set(path)

    def _pick_profile(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Provisioning profile", "*.mobileprovision")])
        if path:
            self.profile_path_var.set(path)

    def _pick_p12(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("P12 certificate", "*.p12")])
        if path:
            self.p12_path_var.set(path)

    def _pick_output(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".ipa", filetypes=[("IPA files", "*.ipa")])
        if path:
            self.output_path_var.set(path)

    # -- backend / identity helpers --------------------------------------

    def _refresh_backends(self) -> None:
        mgr = SigningManager()
        available = mgr.available_backends()
        self.backend_status_label.config(
            text=f"Available on this system: {', '.join(available) if available else 'none found'}"
        )

    def _refresh_identities(self) -> None:
        from ..signing.codesign_backend import CodesignBackend

        backend = CodesignBackend()
        if not backend.is_available():
            messagebox.showinfo("Not available", "codesign is only available on macOS.")
            return
        identities = backend.list_identities()
        self.identity_combo["values"] = identities
        if identities:
            self.identity_var.set(identities[0])

    # -- run --------------------------------------------------------------

    def _log(self, message: str) -> None:
        self.progress_text.config(state="normal")
        self.progress_text.insert("end", message + "\n")
        self.progress_text.see("end")
        self.progress_text.config(state="disabled")

    def _on_run(self) -> None:
        if not self.ipa_path_var.get() or not self.profile_path_var.get():
            messagebox.showerror("Missing input", "Please choose both an IPA file and a provisioning profile.")
            return

        self.run_button.config(state="disabled")
        self.progress_text.config(state="normal")
        self.progress_text.delete("1.0", "end")
        self.progress_text.config(state="disabled")

        backend = None if self.backend_var.get() == "auto" else self.backend_var.get()

        options = SideloadOptions(
            ipa_path=Path(self.ipa_path_var.get()),
            mobileprovision_path=Path(self.profile_path_var.get()),
            p12_path=Path(self.p12_path_var.get()) if self.p12_path_var.get() else None,
            p12_password=self.p12_password_var.get() or None,
            keychain_identity=self.identity_var.get() or None,
            signing_backend=backend,
            install_to_device=self.install_var.get(),
            keep_signed_ipa=Path(self.output_path_var.get()) if self.output_path_var.get() else None,
        )

        thread = threading.Thread(target=self._run_in_thread, args=(options,), daemon=True)
        thread.start()

    def _run_in_thread(self, options: SideloadOptions) -> None:
        def progress(message: str) -> None:
            self.after(0, self._log, message)

        try:
            result_path = asyncio.run(run_sideload(options, on_progress=progress))
            self.after(0, self._log, f"Done. Signed IPA at: {result_path}")
            self.after(0, lambda: messagebox.showinfo("Success", "Sideload completed successfully."))
        except SideloaderError as e:
            self.after(0, self._log, f"Error: {e}")
            self.after(0, lambda: messagebox.showerror("Sideload failed", str(e)))
        finally:
            self.after(0, lambda: self.run_button.config(state="normal"))


class SettingsTab(ttk.Frame):
    def __init__(self, master: tk.Widget, settings: dict, on_save):
        super().__init__(master, padding=12)
        self.settings = settings
        self.on_save = on_save

        self.custom_anisette_var = tk.StringVar(value=settings.get("custom_anisette_url", ""))
        self.apple_id_var = tk.StringVar(value=settings.get("apple_id", ""))

        ttk.Label(self, text="Custom anisette server URL (optional):").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(self, textvariable=self.custom_anisette_var, width=50).grid(row=0, column=1, sticky="we", padx=6)

        ttk.Label(
            self,
            text=(
                "If set, this server is tried first. Otherwise we use a local\n"
                "anisette provider, then fall back to known public servers."
            ),
            foreground="#666666",
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))

        ttk.Label(self, text="Apple ID (for free-account provisioning):").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(self, textvariable=self.apple_id_var, width=50).grid(row=2, column=1, sticky="we", padx=6)

        ttk.Button(self, text="Save settings", command=self._save).grid(row=3, column=0, columnspan=2, pady=10)

        ttk.Label(
            self,
            text=(
                "Note: Apple ID password is never stored on disk here.\n"
                "You'll be prompted for it (and any 2FA code) when needed."
            ),
            foreground="#666666",
            justify="left",
        ).grid(row=4, column=0, columnspan=2, sticky="w")

        self.columnconfigure(1, weight=1)

    def _save(self) -> None:
        self.settings["custom_anisette_url"] = self.custom_anisette_var.get().strip()
        self.settings["apple_id"] = self.apple_id_var.get().strip()
        self.on_save(self.settings)
        messagebox.showinfo("Saved", "Settings saved.")


class DevicesTab(ttk.Frame):
    def __init__(self, master: tk.Widget):
        super().__init__(master, padding=12)
        self.listbox = tk.Listbox(self, width=60, height=10)
        self.listbox.pack(fill="both", expand=True, pady=(0, 8))
        ttk.Button(self, text="Refresh", command=self._refresh).pack()
        self._refresh()

    def _refresh(self) -> None:
        self.listbox.delete(0, "end")
        try:
            devices = asyncio.run(list_connected_devices())
        except Exception as e:
            self.listbox.insert("end", f"Error listing devices: {e}")
            return
        if not devices:
            self.listbox.insert("end", "No devices found.")
        for d in devices:
            self.listbox.insert("end", f"{d.udid}  ({d.connection_type})")


class MainWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ipasideloader")
        self.geometry("700x600")

        settings = load_settings()

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)

        self.sideload_tab = SideloadTab(notebook, settings)
        self.settings_tab = SettingsTab(notebook, settings, on_save=save_settings)
        self.devices_tab = DevicesTab(notebook)

        notebook.add(self.sideload_tab, text="Sideload")
        notebook.add(self.devices_tab, text="Devices")
        notebook.add(self.settings_tab, text="Settings")


def main() -> None:
    app = MainWindow()
    app.mainloop()


if __name__ == "__main__":
    main()
