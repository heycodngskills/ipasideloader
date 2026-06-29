"""
Apple developer-services client: App ID registration + provisioning
profile issuance for a free (non-paid) developer account.

Same honesty caveat as apple/auth.py applies: these are Apple's internal
developerservices2 endpoints (used by Xcode itself), not a public/
documented API. The request shape here matches what Xcode and tools like
AltServer send, but field names can shift between Apple's backend
revisions.
"""
from __future__ import annotations

import base64
import logging
import plistlib
import uuid
from dataclasses import dataclass
from typing import Optional

import requests

from .auth import AppleSession
from ..config import FREE_TEAM_APP_ID_LIMIT
from ..errors import ProvisioningError

logger = logging.getLogger(__name__)

DEV_SERVICES_BASE = "https://developerservices2.apple.com/services/QH65B2/ios"


@dataclass
class DeveloperTeam:
    team_id: str
    name: str


@dataclass
class AppIdRegistration:
    app_id_name: str
    bundle_id: str
    raw: dict


class DeveloperServicesClient:
    """
    Talks to Apple's developer-services backend on behalf of a logged-in
    AppleSession to manage App IDs and provisioning profiles, the same
    operations Xcode performs automatically for free accounts.
    """

    def __init__(self, session: AppleSession, anisette_headers: Optional[dict] = None):
        self.apple_session = session
        self.anisette_headers = anisette_headers or {}
        self._http = requests.Session()

    def _base_params(self) -> dict:
        return {
            "clientId": "XABBG36SBA",
            "protocolVersion": "QH65B2",
            "requestId": str(uuid.uuid4()).upper(),
            "userLocale": ["en_US"],
            "DTDK_Platform": "ios",
        }

    def _post(self, path: str, extra_params: dict) -> dict:
        params = self._base_params()
        params.update(extra_params)
        body = plistlib.dumps(params)
        # Apple's developer-services backend authenticates via X-Apple-DS-ID and
        # X-Apple-Identity-Token headers (dsid + search_token from the GSA session),
        # NOT a myacinfo cookie (that key doesn't exist in the GSA response dict).
        headers = {
            "Content-Type": "text/x-xml-plist",
            "X-Apple-DS-ID": self.apple_session.dsid,
            "X-Apple-Identity-Token": self.apple_session.search_token,
            **self.anisette_headers,
        }
        resp = self._http.post(
            f"{DEV_SERVICES_BASE}/{path}",
            data=body,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        parsed = plistlib.loads(resp.content)
        result_code = parsed.get("resultCode", 0)
        if result_code != 0:
            raise ProvisioningError(
                f"Apple developer-services call '{path}' failed "
                f"(resultCode={result_code}): {parsed.get('userString') or parsed.get('resultString')}"
            )
        return parsed

    def list_teams(self) -> list[DeveloperTeam]:
        resp = self._post("listTeams.action", {})
        teams = []
        for t in resp.get("teams", []):
            teams.append(DeveloperTeam(team_id=t.get("teamId", ""), name=t.get("name", "")))
        return teams

    def list_app_ids(self, team_id: str) -> list[dict]:
        resp = self._post("listAppIds.action", {"teamId": team_id})
        return resp.get("appIds", [])

    def register_app_id(self, team_id: str, bundle_id: str, app_name: str) -> AppIdRegistration:
        """
        Register a new App ID for a free developer team. Free teams are
        limited to a small number of concurrently-registered App IDs
        (Apple currently caps this around FREE_TEAM_APP_ID_LIMIT); if you
        hit the cap, an existing App ID needs to be removed/reused before
        registering another.
        """
        existing = self.list_app_ids(team_id)
        if len(existing) >= FREE_TEAM_APP_ID_LIMIT:
            raise ProvisioningError(
                f"Free developer team already has {len(existing)} registered App IDs "
                f"(Apple's free-account limit is around {FREE_TEAM_APP_ID_LIMIT}). "
                "Remove an unused one in your Apple Developer account before adding another."
            )

        resp = self._post(
            "addAppId.action",
            {
                "teamId": team_id,
                "identifier": bundle_id,
                "name": app_name,
            },
        )
        app_id_info = resp.get("appId", {})
        return AppIdRegistration(app_id_name=app_name, bundle_id=bundle_id, raw=app_id_info)

    def fetch_provisioning_profile(self, team_id: str, app_id: str, device_udids: list[str]) -> bytes:
        """
        Request a development provisioning profile covering the given App
        ID and device UDIDs. Returns the raw .mobileprovision bytes.
        """
        resp = self._post(
            "downloadTeamProvisioningProfile.action",
            {
                "teamId": team_id,
                "appIdId": app_id,
                "deviceIds": device_udids,
            },
        )
        profile = resp.get("provisioningProfile", {})
        encoded_profile = profile.get("encodedProfile")
        if not encoded_profile:
            raise ProvisioningError("Apple did not return a provisioning profile payload.")
        return bytes(encoded_profile)

    def register_device(self, team_id: str, udid: str, device_name: str) -> None:
        """Register a device's UDID with the team so profiles can target it."""
        self._post(
            "addDevice.action",
            {
                "teamId": team_id,
                "deviceNumber": udid,
                "name": device_name,
            },
        )

    def list_certificates(self, team_id: str) -> list[dict]:
        """
        List active development certificates for the team.

        NOTE: The exact endpoint name and response fields are based on what
        AltServer/SideStore-style tooling has observed from Xcode traffic.
        If this call fails, the free_provision flow will fall back to
        generating a fresh certificate rather than crashing.
        """
        try:
            resp = self._post("listAllDevelopmentCerts.action", {"teamId": team_id})
            return resp.get("certRequests", [])
        except ProvisioningError:
            return []

    def submit_csr(self, team_id: str, csr_der: bytes) -> tuple[bytes, str]:
        """
        Submit a Certificate Signing Request to Apple and return the
        issued DER-encoded certificate + its certRequestId.

        The CSR is sent as base64 in the ``csrContent`` field — the same
        way Xcode submits it via ``submitDevelopmentCSR.action``.

        NOTE: Apple's response format for this endpoint has varied slightly
        across Xcode versions. If the cert isn't in the immediate response
        we fall back to downloading it via ``downloadDevelopmentCert.action``.
        If you see ProvisioningError here, capture the live traffic with
        mitmproxy against a real Xcode signing to confirm current field names.
        """
        resp = self._post(
            "submitDevelopmentCSR.action",
            {
                "teamId": team_id,
                "csrContent": base64.b64encode(csr_der).decode(),
            },
        )

        cert_req = resp.get("certRequest", {})
        cert_id = str(cert_req.get("certRequestId", ""))

        # Apple may return the cert immediately or require a separate download.
        cert_data = cert_req.get("certRequestDerEncoded") or cert_req.get("certificate")

        if cert_data is None and cert_id:
            # Try the download endpoint.
            dl = self._post("downloadDevelopmentCert.action", {
                "teamId": team_id,
                "certRequestId": cert_id,
            })
            cert_req2 = dl.get("certRequest", {})
            cert_data = cert_req2.get("certRequestDerEncoded") or cert_req2.get("certificate")

        if cert_data is None:
            raise ProvisioningError(
                "Apple issued a certificate request but did not return certificate data. "
                f"certRequestId={cert_id!r}. "
                "This may be a field-name mismatch — inspect the raw plist response."
            )

        if isinstance(cert_data, str):
            cert_data = base64.b64decode(cert_data)

        return bytes(cert_data), cert_id
