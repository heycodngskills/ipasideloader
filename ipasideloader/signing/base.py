"""
Base interface for IPA signing backends.

Each backend takes an already-unzipped .app bundle (or a directory tree
containing one), a signing identity of some kind, and a provisioning
profile, and signs it in place. We deliberately do NOT reimplement
CMS/CodeDirectory signing in Python — every backend here shells out to a
real, actively-maintained tool that already does this correctly:

  - zsign   : cross-platform (Linux/Windows/macOS), takes a .p12 + .mobileprovision
  - ldid    : cross-platform, fake-signs / ad-hoc signs (no real cert needed)
  - codesign: macOS only, uses a Keychain identity (the "real" Apple path)
"""
from __future__ import annotations

import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..errors import SigningError, ToolNotFoundError


def find_binary(name: str, extra_hints: Optional[list[str]] = None) -> Optional[str]:
    """Locate an external binary on PATH, falling back to common install hints."""
    found = shutil.which(name)
    if found:
        return found
    for hint in extra_hints or []:
        p = Path(hint)
        if p.exists() and p.is_file():
            return str(p)
    return None


def run_subprocess(args: list[str], *, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    """
    Run an external signing tool, capturing output. Raises SigningError on
    non-zero exit so callers don't have to repeat the same boilerplate.
    """
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=600,
        )
    except FileNotFoundError as e:
        raise ToolNotFoundError(f"Required binary not found: {args[0]}") from e
    except subprocess.TimeoutExpired as e:
        raise SigningError(f"Signing tool timed out: {' '.join(args)}") from e

    if result.returncode != 0:
        raise SigningError(
            f"Command failed (exit {result.returncode}): {' '.join(args)}\n"
            f"--- output ---\n{result.stdout}"
        )
    return result


@dataclass
class SigningRequest:
    """Everything a backend needs to sign one .app bundle."""
    app_bundle_path: Path          # path to the .app directory
    mobileprovision_path: Path     # the embedded.mobileprovision to apply
    p12_path: Optional[Path] = None     # signing cert (zsign / real codesign)
    p12_password: Optional[str] = None
    keychain_identity: Optional[str] = None  # e.g. "Apple Development: Jane Doe (TEAMID)"
    entitlements_path: Optional[Path] = None
    bundle_id: Optional[str] = None     # overrides the bundle ID if set


class SigningBackend(ABC):
    """Common interface implemented by each concrete signer."""

    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this backend's dependencies are present on this machine."""

    @abstractmethod
    def sign(self, request: SigningRequest) -> None:
        """Sign the app bundle in place. Raises SigningError on failure."""
