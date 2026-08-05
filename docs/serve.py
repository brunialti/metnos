#!/usr/bin/env python3
"""Serve Metnos documentation on port 8810 (LAN-wide).

Convenzione porte:
- suprastructure docs → 8800
- Metnos documentation → 8810
"""
import http.server
import os

PORT = 8810
DIR = os.path.dirname(os.path.abspath(__file__))

os.chdir(DIR)
print(f"Metnos docs: http://0.0.0.0:{PORT}/  (serving {DIR})")
http.server.HTTPServer(
    ("0.0.0.0", PORT),
    http.server.SimpleHTTPRequestHandler,
).serve_forever()
