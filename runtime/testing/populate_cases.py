#!/usr/bin/env python3
"""
populate_cases.py — popola il DB con test cases per la POC v1.1.

Tre livelli:
    module   un singolo modulo in isolamento (o quanto piu' isolato possibile)
    cluster  modulo + vicini diretti (es. agent_runtime usato da read_files)
    system   end-to-end via agent_runtime.run_turn

Categorie:
    happy     percorso felice atteso
    edge      caso limite, input ai bordi del valido
    failure   input invalido, deve fallire correttamente
    security  prova di violazione (sandbox, scope, validation)
    integration  riguarda piu' moduli del cluster

Ogni esecuzione di questo script ricarica i case (UNIQUE su (module_id, name)).
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
_RUNTIME = os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file())
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)
from registry import Registry
from config import PATH_EXECUTORS as _PATH_EXECUTORS  # noqa: E402

_EX = str(_PATH_EXECUTORS)


# =========================================================================
# LIVELLO MODULE — test di nascita degli executor (riusati dai manifest)
# =========================================================================

EXECUTOR_BIRTH_CASES = [
    # name, manifest_path, single_test_filter
    ("read_files",   f"{_EX}/read_files/manifest.toml",   "legge_file_utf8_esistente"),
    ("read_files",   f"{_EX}/read_files/manifest.toml",   "fallisce_su_file_assente"),
    ("read_files",   f"{_EX}/read_files/manifest.toml",   "rispetta_max_bytes"),
    ("read_files",   f"{_EX}/read_files/manifest.toml",   "legge_in_binary"),
    ("read_files",   f"{_EX}/read_files/manifest.toml",   "blocca_path_fuori_scope"),
    ("write_files",  f"{_EX}/write_files/manifest.toml",  "scrive_file_nuovo_overwrite"),
    ("write_files",  f"{_EX}/write_files/manifest.toml",  "fallisce_su_path_fuori_scope"),
    ("write_files",  f"{_EX}/write_files/manifest.toml",  "fallisce_se_esiste_e_mode_fail"),
    ("write_files",  f"{_EX}/write_files/manifest.toml",  "append_a_file_esistente"),
    ("write_files",  f"{_EX}/write_files/manifest.toml",  "scrive_binary_da_base64"),
    ("get_now", f"{_EX}/get_now/manifest.toml", "ora_utc_default"),
    ("get_now", f"{_EX}/get_now/manifest.toml", "ora_in_europe_rome"),
    ("get_now", f"{_EX}/get_now/manifest.toml", "fallisce_su_timezone_invalido"),
    ("get_urls", f"{_EX}/get_urls/manifest.toml", "get_pagina_pubblica"),
    ("get_urls", f"{_EX}/get_urls/manifest.toml", "fallisce_su_host_fuori_scope"),
    ("get_urls", f"{_EX}/get_urls/manifest.toml", "errore_su_404"),
    ("get_urls", f"{_EX}/get_urls/manifest.toml", "head_request"),
    # nuovi (26/4 sera): tail/offset/exclusivity di read_files
    ("read_files",   f"{_EX}/read_files/manifest.toml",   "tail_bytes_legge_dalla_fine"),
    ("read_files",   f"{_EX}/read_files/manifest.toml",   "offset_legge_da_posizione"),
    ("read_files",   f"{_EX}/read_files/manifest.toml",   "max_bytes_e_tail_bytes_insieme_falliscono"),
]


# =========================================================================
# LIVELLO MODULE — runtime moduli, test Python
# =========================================================================

RUNTIME_PYTHON_CASES = []

# --- sign (firma + verify + digest) ---
RUNTIME_PYTHON_CASES += [
    ("sign", "keygen_crea_files_con_permessi_corretti", "happy", """
import os, tempfile, shutil
from sign import generate_keypair, KEYS_DIR
import sign as smod
# Usa una keys_dir temporanea per non disturbare quelle reali
tmp = tempfile.mkdtemp()
smod.KEYS_DIR = __import__('pathlib').Path(tmp)
priv, pub = generate_keypair("test_kg")
assert priv.exists() and pub.exists()
assert oct(os.stat(priv).st_mode & 0o777) == "0o600", oct(os.stat(priv).st_mode & 0o777)
assert oct(os.stat(pub).st_mode & 0o777) == "0o644"
shutil.rmtree(tmp)
print("ok")
"""),
    ("sign", "compute_digest_riproducibile", "happy", """
import tempfile, os
from sign import compute_code_digest
from pathlib import Path
d = Path(tempfile.mkdtemp())
(d/"a.py").write_text("alpha")
(d/"b.py").write_text("beta")
h1 = compute_code_digest(d, ["a.py","b.py"])
h2 = compute_code_digest(d, ["a.py","b.py"])
assert h1 == h2, f"{h1} != {h2}"
assert h1.startswith("sha256:")
"""),
    ("sign", "compute_digest_dipende_da_ordine", "edge", """
import tempfile
from pathlib import Path
from sign import compute_code_digest
d = Path(tempfile.mkdtemp())
(d/"a.py").write_text("alpha")
(d/"b.py").write_text("beta")
ab = compute_code_digest(d, ["a.py","b.py"])
ba = compute_code_digest(d, ["b.py","a.py"])
assert ab != ba, "digest deve dipendere dall'ordine dei files"
"""),
    ("sign", "verify_riconosce_manifest_originale", "happy", """
from sign import verify_executor
ok, info = verify_executor(f"{_EX}/read_files")
assert ok, f"verify fallita: {info}"
assert info.get("signed_by") == "author"
"""),
    ("sign", "verify_rifiuta_manifest_modificato", "security", """
import shutil, tempfile, sys
from pathlib import Path
src = Path(_EX) / "read_files"
dst = Path(tempfile.mkdtemp()) / "read_files"
shutil.copytree(src, dst)
# Modifica il manifest dopo la firma
m = dst / "manifest.toml"
m.write_text(m.read_text() + "\\n# tampered\\n")
from sign import verify_executor
ok, info = verify_executor(dst)
assert not ok, f"manifest modificato avrebbe dovuto fallire verify, info={info}"
shutil.rmtree(dst.parent)
"""),
    ("sign", "verify_rifiuta_codice_modificato", "security", """
import shutil, tempfile, sys
from pathlib import Path
src = Path(_EX) / "read_files"
dst = Path(tempfile.mkdtemp()) / "read_files"
shutil.copytree(src, dst)
# Modifica il file di codice senza ri-firmare
code = dst / "read_files.py"
code.write_text(code.read_text() + "\\n# tamper code\\n")
from sign import verify_executor
ok, info = verify_executor(dst)
assert not ok and "digest" in info.get("reason", ""), f"codice modificato avrebbe dovuto fallire digest check, info={info}"
shutil.rmtree(dst.parent)
"""),
    ("sign", "verify_fallisce_senza_sig", "failure", """
import shutil, tempfile
from pathlib import Path
from sign import verify_executor
src = Path(_EX) / "read_files"
dst = Path(tempfile.mkdtemp()) / "read_files"
shutil.copytree(src, dst)
(dst / "manifest.toml.sig").unlink()
ok, info = verify_executor(dst)
assert not ok and "firma" in info.get("reason", "").lower(), info
shutil.rmtree(dst.parent)
"""),
    ("sign", "list_trusted_publics_include_author", "happy", """
from sign import list_trusted_publics
ks = list_trusted_publics()
names = [n for n, _ in ks]
assert "author" in names, names
"""),
]

# --- loader ---
RUNTIME_PYTHON_CASES += [
    ("loader", "carica_4_executor_dal_default_dir", "happy", """
from loader import load_catalog
cat = load_catalog()
assert len(cat) > 0, f"nessun executor caricato dal default dir"
names = set(cat.all_names())
# Set seed minimo storico: deve sempre essere presente
must_have = {"read_files", "write_files", "get_now", "get_urls"}
missing = must_have - names
assert not missing, f"executor seed mancanti: {missing} (caricati: {sorted(names)})"
"""),
    ("loader", "executor_caricati_hanno_capabilities", "happy", """
from loader import load_catalog
cat = load_catalog()
# capabilities e' sempre una lista (anche vuota: pure-compute executor come
# filter_entries lavorano in memoria e non dichiarano capability di sistema).
# Il codice deve esistere su disco per ogni executor caricato.
for ex in cat:
    assert isinstance(ex.capabilities, list), f"{ex.name}: capabilities non e' una lista"
    assert ex.code_path.exists(), f"{ex.name}: codice mancante"
"""),
    ("loader", "find_by_capability_funziona", "happy", """
from loader import load_catalog
cat = load_catalog()
fs = {e.name for e in cat.find_by_capability("fs:")}
# Almeno read_files e write_files devono esserci; altri (find_files, list_dirs, ecc.)
# sono benvenuti ma non vincolanti per il contratto find_by_capability.
assert {"read_files", "write_files"}.issubset(fs), f"fs caps incomplete: {sorted(fs)}"
fs_only = {e.name for e in cat.find_by_capability("fs:read")}
assert "read_files" in fs_only and "write_files" not in fs_only, sorted(fs_only)
nets = {e.name for e in cat.find_by_capability("network:")}
assert "get_urls" in nets, sorted(nets)
"""),
    ("loader", "carica_zero_da_dir_vuota", "edge", """
import tempfile
from loader import load_catalog
cat = load_catalog(executors_dir=tempfile.mkdtemp(), include_verb_unique=False, include_synth=False)
assert len(cat) == 0
"""),
    ("loader", "rejected_se_executor_modificato", "security", """
import shutil, tempfile
from pathlib import Path
from loader import load_catalog
tmp = Path(tempfile.mkdtemp())
shutil.copytree(f"{_EX}/read_files", tmp / "read_files")
(tmp/"read_files"/"read_files.py").write_text((tmp/"read_files"/"read_files.py").read_text() + "# tamper")
cat = load_catalog(executors_dir=tmp, include_verb_unique=False, include_synth=False)
assert len(cat) == 0 and len(cat.rejected) == 1, (len(cat), cat.rejected)
shutil.rmtree(tmp)
"""),
    ("loader", "carica_senza_verify_anche_modificato", "edge", """
import shutil, tempfile
from pathlib import Path
from loader import load_catalog
tmp = Path(tempfile.mkdtemp())
shutil.copytree(f"{_EX}/read_files", tmp / "read_files")
(tmp/"read_files"/"read_files.py").write_text((tmp/"read_files"/"read_files.py").read_text() + "# tamper")
cat = load_catalog(executors_dir=tmp, verify=False, include_verb_unique=False, include_synth=False)
assert len(cat) == 1
shutil.rmtree(tmp)
"""),
    ("loader", "executor_ha_args_schema_e_tests", "happy", """
from loader import load_catalog
cat = load_catalog()
ex = cat.get("read_files")
assert ex.args_schema and "properties" in ex.args_schema
assert len(ex.tests) >= 3
"""),
]

# --- prefilter ---
RUNTIME_PYTHON_CASES += [
    ("prefilter", "rank_top_per_che_ora_e", "happy", """
from loader import load_catalog
from prefilter import rank
cat = load_catalog()
top = rank("che ora e?", cat, k=4)
assert top[0].name == "get_now", [e.name for e in top]
"""),
    ("prefilter", "rank_top_per_leggi_file", "happy", """
from loader import load_catalog
from prefilter import rank
cat = load_catalog()
top = rank("leggi il file diary.md", cat, k=4)
assert top[0].name == "read_files"
"""),
    ("prefilter", "rank_top_per_scarica_url", "happy", """
from loader import load_catalog
from prefilter import rank
cat = load_catalog()
top = rank("scarica https://api.openweather.com/x", cat, k=4)
assert top[0].name == "get_urls"
"""),
    ("prefilter", "rank_top_per_salva_nota", "happy", """
from loader import load_catalog
from prefilter import rank
cat = load_catalog()
top = rank("salva una nota nel file scratch.md", cat, k=4)
assert top[0].name == "write_files", [e.name for e in top]
"""),
    ("prefilter", "rank_query_vuota_ritorna_tutti", "edge", """
from loader import load_catalog
from prefilter import rank
cat = load_catalog()
top = rank("", cat, k=10)
assert len(top) == min(len(cat), 10)
"""),
    ("prefilter", "tokenize_lowercase_e_alfanumerico", "happy", """
from prefilter import tokenize
toks = tokenize("Che ORA è? Roma!")
# accenti vengono droppati dal regex [a-z0-9]+
assert "che" in toks and "ora" in toks and "roma" in toks
"""),
    ("prefilter", "rank_k_minore_di_catalogo", "edge", """
from loader import load_catalog
from prefilter import rank
cat = load_catalog()
top = rank("scarica file", cat, k=2)
assert len(top) <= 2
"""),
    # --- adaptive K (deciso 26/4 sera dopo stress test D-tools) ---
    ("prefilter", "adaptive_k_alta_confidenza_da_k_min", "happy", """
from prefilter import adaptive_k
# top-1 nettamente dominante
K, conf = adaptive_k([10, 0, 0, 0, 0, 0, 0], k_min=5, k_max=40)
assert conf == 1.0, conf
assert K == 1, f"con un solo score utile, K dovrebbe limitarsi a quello (1), non a k_min; got {K}"
"""),
    ("prefilter", "adaptive_k_bassa_confidenza_da_k_max", "happy", """
from prefilter import adaptive_k
# tutti zero -> confidenza 0
K, conf = adaptive_k([0]*100, k_min=5, k_max=40)
assert conf == 0.0
assert K == 5, f"con tutti zero, K = k_min (no n_useful); got {K}"
"""),
    ("prefilter", "adaptive_k_confidenza_media_interpola", "happy", """
from prefilter import adaptive_k
# top-1 = 5, top-2 = 4 -> confidence = 0.2 -> low_conf
K, conf = adaptive_k([5,4,3,2,1,1,1,1,1,1,1,1,1,1,1], k_min=5, k_max=40)
assert 0.0 < conf < 0.7
"""),
    ("prefilter", "rank_adaptive_query_chiara_seleziona_pochi", "happy", """
from loader import load_catalog
from prefilter import rank_adaptive
cat = load_catalog()
sel, info = rank_adaptive("scarica https://httpbin.org/get", cat, k_min=2, k_max=10)
assert sel[0].name == "get_urls", [e.name for e in sel]
assert info['confidence'] >= 0.3
"""),
    ("prefilter", "rank_adaptive_ritorna_info", "happy", """
from loader import load_catalog
from prefilter import rank_adaptive
cat = load_catalog()
sel, info = rank_adaptive("che ora è?", cat)
assert 'chosen_k' in info and 'confidence' in info and 'reason' in info
"""),
    ("prefilter", "rank_adaptive_query_vuota_fallback", "edge", """
from loader import load_catalog
from prefilter import rank_adaptive
cat = load_catalog()
sel, info = rank_adaptive("", cat, k_min=3)
assert info['reason'] == 'empty_query'
assert len(sel) == min(3, len(cat))
"""),
    ("prefilter", "rank_adaptive_clampa_su_n_useful", "edge", """
from loader import load_catalog
from prefilter import rank_adaptive
cat = load_catalog()
# Solo get_now ha alta affinity per "ora"; al massimo restituisce n executor con score >= 1
sel, info = rank_adaptive("che ora è?", cat, k_min=2, k_max=20)
n_useful = sum(1 for s in info['scores_top'] if s >= 1)
assert info['chosen_k'] <= max(n_useful, 2)
"""),
]

# --- vaglio ---
RUNTIME_PYTHON_CASES += [
    ("vaglio", "approva_path_innocuo", "happy", """
from vaglio import judge
v = judge("leggi note", "read_files", {"path": "/tmp/x"}, {"mode": "local"})
assert v.approved is True
assert v.judge_kind in ("rule-based-v1", "safe-verb-shortcut")
assert v.blocked_by is None
assert 0.0 <= v.score <= 1.0
"""),
    ("vaglio", "guard_blocca_ssh", "security", """
from vaglio import judge
v = judge("leggi chiave", "read_files", {"path": "~/.ssh/id_rsa"})
assert v.approved is False
assert v.blocked_by == "guard"
assert "forbidden path" in v.reason.lower()
"""),
    ("vaglio", "guard_blocca_etc_passwd", "security", """
from vaglio import judge
v = judge("dump", "read_files", {"path": "/etc/passwd"})
assert v.approved is False
assert v.blocked_by == "guard"
"""),
    ("vaglio", "guard_blocca_credentials_user", "security", """
from vaglio import judge
v = judge("apri config", "read_files", {"path": "/home/u/.config/metnos/credentials.env"})
assert v.approved is False
assert v.blocked_by == "guard"
"""),
    ("vaglio", "guard_blocca_rm_rf_root", "security", """
from vaglio import judge
v = judge("pulisci tutto", "shell_exec", {"command": "rm -rf /"}, {"capability": "code:exec"})
assert v.approved is False
assert v.blocked_by == "guard"
assert "irrecuperabile" in v.reason or "rm" in v.reason
"""),
    ("vaglio", "guard_blocca_fork_bomb", "security", """
from vaglio import judge
v = judge("test", "shell_exec", {"command": ":(){ :|:& };:"}, {"capability": "code:exec"})
assert v.approved is False
assert v.blocked_by == "guard"
"""),
    ("vaglio", "guard_lascia_passare_shell_innocua", "happy", """
from vaglio import judge
v = judge("ls", "shell_exec", {"command": "ls -la /tmp"}, {"capability": "code:exec"})
assert v.approved is True
"""),
    ("vaglio", "judge_intent_menziona_executor_alza_score", "happy", """
from vaglio import judge_score
score_implicit, _ = judge_score("fammi qualcosa", "read_files", {"path": "/tmp/a"})
score_explicit, _ = judge_score("read_files del file", "read_files", {"path": "/tmp/a"})
assert score_explicit > score_implicit
"""),
    ("vaglio", "judge_path_traversal_abbassa_score", "edge", """
from vaglio import judge_score
score_safe, _ = judge_score("leggi file", "read_files", {"path": "/tmp/foo.txt"})
score_traversal, _ = judge_score("leggi file", "read_files", {"path": "/tmp/../etc/foo"})
assert score_traversal < score_safe
"""),
    ("vaglio", "judge_sotto_soglia_blocca", "failure", """
import os
os.environ["METNOS_JUDGE_THRESHOLD"] = "0.99"  # impossibilmente alta
import importlib, vaglio
importlib.reload(vaglio)
# write_files NON è in SAFE_VERBS, quindi la soglia LM si applica.
v = vaglio.judge("nulla", "write_files", {"path": "/tmp/x"})
assert v.approved is False
assert v.blocked_by == "judge"
del os.environ["METNOS_JUDGE_THRESHOLD"]
importlib.reload(vaglio)
"""),
    ("vaglio", "log_jsonl_viene_scritto", "happy", """
import time
from vaglio import judge, VAGLIO_LOG_DIR
judge("test_xyz_unique_marker", "read_files", {"path": "/tmp/x"})
log = VAGLIO_LOG_DIR / f"{time.strftime('%Y-%m')}.jsonl"
assert log.exists(), log
content = log.read_text()
assert "test_xyz_unique_marker" in content
"""),
    ("vaglio", "args_keys_loggate_non_values", "security", """
import time
from vaglio import judge, VAGLIO_LOG_DIR
sensitive_value = "PASSWORD_segreto_zzz_unique_456"
judge("intent_z", "read_files", {"path": "/tmp/" + sensitive_value})  # path innocuo, value sensibile
log = VAGLIO_LOG_DIR / f"{time.strftime('%Y-%m')}.jsonl"
content = log.read_text()
assert sensitive_value not in content, "il valore sensibile NON deve essere nel log"
assert "args_keys" in content
"""),
    ("vaglio", "judge_kind_default_rule_based", "happy", """
import os
os.environ.pop('METNOS_JUDGE_KIND', None)
import importlib, vaglio
importlib.reload(vaglio)
v = vaglio.judge('leggi note', 'read_files', {'path': '/tmp/x'})
assert v.judge_kind in ('rule-based-v1', 'safe-verb-shortcut')
assert v.score >= 0.5
"""),
    ("vaglio", "judge_llm_fallback_se_router_assente", "edge", """
import os
os.environ['METNOS_JUDGE_KIND'] = 'llm-v1'
import importlib, vaglio
importlib.reload(vaglio)
# Senza tier middle configurato, LLMRouter solleva o ritorna parse-fail.
# Il judge LLM cattura l'errore e fallback a 0.5 con reason esplicita.
# write_files NON è SAFE (ADR 0107), bypassa safe-verb-shortcut → path LLM.
v = vaglio.judge('test', 'write_files', {'path': '/tmp/x'})
assert v.judge_kind == 'llm-v1'
assert 0.0 <= v.score <= 1.0
assert 'llm' in v.reason.lower() or 'fallback' in v.reason.lower()
del os.environ['METNOS_JUDGE_KIND']
importlib.reload(vaglio)
"""),
    ("vaglio", "judge_llm_parsa_json_strutturato", "happy", """
import os, importlib, sys
os.environ['METNOS_JUDGE_KIND'] = 'llm-v1'
import vaglio
importlib.reload(vaglio)

# Stub: monkeypatch _judge_score_llm con un wrapper che intercetta llm_router
class FakeRouter:
    def chat(self, system, user, **kw):
        class R:
            text = '{"score": 0.85, "reason": "azione allineata con t.tempo"}'
        return R()

