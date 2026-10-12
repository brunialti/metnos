"""Explicit initial Producer policy adoption; no signing or runtime authority.

Keys are exact initial context IDs. Values bind authority set, first transition,
first signed build and adopted Producer source hash. Empty means no adoption.
The historical reader parses this table from authenticated distribution bytes.
"""
from types import MappingProxyType

INITIAL_PRODUCER_POLICY_V1 = MappingProxyType({
    "sha256:290264a3a057613006badeb399a92e12fd9bc9fc28f134db453e8ecf3b1cdcae": (
        "fac2e29592f936024a39cdfc1a2834e6ac6a3d48784511bc9834b51dd8e7fe8a",
        "sha256:b78926535cc65a43303d69a3e8236691f6a910046a6da76f4eed7a727cfe5e66",
        "sha256:0f2354b537edcc8e790bd8f53d2cf245d290e24b9dd1dc7545391b97af44b862",
        "sha256:62d266497a6d2198ab152267084668f1c6173c99e309ce3ec80b5de6335901f9",
    ),
})
