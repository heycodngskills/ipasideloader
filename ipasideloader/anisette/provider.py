"""
Anisette data resolution.

"Anisette data" is the device-identity blob Apple's authentication APIs
require on every request (normally synthesized by a real Mac/iTunes
install). Since most users running this tool don't have that, we need an
anisette *provider*. We try, in order:

  1. Any custom anisette server URL the user configured (their own
     self-hosted instance), if set.
  2. A local, in-process provider (the `anisette` package / Anisette.py,
     https://github.com/malmeloo/Anisette.py) that synthesizes valid
     anisette data on-device, no server required, by loading Apple Music
     APK libraries. This is the same approach AltServer/SideStore-adjacent
     tooling uses to avoid needing a real Mac.
  3. A list of known public anisette servers, e.g. the ones the
     SideStore/AltServer community publishes, as a last resort.

Each remote candidate is verified with a lightweight call before we
commit to using it, so a dead server doesn't silently break the whole
auth flow.

Note: pymobiledevice3 is NOT involved here — it has no anisette or
Apple-ID-auth functionality. It's used elsewhere in this project purely
for device pairing and installing the final signed IPA.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import requests

from ..config import DEFAULT_ANISETTE_SERVER, DEFAULT_PUBLIC_ANISETTE_SERVERS
from ..errors import AnisetteError

logger = logging.getLogger(__name__)


@dataclass
class AnisetteData:
    """The headers Apple's auth endpoints expect."""
    headers: dict = field(default_factory=dict)
    source: str = ""  # which provider produced this, for diagnostics


class AnisetteProvider:
    """
    Resolves a working anisette source and fetches data from it on demand.

    Priority order: custom URL (if set) -> local in-process provider ->
    known public servers.
    """

    def __init__(
        self,
        custom_url: Optional[str] = None,
        public_servers: Optional[list[str]] = None,
        timeout: float = 8.0,
    ):
        # Use the default server if no custom URL provided
        _url = custom_url or DEFAULT_ANISETTE_SERVER
        self.custom_url = _url.rstrip("/") if _url else None
        self.public_servers = public_servers or DEFAULT_PUBLIC_ANISETTE_SERVERS
        self.timeout = timeout
        self._resolved_remote_url: Optional[str] = None

    # -- built-in (local, no server needed) ----------------------------
    #
    # NOTE: pymobiledevice3 does NOT provide anisette or Apple-ID auth (it's
    # a device-protocol library: pairing/lockdown/install only). The actual
    # local anisette generator we use here is `Anisette.py` (PyPI: anisette),
    # which loads Apple Music APK libraries to synthesize valid anisette
    # data on-device, no remote server required:
    # https://github.com/malmeloo/Anisette.py

    def _builtin_libs_path(self):
        from ..config import CREDS_DIR
        return CREDS_DIR / "anisette_libs.bin"

    def _try_builtin(self) -> Optional[AnisetteData]:
        try:
            from anisette import Anisette
        except ImportError:
            logger.debug("Anisette.py (PyPI: Anisette) not installed; skipping local provider.")
            return None

        libs_path = self._builtin_libs_path()
        try:
            if libs_path.exists():
                # Restore a previously-saved library bundle -- avoids
                # re-downloading the ~3MB Apple Music APK library data.
                provider = Anisette.load(str(libs_path))
            else:
                # First run: Anisette.init() downloads the library bundle
                # automatically when no file/APK is given.
                provider = Anisette.init()
                provider.save_libs(str(libs_path))
            headers = provider.get_data()
            return AnisetteData(headers=dict(headers), source="local:anisette.py")
        except Exception as e:
            logger.warning("Local Anisette.py provider failed: %s", e)
            return None

    # -- remote servers (custom + public) ------------------------------

    def _probe_remote(self, base_url: str) -> bool:
        try:
            resp = requests.get(f"{base_url}/health", timeout=self.timeout)
            if resp.status_code < 500:
                return True
        except requests.RequestException:
            pass
        # Some anisette servers don't expose /health; try the real endpoint.
        try:
            resp = requests.get(base_url, timeout=self.timeout)
            return resp.status_code < 500
        except requests.RequestException:
            return False

    def _fetch_remote(self, base_url: str) -> AnisetteData:
        resp = requests.get(f"{base_url}/v3/client_info", timeout=self.timeout)
        resp.raise_for_status()
        return AnisetteData(headers=resp.json(), source=base_url)

    def get(self) -> AnisetteData:
        """
        Resolve and return anisette data, trying custom -> local -> public
        in order. Raises AnisetteError if every option fails.
        """
        attempts: list[str] = []

        # 1. Custom server, if the user configured one.
        if self.custom_url:
            attempts.append(self.custom_url)
            if self._probe_remote(self.custom_url):
                try:
                    return self._fetch_remote(self.custom_url)
                except Exception as e:
                    logger.warning("Custom anisette server %s failed: %s", self.custom_url, e)

        # 2. Built-in local provider via Anisette.py, if usable.
        builtin = self._try_builtin()
        if builtin is not None:
            return builtin

        # 3. Known public servers, in order.
        for server in self.public_servers:
            attempts.append(server)
            if self._probe_remote(server):
                try:
                    data = self._fetch_remote(server)
                    self._resolved_remote_url = server
                    return data
                except Exception as e:
                    logger.warning("Public anisette server %s failed: %s", server, e)

        raise AnisetteError(
            "Could not reach any anisette server. Tried: " + ", ".join(attempts or ["<none configured>"])
        )