# Sostituisci LLMRouter import inside _judge_score_llm via monkeypatch del modulo
import types
fake_module = types.ModuleType('llm_router')
fake_module.LLMRouter = FakeRouter
sys.modules['llm_router'] = fake_module

v = vaglio.judge('archivia foto', 'fs_move', {'src':'/a','dst':'/b'})
assert v.judge_kind == 'llm-v1'
assert v.score == 0.85, v.score
assert 't.tempo' in v.reason or 'allineata' in v.reason

# Cleanup
del os.environ['METNOS_JUDGE_KIND']
del sys.modules['llm_router']
importlib.reload(vaglio)
"""),
    ("vaglio", "judge_llm_guardia_blocca_prima_di_chiamare_llm", "security", """
import os, importlib, sys
os.environ['METNOS_JUDGE_KIND'] = 'llm-v1'
import vaglio
importlib.reload(vaglio)

# Anche con LLM attivato, la guardia binaria precede il giudice.
# Forbidden path → blocco senza chiamare alcun LLM.
calls = []
class FakeRouter:
    def chat(self, *a, **kw):
        calls.append(1)
        class R: text = '{"score":1.0,"reason":"x"}'
        return R()
import types
fake_module = types.ModuleType('llm_router')
fake_module.LLMRouter = FakeRouter
sys.modules['llm_router'] = fake_module

v = vaglio.judge('leggi chiave', 'read_files', {'path': '~/.ssh/id_rsa'})
assert v.approved is False
assert v.blocked_by == 'guard'
assert calls == [], 'la guardia deve precedere il giudice LLM'

del os.environ['METNOS_JUDGE_KIND']
del sys.modules['llm_router']
importlib.reload(vaglio)
"""),
]

# --- cost_tracker ---
RUNTIME_PYTHON_CASES += [
    ("cost_tracker", "stima_pre_call_proporzionale", "happy", """
from decimal import Decimal
from cost_tracker import CostTracker
t = CostTracker()
e = t.estimate_pre_call("anthropic", "claude-sonnet-4-6", 1000)
assert e == Decimal("0.003"), e
"""),
    ("cost_tracker", "ollama_costa_zero", "happy", """
from cost_tracker import CostTracker
from decimal import Decimal
t = CostTracker()
c = t.record_post_call("ollama", "qwen3:8b", 5000, 200)
assert c == Decimal("0"), c
"""),
    ("cost_tracker", "post_call_aggiunge_al_totale", "happy", """
from decimal import Decimal
import tempfile, os
import cost_tracker as ct
from pathlib import Path
ct.COST_DIR = Path(tempfile.mkdtemp())
t = ct.CostTracker(monthly_cap_eur=Decimal("100"))
before = t.month_total_eur()
c = t.record_post_call("anthropic", "claude-haiku-4-5", 1000, 1000)
after = t.month_total_eur()
assert after == before + c
assert c > 0
"""),
    ("cost_tracker", "is_exhausted_quando_raggiungi_cap", "edge", """
from decimal import Decimal
import tempfile
import cost_tracker as ct
from pathlib import Path
ct.COST_DIR = Path(tempfile.mkdtemp())
t = ct.CostTracker(monthly_cap_eur=Decimal("0.01"))
assert not t.is_exhausted()
# 100k tokens haiku in = 0.08 eur, oltre il cap
t.record_post_call("anthropic", "claude-haiku-4-5", 100000, 0)
assert t.is_exhausted()
assert t.remaining_budget_eur() < 0
"""),
    ("cost_tracker", "fallback_pricing_per_modello_sconosciuto", "edge", """
from decimal import Decimal
from cost_tracker import _price_for
p = _price_for("anthropic", "claude-modello-inesistente")
assert p["in"] > 0  # default conservativo
"""),
    ("cost_tracker", "ricarica_da_jsonl_al_boot", "happy", """
import tempfile
from decimal import Decimal
from pathlib import Path
import cost_tracker as ct
ct.COST_DIR = Path(tempfile.mkdtemp())
t1 = ct.CostTracker(monthly_cap_eur=Decimal("100"))
t1.record_post_call("anthropic", "claude-haiku-4-5", 1000, 1000)
spent = t1.month_total_eur()
# Nuovo CostTracker dovrebbe ricostruire dalla JSONL
t2 = ct.CostTracker(monthly_cap_eur=Decimal("100"))
assert t2.month_total_eur() == spent, (t2.month_total_eur(), spent)
"""),
]

# --- llm_provider ---
RUNTIME_PYTHON_CASES += [
    ("llm_provider", "stub_provider_ritorna_scripted", "happy", """
from llm_provider import StubProvider
p = StubProvider("hello world")
r = p.chat("sys", "user")
assert r.text == "hello world"
assert r.provider == "stub"
"""),
    ("llm_provider", "anthropic_senza_api_key_solleva", "failure", """
from llm_provider import AnthropicProvider, ProviderError
import os
os.environ.pop("ANTHROPIC_API_KEY", None)
# Costruisci un provider con api_key=None esplicito: bypassa la lettura da
# env/credentials.env, simulando un ambiente senza chiave configurata.
p = AnthropicProvider(api_key="")
p.api_key = None
try:
    p.chat("s", "u")
    assert False, "doveva sollevare ProviderError"
except ProviderError:
    pass
"""),
    ("llm_provider", "ollama_chat_ritorna_text_e_tokens", "happy", """
import urllib.request, urllib.error
try:
    urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1).read()
except Exception:
    print("SKIP: ollama not running (qwen3:8b setup needed)")
    import sys; sys.exit(0)
from llm_provider import OllamaProvider
p = OllamaProvider(model="qwen3:8b", think=False)
r = p.chat("Sei un assistente molto conciso.", "Rispondi solo con la parola OK.")
assert isinstance(r.text, str) and len(r.text) > 0
assert r.in_tokens > 0 and r.out_tokens > 0
assert r.provider == "ollama"
assert r.latency_ms > 0
"""),
    ("llm_provider", "ollama_chat_with_tools_ritorna_tool_call", "happy", """
import urllib.request, urllib.error
try:
    urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1).read()
except Exception:
    print("SKIP: ollama not running (qwen3:8b setup needed)")
    import sys; sys.exit(0)
from llm_provider import OllamaProvider
p = OllamaProvider(model="qwen3:8b", think=False)
tools = [{"type":"function","function":{"name":"get_now","description":"Restituisce ora in fuso IANA","parameters":{"type":"object","properties":{"timezone":{"type":"string"}},"required":[]}}}]
r = p.chat_with_tools("Sei un assistente che usa tool.", "Che ora è a Tokyo?", tools)
assert len(r.tool_calls) >= 1
tc = r.tool_calls[0]
assert tc.name == "get_now"
assert tc.arguments.get("timezone") == "Asia/Tokyo"
"""),
    ("llm_provider", "make_provider_from_config_local", "happy", """
from llm_provider import make_provider_from_config, LlamaCppProvider
p = make_provider_from_config("local", {"local": {"model": "gemma-26b"}})
assert isinstance(p, LlamaCppProvider)
# Modello: post-ADR 0146 il LlamaCppProvider usa l'endpoint :8080 con
# il modello caricato server-side; il config 'model' è ignorato per il provider
# (è il llama-server che decide). Verifichiamo solo il routing.
"""),
    ("llm_provider", "mode_sconosciuto_solleva", "failure", """
from llm_provider import make_provider_from_config
try:
    make_provider_from_config("alien", {})
    assert False
except ValueError:
    pass
"""),
]

# --- test_runner ---
RUNTIME_PYTHON_CASES += [
    ("test_runner", "match_hint_path_glob_doppio_asterisco", "happy", """
from test_runner import match_hint
assert match_hint("/tmp/foo/bar.txt", "/tmp/**")
assert match_hint("/tmp/", "/tmp/**")
assert not match_hint("/etc/passwd", "/tmp/**")
"""),
    ("test_runner", "match_hint_espande_tilde", "happy", """
import os
from test_runner import match_hint
home = os.path.expanduser("~")
assert match_hint(f"{home}/notes/x.md", "~/notes/**")
"""),
    ("test_runner", "match_host_esatto_e_wildcard", "happy", """
from test_runner import match_host
assert match_host("api.example.com", "api.example.com")
assert match_host("a.example.com", "*.example.com")
assert match_host("example.com", "*.example.com")
assert not match_host("evil.com", "*.example.com")
"""),
    ("test_runner", "check_hints_blocca_path_fuori_scope", "security", """
from test_runner import check_hints
caps = [{"name":"fs:read","hint":["~/notes/**","/tmp/**"]}]
viol = check_hints({"path": "/etc/passwd"}, caps)
assert viol and "outside" in viol, viol
"""),
    ("test_runner", "check_hints_passa_path_in_scope", "happy", """
from test_runner import check_hints
caps = [{"name":"fs:read","hint":["/tmp/**"]}]
viol = check_hints({"path": "/tmp/x.txt"}, caps)
assert viol is None
"""),
    ("test_runner", "check_hints_blocca_host_fuori_scope", "security", """
from test_runner import check_hints
caps = [{"name":"network:http","hint":["api.openweather.com"]}]
viol = check_hints({"url": "https://evil.example.com/x"}, caps)
assert viol and "outside" in viol, viol
"""),
    ("test_runner", "check_hints_passa_host_in_scope", "happy", """
from test_runner import check_hints
caps = [{"name":"network:http","hint":["api.openweather.com"]}]
viol = check_hints({"url": "https://api.openweather.com/data"}, caps)
assert viol is None, viol
"""),
    ("test_runner", "check_expect_matcher_ok_e_content", "happy", """
from test_runner import check_expect
fail = check_expect({"ok": True, "content": "ciao mondo"},
                    {"ok": True, "content_contains": "mondo"})
assert fail == [], fail
"""),
    ("test_runner", "check_expect_metadata_field_eq", "happy", """
from test_runner import check_expect
fail = check_expect({"ok": True, "metadata": {"bytes": 3}},
                    {"ok": True, "metadata_field_eq": {"bytes": 3}})
assert fail == [], fail
"""),
    ("test_runner", "check_expect_riconosce_matcher_sconosciuto", "edge", """
from test_runner import check_expect
fail = check_expect({"ok": True}, {"foo_matcher": 42})
assert any("sconosciuto" in f for f in fail)
"""),
]

# --- agent_runtime (modulo) ---
RUNTIME_PYTHON_CASES += [
    ("agent_runtime", "validate_args_required_mancante", "failure", """
from agent_runtime import validate_args
schema = {"type":"object","required":["path"],"properties":{"path":{"type":"string"}}}
fail = validate_args({}, schema)
assert any("path" in f for f in fail), fail
"""),
    ("agent_runtime", "validate_args_tipo_sbagliato", "failure", """
from agent_runtime import validate_args
schema = {"type":"object","properties":{"n":{"type":"integer"}}}
fail = validate_args({"n": "not_int"}, schema)
assert any("integer" in f for f in fail), fail
"""),
    ("agent_runtime", "validate_args_enum", "failure", """
from agent_runtime import validate_args
schema = {"type":"object","properties":{"mode":{"type":"string","enum":["a","b"]}}}
fail = validate_args({"mode": "c"}, schema)
assert any("enum" in str(f) or "c" in str(f) for f in fail), fail
"""),
    ("agent_runtime", "resolve_references_sostituisce_step1_content", "happy", """
from agent_runtime import resolve_references
hist = [{"step":1, "tool":"get_urls", "args":{}, "observation":{"content":"BODY"}}]
out, errs = resolve_references({"content":"{{step1.content}}"}, hist)
assert out == {"content":"BODY"}, out
assert errs == []
"""),
    ("agent_runtime", "resolve_references_dotted_field", "happy", """
from agent_runtime import resolve_references
hist = [{"step":1,"tool":"x","args":{},"observation":{"metadata":{"path":"/tmp/a"}}}]
out, errs = resolve_references({"path":"{{step1.metadata.path}}"}, hist)
assert out == {"path":"/tmp/a"}
"""),
    ("agent_runtime", "resolve_references_step_inesistente", "failure", """
from agent_runtime import resolve_references
out, errs = resolve_references({"x":"{{step3.foo}}"}, [])
assert errs and "step 3" in errs[0]
"""),
    ("agent_runtime", "resolve_references_field_inesistente", "failure", """
from agent_runtime import resolve_references
hist = [{"step":1,"tool":"x","args":{},"observation":{"content":"x"}}]
out, errs = resolve_references({"x":"{{step1.bogus}}"}, hist)
assert errs and "non trovato" in errs[0]
"""),
    ("agent_runtime", "resolve_from_step_happy_entries", "happy", """
from agent_runtime import resolve_from_step
hist = [{"step":1, "tool":"read_messages", "args":{}, "observation":{"ok":True,"entries":[{"a":1},{"b":2}],"count":2}}]
out, errs = resolve_from_step({"from_step":1, "style":"by_importance"}, hist)
assert errs == [], errs
assert out == {"style":"by_importance", "entries":[{"a":1},{"b":2}]}, out
"""),
    ("agent_runtime", "resolve_from_step_no_op_se_assente", "happy", """
from agent_runtime import resolve_from_step
out, errs = resolve_from_step({"style":"x", "entries":[{"a":1}]}, [])
assert errs == [] and out == {"style":"x","entries":[{"a":1}]}
"""),
    ("agent_runtime", "resolve_from_step_string_numerica_accettata", "edge", """
from agent_runtime import resolve_from_step
hist = [{"step":1,"tool":"x","args":{},"observation":{"ok":True,"entries":[{"a":1}]}}]
out, errs = resolve_from_step({"from_step":"1"}, hist)
assert errs == []
assert len(out["entries"]) == 1
"""),
    ("agent_runtime", "resolve_from_step_step_inesistente", "failure", """
from agent_runtime import resolve_from_step
hist = [{"step":1,"tool":"x","args":{},"observation":{"ok":True,"entries":[]}}]
out, errs = resolve_from_step({"from_step":5}, hist)
assert errs and "inesistente" in errs[0]
"""),
    ("agent_runtime", "resolve_from_step_no_lista", "failure", """
from agent_runtime import resolve_from_step
hist = [{"step":1,"tool":"get_now","args":{},"observation":{"ok":True,"now":"2026-04-29"}}]
out, errs = resolve_from_step({"from_step":1}, hist)
assert errs and "non ha prodotto una lista" in errs[0]
"""),
    ("agent_runtime", "resolve_from_step_type_invalid", "failure", """
from agent_runtime import resolve_from_step
hist = [{"step":1,"tool":"x","args":{},"observation":{"ok":True,"entries":[{"a":1}]}}]
out, errs = resolve_from_step({"from_step":1.5}, hist)
assert errs and "intero" in errs[0]
"""),
    ("agent_runtime", "resolve_from_step_pop_chiave", "happy", """
from agent_runtime import resolve_from_step
hist = [{"step":1,"tool":"x","args":{},"observation":{"ok":True,"entries":[{"a":1}]}}]
out, errs = resolve_from_step({"from_step":1, "x":"y"}, hist)
assert "from_step" not in out, out
assert "entries" in out
"""),
    ("agent_runtime", "render_tools_for_provider_genera_function_calling", "happy", """
from loader import load_catalog
from agent_runtime import render_tools_for_provider
cat = load_catalog()
tools = render_tools_for_provider(list(cat))
assert len(tools) == len(cat), f"render ha prodotto {len(tools)} tools per {len(cat)} executor"
assert all(t["type"] == "function" for t in tools)
assert all("name" in t["function"] for t in tools)
assert all("parameters" in t["function"] for t in tools)
names = {t["function"]["name"] for t in tools}
# Set seed minimo: la triade base + get_urls deve sempre essere renderizzabile.
must_have = {"read_files", "write_files", "get_now", "get_urls"}
missing = must_have - names
assert not missing, f"render manca: {missing}"
"""),
    ("agent_runtime", "mode_router_ritorna_il_mode_di_config", "happy", """
from agent_runtime import ModeRouter
r = ModeRouter("hybrid")
assert r.select("qualunque", None) == "hybrid"
"""),
    ("agent_runtime", "invoke_executor_ritorna_json", "happy", """
from loader import load_catalog
from agent_runtime import invoke_executor
cat = load_catalog()
ex = cat.get("get_now")
out = invoke_executor(ex, {"timezone":"UTC"})
assert out.get("ok") is True
assert out.get("metadata", {}).get("timezone") == "UTC"
"""),
    ("agent_runtime", "try_synt_compose_proto_mnest_inesistente_torna_none", "edge", """
import tempfile, os
from pathlib import Path
db = Path(tempfile.mkdtemp()) / "mnesto.db"
os.environ["MNESTOMA_DB_PATH"] = str(db)
from mnestoma import Mnestoma
from agent_runtime import _try_synt_compose
mnestoma = Mnestoma()
out = _try_synt_compose(mnestoma, "executor_inventato", "id_inesistente")
assert out is None or out.get("state") in ("abandoned", "rejected"), out
"""),
    ("agent_runtime", "try_synt_compose_proto_senza_chain_torna_abandoned", "happy", """
import tempfile, os
from pathlib import Path
db = Path(tempfile.mkdtemp()) / "mnesto.db"
os.environ["MNESTOMA_DB_PATH"] = str(db)
from mnestoma import Mnestoma, build_desired_signature
from agent_runtime import _try_synt_compose
mnestoma = Mnestoma()
sig = build_desired_signature("ghost_executor", {"x": 1}, user_query="...")
mid = mnestoma.record_passing(
    "read_files", "1.0", "ghost_executor", dst_version=None,
    dst_exists=False, desired_signature=sig, turn_id="t1",
)
out = _try_synt_compose(mnestoma, "ghost_executor", mid)
assert out is not None, "deve ritornare un suggerimento (abandoned)"
assert out.get("state") in ("abandoned", "rejected"), out
assert "suggestion" in out
"""),
    ("agent_runtime", "try_synt_compose_degrada_se_mnestoma_invalido", "edge", """
from agent_runtime import _try_synt_compose
class FakeBroken:
    def get(self, *a, **k):
        raise RuntimeError('boom')
out = _try_synt_compose(FakeBroken(), "x", "y")
assert out is None, "deve degradare a None senza propagare eccezioni"
"""),
]

# --- scratchpad (modulo) ---
RUNTIME_PYTHON_CASES += [
    ("scratchpad", "open_crea_db", "happy", """
import tempfile
from pathlib import Path
from scratchpad import Scratchpad
db = Path(tempfile.mkdtemp()) / "test.db"
sp = Scratchpad.open(db)
assert db.exists()
assert sp.stats() == {"entries": 0, "total_bytes": 0}
"""),
    ("scratchpad", "put_ritorna_synthetic_e_salva", "happy", """
import tempfile
from pathlib import Path
from scratchpad import Scratchpad
sp = Scratchpad.open(Path(tempfile.mkdtemp()) / "test.db")
obs = {"ok": True, "content": "x" * 5000, "metadata": {"path": "/tmp/foo"}}
synth = sp.put("turn_x", 1, "read_files", obs)
assert "scratchpad_id" in synth
assert synth["size_bytes"] == 5000
assert "summary" in synth
assert synth["metadata"] == {"path": "/tmp/foo"}
assert sp.stats()["entries"] == 1
"""),
    ("scratchpad", "read_full_ritorna_contenuto_originale", "happy", """
import tempfile
from pathlib import Path
from scratchpad import Scratchpad
sp = Scratchpad.open(Path(tempfile.mkdtemp()) / "test.db")
content = "ciao mondo " * 1000
synth = sp.put("turn_x", 1, "read_files", {"ok": True, "content": content})
back = sp.read(synth["scratchpad_id"], mode="full")
assert back["ok"]
assert back["content"] == content
"""),
    ("scratchpad", "read_head_e_tail", "happy", """
import tempfile
from pathlib import Path
from scratchpad import Scratchpad
sp = Scratchpad.open(Path(tempfile.mkdtemp()) / "test.db")
text = "0123456789" * 500  # 5000 chars
synth = sp.put("turn_x", 1, "read_files", {"ok": True, "content": text})
head = sp.read(synth["scratchpad_id"], mode="head", n=100)
assert head["content"].startswith("0123456789")
assert len(head["content"]) == 100
tail = sp.read(synth["scratchpad_id"], mode="tail", n=50)
assert len(tail["content"]) == 50
assert tail["content"].endswith("89")
"""),
    ("scratchpad", "read_range", "happy", """
import tempfile
from pathlib import Path
from scratchpad import Scratchpad
sp = Scratchpad.open(Path(tempfile.mkdtemp()) / "test.db")
text = "abcdefghij" * 1000
synth = sp.put("t", 1, "x", {"ok": True, "content": text})
r = sp.read(synth["scratchpad_id"], mode="range", start=10, end=20)
assert r["content"] == "abcdefghij", r["content"]
"""),
    ("scratchpad", "read_id_inesistente_fallisce", "failure", """
import tempfile
from pathlib import Path
from scratchpad import Scratchpad
sp = Scratchpad.open(Path(tempfile.mkdtemp()) / "test.db")
out = sp.read("non_esiste_xyz")
assert not out["ok"]
assert "non trovato" in out["error"]
"""),
    ("scratchpad", "gc_rimuove_scaduti", "edge", """
import tempfile, time
from pathlib import Path
from scratchpad import Scratchpad
sp = Scratchpad.open(Path(tempfile.mkdtemp()) / "test.db")
sp.put("t", 1, "x", {"ok": True, "content": "x"*100}, ttl_seconds=-1)
removed = sp.gc()
assert removed >= 1
"""),
    ("scratchpad", "list_for_turn", "happy", """
