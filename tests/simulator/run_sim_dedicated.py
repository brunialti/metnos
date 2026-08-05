"""run_sim_dedicated.py — run simulator using dedicated Qwen3 parser."""
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

# Monkey-patch parse_query to use dedicated
import query_parser as _qp
from parser_dedicated import parse_query_dedicated

_qp.parse_query = lambda q, **kw: parse_query_dedicated(q, use_cache=kw.get("use_cache", True))

from run_simulation_v2 import main

if __name__ == "__main__":
    main()
