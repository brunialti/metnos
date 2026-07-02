//! pyenv.rs — risoluzione dell'interprete Python sul device (§2/§8 design doc).
//!
//! Piano di progetto: python-build-standalone (Astral) scaricato lazy dal
//! mirror del server, cache read-only, uv per venv/wheel. Per il primo giro
//! (W1-2, executor stdlib-only come find_packages) l'interprete si risolve
//! con questa precedenza, la scelta e' logata (§2.8, mai silenziosa):
//!
//!   1. env `METNOS_PYTHON` — path esplicito (test/override).
//!   2. runtime scaricato in cache (`<cache>/runtime/.../bin/python3`).
//!   3. download da `<server>/agent/runtime/<tarball>` se `METNOS_PYTHON_RUNTIME`
//!      indica il nome del tarball da tirare dal mirror (pin gestito server-side).
//!   4. `python3` di sistema (fallback pragmatico MVP; wheel non garantiti).
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

    // 3. download lazy da mirror, se il pin e' configurato.
    if let Ok(tarball) = std::env::var("METNOS_PYTHON_RUNTIME") {
        download_runtime(server, &tarball, &runtime_dir)
            .await
            .with_context(|| format!("download runtime {}", tarball))?;
        if let Some(py) = find_cached_python(&runtime_dir) {
            return Ok(PyEnv { python: py, source: "mirror:python-build-standalone".into() });
        }
        bail!("runtime scaricato ma nessun bin/python3 trovato in {}", runtime_dir.display());
    }

    // 4. fallback di sistema.
    if let Some(py) = which("python3").or_else(|| which("python")) {
        tracing::warn!(
            "uso python3 di sistema ({}): per la parita' col server configurare \
             METNOS_PYTHON_RUNTIME (python-build-standalone dal mirror)",
            py.display()
        );
        return Ok(PyEnv { python: py, source: "system:python3".into() });
    }

    bail!("nessun interprete Python: imposta METNOS_PYTHON o METNOS_PYTHON_RUNTIME")
}

fn find_cached_python(runtime_dir: &Path) -> Option<PathBuf> {
    // python-build-standalone estrae in <dir>/python/bin/python3 (linux/macos).
    let candidates = [
        runtime_dir.join("python").join("bin").join("python3"),
        runtime_dir.join("python").join("bin").join("python"),
    ];
    candidates.into_iter().find(|p| p.is_file())
}

async fn download_runtime(server: &str, tarball: &str, runtime_dir: &Path) -> Result<()> {
    if tarball.contains('/') || tarball.contains("..") {
        bail!("nome tarball non sicuro: {}", tarball);
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
    let tar_path = runtime_dir.join(tarball);
    std::fs::write(&tar_path, &bytes)?;
    // Estrazione via `tar` di sistema (zstd/gzip a seconda dell'estensione).
    // python-build-standalone: `.tar.gz` o `.tar.zst`. tar moderno gestisce
    // entrambi con --auto-compress; fallback esplicito su gzip.
    let status = std::process::Command::new("tar")
        .arg("-xf")
        .arg(&tar_path)
        .arg("-C")
        .arg(runtime_dir)
        .status()
        .context("estrazione tarball (tar non disponibile?)")?;
    if !status.success() {
        bail!("estrazione runtime fallita");
    }
    Ok(())
}

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
