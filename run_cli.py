try:
    import pip_system_certs.wrapt_requests  # patches requests to use Windows cert store
except ImportError:
    pass

from ipasideloader.cli import main
import sys
sys.exit(main())
