"""
Tkinter GUI for ipasideloader — dark theme redesign.

Tabs:
  "Sign with Apple ID"   — primary Sideloadly-style flow
  "Sign with Cert"       — manual p12 + mobileprovision
  "Devices"              — connected device list
  "Settings"             — anisette, Apple ID, signing prefs, logging
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Optional

from ..config import CREDS_DIR, LOG_DIR
from ..device.manager import list_connected_devices
from ..errors import SideloaderError
from ..pipeline import SideloadOptions, run_sideload, run_sideload_apple_id
from ..signing.manager import SigningManager

SETTINGS_PATH = CREDS_DIR / "gui_settings.json"

# ── Palette ────────────────────────────────────────────────────────────────────
BG        = "#1a1b1e"   # main background
BG2       = "#25262b"   # card / panel background
BG3       = "#2c2d33"   # input / listbox background
BORDER    = "#3a3b42"   # subtle borders
ACCENT    = "#5c7cfa"   # indigo accent (buttons, highlights)
ACCENT_HV = "#4c6ef5"   # accent hover
SUCCESS   = "#51cf66"   # green
WARN      = "#fcc419"   # yellow
ERROR     = "#ff6b6b"   # red
FG        = "#e8e9ed"   # primary text
FG2       = "#909296"   # secondary / muted text
FG3       = "#5c5f66"   # disabled / placeholder

FONT_UI   = ("Segoe UI", 10)
FONT_MONO = ("Consolas", 9)
FONT_H1   = ("Segoe UI Semibold", 13)
FONT_LABEL= ("Segoe UI", 9)


# ── Settings persistence ───────────────────────────────────────────────────────

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


# ── Theme application ──────────────────────────────────────────────────────────

def apply_theme(root: tk.Tk) -> None:
    style = ttk.Style(root)
    style.theme_use("clam")

    # General
    style.configure(".",
        background=BG, foreground=FG,
        fieldbackground=BG3, troughcolor=BG2,
        selectbackground=ACCENT, selectforeground=FG,
        font=FONT_UI, borderwidth=0, relief="flat",
    )

    # Notebook
    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=0)
    style.configure("TNotebook.Tab",
        background=BG2, foreground=FG2,
        padding=(18, 8), font=FONT_UI, borderwidth=0,
    )
    style.map("TNotebook.Tab",
        background=[("selected", BG), ("active", BG3)],
        foreground=[("selected", FG), ("active", FG)],
    )

    # Frame
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=BG2)
    style.configure("Inner.TFrame", background=BG2)

    # Label
    style.configure("TLabel", background=BG, foreground=FG, font=FONT_UI)
    style.configure("Card.TLabel", background=BG2, foreground=FG)
    style.configure("Muted.TLabel", background=BG2, foreground=FG2, font=FONT_LABEL)
    style.configure("Muted2.TLabel", background=BG, foreground=FG2, font=FONT_LABEL)
    style.configure("H1.TLabel", background=BG, foreground=FG, font=FONT_H1)
    style.configure("Success.TLabel", background=BG2, foreground=SUCCESS, font=FONT_LABEL)
    style.configure("Error.TLabel", background=BG2, foreground=ERROR, font=FONT_LABEL)

    # Entry
    style.configure("TEntry",
        fieldbackground=BG3, foreground=FG,
        insertcolor=FG, bordercolor=BORDER,
        lightcolor=BORDER, darkcolor=BORDER,
        borderwidth=1, relief="solid", padding=(6, 4),
    )
    style.map("TEntry",
        fieldbackground=[("focus", BG3)],
        bordercolor=[("focus", ACCENT)],
    )

    # Button — primary
    style.configure("Accent.TButton",
        background=ACCENT, foreground="#ffffff",
        font=("Segoe UI Semibold", 10),
        padding=(14, 7), borderwidth=0, relief="flat",
    )
    style.map("Accent.TButton",
        background=[("active", ACCENT_HV), ("disabled", BG3)],
        foreground=[("disabled", FG3)],
    )

    # Button — secondary
    style.configure("TButton",
        background=BG3, foreground=FG,
        font=FONT_UI, padding=(10, 6),
        borderwidth=1, relief="flat",
    )
    style.map("TButton",
        background=[("active", BORDER)],
    )

    # Combobox
    style.configure("TCombobox",
        fieldbackground=BG3, foreground=FG,
        background=BG3, arrowcolor=FG2,
        bordercolor=BORDER, lightcolor=BORDER,
        darkcolor=BORDER, borderwidth=1,
        padding=(6, 4),
    )
    style.map("TCombobox",
        fieldbackground=[("readonly", BG3)],
        bordercolor=[("focus", ACCENT)],
    )

    # Checkbutton
    style.configure("TCheckbutton",
        background=BG2, foreground=FG,
        indicatorcolor=BG3, indicatordiameter=14,
    )
    style.map("TCheckbutton",
        indicatorcolor=[("selected", ACCENT)],
        background=[("active", BG2)],
    )

    # Separator
    style.configure("TSeparator", background=BORDER)

    # Scrollbar
    style.configure("TScrollbar",
        background=BG2, troughcolor=BG,
        arrowcolor=FG2, borderwidth=0,
    )

    # Progressbar
    style.configure("TProgressbar",
        troughcolor=BG3, background=ACCENT,
        borderwidth=0, thickness=4,
    )

    root.configure(bg=BG)


# ── Reusable card widget ───────────────────────────────────────────────────────

class Card(ttk.Frame):
    def __init__(self, master, **kw):
        super().__init__(master, style="Card.TFrame", padding=16, **kw)


# ── Log widget ─────────────────────────────────────────────────────────────────

class LogBox(tk.Text):
    def __init__(self, master, **kw):
        kw.setdefault("height", 10)
        kw.setdefault("wrap", "word")
        kw.setdefault("state", "disabled")
        kw.setdefault("font", FONT_MONO)
        kw.setdefault("bg", BG3)
        kw.setdefault("fg", FG)
        kw.setdefault("insertbackground", FG)
        kw.setdefault("selectbackground", ACCENT)
        kw.setdefault("relief", "flat")
        kw.setdefault("borderwidth", 0)
        kw.setdefault("padx", 8)
        kw.setdefault("pady", 6)
        super().__init__(master, **kw)
        self.tag_configure("ok",    foreground=SUCCESS)
        self.tag_configure("err",   foreground=ERROR)
        self.tag_configure("warn",  foreground=WARN)
        self.tag_configure("muted", foreground=FG2)

    def append(self, msg: str, tag: str = "") -> None:
        self.config(state="normal")
        self.insert("end", msg + "\n", tag or ())
        self.see("end")
        self.config(state="disabled")

    def clear(self) -> None:
        self.config(state="normal")
        self.delete("1.0", "end")
        self.config(state="disabled")


# ── Status badge ───────────────────────────────────────────────────────────────

class StatusDot(tk.Canvas):
    """Small coloured dot for device/backend status."""
    def __init__(self, master, color=FG3, **kw):
        super().__init__(master, width=10, height=10, bg=BG2,
                         highlightthickness=0, **kw)
        self._dot = self.create_oval(1, 1, 9, 9, fill=color, outline="")

    def set_color(self, color: str) -> None:
        self.itemconfig(self._dot, fill=color)


# ── Apple ID Tab ───────────────────────────────────────────────────────────────

class AppleIdTab(ttk.Frame):
    def __init__(self, master: tk.Widget, settings: dict):
        super().__init__(master, padding=(20, 16))
        self.settings = settings
        self.configure(style="TFrame")

        self.apple_id_var   = tk.StringVar(value=settings.get("apple_id", ""))
        self.password_var   = tk.StringVar()
        self.ipa_path_var   = tk.StringVar()
        self.device_var     = tk.StringVar()
        self.save_ipa_var   = tk.StringVar()
        self._devices: list = []

        self._build()
        self._refresh_devices()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)

        # ── Header
        hdr = ttk.Frame(self, style="TFrame")
        hdr.grid(row=0, column=0, sticky="we", pady=(0, 14))
        ttk.Label(hdr, text="Sign with Apple ID", style="H1.TLabel").pack(side="left")

        # ── Credentials card
        creds = Card(self)
        creds.grid(row=1, column=0, sticky="we", pady=(0, 10))
        creds.columnconfigure(1, weight=1)

        ttk.Label(creds, text="Apple ID", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 2))
        ttk.Entry(creds, textvariable=self.apple_id_var).grid(
            row=1, column=0, columnspan=3, sticky="we", pady=(0, 10))

        ttk.Label(creds, text="Password", style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", pady=(0, 2))
        pw_frame = ttk.Frame(creds, style="Inner.TFrame")
        pw_frame.grid(row=3, column=0, columnspan=3, sticky="we", pady=(0, 4))
        pw_frame.columnconfigure(0, weight=1)
        self._pw_entry = ttk.Entry(pw_frame, textvariable=self.password_var, show="●")
        self._pw_entry.grid(row=0, column=0, sticky="we")
        self._show_pw = tk.BooleanVar(value=False)
        ttk.Checkbutton(pw_frame, text="Show", variable=self._show_pw,
                         command=self._toggle_pw, style="TCheckbutton").grid(
            row=0, column=1, padx=(6, 0))
        ttk.Label(creds, text="Never saved to disk", style="Muted.TLabel").grid(
            row=4, column=0, sticky="w")

        # ── IPA + Device card
        files = Card(self)
        files.grid(row=2, column=0, sticky="we", pady=(0, 10))
        files.columnconfigure(1, weight=1)

        ttk.Label(files, text="IPA File", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 2))
        ipa_row = ttk.Frame(files, style="Inner.TFrame")
        ipa_row.grid(row=1, column=0, columnspan=3, sticky="we", pady=(0, 10))
        ipa_row.columnconfigure(0, weight=1)
        ttk.Entry(ipa_row, textvariable=self.ipa_path_var).grid(row=0, column=0, sticky="we")
        ttk.Button(ipa_row, text="Browse", command=self._pick_ipa).grid(
            row=0, column=1, padx=(6, 0))

        ttk.Label(files, text="Device", style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", pady=(0, 2))
        dev_row = ttk.Frame(files, style="Inner.TFrame")
        dev_row.grid(row=3, column=0, columnspan=3, sticky="we", pady=(0, 10))
        dev_row.columnconfigure(0, weight=1)
        self.device_combo = ttk.Combobox(dev_row, textvariable=self.device_var,
                                          state="readonly")
        self.device_combo.grid(row=0, column=0, sticky="we")
        self._dev_dot = StatusDot(dev_row)
        self._dev_dot.grid(row=0, column=1, padx=(8, 4))
        ttk.Button(dev_row, text="Refresh", command=self._refresh_devices).grid(
            row=0, column=2)

        ttk.Label(files, text="Save signed .ipa (optional)", style="Muted.TLabel").grid(
            row=4, column=0, sticky="w", pady=(0, 2))
        save_row = ttk.Frame(files, style="Inner.TFrame")
        save_row.grid(row=5, column=0, columnspan=3, sticky="we")
        save_row.columnconfigure(0, weight=1)
        ttk.Entry(save_row, textvariable=self.save_ipa_var).grid(row=0, column=0, sticky="we")
        ttk.Button(save_row, text="Browse", command=self._pick_save).grid(
            row=0, column=1, padx=(6, 0))

        # ── Action
        action = ttk.Frame(self, style="TFrame")
        action.grid(row=3, column=0, sticky="we", pady=(4, 10))
        action.columnconfigure(1, weight=1)
        self.run_btn = ttk.Button(action, text="Sign & Install",
                                   style="Accent.TButton", command=self._on_run)
        self.run_btn.grid(row=0, column=0)
        self._status_label = ttk.Label(action, text="", style="Muted2.TLabel")
        self._status_label.grid(row=0, column=1, padx=(12, 0))

        self._progress = ttk.Progressbar(self, mode="indeterminate",
                                          style="TProgressbar")
        self._progress.grid(row=4, column=0, sticky="we", pady=(0, 8))

        # ── Log
        log_card = Card(self)
        log_card.grid(row=5, column=0, sticky="nswe")
        log_card.columnconfigure(0, weight=1)
        log_card.rowconfigure(1, weight=1)
        log_hdr = ttk.Frame(log_card, style="Inner.TFrame")
        log_hdr.grid(row=0, column=0, sticky="we", pady=(0, 6))
        ttk.Label(log_hdr, text="Log", style="Muted.TLabel").pack(side="left")
        ttk.Button(log_hdr, text="Clear", command=lambda: self.logbox.clear()).pack(side="right")
        self.logbox = LogBox(log_card, height=9)
        self.logbox.grid(row=1, column=0, sticky="nswe")
        sb = ttk.Scrollbar(log_card, command=self.logbox.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.logbox.configure(yscrollcommand=sb.set)

        self.rowconfigure(5, weight=1)

    # ── helpers

    def _toggle_pw(self) -> None:
        self._pw_entry.configure(show="" if self._show_pw.get() else "●")

    def _pick_ipa(self) -> None:
        p = filedialog.askopenfilename(filetypes=[("IPA files", "*.ipa")])
        if p:
            self.ipa_path_var.set(p)

    def _pick_save(self) -> None:
        p = filedialog.asksaveasfilename(defaultextension=".ipa",
                                          filetypes=[("IPA files", "*.ipa")])
        if p:
            self.save_ipa_var.set(p)

    def _refresh_devices(self) -> None:
        try:
            self._devices = asyncio.run(list_connected_devices())
        except Exception:
            self._devices = []

        if self._devices:
            vals = [f"{d.udid}  ({d.connection_type})" for d in self._devices]
            self.device_combo["values"] = vals
            self.device_combo.current(0)
            self._dev_dot.set_color(SUCCESS)
        else:
            self.device_combo["values"] = ["No device found"]
            self.device_var.set("No device found")
            self._dev_dot.set_color(ERROR)

    def _log(self, msg: str) -> None:
        tag = "ok" if msg.startswith("Done") or "success" in msg.lower() else \
              "err" if "error" in msg.lower() or "fail" in msg.lower() else ""
        self.after(0, self.logbox.append, msg, tag)

    def _set_status(self, msg: str, color: str = FG2) -> None:
        self.after(0, lambda: self._status_label.configure(
            text=msg, foreground=color))

    def _on_run(self) -> None:
        apple_id = self.apple_id_var.get().strip()
        password = self.password_var.get()
        ipa      = self.ipa_path_var.get().strip()

        if not apple_id:
            messagebox.showerror("Missing Apple ID", "Enter your Apple ID email.")
            return
        if not password:
            messagebox.showerror("Missing Password", "Enter your Apple ID password.")
            return
        if not ipa:
            messagebox.showerror("Missing IPA", "Choose an IPA file.")
            return

        device_udid: Optional[str] = None
        if self._devices:
            idx = self.device_combo.current()
            if idx >= 0:
                device_udid = self._devices[idx].udid

        save_ipa = Path(self.save_ipa_var.get()) if self.save_ipa_var.get().strip() else None

        self.run_btn.config(state="disabled")
        self.logbox.clear()
        self._progress.start(12)
        self._set_status("Running…", WARN)

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
        def _ask():
            code = simpledialog.askstring(
                "Two-Factor Authentication",
                "Enter the 6-digit code from your trusted device:",
                parent=self,
            )
            result.append(code or "")
            event.set()
        self.after(0, _ask)
        event.wait()
        return result[0]

    def _run_thread(self, apple_id, password, ipa_path, device_udid, save_ipa):
        try:
            asyncio.run(run_sideload_apple_id(
                ipa_path=ipa_path,
                apple_id=apple_id,
                password=password,
                device_udid=device_udid,
                keep_signed_ipa=save_ipa,
                on_progress=self._log,
                on_two_factor=self._ask_two_factor,
                custom_anisette_url=self.settings.get("custom_anisette_url") or None,
            ))
            self._log("Done! App installed successfully.")
            self._set_status("Installed ✓", SUCCESS)
            self.after(0, lambda: messagebox.showinfo("Success", "App installed successfully."))
        except SideloaderError as exc:
            self._log(f"Error: {exc}")
            self._set_status("Failed", ERROR)
            self.after(0, lambda: messagebox.showerror("Failed", str(exc)))
        finally:
            self.after(0, self._progress.stop)
            self.after(0, lambda: self.run_btn.config(state="normal"))


# ── Certificate Tab ────────────────────────────────────────────────────────────

class CertTab(ttk.Frame):
    def __init__(self, master: tk.Widget, settings: dict):
        super().__init__(master, padding=(20, 16))
        self.settings = settings
        self.configure(style="TFrame")

        self.ipa_path_var      = tk.StringVar()
        self.profile_path_var  = tk.StringVar()
        self.p12_path_var      = tk.StringVar()
        self.p12_password_var  = tk.StringVar()
        self.identity_var      = tk.StringVar()
        self.backend_var       = tk.StringVar(value="auto")
        self.install_var       = tk.BooleanVar(value=True)
        self.output_path_var   = tk.StringVar()
        self.bundle_id_var     = tk.StringVar()

        self._build()
        self._refresh_backends()

    def _file_row(self, parent, label: str, var: tk.StringVar,
                  filetypes, row: int, save: bool = False) -> None:
        ttk.Label(parent, text=label, style="Muted.TLabel").grid(
            row=row*2, column=0, columnspan=3, sticky="w", pady=(0, 2))
        fr = ttk.Frame(parent, style="Inner.TFrame")
        fr.grid(row=row*2+1, column=0, columnspan=3, sticky="we", pady=(0, 10))
        fr.columnconfigure(0, weight=1)
        ttk.Entry(fr, textvariable=var).grid(row=0, column=0, sticky="we")
        cmd = (lambda v=var, ft=filetypes:
               v.set(filedialog.asksaveasfilename(defaultextension=ft[0][1].lstrip("*"),
                                                   filetypes=ft) or v.get())
               if save else
               lambda v=var, ft=filetypes:
               v.set(filedialog.askopenfilename(filetypes=ft) or v.get()))
        ttk.Button(fr, text="Browse", command=cmd()).grid(row=0, column=1, padx=(6, 0))

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)

        ttk.Label(self, text="Sign with Certificate", style="H1.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 14))

        files = Card(self)
        files.grid(row=1, column=0, sticky="we", pady=(0, 10))
        files.columnconfigure(1, weight=1)

        def browse_row(parent, label, var, ftypes, r, save=False):
            ttk.Label(parent, text=label, style="Muted.TLabel").grid(
                row=r, column=0, sticky="w", pady=(2, 2))
            fr = ttk.Frame(parent, style="Inner.TFrame")
            fr.grid(row=r, column=1, columnspan=2, sticky="we", padx=(8, 0), pady=(2, 2))
            fr.columnconfigure(0, weight=1)
            ttk.Entry(fr, textvariable=var).grid(row=0, column=0, sticky="we")
            if save:
                cmd = lambda v=var, ft=ftypes: v.set(
                    filedialog.asksaveasfilename(
                        defaultextension=ft[0][1].replace("*",""),
                        filetypes=ft) or v.get())
            else:
                cmd = lambda v=var, ft=ftypes: v.set(
                    filedialog.askopenfilename(filetypes=ft) or v.get())
            ttk.Button(fr, text="Browse", command=cmd).grid(row=0, column=1, padx=(6, 0))

        browse_row(files, "IPA File", self.ipa_path_var,
                   [("IPA files", "*.ipa")], 0)
        browse_row(files, "Provisioning Profile (.mobileprovision)",
                   self.profile_path_var,
                   [("Provisioning profile", "*.mobileprovision")], 1)
        browse_row(files, "Signing Certificate (.p12)", self.p12_path_var,
                   [("P12 certificate", "*.p12")], 2)

        ttk.Label(files, text=".p12 Password", style="Muted.TLabel").grid(
            row=3, column=0, sticky="w", pady=(2, 2))
        ttk.Entry(files, textvariable=self.p12_password_var, show="*").grid(
            row=3, column=1, columnspan=2, sticky="we", padx=(8, 0), pady=(2, 2))

        ttk.Label(files, text="Bundle ID Override (optional)", style="Muted.TLabel").grid(
            row=4, column=0, sticky="w", pady=(2, 2))
        ttk.Entry(files, textvariable=self.bundle_id_var).grid(
            row=4, column=1, columnspan=2, sticky="we", padx=(8, 0), pady=(2, 2))

        browse_row(files, "Save Signed .ipa (optional)", self.output_path_var,
                   [("IPA files", "*.ipa")], 5, save=True)

        # Signing options
        opts = Card(self)
        opts.grid(row=2, column=0, sticky="we", pady=(0, 10))
        opts.columnconfigure(1, weight=1)

        ttk.Label(opts, text="Keychain Identity (macOS)", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 2))
        id_row = ttk.Frame(opts, style="Inner.TFrame")
        id_row.grid(row=1, column=0, columnspan=3, sticky="we", pady=(0, 10))
        id_row.columnconfigure(0, weight=1)
        self.identity_combo = ttk.Combobox(id_row, textvariable=self.identity_var)
        self.identity_combo.grid(row=0, column=0, sticky="we")
        ttk.Button(id_row, text="Refresh", command=self._refresh_identities).grid(
            row=0, column=1, padx=(6, 0))

        ttk.Label(opts, text="Signing Backend", style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", pady=(0, 2))
        self.backend_combo = ttk.Combobox(
            opts, textvariable=self.backend_var,
            values=["auto", "codesign", "zsign", "ldid"], state="readonly")
        self.backend_combo.grid(row=3, column=0, columnspan=2, sticky="we", pady=(0, 6))

        self._backend_status = ttk.Label(opts, text="", style="Muted.TLabel")
        self._backend_status.grid(row=4, column=0, columnspan=3, sticky="w", pady=(0, 6))

        ttk.Checkbutton(opts, text="Install to connected device after signing",
                         variable=self.install_var).grid(
            row=5, column=0, columnspan=3, sticky="w")

        # Action
        action = ttk.Frame(self, style="TFrame")
        action.grid(row=3, column=0, sticky="we", pady=(4, 10))
        action.columnconfigure(1, weight=1)
        self.run_btn = ttk.Button(action, text="Sign & Install",
                                   style="Accent.TButton", command=self._on_run)
        self.run_btn.grid(row=0, column=0)
        self._status_label = ttk.Label(action, text="", style="Muted2.TLabel")
        self._status_label.grid(row=0, column=1, padx=(12, 0))

        self._progress = ttk.Progressbar(self, mode="indeterminate")
        self._progress.grid(row=4, column=0, sticky="we", pady=(0, 8))

        log_card = Card(self)
        log_card.grid(row=5, column=0, sticky="nswe")
        log_card.columnconfigure(0, weight=1)
        log_card.rowconfigure(1, weight=1)
        log_hdr = ttk.Frame(log_card, style="Inner.TFrame")
        log_hdr.grid(row=0, column=0, sticky="we", pady=(0, 6))
        ttk.Label(log_hdr, text="Log", style="Muted.TLabel").pack(side="left")
        ttk.Button(log_hdr, text="Clear", command=lambda: self.logbox.clear()).pack(side="right")
        self.logbox = LogBox(log_card, height=8)
        self.logbox.grid(row=1, column=0, sticky="nswe")
        sb = ttk.Scrollbar(log_card, command=self.logbox.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.logbox.configure(yscrollcommand=sb.set)

        self.rowconfigure(5, weight=1)

    def _refresh_backends(self) -> None:
        mgr = SigningManager()
        available = mgr.available_backends()
        if available:
            self._backend_status.configure(
                text=f"Available: {', '.join(available)}", foreground=SUCCESS)
        else:
            self._backend_status.configure(
                text="No signing backends found — install zsign or ldid", foreground=ERROR)

    def _refresh_identities(self) -> None:
        from ..signing.codesign_backend import CodesignBackend
        b = CodesignBackend()
        if not b.is_available():
            messagebox.showinfo("macOS only", "Keychain identities require macOS.")
            return
        ids = b.list_identities()
        self.identity_combo["values"] = ids
        if ids:
            self.identity_var.set(ids[0])

    def _log(self, msg: str) -> None:
        tag = "ok" if "success" in msg.lower() or msg.startswith("Done") else \
              "err" if "error" in msg.lower() or "fail" in msg.lower() else ""
        self.after(0, self.logbox.append, msg, tag)

    def _set_status(self, msg, color=FG2):
        self.after(0, lambda: self._status_label.configure(text=msg, foreground=color))

    def _on_run(self) -> None:
        if not self.ipa_path_var.get() or not self.profile_path_var.get():
            messagebox.showerror("Missing input",
                                  "Choose both an IPA file and a provisioning profile.")
            return

        backend = None if self.backend_var.get() == "auto" else self.backend_var.get()
        options = SideloadOptions(
            ipa_path=Path(self.ipa_path_var.get()),
            mobileprovision_path=Path(self.profile_path_var.get()),
            p12_path=Path(self.p12_path_var.get()) if self.p12_path_var.get() else None,
            p12_password=self.p12_password_var.get() or None,
            keychain_identity=self.identity_var.get() or None,
            bundle_id_override=self.bundle_id_var.get() or None,
            signing_backend=backend,
            install_to_device=self.install_var.get(),
            keep_signed_ipa=Path(self.output_path_var.get()) if self.output_path_var.get() else None,
        )

        self.run_btn.config(state="disabled")
        self.logbox.clear()
        self._progress.start(12)
        self._set_status("Running…", WARN)
        threading.Thread(target=self._run_thread, args=(options,), daemon=True).start()

    def _run_thread(self, options):
        try:
            result = asyncio.run(run_sideload(options, on_progress=self._log))
            self._log(f"Done. Signed IPA: {result}")
            self._set_status("Complete ✓", SUCCESS)
            self.after(0, lambda: messagebox.showinfo("Success", "Sideload complete."))
        except SideloaderError as e:
            self._log(f"Error: {e}")
            self._set_status("Failed", ERROR)
            self.after(0, lambda: messagebox.showerror("Failed", str(e)))
        finally:
            self.after(0, self._progress.stop)
            self.after(0, lambda: self.run_btn.config(state="normal"))


# ── Devices Tab ────────────────────────────────────────────────────────────────

class DevicesTab(ttk.Frame):
    def __init__(self, master: tk.Widget):
        super().__init__(master, padding=(20, 16))
        self.configure(style="TFrame")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        hdr = ttk.Frame(self, style="TFrame")
        hdr.grid(row=0, column=0, sticky="we", pady=(0, 14))
        ttk.Label(hdr, text="Connected Devices", style="H1.TLabel").pack(side="left")
        ttk.Button(hdr, text="Refresh", command=self._refresh).pack(side="right")

        card = Card(self)
        card.grid(row=1, column=0, sticky="nswe")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(0, weight=1)

        self.listbox = tk.Listbox(
            card, bg=BG3, fg=FG, font=FONT_MONO,
            selectbackground=ACCENT, selectforeground="#fff",
            relief="flat", borderwidth=0,
            activestyle="none", highlightthickness=0,
        )
        self.listbox.grid(row=0, column=0, sticky="nswe")
        sb = ttk.Scrollbar(card, command=self.listbox.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=sb.set)

        self._status = ttk.Label(self, text="", style="Muted2.TLabel")
        self._status.grid(row=2, column=0, sticky="w", pady=(8, 0))

        self._refresh()

    def _refresh(self) -> None:
        self.listbox.delete(0, "end")
        try:
            devices = asyncio.run(list_connected_devices())
        except Exception as e:
            self.listbox.insert("end", f"  Error: {e}")
            self._status.configure(text="Could not query devices", foreground=ERROR)
            return
        if not devices:
            self.listbox.insert("end", "  No devices found — plug in your iPhone/iPad")
            self._status.configure(text="0 devices", foreground=FG2)
        else:
            for d in devices:
                self.listbox.insert("end", f"  {d.udid}    {d.connection_type}")
            self._status.configure(
                text=f"{len(devices)} device{'s' if len(devices) != 1 else ''} found",
                foreground=SUCCESS)


# ── Settings Tab ───────────────────────────────────────────────────────────────

class SettingsTab(ttk.Frame):
    def __init__(self, master: tk.Widget, settings: dict, on_save):
        super().__init__(master, padding=(20, 16))
        self.settings = settings
        self.on_save  = on_save
        self.configure(style="TFrame")
        self.columnconfigure(0, weight=1)

        self.apple_id_var        = tk.StringVar(value=settings.get("apple_id", ""))
        self.anisette_url_var    = tk.StringVar(value=settings.get("custom_anisette_url", ""))
        self.zsign_path_var      = tk.StringVar(value=settings.get("zsign_path", ""))
        self.ldid_path_var       = tk.StringVar(value=settings.get("ldid_path", ""))
        self.log_level_var       = tk.StringVar(value=settings.get("log_level", "INFO"))
        self.log_to_file_var     = tk.BooleanVar(value=settings.get("log_to_file", False))
        self.remember_apple_var  = tk.BooleanVar(value=settings.get("remember_apple_id", True))
        self.timeout_var         = tk.StringVar(value=str(settings.get("request_timeout", 20)))

        ttk.Label(self, text="Settings", style="H1.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 14))

        self._section("Apple ID", 1)
        acct = Card(self)
        acct.grid(row=2, column=0, sticky="we", pady=(0, 12))
        acct.columnconfigure(1, weight=1)
        self._field(acct, "Saved Apple ID", self.apple_id_var, 0)
        ttk.Checkbutton(acct, text="Remember Apple ID between sessions",
                         variable=self.remember_apple_var,
                         style="TCheckbutton").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self._section("Anisette", 3)
        ani = Card(self)
        ani.grid(row=4, column=0, sticky="we", pady=(0, 12))
        ani.columnconfigure(1, weight=1)
        self._field(ani, "Custom Anisette Server URL", self.anisette_url_var, 0,
                    placeholder="https://ani.example.com  (leave blank for auto)")

        self._section("Signing Tools", 5)
        tools = Card(self)
        tools.grid(row=6, column=0, sticky="we", pady=(0, 12))
        tools.columnconfigure(1, weight=1)
        self._browse_field(tools, "zsign binary path (optional)", self.zsign_path_var, 0)
        self._browse_field(tools, "ldid binary path (optional)", self.ldid_path_var, 1)

        self._section("Network", 7)
        net = Card(self)
        net.grid(row=8, column=0, sticky="we", pady=(0, 12))
        net.columnconfigure(1, weight=1)
        ttk.Label(net, text="Request timeout (seconds)", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 2))
        ttk.Entry(net, textvariable=self.timeout_var, width=8).grid(
            row=1, column=0, sticky="w")

        self._section("Logging", 9)
        logs = Card(self)
        logs.grid(row=10, column=0, sticky="we", pady=(0, 12))
        logs.columnconfigure(1, weight=1)
        ttk.Label(logs, text="Log Level", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 2))
        ttk.Combobox(logs, textvariable=self.log_level_var,
                      values=["DEBUG", "INFO", "WARNING", "ERROR"],
                      state="readonly", width=12).grid(row=1, column=0, sticky="w", pady=(0, 8))
        ttk.Checkbutton(logs, text="Save logs to file",
                         variable=self.log_to_file_var,
                         style="TCheckbutton").grid(row=2, column=0, sticky="w")
        self._log_path_label = ttk.Label(logs, text=f"Log directory: {LOG_DIR}",
                                          style="Muted.TLabel")
        self._log_path_label.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        btn_row = ttk.Frame(self, style="TFrame")
        btn_row.grid(row=11, column=0, sticky="we", pady=(6, 0))
        ttk.Button(btn_row, text="Save Settings",
                   style="Accent.TButton", command=self._save).pack(side="left")
        ttk.Button(btn_row, text="Open Log Folder",
                   command=lambda: os.startfile(LOG_DIR) if os.name == "nt"
                   else None).pack(side="left", padx=(10, 0))

        self._save_label = ttk.Label(self, text="", style="Muted2.TLabel")
        self._save_label.grid(row=12, column=0, sticky="w", pady=(6, 0))

    def _section(self, title: str, row: int) -> None:
        f = ttk.Frame(self, style="TFrame")
        f.grid(row=row, column=0, sticky="we", pady=(4, 4))
        ttk.Label(f, text=title, foreground=ACCENT,
                  background=BG, font=("Segoe UI Semibold", 9)).pack(side="left")
        ttk.Separator(f, orient="horizontal").pack(side="left", fill="x",
                                                    expand=True, padx=(8, 0))

    def _field(self, parent, label, var, row, placeholder=""):
        ttk.Label(parent, text=label, style="Muted.TLabel").grid(
            row=row*2, column=0, columnspan=2, sticky="w", pady=(0, 2))
        ttk.Entry(parent, textvariable=var).grid(
            row=row*2+1, column=0, columnspan=2, sticky="we", pady=(0, 8))

    def _browse_field(self, parent, label, var, row):
        ttk.Label(parent, text=label, style="Muted.TLabel").grid(
            row=row*2, column=0, columnspan=2, sticky="w", pady=(0, 2))
        fr = ttk.Frame(parent, style="Inner.TFrame")
        fr.grid(row=row*2+1, column=0, columnspan=2, sticky="we", pady=(0, 8))
        fr.columnconfigure(0, weight=1)
        ttk.Entry(fr, textvariable=var).grid(row=0, column=0, sticky="we")
        ttk.Button(fr, text="Browse",
                   command=lambda v=var: v.set(
                       filedialog.askopenfilename() or v.get())).grid(
            row=0, column=1, padx=(6, 0))

    def _save(self) -> None:
        self.settings.update({
            "apple_id":            self.apple_id_var.get().strip()
                                   if self.remember_apple_var.get() else "",
            "custom_anisette_url": self.anisette_url_var.get().strip(),
            "zsign_path":          self.zsign_path_var.get().strip(),
            "ldid_path":           self.ldid_path_var.get().strip(),
            "log_level":           self.log_level_var.get(),
            "log_to_file":         self.log_to_file_var.get(),
            "remember_apple_id":   self.remember_apple_var.get(),
            "request_timeout":     int(self.timeout_var.get() or 20),
        })
        self.on_save(self.settings)

        # Apply log level immediately
        logging.getLogger().setLevel(self.log_level_var.get())
        if self.log_to_file_var.get():
            log_file = LOG_DIR / "ipasideloader.log"
            fh = logging.FileHandler(log_file)
            fh.setLevel(self.log_level_var.get())
            logging.getLogger().addHandler(fh)

        self._save_label.configure(text="Settings saved.", foreground=SUCCESS)
        self.after(3000, lambda: self._save_label.configure(text=""))


# ── Main window ────────────────────────────────────────────────────────────────

class MainWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ipasideloader")
        self.geometry("740x680")
        self.minsize(640, 560)
        self.configure(bg=BG)

        apply_theme(self)

        settings = load_settings()

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=0, pady=0)

        apple_tab    = AppleIdTab(notebook, settings)
        cert_tab     = CertTab(notebook, settings)
        devices_tab  = DevicesTab(notebook)
        settings_tab = SettingsTab(notebook, settings, on_save=save_settings)

        notebook.add(apple_tab,    text="  Sign with Apple ID  ")
        notebook.add(cert_tab,     text="  Sign with Certificate  ")
        notebook.add(devices_tab,  text="  Devices  ")
        notebook.add(settings_tab, text="  Settings  ")

        # Bottom status bar
        bar = tk.Frame(self, bg=BG2, height=24)
        bar.pack(fill="x", side="bottom")
        tk.Label(bar, text="ipasideloader", bg=BG2, fg=FG3,
                 font=("Segoe UI", 8)).pack(side="left", padx=8)


def main() -> None:
    app = MainWindow()
    app.mainloop()


if __name__ == "__main__":
    main()
