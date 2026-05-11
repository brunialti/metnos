use anyhow::{Context, Result};
use ed25519_dalek::{SigningKey, VerifyingKey};
use rand::rngs::OsRng;
use std::path::Path;

pub struct Identity {
    pub signing: SigningKey,
}

impl Identity {
    pub fn load_or_create(path: &Path) -> Result<Self> {
        if path.exists() {
            Self::load(path)
        } else {
            let id = Self::create();
            id.save(path)?;
            Ok(id)
        }
    }

    fn create() -> Self {
        let mut rng = OsRng;
        let signing = SigningKey::generate(&mut rng);
        Self { signing }
    }

    fn load(path: &Path) -> Result<Self> {
        let bytes = std::fs::read(path)
            .with_context(|| format!("read key from {}", path.display()))?;
        let arr: [u8; 32] = bytes.as_slice().try_into()
            .context("key file is not 32 bytes")?;
        Ok(Self { signing: SigningKey::from_bytes(&arr) })
    }

    fn save(&self, path: &Path) -> Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let bytes = self.signing.to_bytes();
        std::fs::write(path, bytes)
            .with_context(|| format!("write key to {}", path.display()))?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = std::fs::metadata(path)?.permissions();
            perms.set_mode(0o600);
            std::fs::set_permissions(path, perms)?;
        }
        Ok(())
    }

    pub fn verifying(&self) -> VerifyingKey {
        self.signing.verifying_key()
    }

    pub fn fingerprint(&self) -> String {
        let bytes = self.verifying().to_bytes();
        let mut hex = String::with_capacity(64);
        for b in bytes {
            hex.push_str(&format!("{:02x}", b));
        }
        hex
    }
}
