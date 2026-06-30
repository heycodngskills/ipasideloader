"""
RSA key generation, CSR creation, and P12 packaging for the free Apple ID signing flow.

The generated private key + Apple-issued certificate are cached as a P12 in
CREDS_DIR so we don't re-generate and re-register on every run.  Apple limits
free accounts to two active development certificates at once, so reusing the
cached one matters.
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key, pkcs12
from cryptography.x509.oid import NameOID

from ..config import CREDS_DIR

logger = logging.getLogger(__name__)

CERT_CACHE_DIR = CREDS_DIR / "certs"


@dataclass
class CertBundle:
    """A signing certificate + private key pair, ready for use as a .p12."""
    p12_bytes: bytes
    p12_password: bytes
    cert_id: str       # Apple's certRequestId — needed for revocation / cache invalidation
    apple_id: str


# ── persistence ──────────────────────────────────────────────────────────────


def _cache_path(apple_id: str, team_id: str) -> Path:
    CERT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = apple_id.replace("@", "_at_").replace(".", "_")
    return CERT_CACHE_DIR / f"{safe}__{team_id}.json"


def save_cert_bundle(bundle: CertBundle, team_id: str) -> None:
    path = _cache_path(bundle.apple_id, team_id)
    # p12_password is the hardcoded internal constant, not the user's Apple ID
    # password. We don't need to persist it — reconstruct on load.
    path.write_text(json.dumps({
        "p12_b64": base64.b64encode(bundle.p12_bytes).decode(),
        "cert_id": bundle.cert_id,
        "apple_id": bundle.apple_id,
    }))
    logger.debug("Cert bundle saved to %s", path)


def load_cert_bundle(apple_id: str, team_id: str) -> Optional[CertBundle]:
    path = _cache_path(apple_id, team_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return CertBundle(
            p12_bytes=base64.b64decode(data["p12_b64"]),
            p12_password=b"ipasideloader",  # hardcoded constant, never user data
            cert_id=data["cert_id"],
            apple_id=data["apple_id"],
        )
    except Exception as exc:
        logger.warning("Failed to load cached cert bundle: %s", exc)
        return None


def clear_cert_bundle(apple_id: str, team_id: str) -> None:
    path = _cache_path(apple_id, team_id)
    if path.exists():
        path.unlink()


# ── crypto helpers ────────────────────────────────────────────────────────────


def generate_key_and_csr(apple_id: str) -> tuple[bytes, bytes]:
    """
    Generate an RSA-2048 private key and a CSR suitable for Apple's
    ``submitDevelopmentCSR.action`` endpoint.

    Returns
    -------
    private_key_pem : bytes
    csr_der         : bytes  — base64-encode this before sending to Apple
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "iPhone Developer"),
            x509.NameAttribute(NameOID.EMAIL_ADDRESS, apple_id),
        ]))
        .sign(private_key, hashes.SHA256())
    )
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    csr_der = csr.public_bytes(serialization.Encoding.DER)
    return private_key_pem, csr_der


def build_p12(private_key_pem: bytes, cert_der: bytes, password: bytes = b"ipasideloader") -> bytes:
    """
    Combine the private key we generated with the DER certificate Apple
    returned into a .p12 blob that zsign / codesign can consume.
    """
    private_key = load_pem_private_key(private_key_pem, password=None)
    certificate = x509.load_der_x509_certificate(cert_der)
    return pkcs12.serialize_key_and_certificates(
        name=b"iPhone Developer",
        key=private_key,
        cert=certificate,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password),
    )
