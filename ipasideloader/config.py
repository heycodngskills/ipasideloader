"""
Shared configuration, paths, and constants for ipasideloader.
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

APP_NAME = "ipasideloader"


def _user_data_dir() -> Path:
    """Cross-platform per-user app data directory."""
    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    elif system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:  # Linux and friends
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / APP_NAME


DATA_DIR = _user_data_dir()
CACHE_DIR = DATA_DIR / "cache"
WORK_DIR = DATA_DIR / "work"          # scratch space for unzipped IPAs etc.
CREDS_DIR = DATA_DIR / "credentials"  # stored Apple ID session / anisette data
LOG_DIR = DATA_DIR / "logs"

for d in (DATA_DIR, CACHE_DIR, WORK_DIR, CREDS_DIR, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

# Apple's free developer team can register at most this many app IDs at once.
FREE_TEAM_APP_ID_LIMIT = 10

# Default public anisette servers to try, in order, before giving up.
# These mirror the public servers AltServer/SideStore-style tooling has
# historically published. Users can prepend their own via settings.
DEFAULT_PUBLIC_ANISETTE_SERVERS = [
    "https://ani.sidestore.io",
    "https://sign.rheaime.dev",
]
