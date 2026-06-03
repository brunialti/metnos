"""parser_dedicated.py — Intent parser usando LLM dedicato (Qwen3) + GBNF.

Universal §7.3: stesso output QueryDescriptor, ma con LLM specializzato e
grammar constrain. Permette ablation testing senza toccare parser principale.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from query_parser import QueryDescriptor, _dict_to_descriptor

DEDICATED_HOST = "127.0.0.1:8082"
_CACHE_DIR = Path(__file__).parent / "parse_cache_dedicated"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# GBNF grammar path
_GBNF_PATH = Path(__file__).parent / "metnos_intent.gbnf"
_GBNF_TEXT = _GBNF_PATH.read_text() if _GBNF_PATH.exists() else ""


FEW_SHOT_PROMPT = """Analizza la query utente Metnos ed estrai intent_verb, intent_object, inputs, target, constraints in formato JSON.

VOCABOLARIO (chiuso §2.2):
- 23 verbi: read|write|move|delete|create|find|list|filter|sort|group|classify|get|set|send|describe|render|extract|compress|compute|compare|change|order|share
- 19 oggetti: files|dirs|packages|messages|events|contacts|places|processes|urls|numbers|images|signatures|texts|proposals|persons|tasks|inputs|credentials|entries

ESEMPI:

Q: leggi mail oggi
A: {"intent_verb":"read","intent_object":"messages","inputs":[],"target":{"semantic_type":"message_entry[]","shape":"list"},"constraints":[{"kind":"filter","key":"time_window","value":"today"}]}

Q: sposta /tmp/a.txt in /home
A: {"intent_verb":"move","intent_object":"files","inputs":[{"name":"src","semantic_type":"file_path","value":"/tmp/a.txt"},{"name":"dst","semantic_type":"dir_path","value":"/home"}],"target":{"semantic_type":"file_entry[]","shape":"list"},"constraints":[]}

Q: rinomina /tmp/test.py in test_v2.py
A: {"intent_verb":"move","intent_object":"files","inputs":[{"name":"src","semantic_type":"file_path","value":"/tmp/test.py"},{"name":"dst","semantic_type":"file_path","value":"test_v2.py"}],"target":{"semantic_type":"file_entry[]","shape":"list"},"constraints":[]}

Q: OCR su /tmp/scansione.png
A: {"intent_verb":"read","intent_object":"files","inputs":[{"name":"path","semantic_type":"image_path","value":"/tmp/scansione.png"}],"target":{"semantic_type":"free_text","shape":"scalar"},"constraints":[{"kind":"transform","key":"ocr","value":null}]}

Q: foto di Mario
A: {"intent_verb":"find","intent_object":"images","inputs":[{"name":"person","semantic_type":"person_name","value":"Mario"}],"target":{"semantic_type":"image_entry[]","shape":"list"},"constraints":[{"kind":"filter","key":"person_name","value":"Mario"}]}

Q: email di Paolo
A: {"intent_verb":"read","intent_object":"contacts","inputs":[{"name":"person","semantic_type":"person_name","value":"Paolo"}],"target":{"semantic_type":"person_entry[]","shape":"list"},"constraints":[{"kind":"filter","key":"name","value":"Paolo"}]}

Q: checksum sha256 di /tmp/a.iso
A: {"intent_verb":"compute","intent_object":"signatures","inputs":[{"name":"path","semantic_type":"file_path","value":"/tmp/a.iso"}],"target":{"semantic_type":"free_text","shape":"scalar"},"constraints":[{"kind":"transform","key":"sha256","value":null}]}

Q: manda telegram lista mail oggi
A: {"intent_verb":"send","intent_object":"messages","inputs":[],"target":{"semantic_type":"scalar_metric","shape":"scalar"},"constraints":[{"kind":"filter","key":"time_window","value":"today"},{"kind":"filter","key":"channel","value":"telegram"}]}

Q: che ora e
A: {"intent_verb":"get","intent_object":"numbers","inputs":[],"target":{"semantic_type":"iso_timestamp","shape":"scalar"},"constraints":[]}

Q: ram usata
A: {"intent_verb":"get","intent_object":"processes","inputs":[],"target":{"semantic_type":"scalar_metric","shape":"scalar"},"constraints":[{"kind":"filter","key":"metric","value":"memory"}]}

