"""
zsign backend.

zsign (https://github.com/zhlynn/zsign) is a C++ tool that implements real
CMS signing and CodeDirectory generation, the same way Apple's own codesign
does. It's the backend Sideloadly itself uses on Windows, and it runs fine
on Linux/macOS too, which makes it our cross-platform default.

We do nothing clever here: build an argv list, run it, check the exit
code. All the actual cryptographic/binary-format work happens inside the
zsign binary, which is exactly the point.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import SigningBackend, SigningRequest, find_binary, run_subprocess
from ..errors import SigningError, ToolNotFoundError


class ZSignBackend(SigningBackend):
    name = "zsign"

    def __init__(self, binary_path: Optional[str] = None):
        self._explicit_path = binary_path

    def _resolve_binary(self) -> str:
        path = self._explicit_path or find_binary(
            "zsign",
            extra_hints=[
                "/usr/local/bin/zsign",
                "/opt/homebrew/bin/zsign",
                str(Path.home() / "bin" / "zsign"),
            ],
        )
        if not path:
            raise ToolNotFoundError(
                "zsign binary not found. Install it from "
                "https://github.com/zhlynn/zsign or place it on your PATH."
            )
        return path

    def is_available(self) -> bool:
        try:
            self._resolve_binary()
            return True
        except ToolNotFoundError:
            return False

    def sign(self, request: SigningRequest) -> None:
        if not request.p12_path or not request.mobileprovision_path:
            raise SigningError("zsign requires both a .p12 certificate and a .mobileprovision file.")

        zsign = self._resolve_binary()

        args = [
            zsign,
            "-k", str(request.p12_path),
            "-m", str(request.mobileprovision_path),
            "-o", str(request.app_bundle_path),  # zsign can sign-in-place on a dir
        ]
        if request.p12_password:
            args += ["-p", request.p12_password]
        if request.entitlements_path:
            args += ["-e", str(request.entitlements_path)]
        if request.bundle_id:
            args += ["-b", request.bundle_id]

        # Sign the bundle directory itself (zsign accepts a .app dir or .ipa).
        args.append(str(request.app_bundle_path))

        run_subprocess(args)
