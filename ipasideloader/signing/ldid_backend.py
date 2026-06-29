"""
ldid backend.

ldid (https://github.com/ProcursusTeam/ldid) is the classic "fake-sign" /
ad-hoc signing tool used across the jailbreak ecosystem. It's useful when:

  - You only need an ad-hoc signature (no real Apple cert), e.g. for
    TrollStore-style installs or resigning for a jailbroken device, or
  - You want to (re)apply entitlements / a provisioning profile without
    a full CMS signature.

Like the zsign backend, this is a thin subprocess wrapper. No signing
logic lives in Python.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import SigningBackend, SigningRequest, find_binary, run_subprocess
from ..errors import ToolNotFoundError


class LdidBackend(SigningBackend):
    name = "ldid"

    def __init__(self, binary_path: Optional[str] = None):
        self._explicit_path = binary_path

    def _resolve_binary(self) -> str:
        path = self._explicit_path or find_binary(
            "ldid",
            extra_hints=[
                "/usr/local/bin/ldid",
                "/opt/homebrew/bin/ldid",
                str(Path.home() / "bin" / "ldid"),
            ],
        )
        if not path:
            raise ToolNotFoundError(
                "ldid binary not found. Install it from "
                "https://github.com/ProcursusTeam/ldid or place it on your PATH."
            )
        return path

    def is_available(self) -> bool:
        try:
            self._resolve_binary()
            return True
        except ToolNotFoundError:
            return False

    def sign(self, request: SigningRequest) -> None:
        ldid = self._resolve_binary()

        # ldid signs the Mach-O executable inside the bundle, not the bundle
        # dir itself. We resolve the main executable from Info.plist.
        executable = self._main_executable_path(request.app_bundle_path)

        args = [ldid]
        if request.entitlements_path:
            args.append(f"-S{request.entitlements_path}")
        else:
            args.append("-S")  # plain ad-hoc sign, no entitlements file

        args.append(str(executable))
        run_subprocess(args)

    @staticmethod
    def _main_executable_path(app_bundle_path: Path) -> Path:
        import plistlib

        info_plist = app_bundle_path / "Info.plist"
        with open(info_plist, "rb") as f:
            info = plistlib.load(f)
        exe_name = info.get("CFBundleExecutable")
        if not exe_name:
            raise ToolNotFoundError(f"Could not determine main executable from {info_plist}")
        return app_bundle_path / exe_name
