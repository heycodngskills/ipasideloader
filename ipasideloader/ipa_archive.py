"""
Low-level .ipa archive handling: unzip to a working directory, locate the
.app bundle inside Payload/, and repack into a fresh, correctly-structured
.ipa once signing is done.

This is plain zipfile/shutil work -- no signing logic lives here.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Optional

from .errors import SideloaderError


def extract_ipa(ipa_path: Path, dest_dir: Path) -> Path:
    """
    Extracts an .ipa into dest_dir and returns the path to the .app bundle
    found inside Payload/.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ipa_path, "r") as zf:
        zf.extractall(dest_dir)

    payload_dir = dest_dir / "Payload"
    if not payload_dir.is_dir():
        raise SideloaderError(f"'{ipa_path}' doesn't look like a valid IPA (no Payload/ directory).")

    app_bundles = [p for p in payload_dir.iterdir() if p.suffix == ".app" and p.is_dir()]
    if not app_bundles:
        raise SideloaderError(f"No .app bundle found inside Payload/ of '{ipa_path}'.")
    if len(app_bundles) > 1:
        # Pick the first but warn -- most IPAs have exactly one.
        pass

    return app_bundles[0]


def repack_ipa(extracted_dir: Path, output_ipa_path: Path) -> Path:
    """
    Zips extracted_dir's contents (expects a Payload/ subdir) back into a
    valid .ipa at output_ipa_path. Apple's tooling expects Payload/ to sit
    at the root of the zip, which this preserves since we zip from
    extracted_dir directly.
    """
    output_ipa_path.parent.mkdir(parents=True, exist_ok=True)
    if output_ipa_path.exists():
        output_ipa_path.unlink()

    with zipfile.ZipFile(output_ipa_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in extracted_dir.rglob("*"):
            if path.is_file():
                arcname = path.relative_to(extracted_dir)
                zf.write(path, arcname)

    return output_ipa_path


def cleanup(*dirs: Path) -> None:
    for d in dirs:
        if Path(d).exists():
            shutil.rmtree(d, ignore_errors=True)
