"""Il canale firmato che porta i programmi sui device.

Due proprieta' che non si possono verificare a valle:

1. L'indirizzo e la FORMA del descrittore del client sono quelli che i client
   GIA' installati conoscono. Quell'endpoint e' l'unica strada per sostituire
   un client: romperlo vorrebbe dire dover aggiornare a mano proprio le
   macchine che non si possono aggiornare a mano.

2. Il descrittore di un componente porta il NOME del componente dentro la
   firma. Senza, due componenti pubblicati per la stessa versione e lo stesso
   sistema condividerebbero una firma valida, e chi si aspetta l'aiutante
   elevato potrebbe ricevere un altro programma con la firma del server a
   garantirlo.
"""
import json
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import make_mocked_request

RADICE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RADICE / "runtime"))

import agent_mirror  # noqa: E402
import agent_server  # noqa: E402
import invocations  # noqa: E402

TARGET = "x86_64-pc-windows-gnu"
SHA_CLIENT = "a" * 64
SHA_HELPER = "b" * 64


@pytest.fixture()
def mirror(tmp_path, monkeypatch):
    """Un mirror con due componenti pubblicati per la stessa versione."""
    d = tmp_path / "client"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({
        "latest": "9.9.9",
        "versions": {"9.9.9": {TARGET: {
            "client": {"filename": "metnos-client.exe",
                       "path": f"9.9.9/{TARGET}/metnos-client.exe",
                       "size": 10, "sha256": SHA_CLIENT},
            "helper": {"filename": "metnos-helper.exe",
                       "path": f"9.9.9/{TARGET}/metnos-helper.exe",
                       "size": 20, "sha256": SHA_HELPER},
        }}},
    }))
    monkeypatch.setattr(agent_mirror, "MIRROR_CLIENT_DIR", d)
    return d


async def _json(handler, **match):
    risposta = await handler(make_mocked_request("GET", "/x", match_info=match))
    return json.loads(risposta.body.decode("utf-8")), risposta.status


@pytest.mark.asyncio
async def test_il_descrittore_del_client_ha_la_forma_che_i_client_conoscono(mirror):
    corpo, stato = await _json(agent_server.client_update_descriptor, target=TARGET)
    assert stato == 200
    # I campi che il client installato legge, e nessuna sorpresa in piu'.
    assert corpo["version"] == "9.9.9"
    assert corpo["target"] == TARGET
    assert corpo["sha256"] == SHA_CLIENT
    assert corpo["url_path"] == f"/agent/client/9.9.9/{TARGET}/metnos-client.exe"
    # La firma copre esattamente i tre campi storici: aggiungerne uno
    # invaliderebbe la verifica su ogni client gia' installato.
    assert invocations.verify_payload(
        invocations.server_public_key_b64(), corpo["sig"],
        {"version": "9.9.9", "target": TARGET, "sha256": SHA_CLIENT})


@pytest.mark.asyncio
async def test_il_vecchio_mirror_piatto_resta_leggibile(mirror):
    (mirror / "manifest.json").write_text(json.dumps({
        "latest": "8.8.8",
        "versions": {"8.8.8": {TARGET: {
            "filename": "metnos-client.exe",
            "path": f"8.8.8/{TARGET}/metnos-client.exe",
            "size": 10,
            "sha256": SHA_CLIENT,
        }}},
    }))
    corpo, stato = await _json(
        agent_server.client_update_descriptor, target=TARGET)
    assert stato == 200
    assert corpo["version"] == "8.8.8"
    assert corpo["sha256"] == SHA_CLIENT


@pytest.mark.asyncio
async def test_il_componente_entra_nella_firma(mirror):
    corpo, stato = await _json(agent_server.component_update_descriptor,
                               component="helper", target=TARGET)
    assert stato == 200
    assert corpo["sha256"] == SHA_HELPER
    assert invocations.verify_payload(
        invocations.server_public_key_b64(), corpo["sig"],
        {"component": "helper", "version": "9.9.9", "target": TARGET,
         "sha256": SHA_HELPER})


@pytest.mark.asyncio
async def test_la_firma_di_un_componente_non_vale_per_un_altro(mirror):
    """Il punto: le due firme non sono intercambiabili."""
    aiutante, _ = await _json(agent_server.component_update_descriptor,
                              component="helper", target=TARGET)
    pub = invocations.server_public_key_b64()
    # La firma dell'aiutante non regge sul payload del client, e viceversa.
    assert not invocations.verify_payload(
        pub, aiutante["sig"],
        {"component": "client", "version": "9.9.9", "target": TARGET,
         "sha256": SHA_CLIENT})


@pytest.mark.asyncio
async def test_un_componente_che_non_esiste_e_un_404_non_un_indovinello(mirror):
    _, stato = await _json(agent_server.component_update_descriptor,
                           component="helper", target="x86_64-unknown-linux-musl")
    assert stato == 404


@pytest.mark.asyncio
async def test_un_nome_di_componente_inventato_non_apre_percorsi(mirror):
    for cattivo in ["../client", "Client", "a/b", ""]:
        _, stato = await _json(agent_server.component_update_descriptor,
                               component=cattivo, target=TARGET)
        assert stato == 400, f"accettato il componente {cattivo!r}"
