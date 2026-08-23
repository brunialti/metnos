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
    /// Livello minimo firmato dal manifest per Windows. `appcontainer`
    /// impedisce il fallback a un mero Job Object (nessun isolamento FS).
    #[cfg_attr(not(windows), allow(dead_code))]
    pub min_sandbox: String,
    /// Lazy read-only providers authorised by the signed manifest.
    pub managed_providers: Vec<ManagedProviderDependency>,
    /// True only when the verified manifest declares both `revertible=true`
    /// and `module.reverse` as a reverse pattern.
    pub module_reverse: bool,
}

#[derive(Debug, Clone)]
pub struct Capability {
    pub name: String,
    pub hint: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ManagedProviderDependency {
    pub key: String,
    pub package_id: String,
    pub interface: String,
    pub domains_arg: String,
    pub sensor_types_arg: String,
    pub assembly: String,
    pub entry_type: String,
}

/// Resolve one closed provider selection from the verified invocation args.
pub fn managed_provider_selection(
    args: &serde_json::Value,
    dependency: &ManagedProviderDependency,
) -> Result<Option<(Vec<String>, Vec<String>)>> {
    fn selectors(value: Option<&serde_json::Value>) -> Result<Vec<String>> {
        let Some(value) = value else {
            return Ok(Vec::new());
        };
        let rows = value
            .as_array()
            .ok_or_else(|| anyhow!("managed provider selectors must be arrays"))?;
        if rows.len() > 16 {
            bail!("managed provider selector limit exceeded");
        }
        let mut values = Vec::with_capacity(rows.len());
        for row in rows {
            let value = row
                .as_str()
                .ok_or_else(|| anyhow!("managed provider selector must be a string"))?;
            if value.len() > 32 || !safe_identifier(value) {
                bail!("managed provider selector is invalid");
            }
            values.push(value.to_string());
        }
        values.sort();
        if values.windows(2).any(|pair| pair[0] == pair[1]) {
            bail!("managed provider selector is duplicated");
        }
        Ok(values)
    }

    let domains = selectors(args.get(&dependency.domains_arg))?;
    let sensor_types = selectors(args.get(&dependency.sensor_types_arg))?;
    if domains.is_empty() && sensor_types.is_empty() {
        return Ok(None);
    }
    if domains.is_empty() || sensor_types.is_empty() {
        bail!("managed provider selector lists must be provided together");
    }
    Ok(Some((domains, sensor_types)))
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
    validate_cache_key(name, manifest_sha256, code_sha256)?;
    // Cache key = manifest_sha: manifest immutabile => dir immutabile.
    let dir = cache_root.join(format!(
        "{}-{}",
        name,
        &manifest_sha256[..16.min(manifest_sha256.len())]
    ));
    let manifest_path = dir.join("manifest.toml");

    if manifest_path.is_file() {
        match verify_cached(&dir, server_pubkey, manifest_sha256, code_sha256) {
            Ok((manifest, entry)) => {
                let capabilities = parse_capabilities(&manifest);
                let min_sandbox = parse_min_sandbox(&manifest)?;
                let managed_providers = parse_managed_providers(&manifest)?;
                let module_reverse = manifest_allows_module_reverse(&manifest);
                return Ok(CachedExecutor {
                    name: name.to_string(),
                    dir,
                    entry,
                    capabilities,
                    min_sandbox,
                    managed_providers,
                    module_reverse,
                });
            }
            Err(e) => {
                tracing::warn!(
                    executor = name,
                    "cache executor non integra, refetch firmato: {e:#}"
                );
                std::fs::remove_dir_all(&dir).context("rimozione cache executor corrotta")?;
            }
        }
    }

    let bundle = fetch_executor(server, name).await?;
    if bundle.name != name {
        bail!("bundle executor inatteso: {} != {}", bundle.name, name);
    }
    materialize(&bundle, server_pubkey, manifest_sha256, code_sha256, &dir)?;
    let (manifest, entry) = verify_cached(&dir, server_pubkey, manifest_sha256, code_sha256)?;
    let capabilities = parse_capabilities(&manifest);
    let min_sandbox = parse_min_sandbox(&manifest)?;
    let managed_providers = parse_managed_providers(&manifest)?;
    let module_reverse = manifest_allows_module_reverse(&manifest);
    Ok(CachedExecutor {
        name: name.to_string(),
        dir,
        entry,
        capabilities,
        min_sandbox,
        managed_providers,
        module_reverse,
    })
}

fn manifest_allows_module_reverse(manifest: &toml::Value) -> bool {
    if manifest.get("revertible").and_then(toml::Value::as_bool) != Some(true) {
        return false;
    }
    match manifest.get("reverse_pattern") {
        Some(toml::Value::String(value)) => value == "module.reverse",
        Some(toml::Value::Array(values)) => values
            .iter()
            .any(|value| value.as_str() == Some("module.reverse")),
        _ => false,
    }
}

fn validate_cache_key(name: &str, manifest_sha256: &str, code_sha256: &str) -> Result<()> {
    let valid_name = name.len() >= 2
        && name.len() <= 65
        && name.bytes().enumerate().all(|(i, b)| {
            if i == 0 {
                b.is_ascii_lowercase()
            } else {
                b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_' || b == b'-'
            }
        });
    let valid_hash = |s: &str| s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit());
    if !valid_name || !valid_hash(manifest_sha256) || !valid_hash(code_sha256) {
        bail!("chiave cache executor non valida");
    }
    Ok(())
}

