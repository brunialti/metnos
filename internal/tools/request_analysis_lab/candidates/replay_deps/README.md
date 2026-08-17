# Exact replay dependencies

Dipendenze byte-identiche che prima esistevano soltanto in `/tmp`:

| File archivio → nome `/tmp` | SHA-256 |
|---|---|
| `metnos_unified_query_bench_v22.py` → `/tmp/metnos_unified_query_bench_v22.py` | `db2abaf6aaba2ad77205dda3fda96cdfab272a17079e57b0e472201395a4a329` |
| `metnos_prompt_contamination_audit.py` → `/tmp/metnos_prompt_contamination_audit.py` | `1cedfa7115744da79764c6b2fd31c2be67eada3fcc18d23bb46a5108eb190169` |

Il primo file è la base esatta importata dal runner V25; non è uguale a
`../../unified_query_bench_v23_checkpoint.py`. Il secondo è lo strumento
redatto bloccato dal freeze autore V26.3. Non sono runtime production files.

Leggere `../REPLAY_CHAIN.md`; la presenza di questi file non autorizza alcun
run V26.3.
