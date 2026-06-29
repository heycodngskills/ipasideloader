"""
Exception types used throughout ipasideloader.

Keeping these centralized makes it easy for the GUI/CLI layers to catch
specific failure classes and show a useful message instead of a raw
traceback.
"""
from __future__ import annotations


class SideloaderError(Exception):
    """Base class for all expected/handled errors in this app."""


class ToolNotFoundError(SideloaderError):
    """Raised when a required external binary (zsign, ldid, codesign) is missing."""


class SigningError(SideloaderError):
    """Raised when a signing backend fails to produce a signed IPA."""


class AnisetteError(SideloaderError):
    """Raised when no anisette server (local or remote) could be reached."""


class AppleAuthError(SideloaderError):
    """Raised when Apple ID authentication (incl. 2FA) fails."""


class ProvisioningError(SideloaderError):
    """Raised when app ID / provisioning profile registration fails."""


class DeviceError(SideloaderError):
    """Raised when no device is found, pairing fails, or install fails."""
