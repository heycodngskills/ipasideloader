"""
High-level sideload pipeline: unpack -> sign -> repack -> install.

Two entry points:
  - run_sideload()           — classic flow, caller supplies p12 + mobileprovision
  - run_sideload_apple_id()  — Sideloadly-style, no certs needed; everything is
                               fetched automatically from Apple using the Apple ID
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .config import WORK_DIR
from .device.manager import DeviceSession
from .errors import SideloaderError
from .ipa_archive import cleanup, extract_ipa, read_app_name, read_bundle_id, repack_ipa
from .signing.base import SigningRequest
from .signing.manager import BackendName, SigningManager

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None]
TwoFactorCallback = Callable[[], str]


def _noop_progress(_message: str) -> None:
    pass


@dataclass
class SideloadOptions:
    ipa_path: Path
    mobileprovision_path: Path
    p12_path: Optional[Path] = None
    p12_password: Optional[str] = None
    keychain_identity: Optional[str] = None
    entitlements_path: Optional[Path] = None
    bundle_id_override: Optional[str] = None
    signing_backend: Optional[BackendName] = None
    install_to_device: bool = True
    device_udid: Optional[str] = None
    keep_signed_ipa: Optional[Path] = None


async def run_sideload(
    options: SideloadOptions,
    on_progress: ProgressCallback = _noop_progress,
) -> Path:
    """
    Classic flow: caller provides a .p12 + .mobileprovision.
    Returns the path to the final signed .ipa.
    """
    work_id = uuid.uuid4().hex[:8]
    extract_dir = WORK_DIR / f"extract-{work_id}"

    try:
        on_progress(f"Unpacking {options.ipa_path.name}...")
        app_bundle_path = extract_ipa(options.ipa_path, extract_dir)

        on_progress("Signing app bundle...")
        signer = SigningManager()
        request = SigningRequest(
            app_bundle_path=app_bundle_path,
            mobileprovision_path=options.mobileprovision_path,
            p12_path=options.p12_path,
            p12_password=options.p12_password,
            keychain_identity=options.keychain_identity,
            entitlements_path=options.entitlements_path,
            bundle_id=options.bundle_id_override,
        )
        used_backend = signer.sign(request, prefer=options.signing_backend)
        on_progress(f"Signed successfully using '{used_backend}'.")

        on_progress("Repacking signed .ipa...")
        signed_ipa_path = WORK_DIR / f"signed-{work_id}.ipa"
        repack_ipa(extract_dir, signed_ipa_path)

        final_path = signed_ipa_path

        if options.install_to_device:
            on_progress("Connecting to device...")
            async with DeviceSession(udid=options.device_udid) as device:
                on_progress("Installing on device (this can take a minute)...")
                await device.install_ipa(signed_ipa_path, developer=True)
            on_progress("Installed successfully.")

        if options.keep_signed_ipa:
            import shutil
            options.keep_signed_ipa.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(signed_ipa_path, options.keep_signed_ipa)
            final_path = options.keep_signed_ipa

        return final_path

    except SideloaderError:
        raise
    except Exception as e:
        raise SideloaderError(f"Unexpected error during sideload: {e}") from e
    finally:
        cleanup(extract_dir)


async def run_sideload_apple_id(
    ipa_path: Path,
    apple_id: str,
    password: str,
    device_udid: Optional[str] = None,
    signing_backend: Optional[BackendName] = None,
    keep_signed_ipa: Optional[Path] = None,
    on_progress: ProgressCallback = _noop_progress,
    on_two_factor: Optional[TwoFactorCallback] = None,
    custom_anisette_url: Optional[str] = None,
) -> Path:
    """
    Sideloadly-style flow: sign and install using only an Apple ID.

    Contacts Apple's developer services to create or reuse a free signing
    certificate and provisioning profile — no paid account or existing certs
    required. The password is NEVER written to disk.
    """
    from .apple.free_provision import FreeProvisionFlow

    work_id = uuid.uuid4().hex[:8]
    extract_dir = WORK_DIR / f"extract-{work_id}"

    try:
        if not device_udid:
            on_progress("Looking for connected device...")
            from .device.manager import list_connected_devices
            devices = await list_connected_devices()
            if not devices:
                raise SideloaderError(
                    "No device found. Plug in your iPhone/iPad and tap 'Trust' if prompted."
                )
            device_udid = devices[0].udid
            on_progress(f"Found device: {device_udid}")

        on_progress("Reading IPA metadata...")
        bundle_id = read_bundle_id(ipa_path)
        app_name = read_app_name(ipa_path)
        on_progress(f"App: {app_name}  ({bundle_id})")

        flow = FreeProvisionFlow(
            apple_id=apple_id,
            password=password,
            on_progress=on_progress,
            on_two_factor=on_two_factor,
            custom_anisette_url=custom_anisette_url,
        )
        result = flow.run(bundle_id=bundle_id, device_udid=device_udid, app_name=app_name)

        on_progress(f"Unpacking {ipa_path.name}...")
        app_bundle_path = extract_ipa(ipa_path, extract_dir)

        on_progress("Signing app bundle...")
        signer = SigningManager()
        request = SigningRequest(
            app_bundle_path=app_bundle_path,
            mobileprovision_path=result.mobileprovision_path,
            p12_path=result.p12_path,
            p12_password=result.p12_password,
            bundle_id=bundle_id,
        )
        used_backend = signer.sign(request, prefer=signing_backend)
        on_progress(f"Signed successfully using '{used_backend}'.")

        on_progress("Repacking signed .ipa...")
        signed_ipa_path = WORK_DIR / f"signed-{work_id}.ipa"
        repack_ipa(extract_dir, signed_ipa_path)

        final_path = signed_ipa_path

        on_progress("Connecting to device...")
        async with DeviceSession(udid=device_udid) as device:
            on_progress("Installing on device (this can take a minute)...")
            await device.install_ipa(signed_ipa_path, developer=True)
        on_progress("Installed successfully.")

        if keep_signed_ipa:
            import shutil
            keep_signed_ipa.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(signed_ipa_path, keep_signed_ipa)
            final_path = keep_signed_ipa

        return final_path

    except SideloaderError:
        raise
    except Exception as e:
        raise SideloaderError(f"Unexpected error during sideload: {e}") from e
    finally:
        cleanup(extract_dir)
