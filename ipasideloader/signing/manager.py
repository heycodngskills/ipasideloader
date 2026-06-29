"""
Picks and runs the right signing backend.

Selection order, unless the caller pins one explicitly:
  1. macOS + a usable Keychain identity  -> codesign  (the "real" Apple path)
  2. zsign available                      -> zsign     (cross-platform, real CMS)
  3. ldid available                       -> ldid      (ad-hoc / fake-sign only)

This mirrors how Sideloadly behaves: prefer native codesign when you're on
a Mac with a real identity, otherwise fall back to a portable signer.
"""
from __future__ import annotations

from typing import Literal, Optional

from .base import SigningBackend, SigningRequest
from .codesign_backend import CodesignBackend
from .ldid_backend import LdidBackend
from .zsign_backend import ZSignBackend
from ..errors import SigningError

BackendName = Literal["codesign", "zsign", "ldid"]


class SigningManager:
    def __init__(
        self,
        zsign_path: Optional[str] = None,
        ldid_path: Optional[str] = None,
    ):
        self.backends: dict[str, SigningBackend] = {
            "codesign": CodesignBackend(),
            "zsign": ZSignBackend(zsign_path),
            "ldid": LdidBackend(ldid_path),
        }

    def available_backends(self) -> list[str]:
        return [name for name, b in self.backends.items() if b.is_available()]

    def choose_backend(self, prefer: Optional[BackendName] = None) -> SigningBackend:
        if prefer:
            backend = self.backends.get(prefer)
            if not backend or not backend.is_available():
                raise SigningError(f"Requested signing backend '{prefer}' is not available on this system.")
            return backend

        for name in ("codesign", "zsign", "ldid"):
            backend = self.backends[name]
            if backend.is_available():
                return backend

        raise SigningError(
            "No signing backend available. Install zsign or ldid, "
            "or run on macOS with a signing identity in your Keychain."
        )

    def sign(self, request: SigningRequest, prefer: Optional[BackendName] = None) -> str:
        """Signs the app bundle, returns the name of the backend used."""
        backend = self.choose_backend(prefer)
        backend.sign(request)
        return backend.name