Q: lista pacchetti installati
A: {"intent_verb":"find","intent_object":"packages","inputs":[],"target":{"semantic_type":"file_entry[]","shape":"list"},"constraints":[]}

Q: indicizza foto in /home/roberto/Foto
A: {"intent_verb":"create","intent_object":"images","inputs":[{"name":"base_path","semantic_type":"dir_path","value":"/home/roberto/Foto"}],"target":{"semantic_type":"image_entry[]","shape":"list"},"constraints":[{"kind":"transform","key":"indices","value":null}]}

Q: dimensione del file /tmp/x.log
A: {"intent_verb":"get","intent_object":"files","inputs":[{"name":"path","semantic_type":"file_path","value":"/tmp/x.log"}],"target":{"semantic_type":"scalar_metric","shape":"scalar"},"constraints":[{"kind":"filter","key":"field","value":"size"}]}

Q: raggruppa mail per mittente
A: {"intent_verb":"group","intent_object":"messages","inputs":[],"target":{"semantic_type":"json_object","shape":"dict"},"constraints":[{"kind":"aggregate","key":"sender","value":null}]}

Q: ricordami comprare pane domani
A: {"intent_verb":"create","intent_object":"tasks","inputs":[{"name":"content","semantic_type":"free_text","value":"comprare pane"},{"name":"trigger","semantic_type":"time_window","value":"tomorrow"}],"target":{"semantic_type":"task_entry[]","shape":"scalar"},"constraints":[]}

Q: scarica https://httpbin.org/get e salva in /tmp/dl.txt
A: {"intent_verb":"get","intent_object":"urls","inputs":[{"name":"url","semantic_type":"url","value":"https://httpbin.org/get"},{"name":"out_path","semantic_type":"file_path","value":"/tmp/dl.txt"}],"target":{"semantic_type":"file_entry[]","shape":"list"},"constraints":[]}

Q: dove sono
A: {"intent_verb":"get","intent_object":"places","inputs":[],"target":{"semantic_type":"json_object","shape":"dict"},"constraints":[{"kind":"filter","key":"current_location","value":"true"}]}

Q: Farmacia piu vicina
A: {"intent_verb":"find","intent_object":"places","inputs":[{"name":"query","semantic_type":"free_text","value":"farmacia"}],"target":{"semantic_type":"url_entry[]","shape":"list"},"constraints":[{"kind":"sort","key":"distance","value":"asc"}]}

Q: scrivi 'hello' nel file /tmp/x.txt
A: {"intent_verb":"write","intent_object":"files","inputs":[{"name":"path","semantic_type":"file_path","value":"/tmp/x.txt"},{"name":"content","semantic_type":"free_text","value":"hello"}],"target":{"semantic_type":"file_entry[]","shape":"scalar"},"constraints":[]}

Q: comprimi /tmp/x.txt con gzip
A: {"intent_verb":"compress","intent_object":"files","inputs":[{"name":"path","semantic_type":"file_path","value":"/tmp/x.txt"}],"target":{"semantic_type":"file_entry[]","shape":"scalar"},"constraints":[{"kind":"transform","key":"gz","value":null}]}

Q: metadata del file /tmp/x.txt
A: {"intent_verb":"get","intent_object":"files","inputs":[{"name":"path","semantic_type":"file_path","value":"/tmp/x.txt"}],"target":{"semantic_type":"json_object","shape":"dict"},"constraints":[{"kind":"filter","key":"field","value":"metadata"}]}

Q: leggi contenuto https://example.com
A: {"intent_verb":"read","intent_object":"urls","inputs":[{"name":"url","semantic_type":"url","value":"https://example.com"}],"target":{"semantic_type":"free_text","shape":"scalar"},"constraints":[]}

Q: quante righe di codice in /opt/metnos
A: {"intent_verb":"compute","intent_object":"files","inputs":[{"name":"path","semantic_type":"dir_path","value":"/opt/metnos"}],"target":{"semantic_type":"scalar_metric","shape":"scalar"},"constraints":[{"kind":"transform","key":"lines","value":null}]}

Q: che ora è a Roma?
A: {"intent_verb":"get","intent_object":"numbers","inputs":[{"name":"timezone","semantic_type":"name","value":"Europe/Rome"}],"target":{"semantic_type":"iso_timestamp","shape":"scalar"},"constraints":[]}

