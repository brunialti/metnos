//! pyenv.rs — risoluzione dell'interprete Python sul device (§2/§8 design doc).
//!
//! Piano di progetto: python-build-standalone (Astral) scaricato lazy dal
//! mirror del server, cache read-only, uv per venv/wheel. Per il primo giro
//! (W1-2, executor stdlib-only come find_packages) l'interprete si risolve
//! con questa precedenza, la scelta e' logata (§2.8, mai silenziosa):
//!
//!   1. env `METNOS_PYTHON` — path esplicito (test/override).
//!   2. runtime scaricato in cache (`<cache>/runtime/.../bin/python3` unix,
//!      `<cache>/runtime/.../python/python.exe` windows).
//!   3. download da `<server>/agent/runtime/<tarball>` se `METNOS_PYTHON_RUNTIME`
//!      (unix) / `METNOS_PYTHON_RUNTIME_WIN` (windows) indica il nome del
//!      tarball da tirare dal mirror (pin gestito server-side).
//!   4. `python3` di sistema — SOLO unix (fallback pragmatico MVP; wheel non
//!      garantiti). Su Windows questo fallback NON esiste (§16.2 W3.1): un
//!      python di sistema non e' verificato ne' garantito compatibile: si
//!      fallisce onesto con `error_class:"python_runtime_missing"` (tradotto
//!      dal chiamante in runner.rs, che intercetta l'`Err` di `resolve`).
//!
//! uv (venv+wheel dal mirror) e' cablato solo quando un executor dichiara
//! dipendenze non-stdlib: quel ramo e' marcato TODO W5 e fallisce ONESTO.

use anyhow::{anyhow, bail, Context, Result};
use std::path::{Path, PathBuf};

pub struct PyEnv {
    pub python: PathBuf,
    pub source: String,
}

pub async fn resolve(server: &str, cache_root: &Path) -> Result<PyEnv> {
    // 1. override esplicito.
    if let Ok(p) = std::env::var("METNOS_PYTHON") {
        let path = PathBuf::from(p);
        if path.is_file() {
            return Ok(PyEnv { python: path, source: "env:METNOS_PYTHON".into() });
        }
        bail!("METNOS_PYTHON={} non e' un file", path.display());
    }

    let runtime_dir = cache_root.join("runtime");

    // 2. runtime gia' scaricato.
    if let Some(py) = find_cached_python(&runtime_dir) {
        return Ok(PyEnv { python: py, source: "cache:python-build-standalone".into() });
    }

    // 3. download lazy da mirror, se il pin e' configurato. Env per-OS
    //    (§16.2 W3.1): il pin e' specifico del target
    //    (cpython-*-x86_64-pc-windows-msvc-install_only.tar.gz su windows),
    //    quindi variabile dedicata invece di far indovinare il tarball giusto
    //    a chi configura un solo METNOS_PYTHON_RUNTIME condiviso fra device
    //    eterogenei.
    let pin_var = if cfg!(windows) { "METNOS_PYTHON_RUNTIME_WIN" } else { "METNOS_PYTHON_RUNTIME" };
    if let Ok(tarball) = std::env::var(pin_var) {
        // sha256 opzionale (baked accanto al pin, come per il binario client):
        // abilita la verifica end-to-end + escalation a consenso su rete che
        // corrompe. Assente = solo gzip/tar CRC come rete di sicurezza.
        let sha_var = format!("{}_SHA256", pin_var);
        let sha256 = std::env::var(&sha_var).ok().filter(|s| !s.is_empty());
        download_runtime(server, &tarball, sha256.as_deref(), &runtime_dir)
            .await
            .with_context(|| format!("download runtime {}", tarball))?;
        if let Some(py) = find_cached_python(&runtime_dir) {
            return Ok(PyEnv { python: py, source: "mirror:python-build-standalone".into() });
        }
        bail!("runtime scaricato ma nessun interprete trovato in {}", runtime_dir.display());
    }

    // 4. fallback di sistema — SOLO unix. Su Windows un python di sistema
    //    non e' verificato ne' garantito compatibile (§16.2 W3.1): niente
    //    fallback silenzioso, errore onesto (runner.rs lo traduce in
    //    error_class:"python_runtime_missing" verso il server).
    #[cfg(unix)]
    if let Some(py) = which("python3").or_else(|| which("python")) {
        tracing::warn!(
            "uso python3 di sistema ({}): per la parita' col server configurare \
             METNOS_PYTHON_RUNTIME (python-build-standalone dal mirror)",
            py.display()
        );
        return Ok(PyEnv { python: py, source: "system:python3".into() });
    }

    bail!(
        "nessun interprete Python: imposta METNOS_PYTHON o {} \
         (nessun fallback al python di sistema su questa piattaforma)",
        pin_var
    )
}