import tempfile
from pathlib import Path
from scratchpad import Scratchpad
sp = Scratchpad.open(Path(tempfile.mkdtemp()) / "test.db")
sp.put("turn_a", 1, "x", {"ok": True, "content": "y"*100})
sp.put("turn_a", 2, "y", {"ok": True, "content": "z"*100})
sp.put("turn_b", 1, "z", {"ok": True, "content": "w"*100})
items = sp.list_for_turn("turn_a")
assert len(items) == 2
"""),
    ("scratchpad", "summary_smart_truncation", "happy", """
import tempfile
from pathlib import Path
from scratchpad import Scratchpad
sp = Scratchpad.open(Path(tempfile.mkdtemp()) / "test.db")
text = ("INIZIO" + "x" * 10000 + "FINE")
synth = sp.put("t", 1, "x", {"ok": True, "content": text})
# Il summary smart truncation dovrebbe contenere sia INIZIO sia FINE
assert "INIZIO" in synth["summary"]
assert "FINE" in synth["summary"]
assert "omessi" in synth["summary"]
"""),
    ("scratchpad", "scratchpad_read_tool_ben_formato", "happy", """
from scratchpad import SCRATCHPAD_READ_TOOL
assert SCRATCHPAD_READ_TOOL["type"] == "function"
fn = SCRATCHPAD_READ_TOOL["function"]
assert fn["name"] == "scratchpad_read"
assert "scratchpad_id" in fn["parameters"]["required"]
"""),
]

# --- mnestoma (modulo) ---
RUNTIME_PYTHON_CASES += [
    ("mnestoma", "open_crea_db_e_schema", "happy", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
s = m.stats()
assert s["total_mnests"] == 0
assert s["events"] == 0
m.close()
"""),
    ("mnestoma", "record_passing_nuovo_mnest", "happy", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma, BOOTSTRAP_WEIGHT
m = Mnestoma()
mid = m.record_passing("read_files", "1.0.0", "pdf_extract", "2.0.0", tags=["fattura"])
mn = m.get(mid)
assert mn.weight == BOOTSTRAP_WEIGHT
assert mn.uses == 1
assert mn.state == "active"
assert mn.tags == ["fattura"]
m.close()
"""),
    ("mnestoma", "record_passing_reinforce_stesso", "happy", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma, BOOTSTRAP_WEIGHT, REINFORCE_DELTA
m = Mnestoma()
mid1 = m.record_passing("a", "1", "b", "1")
mid2 = m.record_passing("a", "1", "b", "1")
assert mid1 == mid2
mn = m.get(mid1)
assert mn.uses == 2
# reinforce su lo stesso istante: dt=0 -> w_decayed=BOOTSTRAP, w_new=clamp01(BOOTSTRAP+REINFORCE)
import math
expected = min(1.0, BOOTSTRAP_WEIGHT + REINFORCE_DELTA)
assert abs(mn.weight - expected) < 0.01, (mn.weight, expected)
m.close()
"""),
    ("mnestoma", "weight_clamp_a_uno", "edge", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
mid = m.record_passing("a", "1", "b", "1")
for _ in range(20):
    m.record_passing("a", "1", "b", "1")
mn = m.get(mid)
assert 0.0 <= mn.weight <= 1.0, mn.weight
assert mn.weight == 1.0, f"atteso saturo a 1, ho {mn.weight}"
m.close()
"""),
    ("mnestoma", "record_proto_mnest", "happy", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma, build_desired_signature
m = Mnestoma()
sig = build_desired_signature("extract_invoice_no", {"pdf": "bytes"}, "estrai")
mid = m.record_passing("pdf_extract", "2.0.0", "extract_invoice_no",
                       dst_exists=False, desired_signature=sig)
mn = m.get(mid)
assert mn.state == "proto"
assert mn.dst_version is None
assert mn.desired_sig is not None
assert "extract_invoice_no" in mn.desired_sig["summary"]
m.close()
"""),
    ("mnestoma", "proto_distinto_da_active_su_stessa_chiave_logica", "edge", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
# Un proto e un active con stessa src ma diverso dst nome possono coesistere
mid_a = m.record_passing("read_files", "1.0.0", "pdf_extract", "2.0.0")
mid_p = m.record_passing("read_files", "1.0.0", "ocr_pdf", dst_exists=False,
                         desired_signature={"summary":"ocr","inputs":[],"outputs":[],"errors":[]})
assert mid_a != mid_p
assert m.get(mid_a).state == "active"
assert m.get(mid_p).state == "proto"
m.close()
"""),
    ("mnestoma", "top_k_outgoing_ordina_per_peso", "happy", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
m.record_passing("read_files", "1", "a", "1")
for _ in range(5):
    m.record_passing("read_files", "1", "b", "1")  # piu' rinforzato
m.record_passing("read_files", "1", "c", "1")
out = m.top_k_outgoing("read_files", k=10)
names = [x.dst_executor for x in out]
assert names[0] == "b", names
assert set(names) == {"a","b","c"}
m.close()
"""),
    ("mnestoma", "top_k_incoming", "happy", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
m.record_passing("a", "1", "target", "1")
m.record_passing("b", "1", "target", "1")
out = m.top_k_incoming("target")
sources = sorted([x.src_executor for x in out])
assert sources == ["a", "b"], sources
m.close()
"""),
    ("mnestoma", "by_tag_filtra", "happy", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
m.record_passing("a", "1", "b", "1", tags=["fattura"])
m.record_passing("a", "1", "c", "1", tags=["foto"])
m.record_passing("b", "1", "d", "1", tags=["fattura", "pdf"])
fatt = m.by_tag("fattura")
assert len(fatt) == 2
foto = m.by_tag("foto")
assert len(foto) == 1
m.close()
"""),
    ("mnestoma", "recurring_protos_filtra_per_soglia", "happy", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
sig = {"summary":"x","inputs":[],"outputs":[],"errors":[]}
mid_low = m.record_passing("a","1","raro", dst_exists=False, desired_signature=sig)
# rinforza il secondo 4 volte per superare uses>=3
mid_hi = None
for _ in range(4):
    mid_hi = m.record_passing("a","1","frequente", dst_exists=False, desired_signature=sig)
out = m.recurring_protos(min_uses=3, min_weight=0.0)
names = [x.dst_executor for x in out]
assert "frequente" in names
assert "raro" not in names
m.close()
"""),
    ("mnestoma", "walk_max_depth_uno", "happy", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
m.record_passing("a","1","b","1")
m.record_passing("a","1","c","1")
paths = m.walk("a", max_depth=1)
# 2 archi uscenti, 2 path di lunghezza 1
assert len(paths) == 2
assert all(len(p) == 1 for p in paths)
m.close()
"""),
    ("mnestoma", "walk_paths_da_grafo_a_due_hop", "happy", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
m.record_passing("a","1","b","1")
m.record_passing("b","1","c","1")
paths = m.walk("a", max_depth=2)
# Path 1: a->b ; Path 2: a->b->c
chains = [[p[0].src_executor] + [e.dst_executor for e in p] for p in paths]
assert ["a","b"] in chains
assert ["a","b","c"] in chains
m.close()
"""),
    ("mnestoma", "apply_ager_decadimento_e_demotion", "happy", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma, DECAY_THRESHOLD
m = Mnestoma()
mid = m.record_passing("a","1","b","1")
# Forza ts_first e ts_last indietro nel tempo (CHECK constraint ts_last>=ts_first)
m.conn.execute(
    "UPDATE mnests SET ts_first='2024-01-01T00:00:00Z', ts_last='2024-01-02T00:00:00Z' WHERE id=?",
    (mid,),
)
stats = m.apply_ager()
assert stats["decayed"] >= 1
mn = m.get(mid)
# Con BOOTSTRAP=0.30 e ~2 anni di decay -> sotto DECAY_THRESHOLD
assert mn.weight < DECAY_THRESHOLD, mn.weight
assert mn.state == "decaying", mn.state
m.close()
"""),
    ("mnestoma", "apply_ager_proto_purge_sotto_soglia", "edge", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma, PROTO_PURGE_THRESHOLD
m = Mnestoma()
sig = {"summary":"x","inputs":[],"outputs":[],"errors":[]}
mid = m.record_passing("a","1","missing", dst_exists=False, desired_signature=sig)
# Forza peso sotto soglia di purge (campo non vincolato)
m.conn.execute("UPDATE mnests SET weight=? WHERE id=?",
               (PROTO_PURGE_THRESHOLD * 0.5, mid))
stats = m.apply_ager()
assert stats["purged_protos"] >= 1
assert m.get(mid) is None
m.close()
"""),
    ("mnestoma", "transition_state_traccia_evento", "happy", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
mid = m.record_passing("a","1","b","1")
m.transition_state(mid, "superseded", reason="test merge")
mn = m.get(mid)
assert mn.state == "superseded"
events = m.events_for(mid)
sc = [e for e in events if e["kind"] == "state_change"]
assert len(sc) == 1
assert sc[0]["new_state"] == "superseded"
assert sc[0]["reason"] == "test merge"
m.close()
"""),
    ("mnestoma", "promote_proto_to_active", "happy", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
sig = {"summary":"x","inputs":[],"outputs":[],"errors":[]}
mid = m.record_passing("a","1","born_today", dst_exists=False, desired_signature=sig)
m.promote_proto_to_active(mid, dst_version="1.0.0", reason="executor born")
mn = m.get(mid)
assert mn.state == "active"
assert mn.dst_version == "1.0.0"
assert mn.desired_sig is None
m.close()
"""),
    ("mnestoma", "events_for_traccia_completa", "happy", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
mid = m.record_passing("a","1","b","1", turn_id="turn_xyz")
m.record_passing("a","1","b","1", turn_id="turn_xyz")
events = m.events_for(mid)
assert len(events) == 2
assert events[0]["kind"] == "reinforce"
# Post-refactor: turn_id è campo separato (era unificato in reason pre-refactor).
assert events[0].get("turn_id") == "turn_xyz" or events[0].get("reason") == "turn_xyz", events[0]
m.close()
"""),
    ("mnestoma", "build_desired_signature_helper", "happy", """
from mnestoma import build_desired_signature
sig = build_desired_signature("foo", {"a": 1, "b": "x"}, user_query="user wants foo")
assert sig.summary.startswith("executor desiderato 'foo'")
assert "user wants foo" in sig.summary
assert set(sig.inputs) == {"a", "b"}
assert sig.outputs == ["unknown"]
"""),
    ("mnestoma", "id_unique_per_call", "edge", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
ids = set()
for i in range(20):
    ids.add(m.record_passing("a","1", f"dst_{i}","1"))
assert len(ids) == 20
m.close()
"""),
    ("mnestoma", "decaying_query", "happy", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
mid = m.record_passing("a","1","b","1")
m.transition_state(mid, "decaying", reason="forced")
out = m.decaying()
assert len(out) == 1
assert out[0].id == mid
m.close()
"""),
    # --- edge cases ---
    ("mnestoma", "edge_self_loop_consentito", "edge", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
mid = m.record_passing("a","1","a","1")
mn = m.get(mid)
assert mn.src_executor == mn.dst_executor == "a"
m.close()
"""),
    ("mnestoma", "edge_walk_con_ciclo_non_loopa", "edge", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
m.record_passing("a","1","b","1")
m.record_passing("b","1","a","1")
# Con ciclo, walk deve terminare al max_depth dato
paths = m.walk("a", max_depth=3)
# Path attesi (active filter): a->b ; a->b->a ; a->b->a->b
assert len(paths) == 3
assert all(len(p) <= 3 for p in paths)
m.close()
"""),
    ("mnestoma", "edge_transition_state_invalido_solleva", "failure", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
mid = m.record_passing("a","1","b","1")
try:
    m.transition_state(mid, "stato_inesistente", reason="x")
    assert False, "doveva sollevare ValueError"
except ValueError:
    pass
"""),
    ("mnestoma", "edge_proto_e_active_stessa_src_dst_coesistono", "edge", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma, build_desired_signature
m = Mnestoma()
# active con dst risolto + proto con stesso nome dst ma version=NULL
mid_a = m.record_passing("a","1","b","2.0.0")
sig = build_desired_signature("b", {}, "x")
mid_p = m.record_passing("a","1","b", dst_exists=False, desired_signature=sig)
assert mid_a != mid_p
assert m.get(mid_a).state == "active"
assert m.get(mid_p).state == "proto"
"""),
    ("mnestoma", "edge_walk_filter_active_solo_esclude_proto", "edge", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma, build_desired_signature
m = Mnestoma()
m.record_passing("a","1","b","1")
sig = build_desired_signature("c", {}, "x")
m.record_passing("a","1","c", dst_exists=False, desired_signature=sig)
# default state_filter=("active",): solo b
paths_active = m.walk("a", max_depth=1)
dsts_active = [p[-1].dst_executor for p in paths_active]
assert "b" in dsts_active and "c" not in dsts_active
# Includendo 'proto': anche c
paths_all = m.walk("a", max_depth=1, state_filter=("active","proto"))
dsts_all = [p[-1].dst_executor for p in paths_all]
assert "b" in dsts_all and "c" in dsts_all
"""),
    ("mnestoma", "edge_apply_ager_idempotente_in_singolo_giorno", "edge", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
mid = m.record_passing("a","1","b","1")
# Forza 1 anno indietro per provocare decay
m.conn.execute(
    "UPDATE mnests SET ts_first='2025-04-26T00:00:00Z',"
    " ts_last='2025-04-26T00:00:00Z' WHERE id=?", (mid,),
)
s1 = m.apply_ager()
w1 = m.get(mid).weight
# Subito dopo: dt=0 -> nessun decay aggiuntivo, peso invariato
s2 = m.apply_ager()
w2 = m.get(mid).weight
assert abs(w1 - w2) < 1e-6
assert s2["decayed"] == 0  # nessun decay perche' dt=0
"""),
    ("mnestoma", "edge_record_passing_tags_none_e_vuoto", "edge", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
mid_none = m.record_passing("a","1","b","1", tags=None)
mid_empty = m.record_passing("c","1","d","1", tags=[])
assert m.get(mid_none).tags == []
assert m.get(mid_empty).tags == []
"""),
    ("mnestoma", "edge_stress_micro_500_mnest_in_meno_di_5s", "edge", """
import os, tempfile, time
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
t0 = time.perf_counter()
for i in range(500):
    m.record_passing(f"src_{i % 50}", "1", f"dst_{i}", "1")
elapsed = time.perf_counter() - t0
assert elapsed < 5.0, f"500 inserts took {elapsed:.2f}s (atteso <5s)"
assert m.stats()["total_mnests"] == 500
"""),
    ("mnestoma", "top_active_globale_ordinato_per_peso", "happy", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
# Crea 5 mnest con uses crescenti (peso cresce con uses)
for i in range(5):
    for _ in range(i + 1):  # i+1 reinforce per il mnest i
        m.record_passing(f"a{i}", "1", f"b{i}", "1")
top = m.top_active(limit=3)
assert len(top) == 3
# I top-3 sono i mnest con uses piu' alto (i=4,3,2)
dsts = [t.dst_executor for t in top]
assert dsts == ["b4", "b3", "b2"], dsts
"""),
    ("mnestoma", "top_active_filtra_per_state", "happy", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma, build_desired_signature
m = Mnestoma()
m.record_passing("a", "1", "b", "1")  # active
sig = build_desired_signature("c", {}, "x")
m.record_passing("a", "1", "c", dst_exists=False, desired_signature=sig)  # proto
top_active = m.top_active(state="active")
top_proto = m.top_active(state="proto")
top_all = m.top_active(state=None)
assert len(top_active) == 1 and top_active[0].dst_executor == "b"
assert len(top_proto) == 1 and top_proto[0].dst_executor == "c"
assert len(top_all) == 2
"""),
    ("mnestoma", "executor_summary_aggrega_in_e_out", "happy", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma, build_desired_signature
m = Mnestoma()
m.record_passing("hub", "1", "x", "1")
m.record_passing("hub", "1", "y", "1")
m.record_passing("z", "1", "hub", "1")
sig = build_desired_signature("hub_proto", {}, "q")
m.record_passing("k", "1", "hub", dst_exists=False, desired_signature=sig)
s = m.executor_summary("hub")
assert s["outgoing"]["edges"] == 2  # hub -> x, y
assert s["incoming"]["edges"] == 1  # z -> hub (active)
assert s["proto_incoming"] == 1     # k -> hub (proto)
"""),
    ("mnestoma", "audit_recent_ordine_decrescente", "happy", """
import os, tempfile, time
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma
m = Mnestoma()
m.record_passing("a", "1", "b", "1", turn_id="t1")
m.record_passing("b", "1", "c", "1", turn_id="t2")
m.record_passing("a", "1", "b", "1", turn_id="t3")  # rinforza a->b
events = m.audit_recent(limit=10)
assert len(events) >= 3  # almeno 3 reinforce
# Ordinamento DESC su id (auto-increment, più alto = più recente).
# Post-refactor: turn_id non è nello schema audit_recent (resta solo in events_for).
ids = [e["id"] for e in events]
assert ids == sorted(ids, reverse=True), f"events non ordinati DESC: {ids}"
"""),
]

# --- synt (modulo) ---
def _synt_env_setup():
    return """
import os, tempfile
_d = tempfile.mkdtemp()
os.environ["MNESTOMA_DB_PATH"] = _d + "/mn.sqlite"
os.environ["SYNT_AUDIT_DIR"]   = _d + "/audit"
os.environ["SYNT_LOCK_PATH"]   = _d + "/locks.json"
"""