fn verify_cached(
    dir: &Path,
    server_pubkey: &str,
    manifest_sha256: &str,
    code_sha256: &str,
) -> Result<(toml::Value, PathBuf)> {
    let manifest_bytes = std::fs::read(dir.join("manifest.toml"))?;
    let got_manifest = hex_sha256(&manifest_bytes);
    if !got_manifest.eq_ignore_ascii_case(manifest_sha256) {
        bail!(
            "manifest cache corrotto: {} != {}",
            got_manifest,
            manifest_sha256
        );
    }

    // La firma viene verificata anche sui cache hit: il digest portato
    // dall'invocazione da solo non sostituisce l'origine autenticata.
    let sig_bytes = std::fs::read(dir.join("manifest.toml.sig"))?;
    let sig_b64u = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(sig_bytes);
    identity::verify_b64(server_pubkey, &sig_b64u, &manifest_bytes)
        .context("firma manifest cache non verificata")?;

    let manifest: toml::Value = toml::from_str(&String::from_utf8_lossy(&manifest_bytes))
        .context("parse manifest cache")?;
    let declared = code_file_names(&manifest);
    if declared.is_empty() {
        bail!("manifest senza [code].files");
    }
    let mut hasher = Sha256::new();
    for fname in &declared {
        let rel = code_rel_path(fname)?;
        let data = std::fs::read(dir.join(rel))
            .with_context(|| format!("lettura codice cache {}", fname))?;
        hasher.update(data);
    }
    let got_code = format!("{:x}", hasher.finalize());
    if !got_code.eq_ignore_ascii_case(code_sha256) {
        bail!("code cache corrotto: {} != {}", got_code, code_sha256);
    }
    let entry = dir.join(code_rel_path(&declared[0])?);
    Ok((manifest, entry))
}

async fn fetch_executor(server: &str, name: &str) -> Result<ExecutorBundle> {
    let url = format!("{}/agent/executor/{}", server.trim_end_matches('/'), name);
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()?;
    let resp = client
        .get(&url)
        .send()
        .await
        .with_context(|| format!("GET {}", url))?;
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
    let manifest_bytes = B64
        .decode(&bundle.manifest_toml)
        .context("decode manifest")?;
    let sig_bytes = B64
        .decode(&bundle.manifest_sig)
        .context("decode manifest sig")?;

    // 1. digest manifest atteso.
    let got_manifest = hex_sha256(&manifest_bytes);
    if got_manifest != manifest_sha256 {
        bail!(
            "manifest sha mismatch: {} != {}",
            got_manifest,
            manifest_sha256
        );
    }
    // 2. firma del manifest verificata con la pubkey server pinnata (§8).
    //    Il server firma i BYTES del manifest (sign.py::sign_executor), b64 std.
    let sig_b64u = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(&sig_bytes);
    identity::verify_b64(server_pubkey, &sig_b64u, &manifest_bytes)
        .context("firma manifest non verificata con pubkey server pinnata")?;

    // 3. digest del codice = concatenazione dei file in ordine dichiarato.
    let manifest: toml::Value = toml::from_str(&String::from_utf8_lossy(&manifest_bytes))?;
    let declared_files = code_file_names(&manifest);
    if declared_files.is_empty() {
        bail!("manifest senza [code].files");
    }
    let mut hasher = Sha256::new();
    let mut decoded: BTreeMap<String, Vec<u8>> = BTreeMap::new();
    for fname in &declared_files {
        code_rel_path(fname)?;
        let b64 = bundle
            .files
            .get(fname)
            .ok_or_else(|| anyhow!("bundle privo del file {}", fname))?;
        let data = B64
            .decode(b64)
            .with_context(|| format!("decode {}", fname))?;
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
        let dest = tmp.join(code_rel_path(fname)?);
        if let Some(parent) = dest.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(dest, data)?;
    }
    let _ = std::fs::remove_dir_all(dir);
    std::fs::rename(&tmp, dir).context("rename executor cache dir")?;
    tracing::info!(executor = %bundle.name, "executor verificato e messo in cache");
    Ok(())
}