fn find_cached_python(runtime_dir: &Path) -> Option<PathBuf> {
    // python-build-standalone estrae in <dir>/python/bin/python3 (unix,
    // layout install_only) o <dir>/python/python.exe (windows, stesso layout
    // install_only ma senza sottodir bin/ — python.exe sta alla radice).
    let candidates = [
        runtime_dir.join("python").join("bin").join("python3"),
        runtime_dir.join("python").join("bin").join("python"),
        runtime_dir.join("python").join("python.exe"),
    ];
    candidates.into_iter().find(|p| p.is_file())
}

/// Scarica ed estrae il tarball python-build-standalone. Estrazione
/// PURE-Rust (§16.2 W3.1: sostituisce lo shell-out a `tar` di sistema —
/// unico path di codice per entrambe le piattaforme, nessuna dipendenza da
/// un `tar.exe` che su Windows potrebbe non esserci). Vincolo: solo
/// `.tar.gz` (le build pbs `install_only` lo offrono sempre; niente
/// `.tar.zst` = niente crate zstd).
// Download robusto (§12): la rete locale di Roberto stronca i flussi lunghi
// (46 MB reset a meta', visto live 3/7) e occasionalmente corrompe il
// contenuto sotto un 200 valido. Porta del disegno PROVATO
// `install/downloads.py::robust_fetch` (Python, server-side): il client e'
// Rust e non puo' chiamare quella funzione, ma l'algoritmo e' lo stesso —
// chunk Range con validazione+retry per chunk, resume da .part, escalation
// a doppio-fetch concorde se lo sha finale non torna. 8 MB/chunk e 10
// tentativi come downloads.py (`_CHUNK_BYTES`/`_CHUNK_ATTEMPTS`).
const RUNTIME_CHUNK: u64 = 8_000_000;
const RUNTIME_CHUNK_ATTEMPTS: u32 = 10;

async fn download_runtime(
    server: &str,
    tarball: &str,
    sha256: Option<&str>,
    runtime_dir: &Path,
) -> Result<()> {
    if tarball.contains('/') || tarball.contains("..") {
        bail!("nome tarball non sicuro: {}", tarball);
    }
    if !tarball.ends_with(".tar.gz") {
        bail!("formato tarball non supportato (solo .tar.gz): {}", tarball);
    }
    let url = format!("{}/agent/runtime/{}", server.trim_end_matches('/'), tarball);
    std::fs::create_dir_all(runtime_dir)?;
    let tmp = runtime_dir.join(format!("{}.part", tarball));

    // Client con timeout PER-RICHIESTA (non totale): un chunk da 8 MB in LAN
    // e' <1s; 30s scatta su uno stallo e fa ritentare quel solo chunk.
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()?;

    // Adaptive integrity (identico a downstream.py): primo giro fetch singolo
    // (rete pulita = 1x banda), se lo sha finale non torna secondo giro con
    // consenso per chunk. 2 passate al massimo.
    let want_sha = sha256.map(|s| s.to_ascii_lowercase());
    let mut verified = false;
    for attempt in 0..2u32 {
        let consensus = attempt == 1;
        fetch_robust(&client, &url, &tmp, consensus)
            .await
            .with_context(|| format!("download {}", tarball))?;
        match &want_sha {
            None => {
                verified = true; // niente sha: gzip/tar CRC resta la rete
                break;           // di sicurezza a valle
            }
            Some(want) => {
                let got = sha256_file(&tmp)?;
                if &got == want {
                    verified = true;
                    break;
                }
                let _ = std::fs::remove_file(&tmp); // parziale corrotto: via
                if consensus {
                    bail!(
                        "sha256 runtime NON combacia dopo consenso (atteso {}…, \
                         ottenuto {}…): la rete corrompe il contenuto",
                        &want[..16.min(want.len())], &got[..16]
                    );
                }
                tracing::warn!(
                    "sha256 runtime non combacia: la rete corrompe il \
                     contenuto — ritento con consenso per chunk"
                );
            }
        }
    }
    if !verified {
        bail!("download runtime non verificato");
    }

    // Estrazione da disco (BufReader): niente 46 MB in RAM. Blocking task per
    // non bloccare il reactor. Se l'estrazione fallisce (gzip/tar CRC su un
    // .part che l'hash non ha intercettato) scarta il .part per un nuovo
    // tentativo pulito al prossimo giro.
    let tmp_extract = tmp.clone();
    let dest = runtime_dir.to_path_buf();
    let res = tokio::task::spawn_blocking(move || extract_tar_gz_file(&tmp_extract, &dest))
        .await
        .context("task di estrazione tarball")?;
    match res {
        Ok(()) => {
            let _ = std::fs::remove_file(&tmp);
            Ok(())
        }
        Err(e) => {
            let _ = std::fs::remove_file(&tmp);
            Err(e).context("estrazione fallita; .part scartato per un nuovo tentativo")
        }
    }
}

