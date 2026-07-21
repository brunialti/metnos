//! executors.rs — cache locale degli executor (pull-on-miss §8 del design doc).
//!
//! Un'invocazione porta `manifest_sha256`+`code_sha256`. Se il client non li
//! ha in cache, tira il bundle da `/agent/executor/{name}`, VERIFICA
//! (firma manifest con la pubkey server pinnata + digest sha256 del codice),
//! poi lo scrive nella cache hash-keyed immutabile. Ri-verifica ad ogni uso.

use anyhow::{anyhow, bail, Context, Result};
use base64::{engine::general_purpose::STANDARD as B64, Engine as _};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use crate::identity;

#[derive(Debug, Deserialize)]
struct ExecutorBundle {
    name: String,
    /// manifest.toml in base64 (standard).
    manifest_toml: String,
    /// firma detached del manifest (base64 standard) — Ed25519 del server.
    manifest_sig: String,
    /// file di codice: nome → contenuto base64.
    files: BTreeMap<String, String>,
}

#[derive(Debug, Deserialize)]
struct ShimBundle {
    files: BTreeMap<String, String>,
    sig: String,
    /// Content-addressing (0.2.15): sha del bundle dichiarato dal server.
    #[serde(default)]
    sha256: String,
}

/// Un executor pronto all'uso nella cache: dir con manifest+codice verificati.
pub struct CachedExecutor {
    pub name: String,
    pub dir: PathBuf,
    /// file di codice principale (primo in [code].files del manifest).
    pub entry: PathBuf,
    /// capabilities dal manifest, per la sandbox. Su Linux → bind bwrap
    /// (`sandbox_linux::bwrap_args`); su Windows → ACL sul SID del container
    /// (AppContainer, W4: `sandbox_common::hint_grants` → `appcontainer.rs`).
    /// La traduzione capability→permessi e' ora reale su ENTRAMBE le piattaforme.
    pub capabilities: Vec<Capability>,
}

#[derive(Debug, Clone)]
pub struct Capability {
    pub name: String,
    pub hint: Vec<String>,
}

/// Scarica (se serve), verifica e materializza un executor nella cache.
pub async fn ensure_executor(
    server: &str,
    server_pubkey: &str,
    name: &str,
    manifest_sha256: &str,
    code_sha256: &str,
    cache_root: &Path,
) -> Result<CachedExecutor> {
    // Cache key = manifest_sha: manifest immutabile => dir immutabile.
    let dir = cache_root.join(format!("{}-{}", name, &manifest_sha256[..16.min(manifest_sha256.len())]));
    let manifest_path = dir.join("manifest.toml");

    if !manifest_path.is_file() {
        let bundle = fetch_executor(server, name).await?;
        materialize(&bundle, server_pubkey, manifest_sha256, code_sha256, &dir)?;
    }

    let manifest_bytes = std::fs::read(&manifest_path)?;
    // Ri-verifica difensiva: digest manifest = quello atteso dall'invocazione.
    let got = hex_sha256(&manifest_bytes);
    if got != manifest_sha256 {
        bail!("manifest cache corrotto per {}: {} != {}", name, got, manifest_sha256);
    }
    let manifest: toml::Value = toml::from_str(&String::from_utf8_lossy(&manifest_bytes))
        .context("parse manifest cache")?;
    let entry = first_code_file(&manifest, &dir)?;
    let capabilities = parse_capabilities(&manifest);
    Ok(CachedExecutor { name: name.to_string(), dir, entry, capabilities })
}

async fn fetch_executor(server: &str, name: &str) -> Result<ExecutorBundle> {
    let url = format!("{}/agent/executor/{}", server.trim_end_matches('/'), name);
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()?;
    let resp = client.get(&url).send().await.with_context(|| format!("GET {}", url))?;
    if !resp.status().is_success() {
        bail!("executor bundle {} HTTP {}", name, resp.status());
    }
    resp.json().await.context("parse executor bundle")
}

fn materialize(
    bundle: &ExecutorBundle,
    server_pubkey: &str,
    manifest_sha256: &str,
    code_sha256: &str,
    dir: &Path,
) -> Result<()> {
    let manifest_bytes = B64.decode(&bundle.manifest_toml).context("decode manifest")?;
    let sig_bytes = B64.decode(&bundle.manifest_sig).context("decode manifest sig")?;

    // 1. digest manifest atteso.
    let got_manifest = hex_sha256(&manifest_bytes);
    if got_manifest != manifest_sha256 {
        bail!("manifest sha mismatch: {} != {}", got_manifest, manifest_sha256);
    }
    // 2. firma del manifest verificata con la pubkey server pinnata (§8).
    //    Il server firma i BYTES del manifest (sign.py::sign_executor), b64 std.
    let sig_b64u = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(&sig_bytes);
    identity::verify_b64(server_pubkey, &sig_b64u, &manifest_bytes)
        .context("firma manifest non verificata con pubkey server pinnata")?;

    // 3. digest del codice = concatenazione dei file in ordine dichiarato.
    let manifest: toml::Value = toml::from_str(&String::from_utf8_lossy(&manifest_bytes))?;
    let declared_files = code_file_names(&manifest);
    let mut hasher = Sha256::new();
    let mut decoded: BTreeMap<String, Vec<u8>> = BTreeMap::new();
    for fname in &declared_files {
        let b64 = bundle
            .files
            .get(fname)
            .ok_or_else(|| anyhow!("bundle privo del file {}", fname))?;
        let data = B64.decode(b64).with_context(|| format!("decode {}", fname))?;
        hasher.update(&data);
        decoded.insert(fname.clone(), data);
    }
    let got_code = format!("{:x}", hasher.finalize());
    if got_code != code_sha256 {
        bail!("code sha mismatch: {} != {}", got_code, code_sha256);
    }

    // 4. scrittura atomica: tmp dir + rename.
    let tmp = dir.with_extension("tmp");
    let _ = std::fs::remove_dir_all(&tmp);
    std::fs::create_dir_all(&tmp)?;
    std::fs::write(tmp.join("manifest.toml"), &manifest_bytes)?;
    std::fs::write(tmp.join("manifest.toml.sig"), &sig_bytes)?;
    for (fname, data) in &decoded {
        std::fs::write(tmp.join(fname), data)?;
    }
    let _ = std::fs::remove_dir_all(dir);
    std::fs::rename(&tmp, dir).context("rename executor cache dir")?;
    tracing::info!(executor = %bundle.name, "executor verificato e messo in cache");
    Ok(())
}

