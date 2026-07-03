use anyhow::{anyhow, Context, Result};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use ed25519_dalek::{Signature, Signer, SigningKey, Verifier, VerifyingKey};
use rand::rngs::OsRng;
use std::path::Path;

/// Clone: la chiave di firma va condivisa col task heartbeat (§B5, task tokio
/// separato) senza spostare l'identita' fuori dal runner. `SigningKey` e'
/// clonabile (materiale a 32 byte), il clone e' la STESSA identita'.
#[derive(Clone)]
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

    /// Firma Ed25519 (b64url no-pad) dei bytes dati — stesso encoding del server.
    pub fn sign_b64(&self, msg: &[u8]) -> String {
        let sig: Signature = self.signing.sign(msg);
        URL_SAFE_NO_PAD.encode(sig.to_bytes())
    }
}

/// Verifica una firma Ed25519 (b64url no-pad) di `msg` contro una pubkey
/// (raw 32 byte, b64url no-pad). Usata per `server_sig` (§6.2) e per i bundle.
pub fn verify_b64(public_key_b64: &str, sig_b64: &str, msg: &[u8]) -> Result<()> {
    let pub_bytes = URL_SAFE_NO_PAD
        .decode(public_key_b64)
        .context("decode server pubkey")?;
    let arr: [u8; 32] = pub_bytes
        .as_slice()
        .try_into()
        .map_err(|_| anyhow!("server pubkey non e' 32 byte"))?;
    let vk = VerifyingKey::from_bytes(&arr).context("bad server pubkey")?;
    let sig_bytes = URL_SAFE_NO_PAD.decode(sig_b64).context("decode signature")?;
    let sig_arr: [u8; 64] = sig_bytes
        .as_slice()
        .try_into()
        .map_err(|_| anyhow!("firma non e' 64 byte"))?;
    vk.verify(msg, &Signature::from_bytes(&sig_arr))
        .map_err(|_| anyhow!("firma non verificata"))
}