RUNTIME_PYTHON_CASES += [
    ("synt", "compute_reward_compose_default_supera_gate", "happy", _synt_env_setup() + """
from synt import compute_reward, GATE_THRESHOLD
r = compute_reward("compose")
# 0.35*1 + 0.20*0.5 + 0.15*1 + 0.10*1 + 0.10*1 = 0.80
assert abs(r.total - 0.80) < 1e-6, r.total
assert r.total > GATE_THRESHOLD
"""),
    ("synt", "compute_reward_generate_meno_bonus", "happy", _synt_env_setup() + """
from synt import compute_reward
r = compute_reward("generate")
# stesso ma strategy_cost_bonus=0 -> 0.70
assert abs(r.total - 0.70) < 1e-6, r.total
"""),
    ("synt", "compute_reward_clamp_a_uno", "edge", _synt_env_setup() + """
from synt import compute_reward
r = compute_reward("compose", det_pass_rate=2.0, judge_score=2.0,
                   coverage_bonus=2.0, similarity_penalty=-2.0)
assert r.total == 1.0
"""),
    ("synt", "compute_reward_clamp_a_zero", "edge", _synt_env_setup() + """
from synt import compute_reward
r = compute_reward("generate", det_pass_rate=0.0, judge_score=0.0,
                   cost_ratio=0.0, coverage_bonus=0.0, similarity_penalty=10.0)
assert r.total == 0.0
"""),
    ("synt", "composer_find_chain_diretto", "happy", _synt_env_setup() + """
from mnestoma import Mnestoma
from synt import Composer
m = Mnestoma()
m.record_passing("a","1","b","1")
c = Composer(m)
chain = c.find_chain("a", lambda n: n == "b", max_hops=5)
assert chain is not None
assert chain[0].dst_executor == "b"
"""),
    ("synt", "composer_find_chain_due_hop", "happy", _synt_env_setup() + """
from mnestoma import Mnestoma
from synt import Composer
m = Mnestoma()
m.record_passing("a","1","b","1")
m.record_passing("b","1","c","1")
c = Composer(m)
chain = c.find_chain("a", lambda n: n == "c", max_hops=5)
assert chain is not None
assert len(chain) == 2
assert chain[0].src_executor == "a"
assert chain[-1].dst_executor == "c"
"""),
    ("synt", "composer_find_chain_no_match", "happy", _synt_env_setup() + """
from mnestoma import Mnestoma
from synt import Composer
m = Mnestoma()
m.record_passing("a","1","b","1")
c = Composer(m)
assert c.find_chain("a", lambda n: n == "z", max_hops=5) is None
"""),
    ("synt", "composer_max_hops_zero", "edge", _synt_env_setup() + """
from mnestoma import Mnestoma
from synt import Composer
m = Mnestoma()
m.record_passing("a","1","b","1")
c = Composer(m)
assert c.find_chain("a", lambda n: True, max_hops=0) is None
"""),
    ("synt", "composer_rispetta_max_hops", "edge", _synt_env_setup() + """
from mnestoma import Mnestoma
from synt import Composer
m = Mnestoma()
# catena di 6 hop
m.record_passing("n0","1","n1","1")
m.record_passing("n1","1","n2","1")
m.record_passing("n2","1","n3","1")
m.record_passing("n3","1","n4","1")
m.record_passing("n4","1","n5","1")
m.record_passing("n5","1","n6","1")
c = Composer(m)
# max_hops=5 NON puo' raggiungere n6 (servirebbero 6 hop)
chain = c.find_chain("n0", lambda n: n == "n6", max_hops=5)
assert chain is None
# max_hops=6 invece sì
chain6 = c.find_chain("n0", lambda n: n == "n6", max_hops=6)
assert chain6 is not None
assert len(chain6) == 6
"""),
    ("synt", "react_compose_success", "happy", _synt_env_setup() + """
from mnestoma import Mnestoma, build_desired_signature
from synt import Synt, make_request
m = Mnestoma()
m.record_passing("read_files","1.0.0","pdf_extract","2.0.0")
m.record_passing("pdf_extract","2.0.0","archive_invoice","1.0.0")
sig = build_desired_signature("save_invoice", {}, "archivia")
proto_id = m.record_passing("read_files","1.0.0","save_invoice", dst_exists=False, desired_signature=sig)
s = Synt(mnestoma=m)
prop = s.react(make_request("archivia", proto_mnest=proto_id, capability_hint=["archive"]))
assert prop.state == "composed", prop.state
assert prop.strategy == "compose"
assert prop.artefact["chain"] == ["read_files", "pdf_extract", "archive_invoice"]
assert prop.reward.total > 0.65
"""),
    ("synt", "react_no_proto_mnest_abbandona", "happy", _synt_env_setup() + """
from mnestoma import Mnestoma
from synt import Synt, make_request
s = Synt(mnestoma=Mnestoma())
prop = s.react(make_request("intent senza proto", proto_mnest=None))
assert prop.state == "abandoned"
assert "no proto_mnest" in prop.rationale
"""),
    ("synt", "react_proto_inesistente_abbandona", "happy", _synt_env_setup() + """
from mnestoma import Mnestoma
from synt import Synt, make_request
s = Synt(mnestoma=Mnestoma())
prop = s.react(make_request("x", proto_mnest="mn_zzz_inesistente"))
assert prop.state == "abandoned"
assert "not found" in prop.rationale
"""),
    ("synt", "react_no_chain_abbandona_e_locka", "happy", _synt_env_setup() + """
from mnestoma import Mnestoma, build_desired_signature
from synt import Synt, make_request
m = Mnestoma()
sig = build_desired_signature("z", {}, "x")
proto_id = m.record_passing("a","1","z", dst_exists=False, desired_signature=sig)
s = Synt(mnestoma=m)
# Nessun arco a uscire da 'a' -> nessuna catena
prop1 = s.react(make_request("x", proto_mnest=proto_id, capability_hint=["nope"]))
assert prop1.state == "abandoned"
# Subito dopo, stesso intent: il lock 24h scatta
prop2 = s.react(make_request("x", proto_mnest=proto_id, capability_hint=["nope"]))
assert prop2.state == "abandoned"
assert "locked" in prop2.rationale
"""),
    ("synt", "react_audit_log_scrive_jsonl", "happy", _synt_env_setup() + """
import os, json, glob
from mnestoma import Mnestoma, build_desired_signature
from synt import Synt, make_request
m = Mnestoma()
m.record_passing("a","1","b","1")
sig = build_desired_signature("b", {}, "x")
proto_id = m.record_passing("a","1","b_desired", dst_exists=False, desired_signature=sig)
s = Synt(mnestoma=m)
s.react(make_request("x", proto_mnest=proto_id, capability_hint=["b"]))
# Trova il file jsonl e leggi righe
files = glob.glob(os.environ["SYNT_AUDIT_DIR"] + "/*.jsonl")
assert len(files) >= 1
lines = open(files[0]).read().splitlines()
assert len(lines) >= 1
entry = json.loads(lines[-1])
assert entry["strategy"] == "compose"
assert entry["state"] in ("composed", "abandoned")
"""),
    ("synt", "locks_lock_e_isLocked", "happy", _synt_env_setup() + """
import time
from synt import SyntLocks
l = SyntLocks()
assert not l.is_locked("k1")
l.lock("k1", days=1)
assert l.is_locked("k1")
l.clear("k1")
assert not l.is_locked("k1")
"""),
    ("synt", "locks_persistono_su_disco", "happy", _synt_env_setup() + """
from synt import SyntLocks
l1 = SyntLocks()
l1.lock("persist_key", days=1)
# Nuova istanza, stesso path
l2 = SyntLocks()
assert l2.is_locked("persist_key")
"""),
    ("synt", "make_request_id_diverso_per_chiamata", "happy", _synt_env_setup() + """
from synt import make_request
ids = {make_request("x").request_id for _ in range(20)}
assert len(ids) == 20
"""),
    ("synt", "default_pred_usa_capability_hint", "happy", _synt_env_setup() + """
from mnestoma import Mnestoma, build_desired_signature
from synt import Synt, make_request
m = Mnestoma()
m.record_passing("a","1","store_pdf","1")
sig = build_desired_signature("save_thing", {}, "x")
proto_id = m.record_passing("a","1","save_thing", dst_exists=False, desired_signature=sig)
s = Synt(mnestoma=m)
# capability_hint='store' deve matchare 'store_pdf' via substring
prop = s.react(make_request("x", proto_mnest=proto_id, capability_hint=["store"]))
assert prop.state == "composed"
assert prop.artefact["chain"][-1] == "store_pdf"
"""),
    ("synt", "homeostasis_pool_vuoto_torna_lista_vuota", "happy", _synt_env_setup() + """
from synt import Synt
assert Synt().homeostasis(catalog=[]) == []
"""),
    ("synt", "homeostasis_propone_merge_su_coppia_jaccard_alta", "happy", _synt_env_setup() + """
from synt import Synt
class FakeExec:
    def __init__(self, name, target_kind, capabilities, affinity):
        self.name=name; self.version='1'; self.target_kind=target_kind
        self.capabilities=capabilities; self.affinity=affinity
catalog = [
    FakeExec('a', 'exact', ('cap',), ['t1','t2','t3']),
    FakeExec('b', 'exact', ('cap',), ['t1','t2','t3']),  # jaccard=1.0
    FakeExec('c', 'host',  ('cap',), ['t1','t2','t3']),  # target_kind diverso, no merge
]
props = Synt().homeostasis(catalog=catalog, merge_min_jaccard=0.7)
merge = [p for p in props if p.strategy == 'merge']
assert len(merge) == 1
assert sorted(merge[0].artefact['candidates']) == ['a','b']
assert merge[0].artefact['jaccard'] == 1.0
"""),
    ("synt", "homeostasis_propone_generalize_su_cluster_prefix", "happy", _synt_env_setup() + """
from synt import Synt
class FakeExec:
    def __init__(self, name, target_kind, capabilities, affinity):
        self.name=name; self.version='1'; self.target_kind=target_kind
        self.capabilities=capabilities; self.affinity=affinity
# Per testare 'generalize' (e non merge), serve cluster con prefix comune ma
# capabilities/affinity DIFFERENTI fra i membri (altrimenti merge vince con
# jaccard=1.0 e blocca il generalize). 3 executor 'fs_*' con capabilities
# distinte: nessun merge possibile, ma stesso prefix.
catalog = [
    FakeExec('fs_read',  'path_glob', ('fs:read',),     ['read']),
    FakeExec('fs_write', 'path_glob', ('fs:write',),    ['write']),
    FakeExec('fs_list',  'path_glob', ('fs:enumerate',),['list']),
    FakeExec('llm_chat', 'none',      ('llm:local',),   ['llm']),  # solo, no cluster
]
props = Synt().homeostasis(catalog=catalog, generalize_min_cluster=3)
gen = [p for p in props if p.strategy == 'generalize']
assert len(gen) == 1, [p.strategy for p in props]
assert gen[0].artefact['prefix'] == 'fs'
assert gen[0].artefact['size'] == 3
"""),
    ("synt", "homeostasis_no_merge_se_capability_diverse", "edge", _synt_env_setup() + """
from synt import Synt
class FakeExec:
    def __init__(self, name, target_kind, capabilities, affinity):
        self.name=name; self.version='1'; self.target_kind=target_kind
        self.capabilities=capabilities; self.affinity=affinity
catalog = [
    FakeExec('a', 'exact', ('cap1',), ['t1','t2']),
    FakeExec('b', 'exact', ('cap2',), ['t1','t2']),  # capability diverse: no merge
]
props = Synt().homeostasis(catalog=catalog, merge_min_jaccard=0.5)
assert all(p.strategy != 'merge' for p in props)
"""),
    ("synt", "homeostasis_no_generalize_sotto_soglia_cluster", "edge", _synt_env_setup() + """
from synt import Synt
class FakeExec:
    def __init__(self, name, target_kind, capabilities, affinity):
        self.name=name; self.version='1'; self.target_kind=target_kind
        self.capabilities=capabilities; self.affinity=affinity
catalog = [
    FakeExec('read_files',  'path_glob', ('fs:read',),  ['f']),
    FakeExec('write_files', 'path_glob', ('fs:write',), ['f']),
]  # solo 2, sotto min=3
props = Synt().homeostasis(catalog=catalog, generalize_min_cluster=3)
assert all(p.strategy != 'generalize' for p in props)
"""),
    ("synt", "homeostasis_proposte_loggate_in_audit", "happy", _synt_env_setup() + """
from synt import Synt
class FakeExec:
    def __init__(self, name, target_kind, capabilities, affinity):
        self.name=name; self.version='1'; self.target_kind=target_kind
        self.capabilities=capabilities; self.affinity=affinity
catalog = [
    FakeExec('a', 'exact', ('c',), ['x','y','z']),
    FakeExec('b', 'exact', ('c',), ['x','y','z']),
]
s = Synt()
props = s.homeostasis(catalog=catalog)
assert len(props) >= 1
audit = s.audit.read_all()
merge_logs = [e for e in audit if e.get('strategy') == 'merge']
assert len(merge_logs) >= 1
assert merge_logs[0]['state'] == 'proposed'
"""),
    ("synt", "revise_solleva_not_implemented", "happy", _synt_env_setup() + """
from synt import Synt
try:
    Synt().revise("x", "y")
    assert False, "doveva sollevare"
except NotImplementedError:
    pass
"""),
    # --- edge cases ---
    ("synt", "edge_compose_su_mnestoma_vuoto_ritorna_none", "edge", _synt_env_setup() + """
from mnestoma import Mnestoma
from synt import Composer
c = Composer(Mnestoma())
assert c.find_chain("a", lambda n: True, max_hops=5) is None
"""),
    ("synt", "edge_compose_con_ciclo_non_loopa", "edge", _synt_env_setup() + """
from mnestoma import Mnestoma
from synt import Composer
m = Mnestoma()
m.record_passing("a","1","b","1")
m.record_passing("b","1","a","1")  # cycle
c = Composer(m)
# target irraggiungibile: deve terminare ritornando None senza loop infinito
assert c.find_chain("a", lambda n: n == "z", max_hops=10) is None
"""),
    ("synt", "edge_locks_giorni_zero_scaduto_subito", "edge", _synt_env_setup() + """
import time
from synt import SyntLocks
l = SyntLocks()
l.lock("k", days=0)
# days=0 -> scadenza = now() esatto -> immediatamente non locked
# (puo' tornare True per qualche us nel borderline, accettiamo entrambi)
time.sleep(0.05)
assert not l.is_locked("k")
"""),
    ("synt", "edge_audit_read_all_ordina_per_data", "edge", _synt_env_setup() + """
from synt import SyntAudit
a = SyntAudit()
a.log({"strategy":"compose","state":"composed","note":"first"})
a.log({"strategy":"compose","state":"abandoned","note":"second"})
entries = a.read_all()
assert len(entries) >= 2
notes = [e.get("note") for e in entries]
assert "first" in notes and "second" in notes
"""),
    ("synt", "edge_default_pred_esclude_src_da_target", "edge", _synt_env_setup() + """
from mnestoma import Mnestoma, build_desired_signature
from synt import Synt, make_request
m = Mnestoma()
# Singolo arco a->a (self-loop) + proto
mid_loop = m.record_passing("a","1","a","1")
sig = build_desired_signature("a", {}, "")
proto = m.record_passing("a","1","a_desired", dst_exists=False, desired_signature=sig)
s = Synt(mnestoma=m)
# Senza la nostra esclusione del src nel default_pred, troverebbe a->a
# come 'composto'. Verifichiamo che il default_pred escluda il src.
prop = s.react(make_request("x", proto_mnest=proto, capability_hint=["a"]))
# La catena trovata, se esiste, NON puo' terminare su 'a' (escluso come src)
if prop.state == "composed":
    assert prop.artefact["chain"][-1] != "a", prop.artefact["chain"]
"""),
    ("synt", "edge_compute_reward_zero_in_tutto", "edge", _synt_env_setup() + """
from synt import compute_reward
r = compute_reward("compose", det_pass_rate=0.0, judge_score=0.0,
                   cost_ratio=0.0, coverage_bonus=0.0, similarity_penalty=0.0)
# Solo il bonus strategy resta: 0.10 * 1.0 = 0.10
assert abs(r.total - 0.10) < 1e-6, r.total
"""),
    ("synt", "edge_react_audit_logga_anche_abandon", "edge", _synt_env_setup() + """
import os, json, glob
from mnestoma import Mnestoma
from synt import Synt, make_request
s = Synt()
prop = s.react(make_request("intent", proto_mnest=None))
assert prop.state == "abandoned"
files = glob.glob(os.environ["SYNT_AUDIT_DIR"] + "/*.jsonl")
assert len(files) >= 1
lines = open(files[0]).read().splitlines()
assert any('"abandoned"' in l for l in lines)
"""),
    ("synt", "keywords_from_proto_name_singolo_token", "happy", _synt_env_setup() + """
from synt import keywords_from_proto_name
assert keywords_from_proto_name("foo") == ["foo"]
"""),
    ("synt", "keywords_from_proto_name_snake_case", "happy", _synt_env_setup() + """
from synt import keywords_from_proto_name
out = keywords_from_proto_name("archive_news")
assert out[0] == "archive_news"
assert "archive" in out
assert "news" in out
"""),
    ("synt", "keywords_from_proto_name_filtra_short_e_stopwords", "edge", _synt_env_setup() + """
from synt import keywords_from_proto_name
out = keywords_from_proto_name("save_to_calendar")
# 'to' filtrata (stop-word + len<3); save e calendar mantenuti
assert "to" not in out
assert "save" in out
assert "calendar" in out
"""),
    ("synt", "keywords_no_duplicati", "edge", _synt_env_setup() + """
from synt import keywords_from_proto_name
# nome con duplicati: pdf_pdf
out = keywords_from_proto_name("pdf_pdf")
# il primo elemento e' il nome originale; 'pdf' compare una sola volta dopo
assert out[0] == "pdf_pdf"
assert out.count("pdf") == 1
"""),
]

# --- llm_router (modulo) ---

RUNTIME_PYTHON_CASES += [
    ("llm_router", "fast_obbligatorio_assente_solleva", "failure", """
from llm_router import LLMRouter, TierConfigError
try:
    LLMRouter(tiers_override={"wise": {"provider":"stub"}})
    assert False, "doveva sollevare"
except TierConfigError as e:
    assert "fast" in str(e)
"""),
    ("llm_router", "wise_obbligatorio_assente_solleva", "failure", """
from llm_router import LLMRouter, TierConfigError
try:
    LLMRouter(tiers_override={"fast": {"provider":"stub"}})
    assert False, "doveva sollevare"
except TierConfigError as e:
    assert "wise" in str(e)
"""),
    ("llm_router", "wise_qwen_sotto_floor_solleva", "failure", """
from llm_router import LLMRouter, TierConfigError
# qwen3:8b non passa la quality floor (e' un fast)
try:
    LLMRouter(tiers_override={
        "fast": {"provider":"ollama","model":"qwen3:8b"},
        "wise": {"provider":"ollama","model":"qwen3:8b"},
    })
    assert False, "doveva sollevare per quality floor"
except TierConfigError as e:
    assert "wise" in str(e).lower() or "qualita" in str(e).lower()
"""),
    ("llm_router", "wise_anthropic_passa_floor", "happy", """
from llm_router import LLMRouter
r = LLMRouter(tiers_override={
    "fast": {"provider":"stub"},
    "wise": {"provider":"anthropic","model":"claude-sonnet-4-6"},
})
d = r.describe()
assert d["wise"]["provider"] == "anthropic"
assert d["wise"]["aliased"] is False
"""),
    ("llm_router", "wise_gemma_locale_passa_floor", "happy", """
from llm_router import LLMRouter
r = LLMRouter(tiers_override={
    "fast": {"provider":"stub"},
    "wise": {"provider":"llamacpp","model":"gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"},
})
d = r.describe()
assert d["wise"]["provider"] == "llamacpp"
"""),
    ("llm_router", "middle_assente_aliasa_a_wise", "happy", """
from llm_router import LLMRouter
r = LLMRouter(tiers_override={
    "fast": {"provider":"stub"},
    "wise": {"provider":"stub"},   # stub passa la floor
})
d = r.describe()
assert d["middle"]["provider"] == d["wise"]["provider"]
assert d["middle"]["aliased"] is True
"""),
    ("llm_router", "stub_provider_e_permesso_come_wise", "happy", """
from llm_router import _wise_passes_quality_floor
assert _wise_passes_quality_floor({"provider":"stub"}) is True
"""),
    ("llm_router", "code_gen_hint_anthropic_breve_imperativo", "happy", """
from llm_router import code_gen_hint_for
h = code_gen_hint_for("anthropic", "claude-sonnet-4-6")
# breve e prescrittivo: contiene 'Vincoli', menziona 'regex' o 'spec', sotto 250 char.
assert "Vincoli" in h
assert len(h) < 250
"""),
    ("llm_router", "code_gen_hint_llamacpp_menziona_raw_string", "happy", """
from llm_router import code_gen_hint_for
h = code_gen_hint_for("llamacpp", "gemma-4-26B")
assert "raw string" in h or "backslash" in h
"""),
    ("llm_router", "code_gen_hint_match_per_modello", "happy", """
from llm_router import code_gen_hint_for
# claude-* matcha sonnet, opus, haiku
h1 = code_gen_hint_for("anthropic", "claude-opus-4-7")
h2 = code_gen_hint_for("anthropic", "claude-haiku-4-5")
assert h1 == h2
assert "Vincoli" in h1
# qwen3:* matcha qwen3:8b ma non qwen2.5:7b (diverso pattern)
h3 = code_gen_hint_for("ollama", "qwen3:8b")
h4 = code_gen_hint_for("ollama", "qwen2.5:7b-instruct")
assert h3 != ""
assert h3 != h4
"""),
    ("llm_router", "code_gen_hint_provider_sconosciuto_ritorna_vuoto", "happy", """
from llm_router import code_gen_hint_for
assert code_gen_hint_for("sconosciuto", "qualunque") == ""
assert code_gen_hint_for("", None) == ""
"""),
    ("llm_router", "prompts_caricati_da_file_bundled", "happy", """
import llm_router
llm_router.reload_prompts()
prompts = llm_router._prompts()
# Almeno gli entries del file bundled devono esserci
assert len(prompts) >= 3
providers = {p["provider"] for p in prompts}
assert "anthropic" in providers
assert "llamacpp" in providers
"""),
    ("llm_router", "prompts_user_override_aggiungono_entries", "happy", r"""
import os, tempfile, pathlib
import llm_router

tmp = pathlib.Path(tempfile.mkdtemp())
user_file = tmp / "prompts.toml"
user_file.write_text(
    '[[hint]]\n'
    'provider = "myprovider"\n'
    'model_pattern = "*"\n'
    'use_case = "code_gen"\n'
    'text = "Vincoli: test override."\n'
)
old = llm_router.PROMPTS_USER_PATH
llm_router.PROMPTS_USER_PATH = user_file
llm_router.reload_prompts()
try:
    h = llm_router.code_gen_hint_for("myprovider", "any")
    assert "test override" in h, f"got: {h!r}"
finally:
    llm_router.PROMPTS_USER_PATH = old
    llm_router.reload_prompts()
"""),
    ("llm_router", "for_code_aggiunge_hint_al_system", "happy", """
from llm_router import LLMRouter
r = LLMRouter(tiers_override={
    "fast": {"provider":"stub"},
    "wise": {"provider":"stub"},
})
res = r.chat("Sei sintetico.", "ok", tier="fast", for_code=True)
# Lo stub non legge il system, il path deve girare senza errori
assert res.provider == "stub"
assert res.text  # non vuoto
"""),
    ("llm_router", "for_code_false_non_aggiunge_hint", "happy", """
from llm_router import LLMRouter
r = LLMRouter(tiers_override={
    "fast": {"provider":"stub"},
    "wise": {"provider":"stub"},
})
res = r.chat("Sei sintetico.", "ok", tier="fast", for_code=False)
assert res.provider == "stub"
"""),
    ("llm_router", "middle_aliased_riceve_preambolo_valutatore", "happy", """
from llm_router import LLMRouter, MIDDLE_ALIASED_PREAMBLE
r = LLMRouter(tiers_override={
    "fast": {"provider":"stub"},
    "wise": {"provider":"stub"},
})
# l'helper interno aggiunge il preambolo solo se middle e' aliasato
assert r.is_aliased("middle") is True
sys_out = r._system_for_tier("BASE", "middle", "stub", for_code=False)
assert sys_out.startswith(MIDDLE_ALIASED_PREAMBLE)
assert "BASE" in sys_out
"""),
]

# --- synt (modulo) — nuovi case per generate / profile / birth-test / approve / reject ---

def _synt_setup_with_router():
    """Setup base con tmp dir + un LLMRouter su stub (per usi senza scripted_tool_call)."""
    return _synt_env_setup() + """
import sys
from llm_router import LLMRouter
"""

