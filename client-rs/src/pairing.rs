use anyhow::{anyhow, Context, Result};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use serde::{Deserialize, Serialize};

use crate::identity::Identity;

#[derive(Debug, Serialize)]
struct RegisterRequest<'a> {
    token: &'a str,
    public_key: String,
    os_family: &'a str,
    os_arch: &'a str,
}

#[derive(Debug, Deserialize)]
pub struct RegisterResponse {
    pub device_id: String,
    pub name: String,
    pub owner_user_id: String,
    pub fingerprint: String,
    pub paired_at: String,
}

#[derive(Debug, Deserialize)]
struct ErrorBody {
    error: String,
    message: String,
}

pub async fn register(
    server: &str,
    token: &str,
    id: &Identity,
) -> Result<RegisterResponse> {
    let pub_b64 = URL_SAFE_NO_PAD.encode(id.verifying().to_bytes());
    let url = format!("{}/agent/register", server.trim_end_matches('/'));
    let body = RegisterRequest {
        token,
        public_key: pub_b64,
        os_family: os_family(),
        os_arch: os_arch(),
    };

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(15))
        .build()?;
    let resp = client.post(&url).json(&body).send().await
        .with_context(|| format!("POST {}", url))?;
    let status = resp.status();
    if status.is_success() {
        let parsed: RegisterResponse = resp.json().await
            .context("parse register response")?;
        return Ok(parsed);
    }
    // server error: prova a interpretare il body come ErrorBody
    if let Ok(err) = resp.json::<ErrorBody>().await {
        return Err(anyhow!("register failed [{}] {}: {}", status, err.error, err.message));
    }
    Err(anyhow!("register failed with status {}", status))
}

fn os_family() -> &'static str {
    if cfg!(target_os = "linux") { "linux" }
    else if cfg!(target_os = "windows") { "windows" }
    else if cfg!(target_os = "macos") { "macos" }
    else { "unknown" }
}

fn os_arch() -> &'static str {
    if cfg!(target_arch = "x86_64") { "x86_64" }
    else if cfg!(target_arch = "aarch64") { "aarch64" }
    else { "unknown" }
}
