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
        download_runtime(server, &tarball, &runtime_dir)
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
async fn download_runtime(server: &str, tarball: &str, runtime_dir: &Path) -> Result<()> {
    if tarball.contains('/') || tarball.contains("..") {
        bail!("nome tarball non sicuro: {}", tarball);
    }
    if !tarball.ends_with(".tar.gz") {
        bail!("formato tarball non supportato (solo .tar.gz): {}", tarball);
    }
    let url = format!("{}/agent/runtime/{}", server.trim_end_matches('/'), tarball);
    tracing::info!(%url, "download python-build-standalone");
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(600))
        .build()?;
    let resp = client.get(&url).send().await.with_context(|| format!("GET {}", url))?;
    if !resp.status().is_success() {
        bail!("runtime download HTTP {}", resp.status());
    }
    let bytes = resp.bytes().await?;

    std::fs::create_dir_all(runtime_dir)?;
    let bytes_owned = bytes.to_vec();
    let dest = runtime_dir.to_path_buf();
    // Estrazione sincrona (I/O-bound su disco locale, breve): eseguita in un
    // blocking task per non bloccare il reactor tokio.
    tokio::task::spawn_blocking(move || extract_tar_gz(&bytes_owned, &dest))
        .await
        .context("task di estrazione tarball")??;
    Ok(())
}

fn extract_tar_gz(bytes: &[u8], dest: &Path) -> Result<()> {
    let decoder = flate2::read::GzDecoder::new(bytes);
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