Q: what time is it in Tokyo?
A: {"intent_verb":"get","intent_object":"numbers","inputs":[{"name":"timezone","semantic_type":"name","value":"Asia/Tokyo"}],"target":{"semantic_type":"iso_timestamp","shape":"scalar"},"constraints":[]}

Q: che data e oggi
A: {"intent_verb":"get","intent_object":"numbers","inputs":[],"target":{"semantic_type":"iso_timestamp","shape":"scalar"},"constraints":[]}

Q: scrivi 'esempio' nel file /tmp/x.txt e dimmi quanti byte hai scritto
A: {"intent_verb":"write","intent_object":"files","inputs":[{"name":"path","semantic_type":"file_path","value":"/tmp/x.txt"},{"name":"content","semantic_type":"free_text","value":"esempio"}],"target":{"semantic_type":"scalar_metric","shape":"scalar"},"constraints":[]}

Q: Crea la cartella /tmp/X e dentro scrivici un file
A: {"intent_verb":"create","intent_object":"dirs","inputs":[{"name":"path","semantic_type":"dir_path","value":"/tmp/X"}],"target":{"semantic_type":"dir_entry[]","shape":"scalar"},"constraints":[]}

Q: trova foto simili
A: {"intent_verb":"find","intent_object":"images","inputs":[],"target":{"semantic_type":"image_entry[]","shape":"list"},"constraints":[{"kind":"transform","key":"similarity","value":null}]}

Q: questo è Roberto Brunialti
A: {"intent_verb":"set","intent_object":"persons","inputs":[{"name":"name","semantic_type":"person_name","value":"Roberto Brunialti"}],"target":{"semantic_type":"person_entry[]","shape":"scalar"},"constraints":[]}

Q: classifica cartelle per linguaggio progetto
A: {"intent_verb":"find","intent_object":"dirs","inputs":[],"target":{"semantic_type":"dir_entry[]","shape":"list"},"constraints":[{"kind":"aggregate","key":"language","value":null}]}

Q: Leggi e confronta https://x.com/a e https://x.com/b
A: {"intent_verb":"read","intent_object":"urls","inputs":[{"name":"url1","semantic_type":"url","value":"https://x.com/a"},{"name":"url2","semantic_type":"url","value":"https://x.com/b"}],"target":{"semantic_type":"free_text","shape":"scalar"},"constraints":[{"kind":"transform","key":"compare","value":null}]}

Q: issue aperti su https://github.com/x/metnos
A: {"intent_verb":"read","intent_object":"urls","inputs":[{"name":"url","semantic_type":"url","value":"https://github.com/x/metnos"}],"target":{"semantic_type":"free_text","shape":"scalar"},"constraints":[{"kind":"filter","key":"issues","value":"open"}]}

Q: Cerca informazioni su https://wikipedia.org/Roma
A: {"intent_verb":"read","intent_object":"urls","inputs":[{"name":"url","semantic_type":"url","value":"https://wikipedia.org/Roma"}],"target":{"semantic_type":"free_text","shape":"scalar"},"constraints":[]}

Q: mostra i task attivi
A: {"intent_verb":"list","intent_object":"tasks","inputs":[],"target":{"semantic_type":"task_entry[]","shape":"list"},"constraints":[{"kind":"filter","key":"state","value":"active"}]}

Q: cerca foto al mare
A: {"intent_verb":"find","intent_object":"images","inputs":[],"target":{"semantic_type":"image_entry[]","shape":"list"},"constraints":[{"kind":"filter","key":"location","value":"mare"}]}

Q: converti /tmp/x.png in jpg
A: {"intent_verb":"change","intent_object":"files","inputs":[{"name":"path","semantic_type":"file_path","value":"/tmp/x.png"}],"target":{"semantic_type":"file_entry[]","shape":"scalar"},"constraints":[{"kind":"transform","key":"format","value":"jpg"}]}

Q: media tra 10 20 30
A: {"intent_verb":"describe","intent_object":"numbers","inputs":[{"name":"values","semantic_type":"scalar_metric","value":"10,20,30"}],"target":{"semantic_type":"scalar_metric","shape":"scalar"},"constraints":[{"kind":"aggregate","key":"avg","value":null}]}

Q: che appuntamento ho domani
A: {"intent_verb":"read","intent_object":"events","inputs":[],"target":{"semantic_type":"event_entry[]","shape":"list"},"constraints":[{"kind":"filter","key":"time_window","value":"tomorrow"}]}