/// Scarica e verifica il bundle shim (executor_helpers + messages fallback)
/// nella dir data. Ritorna la dir dello shim (da mettere su PYTHONPATH).
pub async fn ensure_shim(server: &str, server_pubkey: &str, cache_root: &Path) -> Result<(PathBuf, String)> {
    let dir = cache_root.join("shim");
    let url = format!("{}/agent/shim", server.trim_end_matches('/'));
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()?;
    let resp = client.get(&url).send().await.with_context(|| format!("GET {}", url))?;
    if !resp.status().is_success() {
        bail!("shim bundle HTTP {}", resp.status());
    }
    let bundle: ShimBundle = resp.json().await.context("parse shim bundle")?;

    // Firma: il server firma canonical({"files": {name: b64}}). Ricostruiamo
    // lo stesso Value e verifichiamo con la pubkey pinnata.
    let mut files_val = serde_json::Map::new();
    for (k, v) in &bundle.files {
        files_val.insert(k.clone(), serde_json::Value::String(v.clone()));
    }
    let payload = serde_json::json!({ "files": serde_json::Value::Object(files_val) });
    let canon = crate::wire::canonical_bytes(&payload)?;
    identity::verify_b64(server_pubkey, &bundle.sig, &canon)
        .context("firma shim non verificata")?;

    let tmp = dir.with_extension("tmp");
    let _ = std::fs::remove_dir_all(&tmp);
    std::fs::create_dir_all(&tmp)?;
    for (fname, b64) in &bundle.files {
        let rel = shim_rel_path(fname)?;
        let dest = tmp.join(&rel);
        if let Some(parent) = dest.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let data = B64.decode(b64).with_context(|| format!("decode shim {}", fname))?;
        std::fs::write(dest, data)?;
    }
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::rename(&tmp, &dir)?;
    Ok((dir, bundle.sha256))
}

/// C7 CP1 (0.2.10): valida un nome-file del bundle shim e lo mappa a un path
/// RELATIVO OS-nativo. Il bundle porta anche ALBERI-package (es.
/// `backends/files/local.py`) — ammessi sotto-path relativi con separatore
/// '/' (formato wire). Vietati: '..' (traversal), '\' (separatore nativo nel
/// wire), ':' (drive/ADS windows), slash iniziale/finale (assoluti) e
/// segmenti vuoti o '.'. Il bundle resta firmato dal server (rilievo #6).
fn shim_rel_path(fname: &str) -> Result<std::path::PathBuf> {
    if fname.is_empty() || fname.contains('\\') || fname.contains(':')
        || fname.starts_with('/') || fname.ends_with('/') {
        bail!("nome file shim non sicuro: {}", fname);
    }
    let mut rel = std::path::PathBuf::new();
    for seg in fname.split('/') {
        if seg.is_empty() || seg == "." || seg == ".." {
            bail!("segmento shim non sicuro in {}", fname);
        }
        rel.push(seg);
    }
    Ok(rel)
}

#[cfg(test)]
mod shim_tests {
    use super::shim_rel_path;

    #[test]
    fn flat_and_tree_ok() {
        assert!(shim_rel_path("messages.py").is_ok());
        let p = shim_rel_path("backends/files/local.py").unwrap();
        assert_eq!(p.iter().count(), 3);
    }

    #[test]
    fn traversal_and_bad_forms_rejected() {
        for bad in ["../evil.py", "a/../b.py", "a/./b.py", "/abs.py",
                    "dir/", "a//b.py", "c:\\win.py", "a\\b.py", "", "x:y"] {
            assert!(shim_rel_path(bad).is_err(), "accettato: {}", bad);
        }
    }
}

fn code_file_names(manifest: &toml::Value) -> Vec<String> {
    manifest
        .get("code")
        .and_then(|c| c.get("files"))
        .and_then(|f| f.as_array())
        .map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect())
        .unwrap_or_default()
}

fn first_code_file(manifest: &toml::Value, dir: &Path) -> Result<PathBuf> {
    let names = code_file_names(manifest);
    let first = names.first().ok_or_else(|| anyhow!("manifest senza [code].files"))?;
    Ok(dir.join(first))
}

fn parse_capabilities(manifest: &toml::Value) -> Vec<Capability> {
    let mut out = Vec::new();
    if let Some(arr) = manifest.get("capabilities").and_then(|c| c.as_array()) {
        for c in arr {
            let name = c.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let hint = c
                .get("hint")
                .and_then(|h| h.as_array())
                .map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect())
                .unwrap_or_default();
            if !name.is_empty() {
                out.push(Capability { name, hint });
            }
        }
    }
    out
}

fn hex_sha256(data: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(data);
    format!("{:x}", h.finalize())
}