/// (total_bytes, range_supported) via una GET di 1 byte — gemello di
/// `downloads.py::_probe`. 206+Content-Range => chunking possibile.
async fn probe(client: &reqwest::Client, url: &str) -> Result<(Option<u64>, bool)> {
    let resp = client
        .get(url)
        .header(reqwest::header::RANGE, "bytes=0-0")
        .send()
        .await
        .with_context(|| format!("probe {}", url))?;
    if resp.status() == reqwest::StatusCode::PARTIAL_CONTENT {
        if let Some(cr) = resp.headers().get(reqwest::header::CONTENT_RANGE) {
            if let Some(total) = cr
                .to_str()
                .ok()
                .and_then(|s| s.rsplit('/').next().map(|x| x.to_string()))
                .and_then(|t| t.parse::<u64>().ok())
            {
                return Ok((Some(total), true));
            }
        }
        return Ok((None, true));
    }
    if !resp.status().is_success() {
        bail!("probe runtime HTTP {}", resp.status());
    }
    let total = resp
        .headers()
        .get(reqwest::header::CONTENT_LENGTH)
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.parse::<u64>().ok());
    Ok((total, false))
}

/// Un fetch COMPLETO del range [start,end] → bytes validati, o None (reset,
/// status≠206, Content-Range sbagliato/shiftato, dimensione errata). Gemello
/// di `downloads.py::_one_fetch`: nessun resume intra-chunk (8 MB = breve).
async fn one_fetch(
    client: &reqwest::Client,
    url: &str,
    start: u64,
    end: u64,
) -> Option<Vec<u8>> {
    let want = (end - start + 1) as usize;
    let resp = client
        .get(url)
        .header(reqwest::header::RANGE, format!("bytes={}-{}", start, end))
        .send()
        .await
        .ok()?;
    if resp.status() != reqwest::StatusCode::PARTIAL_CONTENT {
        return None; // 200 = Range ignorato; qualsiasi altro = errore
    }
    // Il server DEVE servire ESATTAMENTE il range chiesto: un middlebox di
    // cache puo' rispondere un range stantio/shiftato sotto lo stesso 206.
    let cr = resp.headers().get(reqwest::header::CONTENT_RANGE)?;
    if !cr.to_str().ok()?.starts_with(&format!("bytes {}-{}/", start, end)) {
        return None;
    }
    let body = resp.bytes().await.ok()?;
    if body.len() != want {
        return None;
    }
    Some(body.to_vec())
}

/// Scarica [start,end] con retry; `consensus` = due fetch indipendenti che
/// coincidono (sha256), difesa contro la corruzione non deterministica.
/// Gemello di `downloads.py::_fetch_chunk`.
async fn fetch_chunk(
    client: &reqwest::Client,
    url: &str,
    start: u64,
    end: u64,
    consensus: bool,
) -> Result<Vec<u8>> {
    for _ in 0..RUNTIME_CHUNK_ATTEMPTS {
        let Some(a) = one_fetch(client, url, start, end).await else {
            tokio::time::sleep(std::time::Duration::from_millis(300)).await;
            continue;
        };
        if !consensus {
            return Ok(a);
        }
        if let Some(b) = one_fetch(client, url, start, end).await {
            if sha256_bytes(&a) == sha256_bytes(&b) {
                return Ok(a);
            }
        }
        tokio::time::sleep(std::time::Duration::from_millis(300)).await;
    }
    bail!("chunk {}-{} fallito dopo {} tentativi", start, end, RUNTIME_CHUNK_ATTEMPTS)
}