Q: vai su https://httpbin.org/uuid e dimmi solo l'UUID
A: {"intent_verb":"get","intent_object":"urls","inputs":[{"name":"url","semantic_type":"url","value":"https://httpbin.org/uuid"}],"target":{"semantic_type":"free_text","shape":"scalar"},"constraints":[]}

Q: scarica e mostrami contenuto https://httpbin.org/json
A: {"intent_verb":"get","intent_object":"urls","inputs":[{"name":"url","semantic_type":"url","value":"https://httpbin.org/json"}],"target":{"semantic_type":"free_text","shape":"scalar"},"constraints":[]}

Q: riassumi notizie su roma in repubblica.it ultimi 7 giorni
A: {"intent_verb":"find","intent_object":"urls","inputs":[{"name":"query","semantic_type":"free_text","value":"roma"},{"name":"site","semantic_type":"free_text","value":"repubblica.it"}],"target":{"semantic_type":"url_entry[]","shape":"list"},"constraints":[{"kind":"filter","key":"time_window","value":"last-7d"}]}

Q: classifica le mail di oggi in importanti e meno importanti
A: {"intent_verb":"read","intent_object":"messages","inputs":[],"target":{"semantic_type":"message_entry[]","shape":"list"},"constraints":[{"kind":"filter","key":"time_window","value":"today"},{"kind":"transform","key":"classify","value":"importance"}]}

Q: questo è Roberto
A: {"intent_verb":"set","intent_object":"persons","inputs":[{"name":"name","semantic_type":"person_name","value":"Roberto"}],"target":{"semantic_type":"scalar_metric","shape":"scalar"},"constraints":[]}

Ora rispondi con il SOLO JSON per la query, niente prosa, niente markdown."""


def _cache_key(query: str) -> str:
    h = hashlib.sha256()
    h.update(FEW_SHOT_PROMPT.encode())
    h.update(b"\x00")
    h.update(query.encode())
    return h.hexdigest()[:16]


def parse_query_dedicated(query: str, *, use_cache: bool = True) -> QueryDescriptor | None:
    if not query:
        return None
    cache_file = _CACHE_DIR / f"{_cache_key(query)}.json"
    raw = None
    if use_cache and cache_file.exists():
        try:
            raw = json.loads(cache_file.read_text())["raw"]
        except Exception:
            raw = None
    if not raw:
        raw = _call_qwen(query)
        if raw and cache_file:
            try:
                cache_file.write_text(json.dumps({"raw": raw}, ensure_ascii=False))
            except Exception:
                pass
    if not raw:
        return None
    raw = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.IGNORECASE)
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    parsed["__raw_query__"] = query
    # SIM_QWEN_OVERRIDE_INTENT=1: Qwen-Emb FT override intent_object se conf>=th.
    import os as _os
    if _os.environ.get("SIM_QWEN_OVERRIDE_INTENT", "0") == "1":
        try:
            from qwen_intent_signal import predict_object_with_score
            qwen_obj, conf = predict_object_with_score(query)
            th = float(_os.environ.get("SIM_QWEN_OVERRIDE_TH", "0.55"))
            if qwen_obj and conf >= th and qwen_obj != parsed.get("intent_object"):
                if _os.environ.get("SIM_QWEN_DEBUG"):
                    import sys as _sys
                    print(f"[qwen-override] '{query[:50]}': {parsed.get('intent_object')} → {qwen_obj} (conf={conf:.2f})",
                           file=_sys.stderr)
                parsed["intent_object"] = qwen_obj
        except Exception as e:
            if _os.environ.get("SIM_QWEN_DEBUG"):
                import sys as _sys
                print(f"qwen override err: {e}", file=_sys.stderr)
    return _dict_to_descriptor(parsed, raw)


def _call_qwen(query: str) -> str:
    body = json.dumps({
        "model": "qwen3-0.6b",
        "messages": [
            {"role": "system", "content": FEW_SHOT_PROMPT},
            {"role": "user", "content": f"Q: {query}\nA: "},
        ],
        "max_tokens": 600,
        "temperature": 0.0,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
        "grammar": _GBNF_TEXT,
    }).encode()
    req = urllib.request.Request(
        f"http://{DEDICATED_HOST}/v1/chat/completions",
        data=body, headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"]
    except Exception as ex:
        print(f"[parser_dedicated] LLM call error: {ex}", file=sys.stderr)
        return ""
