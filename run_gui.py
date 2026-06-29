import os
import certifi

# When bundled with PyInstaller on Windows, Python doesn't use the OS cert store.
# Force requests (and the ssl module) to use the certifi CA bundle.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

from ipasideloader.gui.app import main
main()
