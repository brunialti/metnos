"""compare_synonyms.py — Compare synonym sources side-by-side.

Sources:
A. Hardcoded vocab.py _OBJECT_SYNONYMS_IT/EN (~60 entries)
B. Affinity-derived from manifest.toml (635 obj entries)
C. BGE-M3 embedding cosine (real-time, infinite vocab)

Test queries: hand-picked covering critical patterns.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, '/opt/metnos')


# AUTO test set: extract from real_queries.json (production data)
def _load_auto_pairs() -> list:
    """Extract (query, expected_object) from real_queries.json automatic."""
    import json as _json
    fn = Path(__file__).parent / "real_queries.json"
    if not fn.exists():
        return []
    try:
        raw = _json.loads(fn.read_text())
    except Exception:
        return []
    out = []
    for r in raw:
        q = r.get("query", "")
        path = r.get("expected_path", [])
        if not path or not q: continue
        first = path[0]
        if "_" not in first: continue
        obj = first.split("_", 1)[1].split("_", 1)[0]
        out.append((q, obj))
    return out


AUTO_PAIRS = _load_auto_pairs()

# Manual curated pairs (covers patterns possibly underrepresented in real)
MANUAL_PAIRS = [
    # tasks
    ("Lista timer", "tasks"), ("mostra promemoria", "tasks"),
    ("task schedulati", "tasks"), ("ricorda comprare pane", "tasks"),
    ("cancella task obsoleti", "tasks"), ("pausa daily-backup", "tasks"),
    ("ogni 5 minuti", "tasks"), ("alla mattina manda mail", "tasks"),
    # contacts
    ("mostra rubrica", "contacts"), ("contatti family", "contacts"),
    ("chi è marco", "persons"), ("dimmi tutto su Lucia", "persons"),
    ("ospiti in casa", "persons"), ("telefono di Lucia", "persons"),
    # events
    ("che agenda ho", "events"), ("appuntamenti oggi", "events"),
    ("riunioni martedi", "events"), ("riassumi appuntamenti", "events"),
    ("evento più imminente", "events"), ("compleanni del mese", "events"),
    # places
    ("Farmacia piu vicina", "places"), ("dove sono", "places"),
    ("bar piu vicino", "places"), ("ristorante italiano vicino", "places"),
    ("hotel a Roma", "places"), ("supermercato aperto", "places"),
    # urls
    ("scarica url x", "urls"), ("cerca su google", "urls"),
    ("apri https://example.com", "urls"), ("notizie italia oggi", "urls"),
    ("github metnos", "urls"), ("trova issue github", "urls"),
    # processes
    ("ram usata", "processes"), ("processi attivi", "processes"),
    ("cpu usage", "processes"), ("disco libero", "processes"),
    ("uptime", "processes"), ("kill processo 1234", "processes"),
    # packages
    ("ffmpeg installato", "packages"), ("lista pacchetti", "packages"),
    ("verifica docker installato", "packages"),
    # files
    ("scrivi 'hello' nel file", "files"), ("riassumi documento", "files"),
    ("leggi /tmp/x.txt", "files"), ("file pdf in /home", "files"),
    ("conta linee /tmp/data.csv", "files"), ("file zero byte", "files"),
    # dirs
    ("cancella cartella", "dirs"), ("cartelle vuote", "dirs"),
    ("crea cartella /tmp/x", "dirs"), ("subdir di /home", "dirs"),
    ("alberatura completa /etc", "dirs"),
    # images
    ("foto al mare", "images"), ("indicizza foto", "images"),
    ("foto sfocate", "images"), ("foto di Mario", "persons"),  # ambiguous
    ("scatti notturni", "images"), ("foto duplicate", "images"),
    # messages
    ("manda mail a marco", "messages"), ("mail di oggi", "messages"),
    ("mail con allegati", "messages"), ("inbox non lette", "messages"),
    ("ultima mail ricevuta", "messages"),
    # numbers / time
    ("che ora è", "numbers"), ("data di oggi", "numbers"),
    ("calcola 23 * 45", "numbers"), ("differenza tra date", "numbers"),
    # credentials
    ("password account x", "credentials"),
    ("credenziali per gmail", "credentials"),
    ("cancella credenziali", "credentials"),
    # signatures
    ("hash sha256 file", "signatures"), ("checksum md5", "signatures"),
    # texts
    ("estrai testo da pdf", "texts"), ("filtra righe con TODO", "texts"),
    # proposals
    ("mostra proposte pending", "proposals"),
    ("accetta proposta synth", "proposals"),
    # tasks (cont.)
    ("storico esecuzioni task", "tasks"),
    # composite/ambiguous
    ("scarica readme github", "urls"),
    ("scrivi documento", "files"),
    ("nuovo doc google", "files"),
    ("ferma metnos-http", "processes"),
    ("estrai allegati mail", "messages"),
    ("contatti senza email", "contacts"),
    ("ridimensiona foto", "images"),
    ("comprimi cartella", "dirs"),
]


TEST_PAIRS = AUTO_PAIRS  # universal: solo auto da production logs


def _key_token(q: str) -> list[str]:
    """Tokenize query for matching."""
    import re
    s = q.lower()
    return re.findall(r"[a-zàèéìòù][a-zàèéìòù0-9]+", s)


def src_hardcoded(query: str) -> str | None:
    from runtime.vocab import _OBJECT_SYNONYMS_IT, _OBJECT_SYNONYMS_EN
    tokens = _key_token(query)
    for t in tokens:
        if t in _OBJECT_SYNONYMS_IT: return _OBJECT_SYNONYMS_IT[t]
        if t in _OBJECT_SYNONYMS_EN: return _OBJECT_SYNONYMS_EN[t]
    return None


def src_affinity(query: str) -> str | None:
    from vocab_synonyms_derived import load_cached
    d = load_cached()
    obj_map = d.get("objects", {})
    tokens = _key_token(query)
    # Try single token + multi-token n-grams
    s = query.lower()
    # Best match: longest substring match in obj_map keys
    matches = []
    for k, v in obj_map.items():
        if k in s:
            matches.append((len(k), k, v))
    if matches:
        matches.sort(reverse=True)
        return matches[0][2]
    return None


_BGE_CACHE = {}
def src_embedding(query: str, canonical_objects: list[str]) -> tuple[str, float] | None:
    try:
        sys.path.insert(0, '/opt/metnos/runtime')
        from bge_embedding import BGEEmbeddingService
        if 'svc' not in _BGE_CACHE:
            _BGE_CACHE['svc'] = BGEEmbeddingService()
            # Embed canonical objects once
            import numpy as np
            obj_emb = _BGE_CACHE['svc'].embed_texts(canonical_objects)
            n = np.linalg.norm(obj_emb, axis=1, keepdims=True); n[n==0]=1
            obj_emb = obj_emb / n
            _BGE_CACHE['obj_emb'] = obj_emb
            _BGE_CACHE['obj_names'] = canonical_objects
        import numpy as np
        q_emb = _BGE_CACHE['svc'].embed_query(query)
        qn = np.linalg.norm(q_emb);
        if qn>0: q_emb = q_emb/qn
        scores = _BGE_CACHE['obj_emb'] @ q_emb
        idx = int(np.argmax(scores))
        return _BGE_CACHE['obj_names'][idx], float(scores[idx])
    except Exception as e:
        return None


def src_combined(query: str) -> str | None:
    """Cascade: hardcoded (high precision) → affinity (high recall)
    → embedding (semantic fallback). Universal §7.3 — solo hardcoded
    è human-curated, gli altri due derivati automaticamente.
    """
    r = src_hardcoded(query)
    if r is not None: return r
    r = src_affinity(query)
    if r is not None: return r
    OBJ19 = ["files","dirs","packages","messages","events","contacts",
              "places","processes","urls","numbers","images","signatures",
              "texts","proposals","persons","tasks","inputs","credentials","entries"]
    e = src_embedding(query, OBJ19)
    if e and e[1] >= 0.7:  # confidence threshold
        return e[0]
    return None


def main():
    OBJECTS_19 = ["files", "dirs", "packages", "messages", "events",
                   "contacts", "places", "processes", "urls", "numbers",
                   "images", "signatures", "texts", "proposals", "persons",
                   "tasks", "inputs", "credentials", "entries"]
    counts = {"hardcoded": 0, "affinity": 0, "embedding": 0, "combined": 0}
    noise = {"hardcoded": 0, "affinity": 0, "combined": 0}  # wrong predictions
    for q, exp in TEST_PAIRS:
        a = src_hardcoded(q)
        b = src_affinity(q)
        c_res = src_embedding(q, OBJECTS_19)
        d = src_combined(q)
        if a == exp: counts["hardcoded"] += 1
        elif a: noise["hardcoded"] += 1
        if b == exp: counts["affinity"] += 1
        elif b: noise["affinity"] += 1
        if c_res and c_res[0] == exp: counts["embedding"] += 1
        if d == exp: counts["combined"] += 1
        elif d: noise["combined"] += 1
    print(f"=== SUMMARY ({len(TEST_PAIRS)} auto pairs) ===")
    print(f"  Hardcoded:  {counts['hardcoded']}/{len(TEST_PAIRS)} = {100*counts['hardcoded']/len(TEST_PAIRS):.1f}% (noise: {noise['hardcoded']})")
    print(f"  Affinity:   {counts['affinity']}/{len(TEST_PAIRS)} = {100*counts['affinity']/len(TEST_PAIRS):.1f}% (noise: {noise['affinity']})")
    print(f"  Embedding:  {counts['embedding']}/{len(TEST_PAIRS)} = {100*counts['embedding']/len(TEST_PAIRS):.1f}%")
    print(f"  Combined:   {counts['combined']}/{len(TEST_PAIRS)} = {100*counts['combined']/len(TEST_PAIRS):.1f}% (noise: {noise['combined']})")


if __name__ == "__main__":
    main()
