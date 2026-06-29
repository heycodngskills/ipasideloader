"""
PyInstaller runtime hook — fixes SSL certificate verification in frozen Windows exe.

When PyInstaller bundles the app, Python can't find the system CA certs on Windows.
This hook runs before any app code and points the ssl module at certifi's bundled
cacert.pem, which PyInstaller includes automatically via hook-certifi.py.

Reference: https://github.com/pyinstaller/pyinstaller/wiki/Recipe-OpenSSL-Certificate
"""
import os
import sys

if getattr(sys, "frozen", False):
    # We're running inside a PyInstaller bundle.
    # certifi's cacert.pem is extracted to sys._MEIPASS/certifi/cacert.pem
    # by PyInstaller's standard hook-certifi.py hook.
    _certifi_pem = os.path.join(sys._MEIPASS, "certifi", "cacert.pem")
    if os.path.isfile(_certifi_pem):
        os.environ["SSL_CERT_FILE"] = _certifi_pem
        os.environ["REQUESTS_CA_BUNDLE"] = _certifi_pem
