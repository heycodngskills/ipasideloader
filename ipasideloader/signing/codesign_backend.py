"""
macOS `codesign` backend.

This is the "real" path: macOS's own codesign, driven against an identity
already imported into the user's (or a CI) Keychain. We do not attempt to
replicate codesign's behavior in pure Python — that's a deep rabbit hole
(CMS structure, CodeDirectory hashing, sealed resources, entitlements
blob format...) that a 10-year-old, actively-patched Apple binary already
gets right. So this backend simply requires:

  1. You're running on macOS (or a macOS CI runner), and
  2. The signing identity is already present in a keychain `security` can see.

If those two things aren't true, this backend reports itself unavailable
and the caller should fall back to zsign/ldid instead.
"""
from __future__ import annotations

import platform
import shutil
from pathlib import Path
from typing import Optional

from .base import SigningBackend, SigningRequest, run_subprocess
from ..errors import SigningError, ToolNotFoundError


class CodesignBackend(SigningBackend):
    name = "codesign"

    def is_available(self) -> bool:
        if platform.system() != "Darwin":
            return False
        return shutil.which("codesign") is not None and shutil.which("security") is not None

    def list_identities(self) -> list[str]:
        """Return signing identity names currently visible to `security`."""
        if not self.is_available():
            return []
        result = run_subprocess(["security", "find-identity", "-v", "-p", "codesigning"])
        identities = []
        for line in result.stdout.splitlines():
            # Typical line: '  1) ABCDEF... "Apple Development: Jane Doe (TEAMID)"'
            if '"' in line:
                identities.append(line.split('"')[1])
        return identities

    def import_p12_to_keychain(
        self, p12_path: Path, p12_password: Optional[str], keychain: Optional[str] = None
    ) -> None:
        """Import a .p12 into a keychain so codesign can use it as an identity."""
        if not self.is_available():
            raise ToolNotFoundError("codesign/security not available (requires macOS).")

        args = ["security", "import", str(p12_path)]
        if keychain:
            args += ["-k", keychain]
        if p12_password is not None:
            args += ["-P", p12_password]
        # Allow codesign to use the key without a per-use prompt.
        args += ["-T", "/usr/bin/codesign"]
        run_subprocess(args)

    def sign(self, request: SigningRequest) -> None:
        if not self.is_available():
            raise ToolNotFoundError(
                "The codesign backend requires macOS (or a macOS CI runner) "
                "with the `codesign` and `security` tools available."
            )
        if not request.keychain_identity:
            raise SigningError(
                "codesign backend requires `keychain_identity` "
                "(e.g. 'Apple Development: Jane Doe (TEAMID)')."
            )

        args = [
            "codesign",
            "--force",
            "--sign", request.keychain_identity,
            "--timestamp=none",
        ]
        if request.entitlements_path:
            args += ["--entitlements", str(request.entitlements_path)]
        args.append(str(request.app_bundle_path))

        run_subprocess(args)

        # codesign doesn't embed the provisioning profile itself; copy it in.
        if request.mobileprovision_path:
            dest = request.app_bundle_path / "embedded.mobileprovision"
            dest.write_bytes(Path(request.mobileprovision_path).read_bytes())
