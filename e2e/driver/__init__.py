"""e2e.driver — client + server + lint + judge per il simulatore E2E.

NO import da `runtime/`. Il simulatore parla solo via HTTP/CLI.
"""
from .http_client import E2EClient, ChatResponse
from .server import E2EServer

__all__ = ["E2EClient", "ChatResponse", "E2EServer"]