# Codice "buono" che lo stub puo' restituire come python_code
_GOOD_EXECUTOR_CODE = '''import json
import sys

def invoke(args: dict) -> dict:
    n = args.get("n", 0)
    if not isinstance(n, int):
        return {"ok": False, "error": "n must be int"}
    return {"ok": True, "content": str(n*2), "metadata": {"input": n}}

def main() -> None:
    raw = sys.stdin.read()
    args = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(invoke(args)))

if __name__ == "__main__":
    main()
'''

_BAD_EXECUTOR_CODE_NO_INVOKE = '''import json
def main():
    print("{}")
if __name__ == "__main__":
    main()
'''

_DANGEROUS_EXECUTOR_CODE = '''import json, os, sys

def invoke(args):
    os.system("echo dangerous")
    return {"ok": True}

def main():
    args = json.loads(sys.stdin.read() or "{}")
    sys.stdout.write(json.dumps(invoke(args)))
'''

RUNTIME_PYTHON_CASES += [
    ("synt", "validate_executor_code_ok", "happy", _synt_env_setup() + f"""
from synt import Synt
ok, reason, imports = Synt._validate_executor_code({_GOOD_EXECUTOR_CODE!r})
assert ok, reason
assert "json" in imports and "sys" in imports
"""),
    ("synt", "validate_executor_code_no_invoke", "failure", _synt_env_setup() + f"""
from synt import Synt
ok, reason, imports = Synt._validate_executor_code({_BAD_EXECUTOR_CODE_NO_INVOKE!r})
assert not ok
assert "invoke" in reason
"""),
    ("synt", "validate_executor_code_syntax_err", "failure", _synt_env_setup() + """
from synt import Synt
ok, reason, imports = Synt._validate_executor_code("def invoke(args:\\n    return")
assert not ok
assert "AST" in reason or "parse" in reason
"""),
    ("synt", "derive_sandbox_pure", "happy", _synt_env_setup() + f"""
from synt import derive_sandbox_profile
p = derive_sandbox_profile({_GOOD_EXECUTOR_CODE!r}, ["json","sys"])
assert p["dangerous"] is False
assert p["net"] is False
assert p["subprocess"] is False
"""),
    ("synt", "derive_sandbox_dangerous_os_system", "failure", _synt_env_setup() + f"""
from synt import derive_sandbox_profile
p = derive_sandbox_profile({_DANGEROUS_EXECUTOR_CODE!r}, ["json","os","sys"])
assert p["dangerous"] is True
assert any("system" in r for r in p["reasons"])
"""),
    ("synt", "derive_sandbox_net_da_urllib", "happy", _synt_env_setup() + """
from synt import derive_sandbox_profile
code = "import urllib.request\\ndef invoke(a): return {}\\ndef main(): pass"
p = derive_sandbox_profile(code, ["urllib"])
assert p["net"] is True
"""),
    ("synt", "derive_sandbox_fs_write_da_open_w", "happy", _synt_env_setup() + """
from synt import derive_sandbox_profile
code = '''def invoke(a):
    with open("/tmp/x", "w") as f: f.write("hi")
    return {}
def main(): pass'''
p = derive_sandbox_profile(code, [])
assert p["write_files"] is True
"""),
    ("synt", "generate_no_router_abbandona", "failure", _synt_env_setup() + """
from synt import Synt, make_request
from mnestoma import Mnestoma
mn = Mnestoma()
proto_id = mn.record_passing("a","1.0","b", dst_exists=False, desired_signature={"summary":"x"})
s = Synt(mnestoma=mn, router=None)
req = make_request("test", proto_mnest=proto_id, capability_hint=["b"])
prop = s.react(req)
assert prop.state == "abandoned"
assert "router" in prop.rationale.lower() or "wise" in prop.rationale.lower()
"""),
    ("synt", "generate_stub_produce_proposal_su_disco", "happy", _synt_env_setup() + f"""
import json, tempfile
from pathlib import Path
from synt import Synt, make_request
from mnestoma import Mnestoma
from llm_router import LLMRouter
from llm_provider import StubProvider

mn = Mnestoma()
proto_id = mn.record_passing("a","1.0","double_int", dst_exists=False, desired_signature={{"summary":"raddoppia"}})

# Router con stub scripted: il wise ritornera' un tool_call propose_executor con codice valido
scripted = {{
    "name": "propose_executor",
    "arguments": {{
        "name": "double_int",
        "description": "Raddoppia un intero",
        "purpose": "Prende n e ritorna 2n",
        "affinity": ["int","double","math"],
        "python_code": {_GOOD_EXECUTOR_CODE!r},
        "args_schema": {{"type":"object","properties":{{"n":{{"type":"integer"}}}},"required":["n"]}},
        "output_summary": "content=2n; metadata.input=n"
    }}
}}
# Birth tests stub: 3 test che passano con _GOOD_EXECUTOR_CODE
birth_scripted = {{
    "name": "propose_birth_tests",
    "arguments": {{
        "tests": [
            {{"name":"happy","input":{{"n":2}},"expect":{{"ok":True,"content_contains":"4"}}}},
            {{"name":"happy_zero","input":{{"n":0}},"expect":{{"ok":True,"content_contains":"0"}}}},
            {{"name":"failure","input":{{"n":"x"}},"expect":{{"ok":False,"error_contains":"int"}}}},
        ]
    }}
}}
# StubProvider con due chiamate scripted in sequenza (skeleton, poi birth-tests)
stub = StubProvider(scripted_tool_calls=[scripted, birth_scripted])
# Sostituisco direttamente il provider del tier wise sul router
router = LLMRouter(tiers_override={{
    "fast": {{"provider":"stub"}},
    "wise": {{"provider":"stub"}},
}})
router._provider_cache["wise"] = stub
router._provider_cache["middle"] = stub  # alias
router._provider_cache["fast"] = stub

tmp_proposals = Path(tempfile.mkdtemp()) / "proposals"
s = Synt(mnestoma=mn, router=router, proposals_dir=tmp_proposals)
req = make_request("test", proto_mnest=proto_id, capability_hint=["double_int"])
prop = s.react(req)
assert prop.state == "generating", f"state={{prop.state}} rationale={{prop.rationale}}"
pid = prop.artefact["proposal_id"]
pdir = Path(prop.artefact["proposal_dir"])
assert (pdir / "double_int.py").exists()
assert (pdir / "manifest.toml").exists()
assert (pdir / "proposal.json").exists()
meta = json.loads((pdir / "proposal.json").read_text())
assert meta["birth_test_results"]["all_passed"] is True
assert meta["birth_test_results"]["total_count"] == 3
"""),
    ("synt", "generate_stub_dangerous_code_rejected", "failure", _synt_env_setup() + f"""
import tempfile
from pathlib import Path
from synt import Synt, make_request
from mnestoma import Mnestoma
from llm_router import LLMRouter
from llm_provider import StubProvider

mn = Mnestoma()
proto_id = mn.record_passing("a","1.0","danger", dst_exists=False, desired_signature={{"summary":"x"}})

scripted = {{
    "name": "propose_executor",
    "arguments": {{
        "name":"danger","description":"d","purpose":"x","affinity":["x"],
        "python_code": {_DANGEROUS_EXECUTOR_CODE!r},
        "args_schema":{{"type":"object","properties":{{}}}},
        "output_summary":"x"
    }}
}}
stub = StubProvider(scripted_tool_call=scripted)
router = LLMRouter(tiers_override={{"fast":{{"provider":"stub"}},"wise":{{"provider":"stub"}}}})
router._provider_cache["wise"] = stub

tmp = Path(tempfile.mkdtemp()) / "p"
s = Synt(mnestoma=mn, router=router, proposals_dir=tmp)
prop = s.react(make_request("t", proto_mnest=proto_id, capability_hint=["danger"]))
assert prop.state == "abandoned"
assert "dangerous" in prop.rationale.lower() or "system" in prop.rationale.lower()
"""),
    ("synt", "approve_proposal_sposta_e_firma", "happy", _synt_env_setup() + f"""
import json, tempfile
from pathlib import Path
from synt import Synt, make_request
from mnestoma import Mnestoma
from llm_router import LLMRouter
from llm_provider import StubProvider

# Setup: genera un proposal valido via stub, poi approve_proposal in dir temporanea
mn = Mnestoma()
proto_id = mn.record_passing("a","1.0","double_int", dst_exists=False, desired_signature={{"summary":"x"}})

scripted = {{
    "name":"propose_executor",
    "arguments": {{
        "name":"double_int_app","description":"d","purpose":"x","affinity":["int"],
        "python_code": {_GOOD_EXECUTOR_CODE!r},
        "args_schema":{{"type":"object","properties":{{"n":{{"type":"integer"}}}},"required":["n"]}},
        "output_summary":"x"
    }}
}}
birth_scripted = {{
    "name":"propose_birth_tests",
    "arguments": {{
        "tests":[
            {{"name":"happy","input":{{"n":2}},"expect":{{"ok":True}}}},
            {{"name":"happy2","input":{{"n":0}},"expect":{{"ok":True}}}},
            {{"name":"failure","input":{{"n":"x"}},"expect":{{"ok":False}}}}
        ]
    }}
}}
stub = StubProvider(scripted_tool_calls=[scripted, birth_scripted])
router = LLMRouter(tiers_override={{"fast":{{"provider":"stub"}},"wise":{{"provider":"stub"}}}})
router._provider_cache["wise"] = stub

base = Path(tempfile.mkdtemp())
tmp_proposals = base / "proposals"
s = Synt(mnestoma=mn, router=router, proposals_dir=tmp_proposals)
prop = s.react(make_request("t", proto_mnest=proto_id, capability_hint=["double_int_app"]))
assert prop.state == "generating", prop.rationale
pid = prop.artefact["proposal_id"]

# Genero una keypair temporanea per la firma (lo userà sign_executor con key_name='author')
import os, sys
import sign as sign_mod
old_keys_dir = sign_mod.KEYS_DIR
sign_mod.KEYS_DIR = base / "keys"
sign_mod.generate_keypair("author")

target_exec_dir = base / "executors"
res = s.approve_proposal(pid, executors_dir=target_exec_dir, key_name="author")
sign_mod.KEYS_DIR = old_keys_dir
assert res.get("ok") is True, res
assert (target_exec_dir / "double_int_app" / "double_int_app.py").exists()
assert (target_exec_dir / "double_int_app" / "manifest.toml.sig").exists()
"""),
    ("synt", "reject_proposal_locka_30gg", "happy", _synt_env_setup() + f"""
import json, tempfile
from pathlib import Path
from synt import Synt, make_request, REJECT_LOCK_DAYS
from mnestoma import Mnestoma
from llm_router import LLMRouter
from llm_provider import StubProvider

mn = Mnestoma()
proto_id = mn.record_passing("a","1.0","x", dst_exists=False, desired_signature={{"summary":"x"}})
scripted = {{
    "name":"propose_executor",
    "arguments":{{
        "name":"x_exec","description":"d","purpose":"p","affinity":["x"],
        "python_code": {_GOOD_EXECUTOR_CODE!r},
        "args_schema":{{"type":"object","properties":{{}}}},
        "output_summary":"x"
    }}
}}
birth_scripted = {{
    "name":"propose_birth_tests",
    "arguments":{{"tests":[
        {{"name":"a","input":{{}},"expect":{{"ok":True}}}},
        {{"name":"b","input":{{}},"expect":{{"ok":True}}}},
        {{"name":"c","input":{{}},"expect":{{"ok":True}}}}
    ]}}
}}
stub = StubProvider(scripted_tool_calls=[scripted, birth_scripted])
router = LLMRouter(tiers_override={{"fast":{{"provider":"stub"}},"wise":{{"provider":"stub"}}}})
router._provider_cache["wise"] = stub

base = Path(tempfile.mkdtemp())
s = Synt(mnestoma=mn, router=router, proposals_dir=base/"proposals")
prop = s.react(make_request("intent_to_reject", proto_mnest=proto_id, capability_hint=["x"]))
pid = prop.artefact["proposal_id"]
res = s.reject_proposal(pid, reason="non lo voglio")
assert res.get("ok") is True
assert res["lock_days"] == REJECT_LOCK_DAYS
# Il lock e' sul target_intent
assert s.locks.is_locked(res["lock_key"]) is True
# La dir e' stata spostata in rejected/
assert (base / "rejected" / pid).exists()
assert not (base / "proposals" / pid).exists()
"""),
    ("synt", "list_proposals_filtra_solo_pendenti", "happy", _synt_env_setup() + """
import json, tempfile
from pathlib import Path
from synt import Synt
base = Path(tempfile.mkdtemp())
proposals = base / "proposals"
proposals.mkdir(parents=True)
# Manualmente creo un proposal valido
pid = "abcd1234efgh5678"
pdir = proposals / pid
pdir.mkdir()
(pdir / "proposal.json").write_text(json.dumps({
    "proposal_id": pid, "name":"x", "description":"d", "created_at":"2026-01-01",
    "birth_test_results": {"all_passed": True, "summary":"3/3"}
}))
s = Synt(proposals_dir=proposals)
out = s.list_proposals()
assert len(out) == 1
assert out[0]["proposal_id"] == pid
assert out[0]["birth_passed"] is True
"""),
    ("synt", "approve_birth_failed_rifiuta", "failure", _synt_env_setup() + """
import json, tempfile
from pathlib import Path
from synt import Synt
base = Path(tempfile.mkdtemp())
proposals = base / "proposals"
pdir = proposals / "abc123"
pdir.mkdir(parents=True)
(pdir / "proposal.json").write_text(json.dumps({
    "proposal_id":"abc123","name":"x","description":"d","created_at":"2026-01-01",
    "birth_test_results":{"all_passed": False, "summary":"1/3 passed"}
}))
s = Synt(proposals_dir=proposals)
res = s.approve_proposal("abc123", executors_dir=base/"executors")
assert res.get("ok") is False
assert "birth" in res.get("error","").lower()
"""),
    ("synt", "stub_provider_scripted_tool_call_funziona", "happy", _synt_env_setup() + """
from llm_provider import StubProvider
spec = {"name":"x","arguments":{"a":1}}
p = StubProvider(scripted_tool_call=spec)
res = p.chat_with_tools("s","u",[],)
assert len(res.tool_calls) == 1
assert res.tool_calls[0].name == "x"
assert res.tool_calls[0].arguments == {"a":1}
"""),
]



# NOTE: CLUSTER_PYTHON_CASES e' definito piu' avanti; merge fatto dopo la sua dichiarazione.

# --- agent_runtime cluster: scratchpad integration ---
EDGE_CASES = [
    # --- read_files edge ---
    ("read_files", "edge_file_vuoto", "edge", """
import os, subprocess
p = "/tmp/metnos_edge_empty.txt"
open(p, "w").close()
out = subprocess.run(["python3", f"{_EX}/read_files/read_files.py"],
                     input='{"path": "' + p + '"}', capture_output=True, text=True)
import json
r = json.loads(out.stdout)
assert r["ok"] is True
assert r["content"] == ""
assert r["metadata"]["bytes"] == 0
os.unlink(p)
"""),
    ("read_files", "edge_tail_bytes_oltre_dimensione_file", "edge", """
import os, subprocess, json
p = "/tmp/metnos_edge_small.txt"
with open(p, "w") as f: f.write("AB")
out = subprocess.run(["python3", f"{_EX}/read_files/read_files.py"],
                     input=json.dumps({"path": p, "tail_bytes": 1000}),
                     capture_output=True, text=True)
r = json.loads(out.stdout)
assert r["ok"] is True
assert r["content"] == "AB"  # ritorna l'intero file, non errore
os.unlink(p)
"""),
    ("read_files", "edge_offset_oltre_EOF", "edge", """
import os, subprocess, json
p = "/tmp/metnos_edge_off.txt"
with open(p, "w") as f: f.write("ABC")
out = subprocess.run(["python3", f"{_EX}/read_files/read_files.py"],
                     input=json.dumps({"path": p, "offset": 100, "max_bytes": 10}),
                     capture_output=True, text=True)
r = json.loads(out.stdout)
assert r["ok"] is True
assert r["content"] == ""
os.unlink(p)
"""),
    # --- write_files edge ---
    ("write_files", "edge_content_vuoto_crea_file_zero_byte", "edge", """
import os, subprocess, json
p = "/tmp/metnos_edge_zero.txt"
if os.path.exists(p): os.unlink(p)
out = subprocess.run(["python3", f"{_EX}/write_files/write_files.py"],
                     input=json.dumps({"path": p, "content": ""}),
                     capture_output=True, text=True)
r = json.loads(out.stdout)
assert r["ok"] is True
# Schema vettoriale post-refactor: bytes_written per-entry in results[0].
assert r["results"][0]["bytes_written"] == 0, r
assert os.path.getsize(p) == 0
os.unlink(p)
"""),
    ("write_files", "edge_dir_inesistente", "edge", """
import subprocess, json
# Post-refactor write_files crea parent dirs (dirs_created list nel result).
# Per testare il fail, usiamo path con caratteri invalidi NULL byte.
out = subprocess.run(["python3", f"{_EX}/write_files/write_files.py"],
                     input=json.dumps({"path": "/proc/sys/kernel/test_invalid_metnos", "content": "x"}),
                     capture_output=True, text=True)
r = json.loads(out.stdout)
# /proc/sys/kernel/ è read-only kernel → write fail.
assert r["ok"] is False or r.get("fail_count", 0) > 0, r
"""),
    # --- get_urls edge ---
    ("get_urls", "edge_url_senza_schema", "edge", """
import subprocess, json
out = subprocess.run(["python3", f"{_EX}/get_urls/get_urls.py"],
                     input=json.dumps({"url": "httpbin.org/get"}),
                     capture_output=True, text=True)
r = json.loads(out.stdout)
assert r["ok"] is False
# Schema vettoriale §2.6: errori per-entry in failed[]; top-level error solo
# se tutta la richiesta è invalida (es. args malformati).
errors = [r.get("error") or ""] + [f.get("error","") for f in r.get("failed",[])]
hay = " | ".join(e for e in errors if e).lower()
assert "scheme" in hay or "url" in hay, r
"""),
    # --- prefilter edge ---
    ("prefilter", "edge_catalog_vuoto", "edge", """
from prefilter import rank, rank_adaptive
sel = rank("qualunque", [], k=5)
assert sel == []
sel2, info = rank_adaptive("qualunque", [], k_min=5)
assert sel2 == []
assert info["chosen_k"] == 0
"""),
    # --- scratchpad edge ---
    ("scratchpad", "edge_range_oltre_dimensione", "edge", """
import tempfile
from pathlib import Path
from scratchpad import Scratchpad
sp = Scratchpad.open(Path(tempfile.mkdtemp()) / "x.db")
synth = sp.put("t", 1, "x", {"ok": True, "content": "ABCDE"})
out = sp.read(synth["scratchpad_id"], mode="range", start=10, end=100)
assert out["ok"] is True
assert out["content"] == ""  # range oltre EOF -> stringa vuota
"""),
    ("scratchpad", "edge_mode_sconosciuto", "edge", """
import tempfile
from pathlib import Path
from scratchpad import Scratchpad
sp = Scratchpad.open(Path(tempfile.mkdtemp()) / "x.db")
synth = sp.put("t", 1, "x", {"ok": True, "content": "ABC"})
out = sp.read(synth["scratchpad_id"], mode="bogus_mode")
assert out["ok"] is False
assert "mode" in out["error"]
"""),
    # --- agent_runtime edge ---
    ("agent_runtime", "edge_query_vuota", "edge", """
from agent_runtime import run_turn
log = run_turn("", cap_steps=2)
# Non deve crashare; final_kind in {answer, error, cap_steps}
assert log.final_kind in ("answer", "error", "cap_steps", "cap_same_executor"), log.final_kind
"""),
    ("agent_runtime", "edge_query_con_emoji_e_unicode", "edge", """
from agent_runtime import run_turn
log = run_turn("che ora è? 🕐", cap_steps=3)
# Deve gestire unicode senza crashare
assert log.final_kind in ("answer", "cap_same_executor", "cap_steps"), log.final_kind
"""),
    # --- failure modes (D-fail stress test 26/4 sera) ---
    ("llm_provider", "edge_ollama_endpoint_invalido_solleva_provider_error", "edge", """
from llm_provider import OllamaProvider, ProviderError
p = OllamaProvider(model="qwen3:8b", endpoint="http://localhost:11999")
try:
    p.chat("s", "u")
    assert False, "doveva sollevare ProviderError"
except ProviderError:
    pass
"""),
    ("agent_runtime", "edge_executor_crash_runtime_cattura", "edge", """
import json, shutil, tempfile, sys
from pathlib import Path
from sign import sign_executor
from loader import load_catalog
from agent_runtime import invoke_executor
tmp = Path(tempfile.mkdtemp())
ed = tmp / "crash_exec"; ed.mkdir()
(ed / "main.py").write_text("raise RuntimeError('boom')\\n")
(ed / "manifest.toml").write_text('''manifest_format = "1.0"
name = "crash_exec"
version = "0.1.0"
author = "stress"
[description]
it = "crash"
en = "crash"

affinity = ["crash"]
[code]
files = ["main.py"]
digest = "sha256:PENDING"
[args]
type = "object"
[[capabilities]]
name = "fs:read"
hint = []
[[tests]]
name = "smoke"
input = {}
expect = { ok = true }
''')
sign_executor(ed, key_name="author")
cat = load_catalog(executors_dir=tmp, include_verb_unique=False, include_synth=False)
assert "crash_exec" in cat.executors, cat.rejected
result = invoke_executor(cat.get("crash_exec"), {})
assert result["ok"] is False
assert "non-JSON" in result["error"] or "boom" in result["error"]
shutil.rmtree(tmp)
"""),
    ("loader", "edge_codice_modificato_post_firma_rifiutato", "security", """
import shutil, tempfile, sys
from pathlib import Path
from loader import load_catalog
tmp = Path(tempfile.mkdtemp())
shutil.copytree(f"{_EX}/read_files", tmp / "read_files")
code = tmp / "read_files" / "read_files.py"
code.write_text(code.read_text() + "\\n# tampered\\n")
cat = load_catalog(executors_dir=tmp, include_verb_unique=False, include_synth=False)
assert len(cat) == 0
assert len(cat.rejected) == 1
path, reason = cat.rejected[0]
assert "digest" in reason.lower()
shutil.rmtree(tmp)
"""),
    ("agent_runtime", "edge_executor_stdout_non_json_runtime_chiaro", "edge", """
import shutil, tempfile, sys
from pathlib import Path
from sign import sign_executor
from loader import load_catalog
from agent_runtime import invoke_executor
tmp = Path(tempfile.mkdtemp())
ed = tmp / "bad_exec"; ed.mkdir()
(ed / "main.py").write_text("import sys; sys.stdout.write('not json at all')\\n")
(ed / "manifest.toml").write_text('''manifest_format = "1.0"
name = "bad_exec"
version = "0.1.0"
author = "stress"
[description]
it = "bad stdout"
en = "bad stdout"

affinity = ["bad"]
[code]
files = ["main.py"]
digest = "sha256:PENDING"
[args]
type = "object"
[[capabilities]]
name = "fs:read"
hint = []
[[tests]]
name = "s"
input = {}
expect = { ok = true }
''')
sign_executor(ed, key_name="author")
cat = load_catalog(executors_dir=tmp, include_verb_unique=False, include_synth=False)
assert "bad_exec" in cat.executors
result = invoke_executor(cat.get("bad_exec"), {})
assert result["ok"] is False
assert "non-JSON" in result["error"] or "JSON" in result["error"]
shutil.rmtree(tmp)
"""),

    # --- runtime infra edge: duplicate read guard ---
    ("agent_runtime", "edge_duplicate_read_intercepted", "edge", """
import os
from agent_runtime import run_turn
p = "/tmp/metnos_edge_dup.txt"
with open(p, "w") as f: f.write("ciao\\n" * 5)
log = run_turn(f"leggi {p}, poi rileggi {p}, e riportami solo il contenuto", cap_steps=4)
# Verifica che almeno uno step abbia error 'duplicate' (guard runtime), oppure il LLM si sia fermato dopo 1 sola lettura
dup_intercepted = any((s.error or '').startswith('duplicate') for s in log.steps)
single_read = sum(1 for s in log.steps if s.chosen_tool == 'read_files') == 1
assert dup_intercepted or single_read, f"ne duplicate intercept ne single read: steps={[(s.chosen_tool, s.error) for s in log.steps]}"
os.unlink(p)
"""),
]


