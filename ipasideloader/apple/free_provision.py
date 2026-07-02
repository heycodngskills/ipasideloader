"""
End-to-end Apple ID → provisioning flow (Sideloadly-style, no paid account).

Orchestrates:
  1. Login with Apple ID + password (GSA / SRP-6a via auth.py)
  2. Resolve the personal free developer team
  3. Get or create a signing certificate (CSR → Apple cert → cached P12)
  4. Register the target device UDID with the team
  5. Register an App ID for the IPA's bundle ID (or reuse an existing one)
  6. Fetch a provisioning profile covering that App ID and device
  7. Return paths to the .p12 and .mobileprovision for the signing pipeline

Notes
-----
- The certificate and its private key are cached locally so re-runs don't
  exhaust the free-account certificate limit.
- The password is NEVER written to disk.
- This module handles 2FA by calling an ``on_two_factor`` callback the caller
  provides (the GUI shows a dialog; the CLI prompts stdin).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .auth import AppleAccountClient, AppleSession, TwoFactorRequired
from .certificate import (
    CertBundle,
    build_p12,
    clear_cert_bundle,
    generate_key_and_csr,
    load_cert_bundle,
    save_cert_bundle,
)
from .developer_services import DeveloperServicesClient, DeveloperTeam
from ..anisette.provider import AnisetteProvider
from ..config import WORK_DIR
from ..errors import ProvisioningError

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None]
TwoFactorCallback = Callable[[], str]  # called when Apple wants 2FA; returns the 6-digit code


@dataclass
class ProvisionResult:
    """Everything ``pipeline.run_sideload`` needs to sign with a free Apple account."""
    p12_path: Path
    p12_password: str
    mobileprovision_path: Path
    team_id: str
    bundle_id: str


def _write_work(suffix: str, data: bytes) -> Path:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    p = WORK_DIR / f"autoprovisioned{suffix}"
    p.write_bytes(data)
    return p


class FreeProvisionFlow:
    """
    Run the full Apple ID → provisioning flow.

    Parameters
    ----------
    apple_id : str
    password : str            — never stored to disk
    on_progress : callback    — receives human-readable status strings
    on_two_factor : callback  — called when Apple needs a 2FA code; returns the code
    custom_anisette_url : str, optional
    """

    def __init__(
        self,
        apple_id: str,
        password: str,
        on_progress: Optional[ProgressCallback] = None,
        on_two_factor: Optional[TwoFactorCallback] = None,
        custom_anisette_url: Optional[str] = None,
    ):
        self.apple_id = apple_id
        self.password = password
        self._progress = on_progress or (lambda _: None)
        self._two_factor = on_two_factor
        self._anisette = AnisetteProvider(custom_url=custom_anisette_url)
        self._auth = AppleAccountClient(anisette=self._anisette)
        self._session: Optional[AppleSession] = None
        self._dev: Optional[DeveloperServicesClient] = None

    def _log(self, msg: str) -> None:
        logger.info(msg)
        self._progress(msg)

    # ── login ─────────────────────────────────────────────────────────────────

    def _login(self) -> None:
        import os, sys, certifi
        if getattr(sys, "frozen", False):
            _ca1 = os.path.join(sys._MEIPASS, "ipasideloader", "certs", "ca-bundle.pem")
            _ca2 = os.path.join(sys._MEIPASS, "certs", "ca-bundle.pem")
            self._progress(f"[diag] MEIPASS: {sys._MEIPASS}")
            self._progress(f"[diag] ca path1 exists: {os.path.isfile(_ca1)} -> {_ca1}")
            self._progress(f"[diag] ca path2 exists: {os.path.isfile(_ca2)} -> {_ca2}")
            _root_path = os.path.join(sys._MEIPASS, "ipasideloader", "certs", "apple-root.pem")
            _exists = os.path.isfile(_root_path)
            _count = 0
            if _exists:
                with open(_root_path, "r") as f:
                    _count = f.read().count("BEGIN CERTIFICATE")
            self._progress(f"[diag] apple-root.pem exists: {_exists}, cert count: {_count}, path: {_root_path}")
            try:
                from OpenSSL import SSL
                import socket as _socket
                _ctx = SSL.Context(SSL.TLS_METHOD)
                _ctx.set_verify(SSL.VERIFY_NONE, lambda *a: True)
                _sock = _socket.create_connection(("gsa.apple.com", 443), timeout=10)
                _conn = SSL.Connection(_ctx, _sock)
                _conn.set_tlsext_host_name(b"gsa.apple.com")
                _conn.set_connect_state()
                while True:
                    try:
                        _conn.do_handshake()
                        break
                    except SSL.WantReadError:
                        continue
                for _c in _conn.get_peer_cert_chain():
                    _subj = _c.get_subject().CN
                    _iss = _c.get_issuer().CN
                    self._progress(f"[diag-chain] subject={_subj} | issuer={_iss} | self-signed={_subj == _iss}")
                _conn.close()
            except Exception as _e:
                self._progress(f"[diag-chain] failed: type={type(_e).__name__} args={_e.args!r} repr={_e!r}")
        else:
            self._progress(f"[diag] running as script, certifi: {certifi.where()}")
        self._log("Signing in with Apple ID…")
        try:
            self._session = self._auth.login(self.apple_id, self.password)
        except TwoFactorRequired:
            if not self._two_factor:
                raise ProvisioningError(
                    "This Apple ID requires two-factor authentication. "
                    "Provide a 2FA handler or enter the code when prompted."
                )
            self._log("Two-factor authentication required — check your devices…")
            code = self._two_factor()
            self._session = self._auth.login(self.apple_id, self.password, two_factor_code=code)
        self._log("Signed in successfully.")
        self._dev = DeveloperServicesClient(
            self._session,
            anisette_headers=self._auth._anisette_headers(),
        )

    # ── team ──────────────────────────────────────────────────────────────────

    def _pick_team(self) -> DeveloperTeam:
        self._log("Fetching developer teams…")
        teams = self._dev.list_teams()
        if not teams:
            raise ProvisioningError(
                "No developer teams found for this Apple ID.\n"
                "Open Xcode on a Mac, sign in with the same Apple ID, and accept the "
                "developer agreement to create a free personal team."
            )
        # Prefer the personal team (usually contains "Personal Team" in the name)
        personal = [t for t in teams if "personal" in t.name.lower()]
        chosen = personal[0] if personal else teams[0]
        self._log(f"Using team: {chosen.name} ({chosen.team_id})")
        return chosen

    # ── certificate ───────────────────────────────────────────────────────────

    def _get_or_create_cert(self, team: DeveloperTeam) -> CertBundle:
        cached = load_cert_bundle(self.apple_id, team.team_id)
        if cached:
            try:
                live = self._dev.list_certificates(team.team_id)
                if any(c.get("certRequestId") == cached.cert_id for c in live):
                    self._log("Using cached signing certificate.")
                    return cached
                else:
                    self._log("Cached certificate is no longer valid — generating a new one…")
                    clear_cert_bundle(self.apple_id, team.team_id)
            except Exception as exc:
                logger.warning("Could not verify cached cert against Apple: %s", exc)

        self._log("Generating a new signing certificate…")
        private_key_pem, csr_der = generate_key_and_csr(self.apple_id)
        cert_der, cert_id = self._dev.submit_csr(team.team_id, csr_der)
        self._log(f"Certificate issued (id={cert_id}).")

        p12_bytes = build_p12(private_key_pem, cert_der)
        bundle = CertBundle(
            p12_bytes=p12_bytes,
            p12_password=b"ipasideloader",
            cert_id=cert_id,
            apple_id=self.apple_id,
        )
        save_cert_bundle(bundle, team.team_id)
        return bundle

    # ── app ID ────────────────────────────────────────────────────────────────

    def _ensure_app_id(self, team: DeveloperTeam, bundle_id: str, app_name: str) -> str:
        existing = self._dev.list_app_ids(team.team_id)
        for app in existing:
            if app.get("identifier") == bundle_id:
                self._log(f"App ID already registered: {bundle_id}")
                return str(app.get("appIdId") or app.get("appId") or "")
        self._log(f"Registering App ID: {bundle_id}…")
        reg = self._dev.register_app_id(team.team_id, bundle_id, app_name)
        return str(reg.raw.get("appIdId") or reg.raw.get("appId") or "")

    # ── public entry point ────────────────────────────────────────────────────

    def run(
        self,
        bundle_id: str,
        device_udid: str,
        app_name: str = "App",
    ) -> ProvisionResult:
        """
        Full flow: login → cert → App ID → device → profile.

        Returns a ``ProvisionResult`` with paths to a temporary .p12 and
        .mobileprovision ready for ``pipeline.run_sideload``.
        """
        self._login()
        team = self._pick_team()
        cert_bundle = self._get_or_create_cert(team)

        self._log(f"Registering device {device_udid}…")
        try:
            self._dev.register_device(team.team_id, device_udid, "My iPhone")
        except Exception as exc:
            logger.warning(
                "Device registration returned an error (device may already be registered): %s", exc
            )

        app_id_id = self._ensure_app_id(team, bundle_id, app_name)

        self._log("Fetching provisioning profile…")
        profile_bytes = self._dev.fetch_provisioning_profile(team.team_id, app_id_id, [device_udid])

        p12_path = _write_work(".p12", cert_bundle.p12_bytes)
        profile_path = _write_work(".mobileprovision", profile_bytes)

        return ProvisionResult(
            p12_path=p12_path,
            p12_password=cert_bundle.p12_password.decode(),
            mobileprovision_path=profile_path,
            team_id=team.team_id,
            bundle_id=bundle_id,
        )