/// Scarica e verifica il bundle shim (executor_helpers + messages fallback)
/// nella dir data. Ritorna la dir dello shim (da mettere su PYTHONPATH).
pub async fn ensure_shim(
    server: &str,
    server_pubkey: &str,
    cache_root: &Path,
) -> Result<(PathBuf, String)> {
    let dir = cache_root.join("shim");
    let url = format!("{}/agent/shim", server.trim_end_matches('/'));
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()?;
    let resp = client
        .get(&url)
        .send()
        .await
        .with_context(|| format!("GET {}", url))?;
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
        let data = B64
            .decode(b64)
            .with_context(|| format!("decode shim {}", fname))?;
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
    if fname.is_empty()
        || fname.contains('\\')
        || fname.contains(':')
        || fname.starts_with('/')
        || fname.ends_with('/')
    {
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
    use super::{code_rel_path, parse_managed_providers, shim_rel_path, validate_cache_key};

    #[test]
    fn flat_and_tree_ok() {
        assert!(shim_rel_path("messages.py").is_ok());
        let p = shim_rel_path("backends/files/local.py").unwrap();
        assert_eq!(p.iter().count(), 3);
    }

    #[test]
    fn traversal_and_bad_forms_rejected() {
        for bad in [
            "../evil.py",
            "a/../b.py",
            "a/./b.py",
            "/abs.py",
            "dir/",
            "a//b.py",
            "c:\\win.py",
            "a\\b.py",
            "",
            "x:y",
        ] {
            assert!(shim_rel_path(bad).is_err(), "accettato: {}", bad);
        }
    }

    #[test]
    fn code_paths_and_cache_keys_are_closed() {
        assert!(code_rel_path("pkg/main.py").is_ok());
        for bad in ["../x.py", "a/../x.py", "/x.py", "a\\x.py", "x:y"] {
            assert!(code_rel_path(bad).is_err(), "accepted {bad}");
        }
        let hash = "a".repeat(64);
        assert!(validate_cache_key("read_files", &hash, &hash).is_ok());
        assert!(validate_cache_key("../read", &hash, &hash).is_err());
        assert!(validate_cache_key("read", "abc", &hash).is_err());
    }

    #[test]
    fn signed_manifest_provider_shape_is_closed() {
        let manifest: toml::Value = toml::from_str(
            r#"
            [[managed_dependencies]]
            key = "hardware_sensor_provider"
            package_id = "Vendor.Sensor"
            mode = "provider"
            interface = "hardware_sensors_v1"
            domains_arg = "sensor_domains"
            sensor_types_arg = "sensor_types"
            assembly = "Vendor.SensorLib.dll"
            entry_type = "Vendor.Sensor.Computer"
        "#,
        )
        .unwrap();
        let providers = parse_managed_providers(&manifest).unwrap();
        assert_eq!(providers.len(), 1);
        assert_eq!(providers[0].package_id, "Vendor.Sensor");
        assert_eq!(providers[0].assembly, "Vendor.SensorLib.dll");
        assert_eq!(providers[0].entry_type, "Vendor.Sensor.Computer");

        let malformed: toml::Value = toml::from_str(
            r#"
            [[managed_dependencies]]
            key = "hardware_sensor_provider"
            package_id = "Vendor.Sensor"
            mode = "provider"
            interface = "hardware_sensors_v1"
            domains_arg = "sensor_domains"
            sensor_types_arg = "sensor_types"
            assembly = "Vendor.SensorLib.dll"
            entry_type = "Vendor.Sensor.Computer"
            command = "cmd.exe"
        "#,
        )
        .unwrap();
        assert!(parse_managed_providers(&malformed).is_err());

        let valid_source = r#"
                [[managed_dependencies]]
                key = "hardware_sensor_provider"
                package_id = "Vendor.Sensor"
                mode = "provider"
                interface = "hardware_sensors_v1"
                domains_arg = "sensor_domains"
                sensor_types_arg = "sensor_types"
                assembly = "Vendor.SensorLib.dll"
                entry_type = "Vendor.Sensor.Computer"
                "#;
        for (original, invalid) in [
            ("Vendor.SensorLib.dll", "../Sensor.dll"),
            ("Vendor.Sensor.Computer", "Vendor..Computer"),
        ] {
            let malformed: toml::Value =
                toml::from_str(&valid_source.replace(original, invalid)).unwrap();
            assert!(parse_managed_providers(&malformed).is_err());
        }
    }
}

fn code_file_names(manifest: &toml::Value) -> Vec<String> {
    manifest
        .get("code")
        .and_then(|c| c.get("files"))
        .and_then(|f| f.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default()
}

fn code_rel_path(fname: &str) -> Result<PathBuf> {
    if fname.is_empty()
        || fname.contains('\\')
        || fname.contains(':')
        || fname.starts_with('/')
        || fname.ends_with('/')
    {
        bail!("nome file codice non sicuro: {}", fname);
    }
    let mut rel = PathBuf::new();
    for segment in fname.split('/') {
        if segment.is_empty() || segment == "." || segment == ".." {
            bail!("segmento codice non sicuro in {}", fname);
        }
        rel.push(segment);
    }
    Ok(rel)
}

fn parse_capabilities(manifest: &toml::Value) -> Vec<Capability> {
    let mut out = Vec::new();
    if let Some(arr) = manifest.get("capabilities").and_then(|c| c.as_array()) {
        for c in arr {
            let name = c
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let hint = c
                .get("hint")
                .and_then(|h| h.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|v| v.as_str().map(String::from))
                        .collect()
                })
                .unwrap_or_default();
            if !name.is_empty() {
                out.push(Capability { name, hint });
            }
        }
    }
    out
}