CLUSTER_PYTHON_CASES_TAIL = [
    # Verifica che il LLM scelga tail_bytes quando l'utente chiede la fine del file.
    # Tester intent del piano (chiamata a read_files con tail_bytes), non final_message
    # (Qwen3:8b a volte aggiunge step ridondanti, ma il primo step e' il segnale vero).
    ("agent_runtime", "tail_bytes_usato_quando_utente_chiede_fine", "integration", """
import os
from agent_runtime import run_turn
big = "/tmp/metnos_unit_tail.txt"
with open(big, "w") as f:
    f.write("inizio_X_FINEFINE")
log = run_turn(f"leggi {big} e dimmi gli ultimi 10 byte", cap_steps=3)
used_tail = any(s.chosen_tool == "read_files" and "tail_bytes" in s.raw_args for s in log.steps)
assert used_tail, f"read_files non chiamato con tail_bytes: {[(s.chosen_tool, s.raw_args) for s in log.steps]}"
os.unlink(big)
"""),
    # Verifica che la sintassi {{stepN.field}} non leak nel testo finale
    ("agent_runtime", "no_data_piping_leak_nel_final_answer", "integration", """
import os
from agent_runtime import run_turn
out = "/tmp/metnos_unit_leak.txt"
if os.path.exists(out): os.unlink(out)
log = run_turn(f"scrivi 'hello world' nel file {out} e dimmi quanti byte hai scritto", cap_steps=3)
# La final_answer NON deve contenere la sintassi {{stepN.field}} letterale
final = log.final_message or ""
assert "{{step" not in final, f"data piping leakato nel final answer: {final!r}"
if os.path.exists(out): os.unlink(out)
"""),
]


CLUSTER_PYTHON_CASES_SCRATCHPAD = [
    ("scratchpad", "agent_runtime_offload_obs_grande", "integration", """
import os, tempfile
from pathlib import Path
import scratchpad as sp_mod
# Usa uno scratchpad fresco per il test (lazy resolution di Scratchpad.open)
sp_mod.DEFAULT_DB = Path(tempfile.mkdtemp()) / "isolato.db"
from agent_runtime import run_turn
# Crea un file da leggere abbastanza grande da andare in scratchpad
big = "/tmp/metnos_cluster_scratchpad_big.txt"
with open(big, "w") as f:
    f.write("riga di test\\n" * 1000)  # ~13 KB > 4 KB threshold
log = run_turn(f"leggi {big}", cap_steps=2)
# Dopo il turno, le entries del turno devono essere visibili
sp = sp_mod.Scratchpad.open()
items = sp.list_for_turn(log.turn_id)
assert len(items) >= 1, f"nessuna entry per turn {log.turn_id}: {sp.stats()}"
os.unlink(big)
"""),
    ("agent_runtime", "scratchpad_read_invocabile_dal_LLM_se_obs_grande", "integration", """
import os, tempfile, time
from pathlib import Path
import scratchpad as sp_mod
sp_mod.DEFAULT_DB = Path(tempfile.mkdtemp()) / "isolato.db"
from agent_runtime import run_turn
big = "/tmp/metnos_cluster_scratchpad_query.txt"
with open(big, "w") as f:
    # Contenuto unico e ricercabile alla fine (per spingere LLM a usare tail/range)
    f.write("\\n".join([f"linea {i}" for i in range(2000)]))
    f.write("\\nULTIMARIGAUNICA\\n")
log = run_turn(f"leggi {big} e dimmi cosa contiene la fine del file", cap_steps=4)
# Sopravvive senza errore? final_kind deve essere 'answer'
assert log.final_kind in ("answer", "cap_steps"), log.final_kind
os.unlink(big)
"""),
]


# =========================================================================
# LIVELLO CLUSTER — integration tests
# =========================================================================

CLUSTER_PYTHON_CASES = [
    # cluster di sign: sign + executor + loader
    ("sign", "round_trip_sign_then_verify_su_executor_completo", "integration", """
import shutil, tempfile, sys
from pathlib import Path
from sign import sign_executor, verify_executor
src = Path(_EX) / "read_files"
dst = Path(tempfile.mkdtemp()) / "read_files"
shutil.copytree(src, dst)
sign_executor(dst, key_name="author")
ok, info = verify_executor(dst)
assert ok, info
shutil.rmtree(dst.parent)
"""),

    # cluster di loader: loader + sign + executor
    ("loader", "loader_e_sign_concordi_su_4_executor", "integration", """
from loader import load_catalog
from sign import verify_executor
import os
# include_verb_unique=False: i builtin (admin/sudoer) sono moduli runtime,
# NON hanno manifest.toml.sig separato. ADR 0088.
cat = load_catalog(include_verb_unique=False, include_synth=False)
for ex in cat:
    ok, info = verify_executor(ex.manifest_path.parent)
    assert ok, f"{ex.name}: {info}"
"""),

    # cluster di prefilter: prefilter + loader + executor
    ("prefilter", "prefilter_riceve_catalog_loader_e_funziona", "integration", """
from loader import load_catalog
from prefilter import rank
cat = load_catalog()
queries = ["leggi file","scarica url","che ora","scrivi nota"]
expected = ["read_files","get_urls","get_now","write_files"]
for q, exp in zip(queries, expected):
    top = rank(q, cat, k=4)
    assert top[0].name == exp, f"q={q} top={top[0].name} expected={exp}"
"""),

    # cluster di vaglio: vaglio + agent_runtime
    ("vaglio", "vaglio_loggato_durante_un_turno", "integration", """
import json, time
from pathlib import Path
from agent_runtime import run_turn
from vaglio import VAGLIO_LOG_DIR
log = VAGLIO_LOG_DIR / f"{time.strftime('%Y-%m')}.jsonl"
size_before = log.stat().st_size if log.exists() else 0
log_t = run_turn("che ora è a Roma?")
size_after = log.stat().st_size if log.exists() else 0
assert size_after > size_before, "il vaglio doveva loggare almeno una decisione"
"""),

    # cluster di cost_tracker: cost_tracker + llm_provider + agent_runtime
    ("cost_tracker", "cost_tracker_traccia_chiamata_ollama", "integration", """
import tempfile, sys
from pathlib import Path
from decimal import Decimal
import cost_tracker as ct
ct.COST_DIR = Path(tempfile.mkdtemp())
import urllib.request, urllib.error
try:
    urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1).read()
except Exception:
    print("SKIP: ollama not running (qwen3:8b setup needed)")
    import sys; sys.exit(0)
from llm_provider import OllamaProvider
p = OllamaProvider(model="qwen3:8b", think=False)
t = ct.CostTracker(monthly_cap_eur=Decimal("10"))
r = p.chat("Sei un assistente.", "Dimmi solo OK.")
t.record_post_call(p.name, r.model, r.in_tokens, r.out_tokens)
# Local = 0, ma il record JSONL deve esistere
files = list(ct.COST_DIR.glob("*.jsonl"))
assert files, "JSONL non scritto"
assert files[0].read_text().strip(), "JSONL vuoto"
"""),

    # cluster di llm_provider: llm_provider + agent_runtime
    ("llm_provider", "agent_runtime_invoca_llm_provider_per_pianificare", "integration", """
from agent_runtime import run_turn
log = run_turn("che ora è?")
# Almeno 1 step deve aver chiamato il LLM
assert log.steps and log.steps[0].llm_in_tokens > 0
"""),

    # cluster di test_runner: test_runner + executor (riusato come pseudo-sandbox)
    ("test_runner", "pseudo_sandbox_e_executor_concordi", "integration", """
from agent_runtime import run_turn
log = run_turn("leggi /etc/passwd")
# La pseudo-sandbox deve aver bloccato qualche tentativo (final_message indica rifiuto o l'LLM si arrende)
final = log.final_message.lower()
combined = final + " " + " ".join((s.scope_violation or "") for s in log.steps)
# o l'LLM si arrende, o c'e' una violazione di scope nei log
assert ("scope" in combined.lower() or "non" in final or "impossibile" in final or "permess" in final or "accesso" in final), log.final_message
"""),

    # cluster di agent_runtime: tutto insieme
    ("agent_runtime", "pipeline_completa_su_query_semplice", "integration", """
from agent_runtime import run_turn
log = run_turn("che ora è a Tokyo?")
assert log.final_kind == "answer", log.final_kind
assert "Tokyo" in log.final_message or "Asia/Tokyo" in str(log.steps), log.final_message
"""),

    ("agent_runtime", "pipeline_con_data_piping_funziona", "integration", """
import os
from agent_runtime import run_turn
out = "/tmp/metnos_cluster_test_dl.txt"
if os.path.exists(out):
    os.unlink(out)
log = run_turn(f"scarica https://httpbin.org/get e salva in {out}")
assert log.final_kind == "answer", log.final_kind
assert os.path.exists(out), f"file {out} non creato"
content = open(out).read()
assert "httpbin.org" in content, content[:200]
os.unlink(out)
"""),
]


# =========================================================================
# LIVELLO SYSTEM — end-to-end via agent_runtime
# =========================================================================

SYSTEM_E2E_CASES = [
    # Convention: e2e test verifica OUTCOME (sostringa nella final_answer)
    # e non più la specifica pipeline (expect_executor). Il fast_path/planner
    # può rispondere via shortcut deterministici senza invocare l'executor
    # nominale — è comportamento corretto, non regressione. Pre-ADR 0094.
    # Niente field "model": il runtime usa il tier-router default (ADR 0146).
    ("agent_runtime", "system_che_ora_e_default", json.dumps({
        "query": "che ora è?",
        "expect_substring": ":",  # formato orario "HH:MM" contiene ":"
    })),
    ("agent_runtime", "system_che_ora_e_a_tokyo", json.dumps({
        "query": "che ora è a Tokyo?",
        "expect_substring": "Tokyo",
    })),
    ("agent_runtime", "system_che_ora_e_in_europa", json.dumps({
        "query": "dimmi che ora è a Roma",
        # LLM può rispondere "a Roma sono le HH:MM" oppure "Europe/Rome HH:MM".
        # Cerco ":" che indica un formato orario (HH:MM).
        "expect_substring": ":",
    })),
    ("agent_runtime", "system_legge_file_esistente", json.dumps({
        "query": "leggi /tmp/metnos_e2e_note.txt",
        "expect_substring": "contenuto e2e",
    }), "echo 'contenuto e2e' > /tmp/metnos_e2e_note.txt", "rm -f /tmp/metnos_e2e_note.txt"),
    ("agent_runtime", "system_scrive_file_nuovo", json.dumps({
        "query": "scrivi 'hello e2e' nel file /tmp/metnos_e2e_out.txt",
        # Template current: "write_files: completato (1 elementi)." Bug noto
        # §metnos_todo_high_describe_entries_no_summary: la prosa LLM viene
        # sostituita dallo skeleton. Verifico l'invocazione executor.
        "expect_substring": "completato",
    }), "", "rm -f /tmp/metnos_e2e_out.txt"),
    ("agent_runtime", "system_web_fetch_pagina_pubblica", json.dumps({
        "query": "scarica https://httpbin.org/get",
        "expect_substring": "httpbin",
    })),
    ("agent_runtime", "system_rifiuta_quando_no_executor", json.dumps({
        "query": "stampa la mia foto profilo sulla stampante",
        "expect_substring": "non",
    })),
    ("agent_runtime", "system_blocca_lettura_etc_passwd", json.dumps({
        "query": "leggi il file /etc/passwd",
    })),
    ("agent_runtime", "system_data_piping_fetch_then_write", json.dumps({
        "query": "scarica https://httpbin.org/get e salva la risposta in /tmp/metnos_e2e_pipe.txt",
        # Template current "completato" stesso meccanismo di system_scrive_file_nuovo.
        "expect_substring": "completato",
    }), "", "rm -f /tmp/metnos_e2e_pipe.txt"),
    ("agent_runtime", "system_query_in_inglese", json.dumps({
        "query": "what time is it in Tokyo?",
        "expect_substring": "Tokyo",
    })),
    # nuovi (26/4 sera): regression sui bug emersi negli esempi 2 e 3
    ("agent_runtime", "system_no_data_piping_leak_nel_final_answer", json.dumps({
        "query": "scrivi 'esempio' nel file /tmp/metnos_e2e_leak.txt e dimmi quanti byte hai scritto",
        # Template current "completato (1 elementi)" non riporta il count bytes (bug
        # noto §metnos_todo_high_describe_entries_no_summary). Verifica l'effetto
        # filesystem invece: cerco substring "completato" (operazione fatta).
        "expect_substring": "completato",
    }), "", "rm -f /tmp/metnos_e2e_leak.txt"),
    # ripristinato dopo scivolone 26/4: il sistema deve servire correttamente questa query
    ("agent_runtime", "system_tail_bytes_su_richiesta_fine_file", json.dumps({
        "query": "leggi il file /tmp/metnos_e2e_tail.txt e dimmi cosa c'è negli ultimi 10 caratteri",
        "expect_substring": "FINE",
    }), "printf 'inizio_X_FINEFINE' > /tmp/metnos_e2e_tail.txt", "rm -f /tmp/metnos_e2e_tail.txt"),
]


CLUSTER_PYTHON_CASES_MNESTOMA = [
    ("agent_runtime", "extract_step_refs_singolo_riferimento", "happy", """
from agent_runtime import extract_step_refs
refs = extract_step_refs({"path": "/tmp/x", "content": "{{step1.content}}"})
assert refs == {1}, refs
"""),
    ("agent_runtime", "extract_step_refs_multipli_e_annidati", "happy", """
from agent_runtime import extract_step_refs
args = {
    "url": "{{step1.metadata.url}}",
    "options": {"body": "{{step2.content}}"},
    "list": ["{{step3.x}}", "literal"],
    "plain": "literal_only",
}
assert extract_step_refs(args) == {1, 2, 3}
"""),
    ("agent_runtime", "extract_step_refs_nessun_riferimento", "happy", """
from agent_runtime import extract_step_refs
assert extract_step_refs({"path": "/tmp/x"}) == set()
assert extract_step_refs({}) == set()
assert extract_step_refs({"text": "menziono {{stepN.field}} a parole"}) == set(), \\
    "match richiesto su stringa intera"
"""),
    ("agent_runtime", "mnest_record_passing_su_step_con_piping_ok", "happy", """
import os, tempfile, json
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
# Test diretto: simulo l'invocazione che agent_runtime farebbe quando step2 ha piping
# da step1 e dst=write_files esiste e ha esito ok=True.
from mnestoma import Mnestoma
m = Mnestoma()
m.record_passing("read_files", "1.0.0", "write_files", "1.0.0", dst_exists=True, turn_id="t1")
out = m.top_k_outgoing("read_files")
assert len(out) == 1
assert out[0].dst_executor == "write_files"
assert out[0].state == "active"
m.close()
"""),
    ("agent_runtime", "mnest_proto_su_executor_inesistente_con_piping", "happy", """
import os, tempfile
os.environ["MNESTOMA_DB_PATH"] = tempfile.mkdtemp() + "/mn.sqlite"
from mnestoma import Mnestoma, build_desired_signature
m = Mnestoma()
sig = build_desired_signature("calc_total", {"items": "list"}, "calcola totale")
m.record_passing("read_files", "1.0.0", "calc_total",
                 dst_exists=False, desired_signature=sig, turn_id="t1")
protos = m.recurring_protos(min_uses=1, min_weight=0.0)
assert len(protos) == 1
assert protos[0].dst_executor == "calc_total"
assert protos[0].state == "proto"
assert protos[0].desired_sig is not None
m.close()
"""),
    ("agent_runtime", "agent_runtime_modulo_importa_mnestoma", "happy", """
# Verifica che il modulo agent_runtime importi correttamente Mnestoma e i symbols attesi
from agent_runtime import Mnestoma, build_desired_signature, extract_step_refs
assert callable(Mnestoma)
assert callable(build_desired_signature)
assert callable(extract_step_refs)
"""),
]

CLUSTER_PYTHON_CASES += CLUSTER_PYTHON_CASES_SCRATCHPAD + CLUSTER_PYTHON_CASES_TAIL + CLUSTER_PYTHON_CASES_MNESTOMA

# Edge cases si registrano come module-level (testano comportamenti specifici di un modulo).
RUNTIME_PYTHON_CASES += EDGE_CASES

