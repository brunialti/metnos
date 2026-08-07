"""Does the planner DECLARE the scope of the goal?

The live measure of the `ambito` field has two halves. The second one — does
the pilot then open the personal area — needs the site and, on Booking, a
two-factor code. The first one does not: it only asks whether the planner,
reading the signed manifest, produces the field at all. If it does not, the
fix lives in the manifest description, not in the code.

Two queries, on purpose: one WITHOUT any possessive (the case the whole field
exists for) and one with it (which used to work through the heuristic).
"""
import sys

sys.path.insert(0, "/opt/metnos/runtime")

from engine.proposer import get_proposer          # noqa: E402
from engine.types import Intent                   # noqa: E402
from llm_router import LLMRouter                  # noqa: E402
from llm_workloads import tier_for                # noqa: E402
from loader import load_catalog                   # noqa: E402

CASI = [
    "vai su booking.com e mostrami le prenotazioni",
    "vai su booking.com e mostrami le mie prenotazioni",
]


def _llm(sys_msg, user_msg, *, max_tokens=1200, **kw):
    ck = {"max_tokens": max_tokens, "request_timeout_s": 180}
    if kw.get("grammar") is not None:
        ck["grammar"] = kw["grammar"]
    res = LLMRouter().provider(tier_for("planner.grammar")).chat(
        sys_msg, user_msg, **ck)
    return (getattr(res, "text", res) or "").strip()


catalogo = list(load_catalog())
pool = [e.name for e in catalogo
        if e.name in ("open_sites", "login_sites", "act_sites", "read_sites",
                      "extract_entries", "describe_entries", "final_answer")]
proposer = get_proposer()

for query in CASI:
    piano = proposer.propose(
        query=query,
        intent=Intent(verb="open", object="sites",
                      actions=[{"verb": "open", "object": "sites"},
                               {"verb": "read", "object": "sites"}]),
        pool=pool, excluded_hashes=set(), llm_call=_llm,
        catalog=catalogo)
    print("=" * 70)
    print(query)
    if piano is None:
        print("   nessun piano")
        continue
    for step in piano.steps:
        args = {k: v for k, v in (step.args or {}).items()
                if not k.startswith("_")}
        print(f"   {step.tool:16} {args}")
    atti = [s for s in piano.steps if s.tool == "act_sites"]
    dichiarato = [str((s.args or {}).get("ambito") or "") for s in atti]
    print("   -> ambito dichiarato:", dichiarato or "nessun act_sites")
