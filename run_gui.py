try:
    import truststore
    truststore.inject_into_ssl()  # makes Python use Windows/macOS/Linux native cert store
except Exception:
    pass  # fall back to certifi if truststore unavailable

from ipasideloader.gui.app import main
main()
