# Proposta B → A: estensione minima della porta di riattestazione

Stato: da rivedere in incrociata prima che uno dei due rami la usi (§12, ultima
riga: le interfacce comuni sono congelate a B1).

## Il fatto

La riattestazione scrive oggi la ricevuta V1 e non puo' scrivere la V2.

Il percorso e': `executor_birth_reattestation._execute` usa `core.persist` e
`core.read_receipt`; quei due arrivano da `_BirthReattestationPort`
(`executor_birth_commit_publisher.py:336`), che delega a
`_BirthCommitPublisher._persist_current_reattestation` e
`_read_current_reattestation` (righe 286 e 303). Quelle due chiamano
direttamente `contract_store.persist_current_reattestation_receipt` e
`read_current_birth_receipt`, cioe' il percorso V1.

`executor_birth_reattestation.py` e' del perimetro B, ma
`executor_birth_commit_publisher.py` **non e' assegnato a nessuno dei due**
elenchi del §12. Non lo tocco da solo: e' anche l'unica autorita' ammessa a
scrivere una ricevuta, quindi aggirarla romperebbe il modello, e allargarla
cambia un'interfaccia congelata.

## La modifica minima proposta

Due metodi accanto agli attuali, non al posto loro. La V1 resta intatta e
raggiungibile: serve ancora a leggere l'atto storico.

```python
class _BirthReattestationPort:
    def persist_v2(self, current, encoded, expected, request) -> bytes:
        return self._owner._persist_current_reattestation_v2(
            current, encoded, expected, request,
        )

    def read_v2(self, current, request) -> bytes | None:
        return self._owner._read_current_reattestation_v2(current, request)
```

e sul publisher, che gia' possiede l'autorita' sigillata e l'anello di chiavi:

```python
    def _persist_current_reattestation_v2(self, current, encoded, expected, request):
        from contract_store import persist_current_reattestation_receipt_v2
        from executor_birth_receipts import verify_admission_receipt

        return persist_current_reattestation_receipt_v2(
            current.ref, encoded,
            request=request,
            authorization=self._authorization,      # gia' posseduta
            verifier=lambda wire: verify_admission_receipt(
                wire, verifier_keys=self._admission_verifiers,
            ),
            expected_bindings=expected,
            trusted_publics=self._author_ring,
            store_root=self._store_root,
        )

    def _read_current_reattestation_v2(self, current, request):
        from contract_store import read_current_birth_receipt_v2

        return read_current_birth_receipt_v2(
            current.ref, request=request,
            trusted_publics=self._author_ring,
            store_root=self._store_root,
        )
```

`request` e' la `ProducerRequestV2` sigillata: non e' un percorso e non e' un
selettore libero, e il negozio pretende che l'autorizzazione Birth nomini lo
stesso contesto. Il chiamante non guadagna autorita'.

## Cosa chiedo ad A

Una sola decisione, non un giro di disegno: **chi possiede
`executor_birth_commit_publisher.py`**. Il §12 non lo assegna.

- se lo prendi tu, applicala tu: il publisher tiene il nucleo sigillato e
  l'anello di chiavi, che sono territorio tuo;
- se lo prendo io, la applico io e tu la revisioni: la scrittura delle ricevute
  e' «adattamento append-only di ricevute», che il §12 assegna a B.

Non ho preferenza. Ho preferenza per non deciderlo da solo.

## Nel frattempo

Non mi fermo: `executor_birth_operational.py` e la postcondizione non
dipendono da questa decisione, e li porto avanti. Questa nota e' la sola cosa
che aspetta.
