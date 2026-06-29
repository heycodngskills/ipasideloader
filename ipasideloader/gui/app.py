"""
Tkinter GUI for ipasideloader.

Four tabs:
  "Sign with Apple ID"  — primary, Sideloadly-style: just Apple ID + IPA
  "Sign with Cert"      — advanced: manual p12 + provisioning profile
  "Devices"             — list connected devices
  "Settings"            — anisette server, saved Apple ID
"""
from __future__ import annotations

import asyncio
import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Optional

from ..config import CREDS_DIR
from ..device.manager import list_connected_devices
from ..errors import SideloaderError
from ..pipeline import SideloadOptions, run_sideload, run_sideload_apple_id
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


# ── Apple ID tab (primary) ────────────────────────────────────────────────────

class AppleIdTab(ttk.Frame):
    """
    Sideloadly-style tab: Apple ID + password + IPA file → sign & install.
    No certificates or provisioning profiles needed from the user.
    """

    def __init__(self, master: tk.Widget, settings: dict):
        super().__init__(master, padding=16)
        self.settings = settings

        self.apple_id_var = tk.StringVar(value=settings.get("apple_id", ""))
        self.password_var = tk.StringVar()
        self.ipa_path_var = tk.StringVar()
        self.device_var = tk.StringVar()
        self.save_ipa_var = tk.StringVar()
        self._devices: list = []

        self._build_layout()
        self._refresh_devices()

    def _build_layout(self) -> None:
        row = 0

        note = ttk.Label(
            self,
            text=(
                "Sign and install any IPA using your free Apple ID — "
                "no developer account or certificates required."
            ),
            foreground="#555555",
            wraplength=520,
            justify="left",
        )
        note.grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 12))
        row += 1

        ttk.Label(self, text="Apple ID:").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(self, textvariable=self.apple_id_var, width=46).grid(
            row=row, column=1, columnspan=2, sticky="we", padx=6
        )
        row += 1

        ttk.Label(self, text="Password:").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(self, textvariable=self.password_var, show="●", width=46).grid(
            row=row, column=1, columnspan=2, sticky="we", padx=6
        )
        ttk.Label(self, text="Not saved to disk.", foreground="#888888").grid(
            row=row + 1, column=1, sticky="w", padx=6
        )
        row += 2

        ttk.Label(self, text="IPA file:").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(self, textvariable=self.ipa_path_var, width=40).grid(
            row=row, column=1, sticky="we", padx=6
        )
        ttk.Button(self, text="Browse…", command=self._pick_ipa).grid(row=row, column=2)
        row += 1

        ttk.Label(self, text="Device:").grid(row=row, column=0, sticky="w", pady=4)
        self.device_combo = ttk.Combobox(
            self, textvariable=self.device_var, state="readonly", width=38
        )
        self.device_combo.grid(row=row, column=1, sticky="we", padx=6)
        ttk.Button(self, text="Refresh", command=self._refresh_devices).grid(row=row, column=2)
        row += 1

        ttk.Label(self, text="Save .ipa to (optional):").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(self, textvariable=self.save_ipa_var, width=40).grid(
            row=row, column=1, sticky="we", padx=6
        )
        ttk.Button(self, text="Browse…", command=self._pick_save).grid(row=row, column=2)
        row += 1

        ttk.Separator(self, orient="horizontal").grid(
            row=row, column=0, columnspan=3, sticky="we", pady=8
        )
        row += 1

        self.run_button = ttk.Button(self, text="Sign & Install", command=self._on_run)
        self.run_button.grid(row=row, column=0, columnspan=3, pady=4)
        row += 1

        self.log = tk.Text(self, height=11, width=68, state="disabled", wrap="word")
        self.log.grid(row=row, column=0, columnspan=3, sticky="nswe", pady=(6, 0))
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.log.yview)
        scroll.grid(row=row, column=3, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

        self.columnconfigure(1, weight=1)

    def _pick_ipa(self) -> None:
        p = filedialog.askopenfilename(filetypes=[("IPA files", "*.ipa")])
        if p:
            self.ipa_path_var.set(p)

    def _pick_save(self) -> None:
        p = filedialog.asksaveasfilename(defaultextension=".ipa", filetypes=[("IPA files", "*.ipa")])
        if p:
            self.save_ipa_var.set(p)

    def _refresh_devices(self) -> None:
        try:
            self._devices = asyncio.run(list_connected_devices())
        except Exception:
            self._devices = []

        if self._devices:
            values = [f"{d.udid}  ({d.connection_type})" for d in self._devices]
            self.device_combo["values"] = values
            self.device_combo.current(0)
            self.device_var.set(values[0])
        else:
            self.device_combo["values"] = ["No device found"]
            self.device_var.set("No device found")

    def _log(self, msg: str) -> None:
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _on_run(self) -> None:
        apple_id = self.apple_id_var.get().strip()
        password = self.password_var.get()
        ipa = self.ipa_path_var.get().strip()

        if not apple_id:
            messagebox.showerror("Missing Apple ID", "Please enter your Apple ID (email address).")
            return
        if not password:
            messagebox.showerror("Missing Password", "Please enter your Apple ID password.")
            return
        if not ipa:
            messagebox.showerror("Missing IPA", "Please choose an IPA file to install.")
            return

        device_udid: Optional[str] = None
        if self._devices:
            idx = self.device_combo.current()
            if idx >= 0:
                device_udid = self._devices[idx].udid

        save_ipa = Path(self.save_ipa_var.get()) if self.save_ipa_var.get().strip() else None

        self.run_button.config(state="disabled")
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

        self.settings["apple_id"] = apple_id
        save_settings(self.settings)

        threading.Thread(
            target=self._run_thread,
            args=(apple_id, password, Path(ipa), device_udid, save_ipa),
            daemon=True,
        ).start()

    def _ask_two_factor(self) -> str:
        result: list[str] = []
        event = threading.Event()

        def _ask() -> None:
            code = simpledialog.askstring(
                "Two-Factor Authentication",
                "Apple sent a 6-digit code to your trusted devices.\nEnter it here:",
                parent=self,
            )
            result.append(code or "")
            event.set()

        self.after(0, _ask)
        event.wait()
        return result[0]

    def _run_thread(
        self,
        apple_id: str,
        password: str,
        ipa_path: Path,
        device_udid: Optional[str],
        save_ipa: Optional[Path],
    ) -> None:
        def progress(msg: str) -> None:
            self.after(0, self._log, msg)

        try:
            asyncio.run(run_sideload_apple_id(
                ipa_path=ipa_path,
                apple_id=apple_id,
                password=password,
                device_udid=device_udid,
                keep_signed_ipa=save_ipa,
                on_progress=progress,
                on_two_factor=self._ask_two_factor,
                custom_anisette_url=self.settings.get("custom_anisette_url") or None,
            ))
            self.after(0, self._log, "Done!")
            self.after(0, lambda: messagebox.showinfo("Success", "App installed successfully."))
        except SideloaderError as exc:
            self.after(0, self._log, f"Error: {exc}")
            self.after(0, lambda: messagebox.showerror("Failed", str(exc)))
        finally:
            self.after(0, lambda: self.run_button.config(state="normal"))


# ── Certificate tab (advanced) ────────────────────────────────────────────────

class CertTab(ttk.Frame):
    """Advanced tab for users who already have a .p12 + .mobileprovision."""

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

        self.progress_text = tk.Text(self, height=9, width=70, state="disabled")
        self.progress_text.grid(row=row, column=0, columnspan=3, sticky="we", pady=4)

        self.columnconfigure(1, weight=1)

    def _pick_ipa(self) -> None:
        p = filedialog.askopenfilename(filetypes=[("IPA files", "*.ipa")])
        if p:
            self.ipa_path_var.set(p)

    def _pick_profile(self) -> None:
        p = filedialog.askopenfilename(filetypes=[("Provisioning profile", "*.mobileprovision")])
        if p:
            self.profile_path_var.set(p)

    def _pick_p12(self) -> None:
        p = filedialog.askopenfilename(filetypes=[("P12 certificate", "*.p12")])
        if p:
            self.p12_path_var.set(p)

    def _pick_output(self) -> None:
        p = filedialog.asksaveasfilename(defaultextension=".ipa", filetypes=[("IPA files", "*.ipa")])
        if p:
            self.output_path_var.set(p)

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

        threading.Thread(target=self._run_thread, args=(options,), daemon=True).start()

    def _run_thread(self, options: SideloadOptions) -> None:
        def progress(msg: str) -> None:
            self.after(0, self._log, msg)

        try:
            result_path = asyncio.run(run_sideload(options, on_progress=progress))
            self.after(0, self._log, f"Done. Signed IPA: {result_path}")
            self.after(0, lambda: messagebox.showinfo("Success", "Sideload completed successfully."))
        except SideloaderError as e:
            self.after(0, self._log, f"Error: {e}")
            self.after(0, lambda: messagebox.showerror("Sideload failed", str(e)))
        finally:
            self.after(0, lambda: self.run_button.config(state="normal"))


# ── Devices tab ───────────────────────────────────────────────────────────────

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


# ── Settings tab ──────────────────────────────────────────────────────────────

class SettingsTab(ttk.Frame):
    def __init__(self, master: tk.Widget, settings: dict, on_save):
        super().__init__(master, padding=12)
        self.settings = settings
        self.on_save = on_save

        self.custom_anisette_var = tk.StringVar(value=settings.get("custom_anisette_url", ""))
        self.apple_id_var = tk.StringVar(value=settings.get("apple_id", ""))

        ttk.Label(self, text="Apple ID (saved for convenience):").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(self, textvariable=self.apple_id_var, width=50).grid(row=0, column=1, sticky="we", padx=6)

        ttk.Label(self, text="Custom anisette server URL (optional):").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(self, textvariable=self.custom_anisette_var, width=50).grid(row=1, column=1, sticky="we", padx=6)

        ttk.Label(
            self,
            text=(
                "Leave blank to use the built-in anisette provider.\n"
                "If that fails, a public fallback server is used automatically."
            ),
            foreground="#666666",
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 10))

        ttk.Button(self, text="Save settings", command=self._save).grid(row=3, column=0, columnspan=2, pady=10)

        ttk.Label(
            self,
            text="Apple ID password is never saved — you'll be prompted each time.",
            foreground="#888888",
        ).grid(row=4, column=0, columnspan=2, sticky="w")

        self.columnconfigure(1, weight=1)

    def _save(self) -> None:
        self.settings["custom_anisette_url"] = self.custom_anisette_var.get().strip()
        self.settings["apple_id"] = self.apple_id_var.get().strip()
        self.on_save(self.settings)
        messagebox.showinfo("Saved", "Settings saved.")


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ipasideloader")
        self.geometry("700x620")

        settings = load_settings()

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)

        apple_tab = AppleIdTab(notebook, settings)
        cert_tab = CertTab(notebook, settings)
        devices_tab = DevicesTab(notebook)
        settings_tab = SettingsTab(notebook, settings, on_save=save_settings)

        notebook.add(apple_tab, text="Sign with Apple ID")
        notebook.add(cert_tab, text="Sign with Certificate")
        notebook.add(devices_tab, text="Devices")
        notebook.add(settings_tab, text="Settings")


def main() -> None:
    app = MainWindow()
    app.mainloop()


if __name__ == "__main__":
    main()