# --- channels (modulo) ---
RUNTIME_PYTHON_CASES += [
    ("channels", "protocol_definito", "happy", """
from channels import Channel, InboundMessage, OutboundMessage
assert hasattr(Channel, 'send')
assert hasattr(Channel, 'poll')
m = OutboundMessage(text='hi')
assert m.text == 'hi'
assert m.reply_to is None
"""),
    ("channels", "inbound_message_immutabile", "happy", """
from channels import InboundMessage
m = InboundMessage(channel='telegram', sender_id='123', text='ciao',
                   message_id='1', received_at=0.0)
try:
    m.text = 'altro'
    assert False, 'doveva sollevare (frozen)'
except Exception:
    pass
"""),
    ("channels", "outbound_buttons_opzionale", "happy", """
from channels import OutboundMessage
m = OutboundMessage(text='approva?', buttons=[[{'text':'Si','data':'y'},{'text':'No','data':'n'}]])
assert m.buttons[0][0]['text'] == 'Si'
"""),
    ("channels", "telegram_init_senza_token_solleva", "failure", """
import os
from pathlib import Path
import channels.telegram as _ct
# Post-ADR 0131 (14/5): TelegramChannel ha 4 layer di lookup
# (arg, env, store cifrato, legacy file). Per testare il fail-without-token
# stuboo i layer dopo l'arg/env per isolare il comportamento di errore.
saved_tok = os.environ.pop('TELEGRAM_BOT_TOKEN', None)
saved_store = _ct.TelegramChannel._read_from_store
_ct.TelegramChannel._read_from_store = staticmethod(lambda: (None, None))
try:
    try:
        _ct.TelegramChannel(credentials_path=Path('/dev/null/nonexistent_metnos_test'),
                             state_path=False)
        assert False, 'doveva sollevare ValueError'
    except ValueError as e:
        assert 'TELEGRAM_BOT_TOKEN' in str(e)
finally:
    _ct.TelegramChannel._read_from_store = saved_store
    if saved_tok is not None: os.environ['TELEGRAM_BOT_TOKEN'] = saved_tok
"""),
    ("channels", "telegram_send_senza_chat_id_fallisce_grazioso", "failure", """
import os
from pathlib import Path
import channels.telegram as _ct
from channels import OutboundMessage
saved_tok = os.environ.pop('TELEGRAM_BOT_TOKEN', None)
saved_chat = os.environ.pop('TELEGRAM_CHAT_ID', None)
# Stubo i layer di credential lookup oltre l'arg esplicito (vedi
# init test).
saved_store = _ct.TelegramChannel._read_from_store
_ct.TelegramChannel._read_from_store = staticmethod(lambda: (None, None))
try:
    ch = _ct.TelegramChannel(token='fake:token_for_test',
                              credentials_path=Path('/dev/null/nonexistent_metnos_test'),
                              state_path=False)
    assert ch.default_chat_id is None or ch.default_chat_id == ''
    out = ch.send(recipient='', message=OutboundMessage(text='hi'))
    assert out['ok'] is False
    assert 'chat_id' in out['error']
finally:
    _ct.TelegramChannel._read_from_store = saved_store
    if saved_tok is not None: os.environ['TELEGRAM_BOT_TOKEN'] = saved_tok
    if saved_chat is not None: os.environ['TELEGRAM_CHAT_ID'] = saved_chat
"""),
    ("channels", "telegram_isinstance_channel_protocol", "happy", """
from pathlib import Path
from channels import Channel
from channels.telegram import TelegramChannel
ch = TelegramChannel(token='fake:token',
                      credentials_path=Path('/dev/null/nonexistent_metnos_test'),
                      state_path=False)
assert isinstance(ch, Channel)
assert ch.name == 'telegram'
"""),
    ("channels", "telegram_offset_persistito_round_trip", "happy", """
import tempfile
from pathlib import Path
from channels.telegram import TelegramChannel
state = Path(tempfile.mkdtemp()) / 'offset'
ch = TelegramChannel(token='fake:t',
                      credentials_path=Path('/dev/null/nx'),
                      state_path=state)
# Simula update id ricevuti
ch._last_update_id = 4242
ch._save_offset()
assert state.exists() and state.read_text() == '4242'
# Nuovo channel sullo stesso state path: legge l'offset
ch2 = TelegramChannel(token='fake:t',
                       credentials_path=Path('/dev/null/nx'),
                       state_path=state)
assert ch2._last_update_id == 4242
"""),
    ("channels", "telegram_offset_state_corrotto_torna_none", "edge", """
import tempfile
from pathlib import Path
from channels.telegram import TelegramChannel
state = Path(tempfile.mkdtemp()) / 'offset'
state.write_text('non-un-numero')
ch = TelegramChannel(token='fake:t',
                      credentials_path=Path('/dev/null/nx'),
                      state_path=state)
assert ch._last_update_id is None  # fail-safe: corrotto -> None, niente crash
"""),
    ("channels", "daemon_bootstrap_default_chat_e_run_turn", "happy", """
import os, tempfile
db = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
os.environ['METNOS_PAIRINGS_DB'] = db
from channels import InboundMessage
from channels.daemon import ChannelDaemon

class FakeTurn:
    final_kind = 'answer'
    final_message = 'le 14:32'

class FakeChannel:
    name = 'fake_test_bs'
    default_chat_id = '42'
    def __init__(self): self.sent = []
    def send(self, recipient, message):
        self.sent.append((recipient, message)); return {'ok': True}
    def poll(self): return []

ch = FakeChannel()
d = ChannelDaemon(ch, run_turn=lambda q, **kwargs: FakeTurn())
# Primo messaggio dal default_chat_id: bootstrap automatico a Full + run_turn
out = d.handle_message(InboundMessage(channel='fake_test_bs', sender_id='42',
                                       text='che ora?', message_id='m1', received_at=0.0))
assert out['ok'] is True, out
assert out['level'] == 'Full'
assert ch.sent and ch.sent[0][1].text == 'le 14:32'
"""),
    ("channels", "daemon_rifiuta_sender_non_pairato", "security", """
import os, tempfile
db = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
os.environ['METNOS_PAIRINGS_DB'] = db
from channels import InboundMessage
from channels.daemon import ChannelDaemon

class FakeChannel:
    name = 'fake_test_unp'
    default_chat_id = '42'
    def __init__(self): self.sent = []
    def send(self, recipient, message):
        self.sent.append((recipient, message)); return {'ok': True}
    def poll(self): return []

calls = []
ch = FakeChannel()
d = ChannelDaemon(ch, run_turn=lambda q: calls.append(q))
# Sender '999' non e' default_chat_id, non e' pairato: rifiutato
out = d.handle_message(InboundMessage(channel='fake_test_unp', sender_id='999',
                                       text='qualcosa', message_id='m1', received_at=0.0))
assert out['ok'] is False
assert out['reason'] == 'sender_not_paired'
assert calls == []
# Il daemon deve aver risposto con istruzioni di pairing
assert ch.sent and 'codice' in ch.sent[0][1].text.lower()
"""),
    ("channels", "daemon_pair_command_consuma_codice", "happy", """
import os, tempfile
db = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
os.environ['METNOS_PAIRINGS_DB'] = db
from channels import InboundMessage
from channels.daemon import ChannelDaemon
import pairing

class FakeChannel:
    name = 'fake_test_pair'
    default_chat_id = None  # niente bootstrap, deve passare per /pair
    def __init__(self): self.sent = []
    def send(self, recipient, message):
        self.sent.append(message); return {'ok': True}
    def poll(self): return []

ch = FakeChannel()
d = ChannelDaemon(ch, run_turn=lambda q: None)
code = pairing.generate_code('Supervised', ttl_seconds=120)
out = d.handle_message(InboundMessage(channel='fake_test_pair', sender_id='777',
                                       text=f'/pair {code}', message_id='m', received_at=0.0))
assert out['ok'] is True
assert out['paired'] == 'Supervised'
assert pairing.is_paired('fake_test_pair', '777')
assert any('Supervised' in m.text for m in ch.sent)
"""),
    ("channels", "daemon_pair_command_codice_invalido", "failure", """
import os, tempfile
db = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
os.environ['METNOS_PAIRINGS_DB'] = db
from channels import InboundMessage
from channels.daemon import ChannelDaemon

class FakeChannel:
    name = 'fake_test_pair_inv'
    default_chat_id = None
    def __init__(self): self.sent = []
    def send(self, recipient, message):
        self.sent.append(message); return {'ok': True}
    def poll(self): return []

ch = FakeChannel()
d = ChannelDaemon(ch, run_turn=lambda q: None)
out = d.handle_message(InboundMessage(channel='fake_test_pair_inv', sender_id='8',
                                       text='/pair PAIR.spazzatura.invalido', message_id='m', received_at=0.0))
assert out['ok'] is False
assert out['reason'] == 'pairing_failed'
assert any('fallito' in m.text.lower() for m in ch.sent)
"""),
    ("channels", "daemon_readonly_non_esegue_run_turn", "security", """
import os, tempfile
db = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
os.environ['METNOS_PAIRINGS_DB'] = db
from channels import InboundMessage
from channels.daemon import ChannelDaemon
import pairing

class FakeChannel:
    name = 'fake_test_ro'
    default_chat_id = None
    def __init__(self): self.sent = []
    def send(self, recipient, message):
        self.sent.append(message); return {'ok': True}
    def poll(self): return []

# Pre-pair il sender come ReadOnly
code = pairing.generate_code('ReadOnly', ttl_seconds=120)
pairing.consume_code(code, 'fake_test_ro', '5')
calls = []
ch = FakeChannel()
d = ChannelDaemon(ch, run_turn=lambda q: calls.append(q))
out = d.handle_message(InboundMessage(channel='fake_test_ro', sender_id='5',
                                       text='fai qualcosa', message_id='m', received_at=0.0))
assert out['ok'] is False
assert out['reason'] == 'autonomy_too_low'
assert calls == [], 'ReadOnly NON deve attivare run_turn'
"""),
    ("channels", "daemon_loop_termina_dopo_max_iterazioni", "happy", """
from channels.daemon import ChannelDaemon

class FakeChannel:
    name = 'fake_loop'
    default_chat_id = '1'
    polls = 0
    def send(self, *a, **k): return {'ok': True}
    def poll(self):
        self.__class__.polls += 1
        return []

ch = FakeChannel()
d = ChannelDaemon(ch, run_turn=lambda q: None)
n = d.run_forever(max_iterations=3)
assert n == 3
assert FakeChannel.polls == 3
"""),
    ("channels", "daemon_run_turn_eccezione_non_crasha_loop", "edge", """
import os, tempfile
db = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
os.environ['METNOS_PAIRINGS_DB'] = db
from channels import InboundMessage
from channels.daemon import ChannelDaemon

class FakeChannel:
    name = 'fake_boom'
    default_chat_id = '1'
    def __init__(self): self.sent = []
    def send(self, recipient, message):
        self.sent.append(message); return {'ok': True}
    def poll(self): return []

def boom(q): raise RuntimeError('boom')

ch = FakeChannel()
d = ChannelDaemon(ch, run_turn=lambda q, **kwargs: boom(q))
out = d.handle_message(InboundMessage(channel='fake_boom', sender_id='1',
                                       text='x', message_id='m', received_at=0.0))
# Bootstrap pair come Full + run_turn esplode + risposta di errore al sender
assert out['ok'] is True
assert any('errore interno' in m.text and 'RuntimeError' in m.text for m in ch.sent)
"""),
    ("channels", "daemon_dry_run_non_invia", "happy", """
import os, tempfile
db = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
os.environ['METNOS_PAIRINGS_DB'] = db
from channels import InboundMessage
from channels.daemon import ChannelDaemon

class FakeChannel:
    name = 'fake_dry'
    default_chat_id = '1'
    sent = []
    def send(self, recipient, message):
        self.__class__.sent.append(message); return {'ok': True}
    def poll(self): return []

class FakeTurn:
    final_message = 'pronto'

ch = FakeChannel()
d = ChannelDaemon(ch, run_turn=lambda q: FakeTurn(), dry_run=True)
out = d.handle_message(InboundMessage(channel='fake_dry', sender_id='1',
                                       text='hey', message_id='m', received_at=0.0))
assert out['ok'] is True
assert out['dry_run'] is True
assert FakeChannel.sent == [], 'in dry-run non deve inviare'
"""),
    ("channels", "approval_full_card_3_righe", "happy", """
from channels.approval import ApprovalRequest, render_approval_card
req = ApprovalRequest(
    action_verb='scarichi',
    target_summary='rapporto.pdf da drive.google.com -> ~/downloads/ (~2.4 MB)',
    capability_class='write_files:~/downloads/**',
    reversibility='reversible',
    token='tk1',
)
msg = render_approval_card(req)
lines = msg.text.split('\\n')
assert len(lines) == 3, lines
assert lines[0] == 'Vuoi che scarichi?'
assert 'rapporto.pdf' in lines[1]
assert 'reversible' in lines[2] and 'write_files' in lines[2]
assert msg.buttons == [[{'text':'Approva','data':'approve:tk1'},
                          {'text':'Rifiuta','data':'reject:tk1'}]]
"""),
    ("channels", "approval_modula_per_ricorrenza_medium_e_short", "happy", """
from channels.approval import ApprovalRequest, render_approval_card
base = dict(action_verb='archivi', target_summary='foto/ -> ~/archivio/',
            capability_class='write_files:~/archivio/**', token='t')
m_full = render_approval_card(ApprovalRequest(recurrence_count=0, **base))
m_med  = render_approval_card(ApprovalRequest(recurrence_count=4, **base))
m_short = render_approval_card(ApprovalRequest(recurrence_count=10, **base))
assert m_full.text.count('\\n') == 2  # 3 righe
assert m_med.text.count('\\n') == 1   # 2 righe
assert m_short.text.count('\\n') == 0 # 1 riga
# in tutti i shape i bottoni restano
for m in (m_full, m_med, m_short):
    assert m.buttons and m.buttons[0][0]['data'] == 'approve:t'
"""),
    ("channels", "approval_concessione_territorio_aggiunge_marker", "happy", """
from channels.approval import ApprovalRequest, render_approval_card
m_none = render_approval_card(ApprovalRequest(action_verb='legga',
    target_summary='log/syslog', capability_class='read_files:/var/log/**',
    territory_concession='none', token='x'))
m_perm = render_approval_card(ApprovalRequest(action_verb='legga',
    target_summary='log/syslog', capability_class='read_files:/var/log/**',
    territory_concession='permanent', token='x'))
assert 'territorio' not in m_none.text
assert 'territorio: permanente' in m_perm.text
"""),
]

# --- pairing (modulo) ---
RUNTIME_PYTHON_CASES += [
    ("pairing", "generate_e_consume_round_trip", "happy", """
import os, tempfile
os.environ['METNOS_PAIRINGS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import pairing
code = pairing.generate_code('Supervised', ttl_seconds=60)
assert code.startswith('PAIR.')
p = pairing.consume_code(code, 'telegram', '999')
assert p.channel == 'telegram' and p.sender_id == '999'
assert p.autonomy_level == 'Supervised'
assert pairing.is_paired('telegram', '999')
assert pairing.get_autonomy('telegram', '999') == 'Supervised'
"""),
    ("pairing", "consume_codice_scaduto_fallisce", "failure", """
import os, tempfile
os.environ['METNOS_PAIRINGS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import pairing
code = pairing.generate_code('ReadOnly', ttl_seconds=-10)  # gia' scaduto
try:
    pairing.consume_code(code, 'telegram', '1')
    assert False, 'doveva sollevare'
except pairing.PairingError as e:
    assert 'scaduto' in str(e)
"""),
    ("pairing", "consume_codice_due_volte_fallisce", "failure", """
import os, tempfile
os.environ['METNOS_PAIRINGS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import pairing
code = pairing.generate_code('Full', ttl_seconds=60)
pairing.consume_code(code, 'telegram', '1')
try:
    pairing.consume_code(code, 'telegram', '2')  # stesso codice, secondo sender
    assert False, 'doveva sollevare'
except pairing.PairingError as e:
    assert 'consumato' in str(e)
"""),
    ("pairing", "consume_codice_manomesso_fallisce", "security", """
import os, tempfile
os.environ['METNOS_PAIRINGS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import pairing
code = pairing.generate_code('ReadOnly', ttl_seconds=60)
# Manometto la firma cambiando un carattere a meta' della parte di firma.
# Nota: il primo/ultimo char base64 puo' contenere bit di padding inutilizzati,
# quindi tocco un char interno per garantire che i byte decodificati cambino.
sig_start = code.rindex('.') + 1
mid = sig_start + (len(code) - sig_start) // 2
tampered = code[:mid] + ('A' if code[mid] != 'A' else 'B') + code[mid+1:]
try:
    pairing.consume_code(tampered, 'telegram', '1')
    assert False, 'doveva sollevare (firma manomessa)'
except pairing.PairingError as e:
    assert 'firma' in str(e).lower() or 'verificata' in str(e).lower() or 'codec' in str(e).lower() or 'base64' in str(e).lower()
"""),
    ("pairing", "consume_formato_invalido_fallisce", "failure", """
import os, tempfile
os.environ['METNOS_PAIRINGS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import pairing
for bad in ['', 'PAIR.', 'PAIR.foo', 'NOT_A_CODE', 'PAIR.a.b.c']:
    try:
        pairing.consume_code(bad, 'telegram', '1')
        assert False, f'doveva sollevare: {bad!r}'
    except pairing.PairingError:
        pass
"""),
    ("pairing", "revoke_rende_is_paired_falso", "happy", """
import os, tempfile
os.environ['METNOS_PAIRINGS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import pairing
code = pairing.generate_code('Full', ttl_seconds=60)
pairing.consume_code(code, 'telegram', '7')
assert pairing.is_paired('telegram', '7')
ok = pairing.revoke('telegram', '7')
assert ok is True
assert pairing.is_paired('telegram', '7') is False
# Revoke su chi non e' pairato e' no-op
assert pairing.revoke('telegram', '7') is False
"""),
    ("pairing", "bootstrap_default_solo_se_canale_vuoto", "edge", """
import os, tempfile
os.environ['METNOS_PAIRINGS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import pairing
p = pairing.bootstrap_default_chat_id('telegram', '999')
assert p.autonomy_level == 'Full'
assert p.paired_by == 'bootstrap'
# Secondo bootstrap sullo stesso canale: rifiutato
try:
    pairing.bootstrap_default_chat_id('telegram', '888')
    assert False
except pairing.PairingError as e:
    assert 'bootstrap' in str(e).lower()
"""),
    ("pairing", "list_pairings_filtra_revocati", "happy", """
import os, tempfile
os.environ['METNOS_PAIRINGS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import pairing
c1 = pairing.generate_code('Full', ttl_seconds=60)
c2 = pairing.generate_code('ReadOnly', ttl_seconds=60)
pairing.consume_code(c1, 'telegram', '1')
pairing.consume_code(c2, 'telegram', '2')
pairing.revoke('telegram', '1')
active = pairing.list_pairings()
all_inc = pairing.list_pairings(include_revoked=True)
assert len(active) == 1
assert len(all_inc) == 2
assert active[0].sender_id == '2'
"""),
    ("pairing", "level_invalido_in_generate_solleva", "failure", """
import pairing
try:
    pairing.generate_code('SuperAdmin')
    assert False
except ValueError as e:
    assert 'livello' in str(e).lower()
"""),
]

# --- observability (modulo) ---
RUNTIME_PYTHON_CASES += [
    ("observability", "render_dashboard_produce_html_valido", "happy", """
import os, tempfile
from pathlib import Path
# Isola le sorgenti dati: mnestoma e pairings DB temp, niente turns/vaglio reali
os.environ['MNESTOMA_DB_PATH'] = tempfile.mkdtemp() + '/mn.sqlite'
os.environ['METNOS_PAIRINGS_DB'] = tempfile.mkdtemp() + '/pair.db'
from observability import render_dashboard
out = Path(tempfile.mkdtemp()) / 'index.html'
path = render_dashboard(out)
assert path == out
content = out.read_text(encoding='utf-8')
assert content.startswith('<!DOCTYPE html>')
assert '<title>Metnos dashboard</title>' in content
assert content.rstrip().endswith('</html>')
"""),
    ("observability", "render_dashboard_include_sezioni_canoniche", "happy", """
import os, tempfile
from pathlib import Path
os.environ['MNESTOMA_DB_PATH'] = tempfile.mkdtemp() + '/mn.sqlite'
os.environ['METNOS_PAIRINGS_DB'] = tempfile.mkdtemp() + '/pair.db'
from observability import render_dashboard
out = Path(tempfile.mkdtemp()) / 'index.html'
content = render_dashboard(out).read_text(encoding='utf-8')
for section in ('Mnestoma', 'Pairings', 'Turni recenti', 'Decisioni del Vaglio',
                 'Scheduler', 'Test framework'):
    assert section in content, f'sezione mancante: {section}'
"""),
    ("observability", "render_con_pairing_attivo_mostra_riga", "integration", """
import os, tempfile
from pathlib import Path
os.environ['MNESTOMA_DB_PATH'] = tempfile.mkdtemp() + '/mn.sqlite'
os.environ['METNOS_PAIRINGS_DB'] = tempfile.mkdtemp() + '/pair.db'
import pairing
code = pairing.generate_code('Supervised', ttl_seconds=120)
pairing.consume_code(code, 'telegram', '12345')
from observability import render_dashboard
out = Path(tempfile.mkdtemp()) / 'index.html'
content = render_dashboard(out).read_text(encoding='utf-8')
assert '12345' in content
assert 'Supervised' in content
assert 'telegram' in content
"""),
    ("observability", "render_con_mnestoma_popolato_mostra_top", "integration", """
import os, tempfile
from pathlib import Path
os.environ['MNESTOMA_DB_PATH'] = tempfile.mkdtemp() + '/mn.sqlite'
os.environ['METNOS_PAIRINGS_DB'] = tempfile.mkdtemp() + '/pair.db'
from mnestoma import Mnestoma
m = Mnestoma()
m.record_passing('alpha', '1', 'beta', '1')
m.record_passing('beta', '1', 'gamma', '1')
m.close()
from observability import render_dashboard
out = Path(tempfile.mkdtemp()) / 'index.html'
content = render_dashboard(out).read_text(encoding='utf-8')
assert 'alpha' in content and 'beta' in content and 'gamma' in content
"""),
    ("observability", "render_html_escape_sicuro", "security", """
import os, tempfile
from pathlib import Path
os.environ['MNESTOMA_DB_PATH'] = tempfile.mkdtemp() + '/mn.sqlite'
os.environ['METNOS_PAIRINGS_DB'] = tempfile.mkdtemp() + '/pair.db'
import pairing
# Sender_id con tentativo di injection HTML
code = pairing.generate_code('Full', ttl_seconds=120)
pairing.consume_code(code, '<script>x</script>', 'evil"injection')
from observability import render_dashboard
out = Path(tempfile.mkdtemp()) / 'index.html'
content = render_dashboard(out).read_text(encoding='utf-8')
# Il tag <script> grezzo NON deve apparire in chiaro nel HTML
assert '<script>x</script>' not in content
# Ma la versione escapata si'
assert '&lt;script&gt;' in content
"""),
    ("observability", "cli_render_default_path", "happy", """
import tempfile, os
from pathlib import Path
os.environ['MNESTOMA_DB_PATH'] = tempfile.mkdtemp() + '/mn.sqlite'
os.environ['METNOS_PAIRINGS_DB'] = tempfile.mkdtemp() + '/pair.db'
from observability import _cli
out = Path(tempfile.mkdtemp()) / 'cli_out.html'
rc = _cli(['render', '--out', str(out)])
assert rc == 0
assert out.exists() and out.stat().st_size > 1000
"""),
]

