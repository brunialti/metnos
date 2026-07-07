//! wire.rs — tipi serde del protocollo core↔client (§6 del design doc) +
//! JSON canonico condiviso col server (runtime/invocations.py::canonical_bytes).
//!
//! Contratto di firma: `serde_json::to_vec` di un `Value` con chiavi ordinate
//! (BTreeMap di default, feature preserve_order NON attiva), separatori
//! compatti, UTF-8 non-escaped — identico a
//! `json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False)`.

use anyhow::{anyhow, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Wire object di un'invocazione (§6.2): i campi firmati + `server_sig`.
#[derive(Debug, Clone, Deserialize)]
pub struct Invocation {
    pub invocation_id: String,
    #[serde(default)]
    pub turn_id: String,
    pub executor: String,
    pub manifest_sha256: String,
    pub code_sha256: String,
    #[serde(default)]
    pub args: Value,
    #[serde(default)]
    pub scope: String,
    #[serde(default)]
    pub reversibility: String,
    #[serde(default)]
    pub env_injections: std::collections::BTreeMap<String, String>,
    #[serde(default = "default_deadline")]
    pub deadline_ms: u64,
    pub server_sig: String,
}

fn default_deadline() -> u64 {
    60_000
}

impl Invocation {
    /// Ricostruisce i bytes canonici del payload SENZA `server_sig`, per la
    /// verifica della firma del server (§6.2). L'ordine dei campi e' irrilevante:
    /// la canonicalizzazione ordina le chiavi.
    pub fn signed_bytes(&self) -> Result<Vec<u8>> {
        let payload = serde_json::json!({
            "invocation_id": self.invocation_id,
            "turn_id": self.turn_id,
            "executor": self.executor,
            "manifest_sha256": self.manifest_sha256,
            "code_sha256": self.code_sha256,
            "args": self.args,
            "scope": self.scope,
            "reversibility": self.reversibility,
            "env_injections": self.env_injections,
            "deadline_ms": self.deadline_ms,
        });
        canonical_bytes(&payload)
    }
}

#[derive(Debug, Deserialize)]
pub struct PollResponse {
    #[serde(default)]
    pub invocation: Option<Invocation>,
    #[serde(default)]
    pub server_client_version: Option<String>,
    /// Content-addressing dello shim (0.2.15): sha del bundle runtime lato
    /// server. Metadato di trasporto (fuori dalla firma per-invocazione);
    /// assente su server vecchi -> nessun confronto.
    #[serde(default)]
    pub shim_sha256: Option<String>,
}

/// Result inviato dal device (§6.3). Serializzato a mano per controllare la
/// canonicalizzazione della parte firmata.
#[derive(Debug, Clone)]
pub struct InvocationResult {
    pub invocation_id: String,
    pub device_id: String,
    pub ok: bool,
    pub entries: Value,
    pub n_processed: i64,
    pub elapsed_ms: i64,
    pub sandbox: String,
    /// W4: motivo del declassamento del livello di sandbox (AppContainer non
    /// costruito → job-object). Additivo e OPZIONALE: emesso SOLO quando
    /// presente, cosi' il body resta byte-identico nel caso normale (§2.8).
    pub sandbox_downgrade_reason: Option<String>,
    pub error: Option<String>,
    pub error_class: Option<String>,
    /// Output COMPLETO dell'executor (§2.6): non solo `entries`, ma anche le
    /// chiavi di dominio (`total_lines`, `by_path`, `summary`, …) che un
    /// executor NON-lista produce. Senza questo il round-trip remoto le
    /// perdeva e il runtime vedeva un result vuoto ma «ok» (§2.8, bug live
    /// 3/7: compute_files_loc → n_processed 5 ma entries []).
    pub payload: Value,
}

impl InvocationResult {
    /// Corpo del result (§6.3), SENZA `device_sig`. Il client firma i bytes
    /// ESATTI con cui invia questo oggetto (header X-Metnos-Device-Sig) e il
    /// server verifica gli stessi bytes ricevuti: nessuna dipendenza da un
    /// round-trip canonico, quindi gli `entries` possono contenere float
    /// (punteggi, coordinate) senza rompere la firma.
    pub fn body_value(&self) -> Value {
        let mut obj = serde_json::Map::new();
        obj.insert("invocation_id".into(), Value::String(self.invocation_id.clone()));
        obj.insert("device_id".into(), Value::String(self.device_id.clone()));
        obj.insert("ok".into(), Value::Bool(self.ok));
        obj.insert("entries".into(), self.entries.clone());
        obj.insert("n_processed".into(), Value::from(self.n_processed));
        obj.insert("elapsed_ms".into(), Value::from(self.elapsed_ms));
        obj.insert("sandbox".into(), Value::String(self.sandbox.clone()));
        // Emesso SOLO quando presente: nel caso normale (nessun declassamento)
        // il body non guadagna chiavi → wire invariato per i server esistenti.
        if let Some(reason) = &self.sandbox_downgrade_reason {
            obj.insert(
                "sandbox_downgrade_reason".into(),
                Value::String(reason.clone()),
            );
        }
        obj.insert("payload".into(), self.payload.clone());
        if let Some(e) = &self.error {
            obj.insert("error".into(), Value::String(e.clone()));
        }
        if let Some(c) = &self.error_class {
            obj.insert("error_class".into(), Value::String(c.clone()));
        }
        Value::Object(obj)
    }
}

/// Bytes canonici di un JSON value (contratto condiviso col server).
///
/// `serde_json::to_vec` su un `Value`: chiavi ordinate (BTreeMap default),
/// nessuno spazio, non-ASCII non escaped. Rifiuta i float (non riproducibili
/// cross-linguaggio, come il server).
pub fn canonical_bytes(value: &Value) -> Result<Vec<u8>> {
    reject_floats(value)?;
    Ok(serde_json::to_vec(value)?)
}

fn reject_floats(v: &Value) -> Result<()> {
    match v {
        Value::Number(n) if n.as_i64().is_none() && n.as_u64().is_none() => {
            Err(anyhow!("float in payload firmato: non riproducibile"))
        }
        Value::Array(a) => a.iter().try_for_each(reject_floats),
        Value::Object(o) => o.values().try_for_each(reject_floats),
        _ => Ok(()),
    }
}

/// Body firmato del poll/heartbeat (client→server): il server verifica la
/// firma sui bytes canonici del body (§6.2).
#[derive(Debug, Serialize)]
pub struct PollRequest<'a> {
    pub device_id: &'a str,
    pub cursor: Option<&'a str>,
    pub capabilities: &'a [String],
    pub block_ms: u64,
}

#[derive(Debug, Serialize)]
pub struct HeartbeatRequest<'a> {
    pub device_id: &'a str,
    pub profile: Value,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_matches_python_contract() {
        // Stesso input del test Python: chiavi ordinate, compatto.
        let v = serde_json::json!({"b": 1, "a": {"z": true, "e": ["x", 2]}});
        let bytes = canonical_bytes(&v).unwrap();
        assert_eq!(bytes, br#"{"a":{"e":["x",2],"z":true},"b":1}"#);
    }

    #[test]
    fn rejects_float() {
        let v = serde_json::json!({"x": 1.5});
        assert!(canonical_bytes(&v).is_err());
    }

    #[test]
    fn preserves_non_ascii() {
        let v = serde_json::json!({"k": "città"});
        let bytes = canonical_bytes(&v).unwrap();
        assert_eq!(bytes, "{\"k\":\"città\"}".as_bytes());
    }
}
