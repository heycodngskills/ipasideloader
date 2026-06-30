import truststore
truststore.inject_into_ssl()  # replace ssl.SSLContext with OS native trust store

from ipasideloader.gui.app import main
main()