/// Riempie `tmp` con l'intero contenuto di `url`, chunk Range sequenziali con
/// resume da un `.part` parziale (crash/stallo precedente). Fallback a stream
/// singolo se il server non supporta i Range.
async fn fetch_robust(
    client: &reqwest::Client,
    url: &str,
    tmp: &Path,
    consensus: bool,
) -> Result<()> {
    use std::io::Write;
    let (total, ranges_ok) = probe(client, url).await?;
    let Some(total) = total else {
        bail!("dimensione runtime ignota dal server");
    };
    if !ranges_ok {
        return fetch_stream(client, url, tmp, total).await;
    }
    let mut have = std::fs::metadata(tmp).map(|m| m.len()).unwrap_or(0);
    if have > total {
        let _ = std::fs::remove_file(tmp); // .part piu' grande del reale: via
        have = 0;
    }
    if have == total {
        tracing::info!("runtime .part gia' completo, estraggo");
        return Ok(());
    }
    tracing::info!(total, resume_from = have, consensus,
                   "download python-build-standalone (chunk Range)");
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(tmp)
        .with_context(|| format!("apri {}", tmp.display()))?;
    let mut next_log = have + 16_000_000;
    while have < total {
        let end = (have + RUNTIME_CHUNK - 1).min(total - 1);
        let chunk = fetch_chunk(client, url, have, end, consensus).await?;
        f.write_all(&chunk).with_context(|| "scrittura chunk su .part")?;
        have += chunk.len() as u64;
        if have >= next_log {
            tracing::info!(pct = have * 100 / total, "runtime download");
            next_log = have + 16_000_000;
        }
    }
    f.flush().ok();
    let got = std::fs::metadata(tmp).map(|m| m.len()).unwrap_or(0);
    if got != total {
        bail!("runtime .part {} byte != atteso {}", got, total);
    }
    Ok(())
}

/// Fallback singola richiesta (server senza Range). Raro: il mirror Metnos
/// supporta i Range (aiohttp FileResponse); qui basta la robustezza del retry
/// esterno, niente streaming (evita una dipendenza per un percorso marginale).
async fn fetch_stream(
    client: &reqwest::Client,
    url: &str,
    tmp: &Path,
    total: u64,
) -> Result<()> {
    tracing::warn!("server senza Range: download runtime in singola richiesta");
    let resp = client.get(url).send().await?;
    if !resp.status().is_success() {
        bail!("runtime download HTTP {}", resp.status());
    }
    let bytes = resp.bytes().await.context("corpo runtime interrotto")?;
    if bytes.len() as u64 != total {
        bail!("runtime {} byte != atteso {} (singola richiesta)", bytes.len(), total);
    }
    std::fs::write(tmp, &bytes).with_context(|| format!("scrivi {}", tmp.display()))?;
    Ok(())
}

fn sha256_bytes(b: &[u8]) -> [u8; 32] {
    use sha2::{Digest, Sha256};
    let mut h = Sha256::new();
    h.update(b);
    h.finalize().into()
}

fn sha256_file(path: &Path) -> Result<String> {
    use sha2::{Digest, Sha256};
    let mut f = std::fs::File::open(path)
        .with_context(|| format!("apri {} per sha256", path.display()))?;
    let mut h = Sha256::new();
    std::io::copy(&mut f, &mut h).context("lettura per sha256")?;
    Ok(hex_lower(&h.finalize()))
}

fn hex_lower(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push_str(&format!("{:02x}", b));
    }
    s
}

fn extract_tar_gz_file(part: &Path, dest: &Path) -> Result<()> {
    let f = std::fs::File::open(part)
        .with_context(|| format!("apri {}", part.display()))?;
    let decoder = flate2::read::GzDecoder::new(std::io::BufReader::new(f));
    let mut archive = tar::Archive::new(decoder);
    archive
        .unpack(dest)
        .context("estrazione tarball (tar.gz malformato o I/O)")?;
    Ok(())
}

#[cfg(unix)]
fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path).find_map(|dir| {
        let full = dir.join(name);
        if full.is_file() {
            Some(full)
        } else {
            None
        }
    })
}

/// Verifica che un executor sia eseguibile con questo pyenv: per l'MVP
/// accettiamo solo executor stdlib-only (nessun `[dependencies]` non vuoto).
pub fn assert_stdlib_only(manifest_dir: &Path) -> Result<()> {
    let mpath = manifest_dir.join("manifest.toml");
    let text = std::fs::read_to_string(&mpath).context("read manifest for deps check")?;
    let manifest: toml::Value = toml::from_str(&text)?;
    let has_deps = manifest
        .get("dependencies")
        .and_then(|d| d.as_array())
        .map(|a| !a.is_empty())
        .unwrap_or(false);
    if has_deps {
        return Err(anyhow!(
            "executor con dipendenze non-stdlib: venv/uv non ancora cablato (W5)"
        ));
    }
    Ok(())
}
