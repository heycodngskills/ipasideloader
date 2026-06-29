"""
ipasideloader: a cross-platform, open-source IPA sideloader.

Signing is delegated to zsign / ldid / macOS codesign (subprocess calls --
no hand-rolled CMS/CodeDirectory signing). Device communication and
on-device install/provisioning-profile management is delegated to
pymobiledevice3. Apple ID login (free developer account flow) is a
from-scratch GSA client, since no actively-maintained library exists for
that piece -- see apple/auth.py for details and caveats.
"""
__version__ = "0.1.0"
