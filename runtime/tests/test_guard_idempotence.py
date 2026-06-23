"""test_guard_idempotence — M0 (ADR 0177 S3): i guard deterministici di
struttura (`_apply_deterministic_structure_guards`) girano su OGNI hit cache
L0/L1 (ADR 0174) oltre che su L3. Il loro commento li dichiara «idempotenti»,
ma l'idempotenza era ASSERITA, mai PROVATA — rischio silenzioso: un piano
cachato che attraversa i guard a ogni hit potrebbe mutare in modo non
fixed-point e divergere dalla forma cachata.

Questo test prova `guard(guard(fw)) == guard(fw)` su un corpus di piani
compound reali (catalogo reale, v3). Se un caso non converge in un passo, qui
si vede subito.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("METNOS_ENGINE", "v3")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import dispatch as D            # noqa: E402
from engine.types import Framework, StepSpec, Intent  # noqa: E402


def _catalog():
    from store_bootstrap import register_builtin_stores
    register_builtin_stores()
    from loader import load_catalog, filter_for_visibility, VISIBILITY_COMPOSER
    return filter_for_visibility(load_catalog(verify=True), VISIBILITY_COMPOSER)


_CAT = _catalog()


def _sig(fw: Framework):
    """Firma strutturale stabile del framework: (tool, args canonici) per step."""
    return [(s.tool, json.dumps(s.args, sort_keys=True, default=str))
            for s in fw.steps]


def _intent(acts):
    primary = acts[0] if acts else {"verb": "", "object": ""}
    return Intent(verb=primary["verb"], object=primary["object"],
                  keywords=[], confidence=1.0, lang="it", actions=acts)


def _twice(spec, acts, query):
    fw0 = Framework(steps=[StepSpec(tool=t, args=dict(a)) for t, a in spec])
    intent = _intent(acts)
    fw1 = D._apply_deterministic_structure_guards(fw0, intent, query, _CAT)
    fw2 = D._apply_deterministic_structure_guards(fw1, intent, query, _CAT)
    return _sig(fw1), _sig(fw2)


# Corpus: casi che ESERCITANO i vari guard (extract mancante, ordine, oggetto,
# mono-azione, già-corretto), così l'idempotenza è provata dove conta.
_CASES = [
    # extract droppato fra read e create → ensure_extract lo inserisce
    ("eventi→foglio",
     [("read_events", {}), ("create_files_spreadsheet", {"from_step": 1})],
     [{"verb": "read", "object": "events"},
      {"verb": "extract", "object": "events"},
      {"verb": "create", "object": "files"}],
     "leggi gli eventi, estrai titolo e orario, crea un foglio"),
    # mail → filtro → move (spam) — già coerente con le sue azioni
    ("mail→filtro→move",
     [("read_messages", {}), ("filter_entries", {"from_step": 1}),
      ("move_messages", {"from_step": 2, "dst_folder": "Spam"})],
     [{"verb": "read", "object": "messages"},
      {"verb": "filter", "object": "messages"},
      {"verb": "move", "object": "messages"}],
     "sposta le email di spam nella cartella Spam"),
    # compound 2-dominio: cerca url + apri
    ("web→apri",
     [("find_urls", {}), ("get_urls", {"from_step": 1})],
     [{"verb": "find", "object": "urls"},
      {"verb": "get", "object": "urls"}],
     "cerca online e apri i risultati"),
    # mono-azione: i guard sono no-op (richiedono intent.actions multi)
    ("mono read",
     [("read_messages", {})],
     [{"verb": "read", "object": "messages"}],
     "leggi le mail"),
    # estrai → crea csv (ordini)
    ("ordini→csv",
     [("read_messages", {}),
      ("create_files_spreadsheet", {"from_step": 1})],
     [{"verb": "read", "object": "messages"},
      {"verb": "extract", "object": "messages"},
      {"verb": "create", "object": "files"}],
     "trova gli ordini nelle mail, estrai numero e totale, salvali in un csv"),
]


class TestGuardIdempotence(unittest.TestCase):
    def test_guards_are_a_fixed_point(self):
        for name, spec, acts, query in _CASES:
            with self.subTest(case=name):
                sig1, sig2 = _twice(spec, acts, query)
                # `guard(guard(fw))` deve essere identico a `guard(fw)`:
                # se diverge, il piano cachato muterebbe a ogni hit (S3).
                self.assertEqual(
                    sig1, sig2,
                    f"guard NON idempotente su «{name}»:\n  1°: {sig1}\n  2°: {sig2}")


if __name__ == "__main__":
    unittest.main()
