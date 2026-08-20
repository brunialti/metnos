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
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

use crate::identity;

pub struct PyEnv {
    pub python: PathBuf,
    pub source: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct RuntimeDescriptor {
    version: u32,
    filename: String,
    archive_sha256: String,
    archive_size: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct RuntimeEnvelope {
    descriptor: RuntimeDescriptor,
    sig: String,
}

pub async fn resolve(server: &str, server_pubkey: &str, cache_root: &Path) -> Result<PyEnv> {
    // 1. override esplicito.
    if let Ok(p) = std::env::var("METNOS_PYTHON") {
        let path = PathBuf::from(p);
        if path.is_file() {
            return Ok(PyEnv {
                python: path,
                source: "env:METNOS_PYTHON".into(),
            });
        }
        bail!("METNOS_PYTHON={} non e' un file", path.display());
    }

    let runtime_dir = cache_root.join("runtime");

    // 2. Runtime da mirror: il nome configurato non e' una radice di fiducia.
    //    Il server restituisce un descrittore Ed25519 con hash+dimensione;
    //    il client lo verifica con la stessa pubkey pinnata delle invocazioni.
    //    (§16.2 W3.1): il pin e' specifico del target
    //    (cpython-*-x86_64-pc-windows-msvc-install_only.tar.gz su windows),
    //    quindi variabile dedicata invece di far indovinare il tarball giusto
    //    a chi configura un solo METNOS_PYTHON_RUNTIME condiviso fra device
    //    eterogenei.
    let pin_var = if cfg!(windows) {
        "METNOS_PYTHON_RUNTIME_WIN"
    } else {
        "METNOS_PYTHON_RUNTIME"
    };
    if let Ok(tarball) = std::env::var(pin_var) {
        validate_tarball_name(&tarball)?;
        let envelope =
            fetch_or_cached_descriptor(server, server_pubkey, &tarball, &runtime_dir).await?;
        let python = ensure_verified_runtime(server, &envelope.descriptor, &runtime_dir).await?;
        return Ok(PyEnv {
            python,
            source: "signed-mirror:python-build-standalone".into(),
        });
    }

    // Compatibilita' offline: un runtime gia' ammesso resta utilizzabile solo
    // se il suo descrittore cached e' firmato e l'archivio conserva lo hash.
    if let Ok(envelope) = load_cached_descriptor(server_pubkey, &runtime_dir, None) {
        let python = ensure_verified_runtime(server, &envelope.descriptor, &runtime_dir).await?;
        return Ok(PyEnv {
            python,
            source: "signed-cache:python-build-standalone".into(),
        });
    }

    // 3. fallback di sistema — SOLO unix. Su Windows un python di sistema
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
        return Ok(PyEnv {
            python: py,
            source: "system:python3".into(),
        });
    }

    bail!(
        "nessun interprete Python: imposta METNOS_PYTHON o {} \
         (nessun fallback al python di sistema su questa piattaforma)",
        pin_var
    )
}

fn validate_tarball_name(tarball: &str) -> Result<()> {
    if tarball.is_empty()
        || tarball.len() > 240
        || tarball.contains('/')
        || tarball.contains('\\')
        || tarball.contains("..")
        || !tarball
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-' | b'+'))
    {
        bail!("nome tarball non sicuro: {}", tarball);
    }
    if !tarball.ends_with(".tar.gz") {
        bail!("formato tarball non supportato (solo .tar.gz): {}", tarball);
    }
    Ok(())
}

fn descriptor_path(runtime_dir: &Path) -> PathBuf {
    runtime_dir.join("runtime-descriptor.json")
}

fn verify_descriptor(
    server_pubkey: &str,
    envelope: &RuntimeEnvelope,
    expected_filename: Option<&str>,
) -> Result<()> {
    let descriptor = &envelope.descriptor;
    validate_tarball_name(&descriptor.filename)?;
    if descriptor.version != 1
        || descriptor.archive_size == 0
        || descriptor.archive_sha256.len() != 64
        || !descriptor
            .archive_sha256
            .bytes()
            .all(|b| b.is_ascii_hexdigit())
    {
        bail!("descrittore runtime malformato");
    }
    if expected_filename.is_some_and(|name| name != descriptor.filename) {
        bail!("descrittore runtime riferito a un artefatto differente");
    }
    let value = serde_json::to_value(descriptor)?;
    let canonical = crate::wire::canonical_bytes(&value)?;
    identity::verify_b64(server_pubkey, &envelope.sig, &canonical)
        .context("firma descrittore runtime non verificata")
}

fn load_cached_descriptor(
    server_pubkey: &str,
    runtime_dir: &Path,
    expected_filename: Option<&str>,
) -> Result<RuntimeEnvelope> {
    let body = std::fs::read(descriptor_path(runtime_dir))
        .context("descrittore runtime cached assente")?;
    let envelope: RuntimeEnvelope =
        serde_json::from_slice(&body).context("descrittore runtime cached malformato")?;
    verify_descriptor(server_pubkey, &envelope, expected_filename)?;
    Ok(envelope)
}

async fn fetch_or_cached_descriptor(
    server: &str,
    server_pubkey: &str,
    tarball: &str,
    runtime_dir: &Path,
) -> Result<RuntimeEnvelope> {
    let url = format!(
        "{}/agent/runtime/descriptor/{}",
        server.trim_end_matches('/'),
        tarball
    );
    let fetched = async {
        let response = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(30))
            .build()?
            .get(&url)
            .send()
            .await
            .context("download descrittore runtime")?
            .error_for_status()
            .context("status descrittore runtime")?;
        let envelope: RuntimeEnvelope =
            response.json().await.context("parse descrittore runtime")?;
        verify_descriptor(server_pubkey, &envelope, Some(tarball))?;
        std::fs::create_dir_all(runtime_dir)?;
        let path = descriptor_path(runtime_dir);
        let tmp = path.with_extension("tmp");
        std::fs::write(&tmp, serde_json::to_vec(&envelope)?)?;
        std::fs::rename(&tmp, &path)?;
        Ok::<RuntimeEnvelope, anyhow::Error>(envelope)
    }
    .await;
    match fetched {
        Ok(envelope) => Ok(envelope),
        Err(network_error) => {
            tracing::warn!("descrittore runtime remoto non disponibile: {network_error:#}; provo cache firmata");
            load_cached_descriptor(server_pubkey, runtime_dir, Some(tarball))
                .context("nessun descrittore runtime firmato utilizzabile")
        }
    }
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

fn verified_archive_path(runtime_dir: &Path, filename: &str) -> PathBuf {
    runtime_dir.join(format!("{filename}.verified"))
}

async fn ensure_verified_runtime(
    server: &str,
    descriptor: &RuntimeDescriptor,
    runtime_dir: &Path,
) -> Result<PathBuf> {
    let archive = verified_archive_path(runtime_dir, &descriptor.filename);
    let valid_cache = std::fs::metadata(&archive)
        .ok()
        .is_some_and(|m| m.len() == descriptor.archive_size)
        && sha256_file(&archive)
            .ok()
            .as_deref()
            .is_some_and(|got| got.eq_ignore_ascii_case(&descriptor.archive_sha256));
    if !valid_cache {
        if archive.exists() {
            tracing::warn!(file = %archive.display(),
                           "archivio runtime cached non integro: riscarico");
            std::fs::remove_file(&archive).context("rimozione runtime cache corrotta")?;
        }
        download_runtime(server, descriptor, runtime_dir).await?;
    }

    // Ogni avvio ricostruisce l'albero eseguibile dall'archivio il cui hash e'
    // firmato. Cosi' una modifica offline a python/stdlib non sopravvive al
    // restart del client, senza dover firmare migliaia di file separatamente.
    let archive_for_extract = archive.clone();
    let dest = runtime_dir.to_path_buf();
    tokio::task::spawn_blocking(move || install_verified_archive(&archive_for_extract, &dest))
        .await
        .context("task installazione runtime")??;
    find_cached_python(runtime_dir).context("runtime firmato installato senza interprete")
}

async fn download_runtime(
    server: &str,
    descriptor: &RuntimeDescriptor,
    runtime_dir: &Path,
) -> Result<()> {
    let tarball = &descriptor.filename;
    validate_tarball_name(tarball)?;
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
    let want_sha = descriptor.archive_sha256.to_ascii_lowercase();
    let mut verified = false;
    for attempt in 0..2u32 {
        let consensus = attempt == 1;
        fetch_robust(&client, &url, &tmp, consensus)
            .await
            .with_context(|| format!("download {}", tarball))?;
        let got = sha256_file(&tmp)?;
        let size_ok =
            std::fs::metadata(&tmp).map(|m| m.len()).unwrap_or(0) == descriptor.archive_size;
        if got.eq_ignore_ascii_case(&want_sha) && size_ok {
            verified = true;
            break;
        }
        let _ = std::fs::remove_file(&tmp); // parziale corrotto: via
        if consensus {
            bail!(
                "runtime NON combacia col descrittore firmato (atteso {}…, \
                 ottenuto {}…)",
                &want_sha[..16],
                &got[..16]
            );
        }
        tracing::warn!("runtime non combacia col descrittore: ritento con consenso per chunk");
    }
    if !verified {
        bail!("download runtime non verificato");
    }

    let archive = verified_archive_path(runtime_dir, tarball);
    if archive.exists() {
        std::fs::remove_file(&archive)?;
    }
    std::fs::rename(&tmp, &archive).context("promozione archivio runtime verificato")?;
    Ok(())
}

fn install_verified_archive(archive: &Path, runtime_dir: &Path) -> Result<()> {
    let staging = runtime_dir.join(".runtime-installing");
    if staging.exists() {
        std::fs::remove_dir_all(&staging)?;
    }
    std::fs::create_dir_all(&staging)?;
    if let Err(e) = extract_tar_gz_file(archive, &staging) {
        let _ = std::fs::remove_dir_all(&staging);
        return Err(e).context("estrazione archivio runtime verificato");
    }
    let staged_python = staging.join("python");
    if find_cached_python(&staging).is_none() || !staged_python.is_dir() {
        let _ = std::fs::remove_dir_all(&staging);
        bail!("archivio runtime firmato privo dell'albero python atteso");
    }
    let live_python = runtime_dir.join("python");
    if live_python.exists() {
        std::fs::remove_dir_all(&live_python)?;
    }
    match std::fs::rename(&staged_python, &live_python) {
        Ok(()) => {
            let _ = std::fs::remove_dir_all(&staging);
            Ok(())
        }
        Err(e) => {
            let _ = std::fs::remove_dir_all(&staging);
            Err(e).context("promozione albero runtime verificato")
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
async fn one_fetch(client: &reqwest::Client, url: &str, start: u64, end: u64) -> Option<Vec<u8>> {
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
    if !cr
        .to_str()
        .ok()?
        .starts_with(&format!("bytes {}-{}/", start, end))
    {
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
    bail!(
        "chunk {}-{} fallito dopo {} tentativi",
        start,
        end,
        RUNTIME_CHUNK_ATTEMPTS
    )
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
    tracing::info!(
        total,
        resume_from = have,
        consensus,
        "download python-build-standalone (chunk Range)"
    );
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(tmp)
        .with_context(|| format!("apri {}", tmp.display()))?;
    let mut next_log = have + 16_000_000;
    while have < total {
        let end = (have + RUNTIME_CHUNK - 1).min(total - 1);
        let chunk = fetch_chunk(client, url, have, end, consensus).await?;
        f.write_all(&chunk)
            .with_context(|| "scrittura chunk su .part")?;
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
async fn fetch_stream(client: &reqwest::Client, url: &str, tmp: &Path, total: u64) -> Result<()> {
    tracing::warn!("server senza Range: download runtime in singola richiesta");
    let resp = client.get(url).send().await?;
    if !resp.status().is_success() {
        bail!("runtime download HTTP {}", resp.status());
    }
    let bytes = resp.bytes().await.context("corpo runtime interrotto")?;
    if bytes.len() as u64 != total {
        bail!(
            "runtime {} byte != atteso {} (singola richiesta)",
            bytes.len(),
            total
        );
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
    let mut f =
        std::fs::File::open(path).with_context(|| format!("apri {} per sha256", path.display()))?;
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
    let f = std::fs::File::open(part).with_context(|| format!("apri {}", part.display()))?;
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

#[cfg(test)]
mod descriptor_tests {
    use super::*;
    use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
    use ed25519_dalek::{Signer, SigningKey};
    use rand::rngs::OsRng;

    fn signed_envelope() -> (String, RuntimeEnvelope) {
        let signing = SigningKey::generate(&mut OsRng);
        let descriptor = RuntimeDescriptor {
            version: 1,
            filename: "cpython-test-install_only.tar.gz".into(),
            archive_sha256: "a".repeat(64),
            archive_size: 123,
        };
        let canonical =
            crate::wire::canonical_bytes(&serde_json::to_value(&descriptor).unwrap()).unwrap();
        let sig = URL_SAFE_NO_PAD.encode(signing.sign(&canonical).to_bytes());
        let public = URL_SAFE_NO_PAD.encode(signing.verifying_key().to_bytes());
        (public, RuntimeEnvelope { descriptor, sig })
    }

    #[test]
    fn runtime_descriptor_is_pinned_and_tamper_evident() {
        let (public, mut envelope) = signed_envelope();
        assert!(
            verify_descriptor(&public, &envelope, Some("cpython-test-install_only.tar.gz")).is_ok()
        );
        envelope.descriptor.archive_size += 1;
        assert!(
            verify_descriptor(&public, &envelope, Some("cpython-test-install_only.tar.gz"))
                .is_err()
        );
    }

    #[test]
    fn runtime_descriptor_rejects_filename_substitution() {
        let (public, envelope) = signed_envelope();
        assert!(verify_descriptor(&public, &envelope, Some("different.tar.gz")).is_err());
        for bad in ["../x.tar.gz", "a/b.tar.gz", "x.zip", ""] {
            assert!(validate_tarball_name(bad).is_err(), "accepted {bad:?}");
        }
    }
}
