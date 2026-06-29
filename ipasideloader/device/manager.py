"""
Device communication layer.

Everything here is a thin wrapper around pymobiledevice3's real, current
APIs (verified directly against the installed package source, not
assumed from memory):

  - usbmux.list_devices()                          -> enumerate connected devices
  - lockdown.create_using_usbmux(serial=...)        -> pair/connect to one
  - InstallationProxyService.install_from_local(..) -> install a signed IPA
  - InstallationProxyService.uninstall(bundle_id)   -> remove an app
  - InstallationProxyService.get_apps(...)          -> list installed apps
  - MisagentService.install/remove/copy_all(...)    -> manage provisioning
    profiles already pushed to the device

pymobiledevice3's public API is async, so this module is async too;
the CLI/GUI layers run it via asyncio.run() or an event loop as needed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Optional

from ..errors import DeviceError

logger = logging.getLogger(__name__)


@dataclass
class ConnectedDevice:
    udid: str
    connection_type: str  # "USB" or "Network"


async def list_connected_devices() -> list[ConnectedDevice]:
    """Enumerate devices currently visible to usbmux (USB or already-paired WiFi)."""
    try:
        from pymobiledevice3 import usbmux
    except ImportError as e:
        raise DeviceError(
            "pymobiledevice3 is not installed. Install it with `pip install pymobiledevice3`."
        ) from e

    try:
        devices = await usbmux.list_devices()
    except Exception as e:
        raise DeviceError(f"Could not query usbmux for connected devices: {e}") from e

    return [ConnectedDevice(udid=d.serial, connection_type=d.connection_type) for d in devices]


class DeviceSession:
    """
    A connected, paired lockdown session to one device. Use as an async
    context manager:

        async with DeviceSession(udid) as dev:
            await dev.install_ipa(path)
    """

    def __init__(self, udid: Optional[str] = None):
        self.udid = udid
        self._lockdown = None

    async def __aenter__(self) -> "DeviceSession":
        from pymobiledevice3.lockdown import create_using_usbmux

        try:
            self._lockdown = await create_using_usbmux(serial=self.udid, autopair=True)
        except Exception as e:
            raise DeviceError(
                f"Could not connect to device{f' {self.udid}' if self.udid else ''}. "
                "Make sure it's plugged in (or paired over WiFi), unlocked, and that "
                "you've tapped 'Trust' on the device if prompted. "
                f"Underlying error: {e}"
            ) from e
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._lockdown is not None:
            await self._lockdown.close()

    @property
    def lockdown(self):
        if self._lockdown is None:
            raise DeviceError("DeviceSession used outside of an active `async with` block.")
        return self._lockdown

    # -- app install/uninstall -----------------------------------------

    async def install_ipa(self, ipa_path: Path, developer: bool = True) -> None:
        """Install a (already signed) .ipa file onto this device."""
        from pymobiledevice3.services.installation_proxy import InstallationProxyService

        try:
            await InstallationProxyService(lockdown=self.lockdown).install_from_local(
                Path(ipa_path), developer=developer
            )
        except Exception as e:
            raise DeviceError(f"Install failed: {e}") from e

    async def uninstall(self, bundle_id: str) -> None:
        from pymobiledevice3.services.installation_proxy import InstallationProxyService

        await InstallationProxyService(lockdown=self.lockdown).uninstall(bundle_id)

    async def list_installed_apps(self, app_type: str = "User") -> list[dict]:
        from pymobiledevice3.services.installation_proxy import InstallationProxyService

        return await InstallationProxyService(lockdown=self.lockdown).get_apps(application_type=app_type)

    # -- provisioning profile sync --------------------------------------

    async def push_provisioning_profile(self, profile_bytes: bytes) -> None:
        """Install a .mobileprovision directly onto the device via misagent."""
        from pymobiledevice3.services.misagent import MisagentService

        await MisagentService(lockdown=self.lockdown).install(BytesIO(profile_bytes))

    async def list_provisioning_profiles(self) -> list:
        from pymobiledevice3.services.misagent import MisagentService

        return await MisagentService(lockdown=self.lockdown).copy_all()

    async def remove_provisioning_profile(self, profile_uuid: str) -> None:
        from pymobiledevice3.services.misagent import MisagentService

        await MisagentService(lockdown=self.lockdown).remove(profile_uuid)

    # -- device identity --------------------------------------------------

    async def get_udid(self) -> str:
        """Return this device's UDID (useful when one wasn't specified up front)."""
        return self.lockdown.udid
