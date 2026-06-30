import os, sys

# Diagnostic: log what SSL fix loaded, visible in the GUI log
_ssl_fix = "none"
try:
    import truststore
    truststore.inject_into_ssl()
    _ssl_fix = f"truststore {truststore.__version__}"
except Exception as e:
    _ssl_fix = f"truststore FAILED: {e}"

# Store so GUI can display it on startup
os.environ["_IPASIDELOADER_SSL_FIX"] = _ssl_fix

from ipasideloader.gui.app import main
main()
