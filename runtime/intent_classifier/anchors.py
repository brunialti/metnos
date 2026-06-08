"""anchors.py — Anchor text per ogni OBJECT canonico (§2.2).

Usato come prototipo testuale per cosine-similarity vs embedding query.
Aggiungere/migliorare gli anchor migliora la classificazione ZERO-SHOT;
con fine-tune, gli anchor sono usati solo come query di confronto.

Locale-aware: ANCHORS_IT (default), ANCHORS_EN. Caller deve scegliere via
`runtime.config.DEFAULT_LANG` o env `LANG`.
"""
from __future__ import annotations

# Italian anchors — production locale Metnos
ANCHORS_IT: dict[str, str] = {
    "files": "file documento testo /tmp/data.txt pdf csv leggi/scrivi file",
    "dirs": "cartella directory folder sottocartella alberatura",
    "packages": "pacchetto installato apt deb python pip software",
    "messages": "email mail messaggio posta gmail allegato inbox mittente",
    "events": "evento appuntamento calendario riunione agenda compleanno",
    "contacts": "contatto rubrica indirizzo email salvato",
    "places": "luogo posto farmacia ristorante hotel vicino geografico",
    "processes": "processo ram cpu memoria disco uptime systemctl",
    "urls": "url sito web pagina google https link risorsa",
    "numbers": "numero calcolo data ora tempo timezone matematica",
    "images": "foto immagine pic jpg foto scattate album fotografico",
    "signatures": "hash firma checksum md5 sha256 digest crittografico",
    "texts": "testo paragrafo riga linea estratto contenuto testuale",
    "proposals": "proposta synth introvertiva pending accettata rifiutata",
    "persons": "persona ospite chi-è guest profilo identità individuo",
    "tasks": "task promemoria timer scheduler ricordami ricorrenza",
    "inputs": "input form dialog valore richiesta utente",
    "credentials": "password credenziali account oauth login chiave",
    "entries": "voce elemento lista record entry oggetto interno",
}

ANCHORS_EN: dict[str, str] = {
    "files": "file document text /tmp/data.txt pdf csv read/write file",
    "dirs": "folder directory subdir tree",
    "packages": "package installed apt deb python pip software dependency",
    "messages": "email mail message inbox gmail attachment sender",
    "events": "event appointment calendar meeting agenda birthday",
    "contacts": "contact addressbook saved email",
    "places": "place location pharmacy restaurant hotel nearby geographic",
    "processes": "process ram cpu memory disk uptime systemctl",
    "urls": "url website webpage google https link resource",
    "numbers": "number computation date time timezone math",
    "images": "photo image picture jpg snapshot album",
    "signatures": "hash signature checksum md5 sha256 digest crypto",
    "texts": "text paragraph line extract content snippet",
    "proposals": "proposal synth introvertiva pending accepted rejected",
    "persons": "person guest who is profile identity individual",
    "tasks": "task reminder timer scheduler recurring",
    "inputs": "input form dialog value prompt user",
    "credentials": "password credentials account oauth login key",
    "entries": "entry item list record internal object",
}


def for_lang(lang: str) -> dict[str, str]:
    """Return anchor map per language (IT default, EN fallback)."""
    if lang and lang.startswith("en"):
        return ANCHORS_EN
    return ANCHORS_IT


# Canonical OBJECTS list (matches runtime/vocab.py OBJECTS order)
OBJECTS: list[str] = list(ANCHORS_IT.keys())