fn parse_min_sandbox(manifest: &toml::Value) -> Result<String> {
    let value = manifest
        .get("placement")
        .and_then(|p| p.get("min_sandbox"))
        .and_then(|v| v.as_str())
        .unwrap_or("job-object");
    if !matches!(value, "job-object" | "appcontainer") {
        bail!("placement.min_sandbox non supportato: {}", value);
    }
    Ok(value.to_string())
}

fn safe_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value.is_ascii()
        && value.as_bytes()[0].is_ascii_alphabetic()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
}

fn safe_package_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value.is_ascii()
        && value.as_bytes()[0].is_ascii_alphanumeric()
        && value.bytes().all(|byte| {
            byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'+' | b':' | b'@' | b'-')
        })
}

fn safe_assembly_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value.is_ascii()
        && value.as_bytes()[0].is_ascii_alphanumeric()
        && value.to_ascii_lowercase().ends_with(".dll")
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
}

fn safe_dotnet_type(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 192
        && value.is_ascii()
        && value.split('.').all(safe_identifier)
}

/// Parse only the closed provider shape. Process dependencies remain owned by
/// the server's consent flow and require no client-side metadata.
fn parse_managed_providers(manifest: &toml::Value) -> Result<Vec<ManagedProviderDependency>> {
    let Some(rows) = manifest.get("managed_dependencies") else {
        return Ok(Vec::new());
    };
    let rows = rows
        .as_array()
        .ok_or_else(|| anyhow!("managed_dependencies must be an array"))?;
    let mut providers = Vec::new();
    let mut keys = std::collections::HashSet::new();
    for row in rows {
        let table = row
            .as_table()
            .ok_or_else(|| anyhow!("managed dependency must be a table"))?;
        let names: std::collections::BTreeSet<&str> = table.keys().map(String::as_str).collect();
        let process: std::collections::BTreeSet<&str> = ["key", "package_id"].into_iter().collect();
        let provider: std::collections::BTreeSet<&str> = [
            "assembly",
            "domains_arg",
            "entry_type",
            "interface",
            "key",
            "mode",
            "package_id",
            "sensor_types_arg",
        ]
        .into_iter()
        .collect();
        if names == process {
            continue;
        }
        if names != provider || table.get("mode").and_then(toml::Value::as_str) != Some("provider")
        {
            bail!("managed provider shape is invalid");
        }
        let value = |name: &str| table.get(name).and_then(toml::Value::as_str).unwrap_or("");
        let key = value("key");
        let package_id = value("package_id");
        let interface = value("interface");
        let domains_arg = value("domains_arg");
        let sensor_types_arg = value("sensor_types_arg");
        let assembly = value("assembly");
        let entry_type = value("entry_type");
        if !safe_identifier(key)
            || !safe_package_id(package_id)
            || !safe_identifier(interface)
            || !safe_identifier(domains_arg)
            || !safe_identifier(sensor_types_arg)
            || !safe_assembly_name(assembly)
            || !safe_dotnet_type(entry_type)
            || !keys.insert(key.to_string())
        {
            bail!("managed provider values are invalid");
        }
        providers.push(ManagedProviderDependency {
            key: key.to_string(),
            package_id: package_id.to_string(),
            interface: interface.to_string(),
            domains_arg: domains_arg.to_string(),
            sensor_types_arg: sensor_types_arg.to_string(),
            assembly: assembly.to_string(),
            entry_type: entry_type.to_string(),
        });
    }
    Ok(providers)
}

fn hex_sha256(data: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(data);
    format!("{:x}", h.finalize())
}
