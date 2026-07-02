"""
Apple ID authentication via GrandSlam (GSA).

IMPORTANT HONESTY NOTE: unlike the signing backends and device layer
(which lean entirely on actively-maintained external tools/libraries),
there is currently no actively-maintained, batteries-included open-source
Python library that does the full GSA login + 2FA + developer-services
flow end to end. Every open-source sideloading tool -- Sideloadly,
AltServer, SideStore -- has had to implement this protocol themselves
against Apple's (undocumented, occasionally-changing) endpoints. This
module is that same kind of implementation: a from-scratch GSA/SRP-6a
client based on the publicly documented protocol shape (see
https://theapplewiki.com/wiki/Grand_Slam_Authentication and the (now
unmaintained) reference implementations it links).

Expect this module to need maintenance if Apple changes their auth flow.
It is intentionally isolated from the rest of the app (signing, device
comms) so a break here doesn't take down everything else: you can still
sign + install with an existing .p12/.mobileprovision even if Apple-ID
login stops working.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import plistlib
import uuid
from dataclasses import dataclass, field
from typing import Optional

import certifi
import os
import sys
import tempfile
import requests
import srp

from ..anisette.provider import AnisetteProvider
from ..errors import AppleAuthError

logger = logging.getLogger(__name__)

GSA_ENDPOINT = "https://gsa.apple.com/grandslam/GsService2"


def _build_ca_bundle() -> str:
    """
    Merge certifi's standard CA bundle with the bundled apple-root.pem so
    that requests can verify both standard TLS chains (e.g. DigiCert-rooted
    gsa.apple.com) and Apple-specific chains.  Returns a path to a temporary
    merged PEM file, or certifi's bundle alone as a fallback.
    """
    if getattr(sys, "frozen", False):
        _apple_root = os.path.join(sys._MEIPASS, "ipasideloader", "certs", "apple-root.pem")
        if not os.path.isfile(_apple_root):
            _apple_root = os.path.join(sys._MEIPASS, "certs", "apple-root.pem")
    else:
        _apple_root = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "certs", "apple-root.pem")
        )

    if not os.path.isfile(_apple_root):
        logger.warning("apple-root.pem not found, using certifi bundle only")
        return certifi.where()

    try:
        with open(certifi.where(), "rb") as f:
            certifi_data = f.read()
        with open(_apple_root, "rb") as f:
            apple_data = f.read()
        tmp = tempfile.NamedTemporaryFile(
            mode="wb", suffix=".pem", prefix="ipasideloader_ca_", delete=False
        )
        tmp.write(certifi_data)
        if not certifi_data.endswith(b"\n"):
            tmp.write(b"\n")
        tmp.write(apple_data)
        tmp.close()
        logger.debug("Merged CA bundle written to %s", tmp.name)
        return tmp.name
    except Exception as exc:
        logger.warning("Failed to build merged CA bundle (%s), using certifi", exc)
        return certifi.where()
# NOTE: we deliberately do NOT hardcode our own SRP "N"/"g" group constants
# here. The `srp` library already ships the standard RFC 5054 2048-bit
# group via `srp.NG_2048`, which is what GSA uses, so we just reference
# that named constant below instead of re-deriving or guessing at the
# modulus ourselves.


@dataclass
class AppleSession:
    """Result of a successful (post-2FA) GSA login."""
    dsid: str
    search_token: str
    raw: dict = field(default_factory=dict)


class TwoFactorRequired(AppleAuthError):
    """Raised mid-login when Apple wants a 2FA/SMS code; caller should prompt and retry."""


class AppleAccountClient:
    """
    Minimal GSA client: login with Apple ID + password, handle 2FA,
    and produce a session usable for developer-services calls.
    """

    def __init__(self, anisette: Optional[AnisetteProvider] = None):
        self.anisette = anisette or AnisetteProvider()
        self._session = requests.Session()
        self._session.verify = _build_ca_bundle()

    def _anisette_headers(self) -> dict:
        data = self.anisette.get()
        return dict(data.headers)

    def _common_headers(self) -> dict:
        headers = {
            "Content-Type": "text/x-xml-plist",
            "Accept": "*/*",
            "User-Agent": "akd/1.0 CFNetwork/978.0.7 Darwin/18.7.0",
            "X-Mme-Client-Info": "<MacBookPro15,1> <Mac OS X;10.14.6;18G103> <com.apple.AuthKit/1 (com.apple.akd/1.0)>",
            "X-Apple-I-Request-UUID": str(uuid.uuid4()),
        }
        headers.update(self._anisette_headers())
        return headers

    def _gsa_request(self, request_body: dict) -> dict:
        body = plistlib.dumps({
            "Header": {"Version": "1.0.1"},
            "Request": request_body,
        })
        resp = self._session.post(GSA_ENDPOINT, data=body, headers=self._common_headers(), timeout=20)
        resp.raise_for_status()
        parsed = plistlib.loads(resp.content)
        return parsed.get("Response", parsed)

    def login(self, apple_id: str, password: str, two_factor_code: Optional[str] = None) -> AppleSession:
        """
        Perform the SRP-6a GSA login flow. If two_factor_code is None and
        the account needs 2FA, raises TwoFactorRequired -- the caller
        (GUI/CLI) should prompt the user for the 6-digit code and call
        login() again, this time passing it.

        CAVEAT: the exact plist field names/casing below ("A2k", "ps", "sp",
        "spd", etc.) reflect the publicly documented shape of GsService2
        requests/responses. I have not verified them against a live
        capture, and Apple has changed small details before (e.g. adding
        new "ps" protocol entries, renaming status fields). If login starts
        failing, capturing real traffic from Sideloadly/AltServer/Configurator
        with a proxy (mitmproxy/Charles) against your own account is the
        reliable way to confirm current field names and update this method.
        """
        usr = srp.User(apple_id, password.encode("utf-8"), hash_alg=srp.SHA256, ng_type=srp.NG_2048)
        _, A = usr.start_authentication()

        init_resp = self._gsa_request({
            "A2k": A,
            "ps": ["s2k", "s2k_fo"],
            "u": apple_id,
            "o": "init",
        })

        if init_resp.get("Status", {}).get("ec", 0) != 0:
            raise AppleAuthError(f"GSA init failed: {init_resp.get('Status')}")

        salt = init_resp["s"]
        b = init_resp["B"]
        iterations = init_resp.get("i", 1000)
        protocol = init_resp.get("sp", "s2k")

        if protocol == "s2k":
            pw_hash = hashlib.sha256(password.encode("utf-8")).digest()
        elif protocol == "s2k_fo":
            # s2k_fo: PBKDF2-HMAC-SHA256 using the server-supplied iteration count.
            pw_hash = hashlib.pbkdf2_hmac(
                "sha256",
                hashlib.sha256(password.encode("utf-8")).digest(),
                salt,
                iterations,
            )
        else:
            pw_hash = password.encode("utf-8")
        usr.p = pw_hash  # srp library uses self.p inside process_challenge via gen_x

        M = usr.process_challenge(salt, b)
        if M is None:
            raise AppleAuthError("SRP challenge processing failed (likely wrong password).")

        challenge_resp = self._gsa_request({
            "M1": M,
            "c": init_resp.get("c"),
            "u": apple_id,
            "o": "complete",
        })

        status = challenge_resp.get("Status", {})
        ec = status.get("ec", 0)

        # -22406 is Apple's "2FA required" status code — must be handled before
        # the generic error raise, otherwise it would be raised as a plain auth failure.
        needs_2fa = "trustedDeviceTimeout" in challenge_resp or ec == -22406

        if ec != 0 and not needs_2fa:
            raise AppleAuthError(f"GSA authentication failed: {status.get('em', status)}")

        usr.verify_session(challenge_resp.get("M2"))
        if not usr.authenticated():
            raise AppleAuthError("SRP server proof verification failed.")
        if needs_2fa and not two_factor_code:
            raise TwoFactorRequired(
                "This Apple ID requires a two-factor authentication code. "
                "Request one and call login() again with two_factor_code set."
            )

        if needs_2fa and two_factor_code:
            self._submit_2fa_code(two_factor_code)

        dsid = str(challenge_resp.get("dsid", ""))
        search_token = base64.b64encode(
            challenge_resp.get("spd", {}).get("t", b"") if isinstance(challenge_resp.get("spd"), dict) else b""
        ).decode()

        if not dsid:
            raise AppleAuthError("Login appeared to succeed but no DSID was returned.")

        return AppleSession(dsid=dsid, search_token=search_token, raw=challenge_resp)

    def _submit_2fa_code(self, code: str) -> None:
        resp = self._session.get(
            "https://gsa.apple.com/auth/verify/trusteddevice/securitycode",
            headers={**self._common_headers(), "security-code": code},
            timeout=20,
        )
        if resp.status_code >= 400:
            raise AppleAuthError(f"2FA verification failed (HTTP {resp.status_code}).")
