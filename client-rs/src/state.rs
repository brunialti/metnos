use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct State {
    pub device_id: Option<String>,
    pub device_name: Option<String>,
    pub server_url: Option<String>,
    pub fingerprint: Option<String>,
    pub paired_at: Option<String>,
    /// Pubkey del server (raw Ed25519, b64url) pinnata al pairing. Con essa
    /// il client verifica `server_sig` delle invocazioni e i bundle firmati.
    pub server_public_key: Option<String>,
}

impl State {
    pub fn load_or_default(path: &Path) -> Result<Self> {
        if !path.exists() {
            return Ok(Self::default());
        }
        let bytes = std::fs::read(path)
            .with_context(|| format!("read state from {}", path.display()))?;
        serde_json::from_slice(&bytes)
            .with_context(|| format!("parse state {}", path.display()))
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let bytes = serde_json::to_vec_pretty(self)?;
        let tmp = path.with_extension("json.tmp");
        std::fs::write(&tmp, bytes)
            .with_context(|| format!("write tmp state to {}", tmp.display()))?;
        std::fs::rename(&tmp, path)
            .with_context(|| format!("rename tmp -> {}", path.display()))?;
        Ok(())
    }

    pub fn is_paired(&self) -> bool {
        self.device_id.is_some()
    }
}
