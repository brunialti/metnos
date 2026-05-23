#!/usr/bin/env python3
"""
seed_modules.py — popola la tabella modules + module_dependencies.

I moduli corrispondono ai canonici della microprogettazione (per quelli implementati
nella POC) + i 4 executor concreti. Le dipendenze definiscono il grafo da cui i
cluster sono derivati: cluster(X) = {X} U vicini diretti(X) (in entrambe le direzioni).
"""
import os as _os
import sys as _sys
from pathlib import Path as _Path

_RUNTIME = _os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in _Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file())
if _RUNTIME not in _sys.path:
    _sys.path.insert(0, _RUNTIME)

from registry import Registry
from config import PATH_ROOT as _PATH_ROOT  # noqa: E402

_RT = f"{_PATH_ROOT}/runtime"
_EX = f"{_PATH_ROOT}/executors"


def main():
    r = Registry.open()

    # --- Moduli runtime (microprogettazione canonica) ---
    runtime_modules = [
        ("agent_runtime",  "runtime", f"{_RT}/agent_runtime.py",
         "Loop pianificatore multistep ReAct con tool-use nativo, mode router, vaglio, sandbox check."),
        ("loader",         "runtime", f"{_RT}/loader.py",
         "Scopre, verifica e carica gli executor in un Catalog."),
        ("sign",           "runtime", f"{_RT}/sign.py",
         "Firma Ed25519 + verifica digest dei file di codice degli executor."),
        ("prefilter",      "runtime", f"{_RT}/prefilter.py",
         "Pre-filtro bag-of-words sui tag affinity per ridurre il catalogo a top-K."),
        ("vaglio",         "runtime", f"{_RT}/vaglio.py",
         "Valutatore costituzionale (stub always-approve in PoC)."),
        ("cost_tracker",   "runtime", f"{_RT}/cost_tracker.py",
         "Contabilita' della spesa LLM con cap mensile, JSONL append-only."),
        ("llm_provider",   "runtime", f"{_RT}/llm_provider.py",
         "Astrazione LLM mode-aware: OllamaProvider, LlamaCppProvider, AnthropicProvider, StubProvider."),
        ("llm_router",     "runtime", f"{_RT}/llm_router.py",
         "Tier resolver fast/middle/wise con quality floor wise + provider hints brevi prescrittivi."),
        ("test_runner",    "runtime", f"{_RT}/test_runner.py",
         "Esegue i test di nascita dichiarativi nei manifest degli executor."),
        ("scratchpad",     "runtime", f"{_RT}/scratchpad.py",
         "Archivio temporaneo per observation grandi (oltre soglia) con builtin executor scratchpad_read."),
        ("mnestoma",       "runtime", f"{_RT}/mnestoma.py",
         "Storage SQLite di mnest e proto-mnest; record_passing, query, walk, ager."),
        ("synt",           "runtime", f"{_RT}/synt.py",
         "Synth orchestrator MVP compose-only; cascata reattiva, BFS sul mnestoma, audit, lock."),
        ("scheduler_v2",   "runtime", f"{_RT}/scheduler_v2/daemon.py",
         "Scheduler v2 asyncio-native co-host nel server HTTP (ADR 0112). "
         "Single table schedule_entries, next_fire_at materializzato, "
         "ThreadPool offload per callback sync, in-process kick."),
        ("channels",       "runtime", f"{_RT}/channels/__init__.py",
         "Astrazione canale (Channel Protocol + InboundMessage/OutboundMessage). Telegram come prima implementazione."),
        ("pairing",        "runtime", f"{_RT}/pairing.py",
         "Riconoscimento channel+sender via codici Ed25519. Registry SQLite, bootstrap default_chat_id, /pair flow."),
        ("observability",  "runtime", f"{_RT}/observability.py",
         "Dashboard statica HTML che aggrega mnestoma, pairings, turns, vaglio, scheduler, test framework. Singolo file generato on-demand."),
        ("approval_registry", "runtime", f"{_RT}/approval_registry.py",
         "Registry SQLite per pending approval requests con TTL. Dispatcher Telegram risolve approve:<token>/reject:<token>."),
        ("sandbox", "runtime", f"{_RT}/sandbox.py",
         "Sandbox bubblewrap per gli executor. Deriva bwrap args da capabilities+hint del manifest. Fallback graceful se bwrap manca."),
        ("policy", "runtime", f"{_RT}/policy.py",
         "Capability Registry esteso (13 voci) + tabella autonomy x capability + grants per_target persistenti SQLite + effective_outcome combinato."),
    ]
    for name, kind, path, desc in runtime_modules:
        r.add_module(name, kind, path, desc)

    # --- Executor concreti ---
    executor_modules = [
        ("read_files",   f"{_EX}/read_files/read_files.py",
         "Legge file dal filesystem locale. capability=fs:read"),
        ("write_files",  f"{_EX}/write_files/write_files.py",
         "Scrive file sul filesystem locale. capability=fs:write critical"),
        ("get_now", f"{_EX}/get_now/get_now.py",
         "Restituisce ora corrente in fuso IANA. capability=time:read"),
        ("get_urls", f"{_EX}/get_urls/get_urls.py",
         "Esegue HTTP GET/HEAD verso host. capability=network:http"),
    ]
    for name, path, desc in executor_modules:
        r.add_module(name, "executor", path, desc)

    # --- Dipendenze (grafo) ---
    # agent_runtime usa: loader, prefilter, llm_provider, vaglio, cost_tracker, test_runner (per check_hints)
    deps = [
        ("agent_runtime", "loader"),
        ("agent_runtime", "prefilter"),
        ("agent_runtime", "llm_provider"),
        ("agent_runtime", "vaglio"),
        ("agent_runtime", "cost_tracker"),
        ("agent_runtime", "test_runner"),  # per check_hints / pseudo-sandbox
        ("agent_runtime", "scratchpad"),   # offload obs grandi + builtin scratchpad_read
        ("agent_runtime", "mnestoma"),     # registra mnest e proto-mnest per turn
        ("synt", "mnestoma"),               # synt legge il grafo per la BFS
        ("synt", "llm_router"),             # generate stadi 2+3+5 via wise tier
        ("synt", "sign"),                   # approve_proposal chiama sign_executor
        ("llm_router", "llm_provider"),     # router compone provider concreti
        ("scheduler_v2", "mnestoma"),       # scheduler_v2 callback apply_ager
        ("scheduler_v2", "synt"),           # scheduler_v2 callback synt_suggest
        ("pairing", "sign"),                # pairing usa Ed25519 (load_private/list_trusted_publics)
        ("channels", "pairing"),            # daemon channel valida sender via pairing
        ("observability", "mnestoma"),      # dashboard legge stats/top/audit dal mnestoma
        ("observability", "pairing"),       # dashboard mostra pairings attivi
        ("observability", "scheduler_v2"),  # dashboard mostra scheduler_v2 tasks
        ("channels", "approval_registry"),  # daemon dispatcher risolve callback approve/reject
        ("agent_runtime", "sandbox"),       # invoke_executor wrappa via sandbox.wrap_command
        # loader usa sign per verificare
        ("loader", "sign"),
        # ognuno degli executor è invocato dall'agent_runtime
        ("agent_runtime", "read_files"),
        ("agent_runtime", "write_files"),
        ("agent_runtime", "get_now"),
        ("agent_runtime", "get_urls"),
        # gli executor sono firmati da sign
        ("read_files",   "sign"),
        ("write_files",  "sign"),
        ("get_now", "sign"),
        ("get_urls", "sign"),
        # gli executor sono caricati da loader
        ("read_files",   "loader"),
        ("write_files",  "loader"),
        ("get_now", "loader"),
        ("get_urls", "loader"),
        # test_runner valida ogni executor (test di nascita)
        ("read_files",   "test_runner"),
        ("write_files",  "test_runner"),
        ("get_now", "test_runner"),
        ("get_urls", "test_runner"),
    ]
    for a, b in deps:
        r.add_dependency(a, b)

    print("--- Moduli ---")
    for m in r.list_modules():
        print(f"  {m['kind']:8s}  {m['name']:14s}  {m['source_path']}")

    print("\n--- Cluster esempi ---")
    for who in ("agent_runtime", "read_files", "sign", "vaglio"):
        c = sorted(r.cluster_of(who))
        print(f"  cluster({who}): {', '.join(c)}")

    print(f"\n--- Summary ---\n{r.summary()}")


if __name__ == "__main__":
    main()
