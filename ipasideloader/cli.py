"""
Command-line interface for ipasideloader.

Examples:
    ipasideloader devices
    ipasideloader sign-install MyApp.ipa --p12 cert.p12 --profile app.mobileprovision --p12-password hunter2
    ipasideloader sign-install MyApp.ipa --backend ldid --profile app.mobileprovision --no-install -o signed.ipa
    ipasideloader profiles list
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from . import __version__
from .device.manager import DeviceSession, list_connected_devices
from .errors import SideloaderError
from .pipeline import SideloadOptions, run_sideload
from .signing.manager import SigningManager

logger = logging.getLogger(__name__)


def _print_progress(message: str) -> None:
    print(f"  -> {message}")


def cmd_devices(_args: argparse.Namespace) -> int:
    try:
        devices = asyncio.run(list_connected_devices())
    except SideloaderError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if not devices:
        print("No devices found. Plug one in over USB, or pair it for WiFi first.")
        return 1
    for d in devices:
        print(f"{d.udid}  ({d.connection_type})")
    return 0


def cmd_backends(_args: argparse.Namespace) -> int:
    mgr = SigningManager()
    available = mgr.available_backends()
    for name in ("codesign", "zsign", "ldid"):
        marker = "available" if name in available else "NOT FOUND"
        print(f"{name:10s} {marker}")
    return 0


def cmd_sign_install(args: argparse.Namespace) -> int:
    options = SideloadOptions(
        ipa_path=Path(args.ipa),
        mobileprovision_path=Path(args.profile),
        p12_path=Path(args.p12) if args.p12 else None,
        p12_password=args.p12_password,
        keychain_identity=args.identity,
        entitlements_path=Path(args.entitlements) if args.entitlements else None,
        bundle_id_override=args.bundle_id,
        signing_backend=args.backend,
        install_to_device=not args.no_install,
        device_udid=args.udid,
        keep_signed_ipa=Path(args.output) if args.output else None,
    )

    try:
        result_path = asyncio.run(run_sideload(options, on_progress=_print_progress))
    except SideloaderError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Done. Signed IPA: {result_path}")
    return 0


def cmd_profiles_list(args: argparse.Namespace) -> int:
    async def _run() -> int:
        async with DeviceSession(udid=args.udid) as device:
            profiles = await device.list_provisioning_profiles()
            for p in profiles:
                uuid_ = p.plist.get("UUID", "?")
                name = p.plist.get("Name", "?")
                print(f"{uuid_}  {name}")
        return 0

    try:
        return asyncio.run(_run())
    except SideloaderError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ipasideloader", description="Cross-platform open-source IPA sideloader.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_devices = sub.add_parser("devices", help="List connected iOS devices.")
    p_devices.set_defaults(func=cmd_devices)

    p_backends = sub.add_parser("backends", help="Show which signing backends are available.")
    p_backends.set_defaults(func=cmd_backends)

    p_sign = sub.add_parser("sign-install", help="Sign an .ipa and (by default) install it on a connected device.")
    p_sign.add_argument("ipa", help="Path to the input .ipa file.")
    p_sign.add_argument("--profile", required=True, help="Path to the .mobileprovision file to embed.")
    p_sign.add_argument("--p12", help="Path to a .p12 signing certificate (for zsign).")
    p_sign.add_argument("--p12-password", help="Password for the .p12 file, if any.")
    p_sign.add_argument("--identity", help="Keychain identity name to use (for macOS codesign).")
    p_sign.add_argument("--entitlements", help="Optional entitlements .plist to apply.")
    p_sign.add_argument("--bundle-id", help="Override the app's bundle identifier.")
    p_sign.add_argument(
        "--backend", choices=["codesign", "zsign", "ldid"], default=None,
        help="Force a specific signing backend (default: auto-pick best available).",
    )
    p_sign.add_argument("--no-install", action="store_true", help="Sign only; don't install to a device.")
    p_sign.add_argument("--udid", help="Target a specific device by UDID (default: first connected device).")
    p_sign.add_argument("-o", "--output", help="Where to save the final signed .ipa.")
    p_sign.set_defaults(func=cmd_sign_install)

    p_profiles = sub.add_parser("profiles", help="Manage provisioning profiles on a connected device.")
    profiles_sub = p_profiles.add_subparsers(dest="profiles_command", required=True)
    p_profiles_list = profiles_sub.add_parser("list", help="List provisioning profiles installed on the device.")
    p_profiles_list.add_argument("--udid", help="Target a specific device by UDID.")
    p_profiles_list.set_defaults(func=cmd_profiles_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