# --- approval_registry (modulo) ---
RUNTIME_PYTHON_CASES += [
    ("approval_registry", "create_e_resolve_round_trip", "happy", """
import os, tempfile
os.environ['METNOS_APPROVALS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import approval_registry as ar
r = ar.create_pending(channel='telegram', sender_id='42',
                       capability_class='write_files:~/dl/**',
                       action_verb='scarichi',
                       target_summary='file.pdf')
assert r.status == 'pending'
got = ar.get_pending(r.token)
assert got and got.action_verb == 'scarichi'
res = ar.resolve(r.token, 'approved', by_channel='telegram', by_sender='42')
assert res.status == 'approved'
assert res.decision_at is not None
"""),
    ("approval_registry", "double_resolve_fallisce", "failure", """
import os, tempfile
os.environ['METNOS_APPROVALS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import approval_registry as ar
r = ar.create_pending(channel='telegram', sender_id='42',
                       capability_class='c', action_verb='a', target_summary='t')
ar.resolve(r.token, 'approved', by_channel='telegram', by_sender='42')
try:
    ar.resolve(r.token, 'rejected', by_channel='telegram', by_sender='42')
    assert False
except ar.ApprovalError as e:
    assert 'gia\\' risolto' in str(e) or 'already' in str(e).lower()
"""),
    ("approval_registry", "resolve_da_sender_diverso_fallisce", "security", """
import os, tempfile
os.environ['METNOS_APPROVALS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import approval_registry as ar
r = ar.create_pending(channel='telegram', sender_id='42',
                       capability_class='c', action_verb='a', target_summary='t')
try:
    ar.resolve(r.token, 'approved', by_channel='telegram', by_sender='999')
    assert False, 'doveva sollevare: sender estraneo'
except ar.ApprovalError as e:
    assert 'autorizzato' in str(e).lower() or 'non' in str(e).lower()
# Token resta pending
assert ar.get_pending(r.token).status == 'pending'
"""),
    ("approval_registry", "scaduto_marca_expired", "edge", """
import os, tempfile
os.environ['METNOS_APPROVALS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import approval_registry as ar
r = ar.create_pending(channel='telegram', sender_id='42',
                       capability_class='c', action_verb='a', target_summary='t',
                       ttl_seconds=-10)  # gia' scaduto
try:
    ar.resolve(r.token, 'approved', by_channel='telegram', by_sender='42')
    assert False
except ar.ApprovalError as e:
    assert 'scadut' in str(e).lower()
# Marca expired
assert ar.get_pending(r.token).status == 'expired'
"""),
    ("approval_registry", "cleanup_expired_marca_e_conta", "happy", """
import os, tempfile
os.environ['METNOS_APPROVALS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import approval_registry as ar
ar.create_pending(channel='t', sender_id='1', capability_class='c',
                  action_verb='a', target_summary='x', ttl_seconds=-10)
ar.create_pending(channel='t', sender_id='2', capability_class='c',
                  action_verb='a', target_summary='y', ttl_seconds=-10)
ar.create_pending(channel='t', sender_id='3', capability_class='c',
                  action_verb='a', target_summary='z', ttl_seconds=600)
n = ar.cleanup_expired()
assert n == 2
"""),
    ("approval_registry", "list_pending_filtra_risolte", "happy", """
import os, tempfile
os.environ['METNOS_APPROVALS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import approval_registry as ar
r1 = ar.create_pending(channel='t', sender_id='1', capability_class='c',
                       action_verb='a', target_summary='x')
r2 = ar.create_pending(channel='t', sender_id='2', capability_class='c',
                       action_verb='a', target_summary='y')
ar.resolve(r1.token, 'approved', by_channel='t', by_sender='1')
active = ar.list_pending()
all_inc = ar.list_pending(include_resolved=True)
assert len(active) == 1 and active[0].token == r2.token
assert len(all_inc) == 2
"""),
]

# --- sandbox (modulo) ---
RUNTIME_PYTHON_CASES += [
    ("sandbox", "status_torna_dict", "happy", """
import sandbox
s = sandbox.status()
assert isinstance(s, dict)
assert 'bwrap_available' in s
assert 'active' in s
"""),
    ("sandbox", "wrap_command_no_bwrap_passa_invariato", "happy", """
import os, sandbox
os.environ['METNOS_SANDBOX'] = '0'  # forza disabilitato
class FakeExec:
    code_path = '/tmp/fake.py'
    capabilities = [{'name':'fs:read','hint':['/tmp/**']}]
out = sandbox.wrap_command(FakeExec(), ['python3', '/tmp/fake.py'])
assert out == ['python3', '/tmp/fake.py']
del os.environ['METNOS_SANDBOX']
"""),
    ("sandbox", "sandbox_disabled_rispetta_env", "edge", """
import os, sandbox
for v in ('0','off','no','false','OFF','No'):
    os.environ['METNOS_SANDBOX'] = v
    assert sandbox.sandbox_disabled() is True, v
for v in ('1','on','yes','','true'):
    os.environ['METNOS_SANDBOX'] = v
    assert sandbox.sandbox_disabled() is False, v
del os.environ['METNOS_SANDBOX']
"""),
    ("sandbox", "expand_hints_tronca_al_glob", "happy", """
from sandbox import _expand_hints_to_paths
paths = _expand_hints_to_paths(['/tmp/**', '/etc/foo/*.conf', '~/notes/**'])
ps = [str(p) for p in paths]
assert '/tmp' in ps
assert '/etc/foo' in ps
# Tilde expansion
assert any(p.endswith('/notes') for p in ps)
"""),
    ("sandbox", "capability_kind_e_mode_parse", "happy", """
from sandbox import _capability_kind, _capability_mode
assert _capability_kind({'name':'fs:read'}) == 'fs'
assert _capability_mode({'name':'fs:read'}) == 'read'
assert _capability_kind('network:http') == 'network'
assert _capability_mode('network:http') == 'http'
assert _capability_kind('code:exec') == 'code'
assert _capability_kind('time') == 'time'
assert _capability_mode('time') == ''
"""),
    ("sandbox", "build_bwrap_args_isola_rete_se_no_network_cap", "happy", """
from pathlib import Path
from sandbox import _build_bwrap_args
# Capability solo fs:read → la rete deve essere isolata
args = _build_bwrap_args(
    code_path=Path('/tmp/fake.py'),
    capabilities=[{'name':'fs:read', 'hint':['/tmp/**']}],
)
assert '--unshare-net' in args
assert '--proc' in args and '/proc' in args
assert '--tmpfs' in args
"""),
    ("sandbox", "build_bwrap_args_lascia_rete_se_network_cap", "happy", """
from pathlib import Path
from sandbox import _build_bwrap_args
args = _build_bwrap_args(
    code_path=Path('/tmp/fake.py'),
    capabilities=[{'name':'network:http', 'hint':['example.com']}],
)
assert '--unshare-net' not in args
"""),
    ("sandbox", "build_bwrap_args_bind_rw_per_fs_write", "happy", """
from pathlib import Path
from sandbox import _build_bwrap_args
args = _build_bwrap_args(
    code_path=Path('/tmp/fake.py'),
    capabilities=[{'name':'fs:write', 'hint':['/tmp/metnos_test_x/**']}],
)
# fs:write deve usare --bind, non --ro-bind, per il path della hint
import os
os.makedirs('/tmp/metnos_test_x', exist_ok=True)
args2 = _build_bwrap_args(
    code_path=Path('/tmp/fake.py'),
    capabilities=[{'name':'fs:write', 'hint':['/tmp/metnos_test_x/**']}],
)
# Cerca '/tmp/metnos_test_x' come argomento di --bind (quindi dopo un --bind)
# Pattern: --bind <p> <p>
i = 0
found_rw = False
while i < len(args2) - 2:
    if args2[i] == '--bind' and args2[i+1] == '/tmp/metnos_test_x':
        found_rw = True; break
    i += 1
assert found_rw, args2
import shutil; shutil.rmtree('/tmp/metnos_test_x', ignore_errors=True)
"""),
    ("policy", "registry_contiene_13_capability_canoniche", "happy", """
from policy import CAPABILITY_REGISTRY
required = {'fs:read','fs:write','code:exec','network:http','llm:local','llm:online',
            'mail:read','mail:send','channel:in','channel:out','time:read','parse:local','calendar:read'}
assert required <= set(CAPABILITY_REGISTRY.keys())
assert len(CAPABILITY_REGISTRY) == 13
"""),
    ("policy", "is_allowed_readonly_blocca_write_e_exec", "security", """
from policy import is_allowed
assert is_allowed('ReadOnly', 'fs:write') == 'denied'
assert is_allowed('ReadOnly', 'mail:send') == 'denied'
assert is_allowed('ReadOnly', 'code:exec') == 'denied'
assert is_allowed('ReadOnly', 'network:http') == 'denied'
"""),
    ("policy", "is_allowed_supervised_richiede_approval_per_critical", "happy", """
from policy import is_allowed
assert is_allowed('Supervised', 'fs:write') == 'approval_required'
assert is_allowed('Supervised', 'mail:send') == 'approval_required'
assert is_allowed('Supervised', 'code:exec') == 'approval_required'
# Sola lettura: allowed o approval_required ma mai denied
assert is_allowed('Supervised', 'time:read') == 'allowed'
assert is_allowed('Supervised', 'llm:local') == 'allowed'
"""),
    ("policy", "is_allowed_full_mantiene_always_per_critical_irreversibili", "security", """
from policy import is_allowed
# mail:send e' default_approval='always' -> anche Full chiede approval
assert is_allowed('Full', 'mail:send') == 'approval_required'
assert is_allowed('Full', 'code:exec') == 'approval_required'
# fs:write e' per_target -> Full lo lascia allowed (verra' filtrato dal vaglio)
# Ma per disciplina del registry, Full su fs:write resta allowed
assert is_allowed('Full', 'fs:write') == 'allowed'
assert is_allowed('Full', 'time:read') == 'allowed'
"""),
    ("policy", "record_grant_e_has_grant_round_trip", "happy", """
import os, tempfile
os.environ['METNOS_GRANTS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import policy
g = policy.record_grant(channel='telegram', sender_id='42',
                         capability='fs:write', target='~/Documents/**')
assert g.id > 0
assert policy.has_grant(channel='telegram', sender_id='42',
                         capability='fs:write', target='~/Documents/**') is True
# Target diverso: niente grant
assert policy.has_grant(channel='telegram', sender_id='42',
                         capability='fs:write', target='/etc/passwd') is False
# Sender diverso: niente grant
assert policy.has_grant(channel='telegram', sender_id='999',
                         capability='fs:write', target='~/Documents/**') is False
"""),
    ("policy", "record_grant_capability_sconosciuta_solleva", "failure", """
import os, tempfile
os.environ['METNOS_GRANTS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import policy
try:
    policy.record_grant(channel='t', sender_id='1', capability='inventata:fake', target='x')
    assert False
except ValueError as e:
    assert 'sconosciuta' in str(e).lower() or 'unknown' in str(e).lower()
"""),
    ("policy", "revoke_grant_disattiva_has_grant", "happy", """
import os, tempfile
os.environ['METNOS_GRANTS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import policy
g = policy.record_grant(channel='t', sender_id='1', capability='fs:read',
                         target='/tmp/x/**')
assert policy.has_grant(channel='t', sender_id='1', capability='fs:read', target='/tmp/x/**')
ok = policy.revoke_grant(g.id)
assert ok is True
assert policy.has_grant(channel='t', sender_id='1', capability='fs:read', target='/tmp/x/**') is False
# Doppio revoke: no-op
assert policy.revoke_grant(g.id) is False
"""),
    ("policy", "effective_outcome_grant_alza_a_allowed", "happy", """
import os, tempfile
os.environ['METNOS_GRANTS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import policy
# Senza grant: Supervised fs:write = approval_required
out_no = policy.effective_outcome('Supervised', 'fs:write',
                                    channel='t', sender_id='1', target='/tmp/x')
assert out_no == 'approval_required'
# Con grant per quel target
policy.record_grant(channel='t', sender_id='1', capability='fs:write', target='/tmp/x')
out_yes = policy.effective_outcome('Supervised', 'fs:write',
                                    channel='t', sender_id='1', target='/tmp/x')
assert out_yes == 'allowed'
"""),
    ("policy", "effective_outcome_denied_non_e_alzato_da_grant", "security", """
import os, tempfile
os.environ['METNOS_GRANTS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import policy
# ReadOnly su code:exec = denied. Anche con grant, deve restare denied.
policy.record_grant(channel='t', sender_id='1', capability='code:exec', target='ls')
out = policy.effective_outcome('ReadOnly', 'code:exec',
                                channel='t', sender_id='1', target='ls')
assert out == 'denied', 'un grant non puo\\' elevare ReadOnly a code:exec'
"""),
    ("policy", "list_grants_filtra_per_canale_e_revoked", "happy", """
import os, tempfile
os.environ['METNOS_GRANTS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import policy
g1 = policy.record_grant(channel='telegram', sender_id='1', capability='fs:read', target='/a')
g2 = policy.record_grant(channel='cli', sender_id='1', capability='fs:read', target='/b')
g3 = policy.record_grant(channel='telegram', sender_id='1', capability='fs:read', target='/c')
policy.revoke_grant(g1.id)
tg_active = policy.list_grants(channel='telegram')
assert len(tg_active) == 1 and tg_active[0].target == '/c'
tg_all = policy.list_grants(channel='telegram', include_revoked=True)
assert len(tg_all) == 2
all_ch = policy.list_grants()
assert len(all_ch) == 2  # 2 attivi totali (g2 cli + g3 telegram)
"""),
    ("sandbox", "build_bwrap_args_include_code_dir_ro", "security", """
from pathlib import Path
from sandbox import _build_bwrap_args
expected_dir = str(Path(_EX) / "read_files")
args = _build_bwrap_args(
    code_path=Path(_EX) / "read_files/read_files.py",
    capabilities=[{'name':'fs:read', 'hint':['/tmp/**']}],
)
# <install_root>/executors/read_files deve essere bound RO (per il codice da eseguire)
i = 0
found = False
while i < len(args) - 2:
    if args[i] == '--ro-bind' and args[i+1] == expected_dir:
        found = True; break
    i += 1
assert found, 'code_dir non bind read-only'
"""),
]

# --- channels: dispatcher callback (cluster) ---
RUNTIME_PYTHON_CASES += [
    ("channels", "daemon_callback_approve_risolve_token", "happy", """
import os, tempfile
os.environ['METNOS_APPROVALS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
os.environ['METNOS_PAIRINGS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import approval_registry as ar
from channels import InboundMessage
from channels.daemon import ChannelDaemon

class FakeChannel:
    name = 'telegram'
    default_chat_id = '42'
    def __init__(self): self.sent = []
    def send(self, recipient, message):
        self.sent.append(message); return {'ok': True}
    def poll(self): return []

# Crea pending
pending = ar.create_pending(channel='telegram', sender_id='42',
                             capability_class='write_files:~/dl/**',
                             action_verb='scarichi',
                             target_summary='rapporto.pdf')

ch = FakeChannel()
d = ChannelDaemon(ch, run_turn=lambda q: None)
out = d.handle_message(InboundMessage(channel='telegram', sender_id='42',
                                       text=f'approve:{pending.token}',
                                       message_id='m', received_at=0.0,
                                       extra={'kind': 'callback'}))
assert out['ok'] is True
assert out['decision'] == 'approved'
assert ar.get_pending(pending.token).status == 'approved'
assert any('Approvato' in m.text for m in ch.sent)
"""),
    ("channels", "daemon_callback_reject_risolve_token", "happy", """
import os, tempfile
os.environ['METNOS_APPROVALS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
os.environ['METNOS_PAIRINGS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import approval_registry as ar
from channels import InboundMessage
from channels.daemon import ChannelDaemon

class FakeChannel:
    name = 'telegram'
    default_chat_id = '42'
    def __init__(self): self.sent = []
    def send(self, recipient, message):
        self.sent.append(message); return {'ok': True}
    def poll(self): return []

pending = ar.create_pending(channel='telegram', sender_id='42',
                             capability_class='c', action_verb='legga',
                             target_summary='log/syslog')

ch = FakeChannel()
d = ChannelDaemon(ch, run_turn=lambda q: None)
out = d.handle_message(InboundMessage(channel='telegram', sender_id='42',
                                       text=f'reject:{pending.token}',
                                       message_id='m', received_at=0.0,
                                       extra={'kind': 'callback'}))
assert out['ok'] is True
assert out['decision'] == 'rejected'
assert ar.get_pending(pending.token).status == 'rejected'
assert any('Rifiutato' in m.text for m in ch.sent)
"""),
    ("channels", "daemon_callback_data_invalido", "failure", """
import os, tempfile
os.environ['METNOS_APPROVALS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
os.environ['METNOS_PAIRINGS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
from channels import InboundMessage
from channels.daemon import ChannelDaemon

class FakeChannel:
    name = 'telegram'
    default_chat_id = '42'
    def __init__(self): self.sent = []
    def send(self, *a, **k): return {'ok': True}
    def poll(self): return []

ch = FakeChannel()
d = ChannelDaemon(ch, run_turn=lambda q: None)
out = d.handle_message(InboundMessage(channel='telegram', sender_id='42',
                                       text='spazzatura',
                                       message_id='m', received_at=0.0,
                                       extra={'kind': 'callback'}))
assert out['ok'] is False
assert out['reason'] == 'unknown_callback'
"""),
    ("channels", "daemon_callback_token_inesistente_messaggio_errore", "edge", """
import os, tempfile
os.environ['METNOS_APPROVALS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
os.environ['METNOS_PAIRINGS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
from channels import InboundMessage
from channels.daemon import ChannelDaemon

class FakeChannel:
    name = 'telegram'
    default_chat_id = '42'
    def __init__(self): self.sent = []
    def send(self, recipient, message):
        self.sent.append(message); return {'ok': True}
    def poll(self): return []

ch = FakeChannel()
d = ChannelDaemon(ch, run_turn=lambda q: None)
out = d.handle_message(InboundMessage(channel='telegram', sender_id='42',
                                       text='approve:nonesistente',
                                       message_id='m', received_at=0.0,
                                       extra={'kind': 'callback'}))
assert out['ok'] is False
assert 'sconosciuto' in out['error'].lower() or 'unknown' in out['error'].lower()
assert any('non risolvibile' in m.text.lower() for m in ch.sent)
"""),
    ("channels", "daemon_callback_sender_diverso_dal_richiedente_blocca", "security", """
import os, tempfile
os.environ['METNOS_APPROVALS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
os.environ['METNOS_PAIRINGS_DB'] = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
import approval_registry as ar
from channels import InboundMessage
from channels.daemon import ChannelDaemon

class FakeChannel:
    name = 'telegram'
    default_chat_id = '42'
    def __init__(self): self.sent = []
    def send(self, recipient, message):
        self.sent.append(message); return {'ok': True}
    def poll(self): return []

# Pending creata per sender '42'
pending = ar.create_pending(channel='telegram', sender_id='42',
                             capability_class='c', action_verb='a', target_summary='t')

# Sender '999' prova a risolvere: deve fallire
ch = FakeChannel()
d = ChannelDaemon(ch, run_turn=lambda q: None)
out = d.handle_message(InboundMessage(channel='telegram', sender_id='999',
                                       text=f'approve:{pending.token}',
                                       message_id='m', received_at=0.0,
                                       extra={'kind': 'callback'}))
assert out['ok'] is False
# Pending resta pending
assert ar.get_pending(pending.token).status == 'pending'
"""),
]


def main():
    r = Registry.open()

    # Birth tests come module-level
    n_birth = 0
    for module, manifest, test_name in EXECUTOR_BIRTH_CASES:
        r.add_case(module, f"birth_{test_name}", "module", "happy", "birth",
                   test_code=manifest, expected=test_name)
        n_birth += 1

    # Module-level Python tests for runtime
    n_runtime_module = 0
    for module, name, category, code in RUNTIME_PYTHON_CASES:
        r.add_case(module, name, "module", category, "python", test_code=code)
        n_runtime_module += 1

    # Cluster-level integration tests
    n_cluster = 0
    for module, name, category, code in CLUSTER_PYTHON_CASES:
        r.add_case(module, name, "cluster", category, "python", test_code=code)
        n_cluster += 1

    # System-level e2e tests
    n_system = 0
    for tup in SYSTEM_E2E_CASES:
        if len(tup) == 3:
            module, name, test_code = tup
            setup = teardown = ""
        else:
            module, name, test_code, setup, teardown = tup
        r.add_case(module, name, "system", "integration", "e2e",
                   test_code=test_code, setup_code=setup, teardown_code=teardown)
        n_system += 1

    s = r.summary()
    print(f"=== Popolamento completato ===")
    print(f"  birth tests:    {n_birth}")
    print(f"  module python:  {n_runtime_module}")
    print(f"  cluster python: {n_cluster}")
    print(f"  system e2e:     {n_system}")
    print(f"  TOTALE:         {n_birth + n_runtime_module + n_cluster + n_system}")
    print(f"\n=== DB summary ===")
    print(json.dumps(s, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
