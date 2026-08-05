"""Repository-wide pytest bootstrap.

The runtime sandbox originally lived only below ``tests/runtime``.  A command
from the repository root therefore collected E2E tests first and never loaded
that bootstrap: tests could read or write the live installation.  Register it
as a plugin before collection so every supported suite runs in the same
ephemeral, credential-free test installation.
"""

pytest_plugins = ("tests.runtime.conftest",)
