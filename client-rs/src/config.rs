use anyhow::{Context, Result};
use std::path::PathBuf;

pub struct Paths {
    pub data_dir: PathBuf,
    pub cache_dir: PathBuf,
    pub key_file: PathBuf,
    pub state_file: PathBuf,
    pub spool_dir: PathBuf,
    pub runtime_dir: PathBuf,
    pub executors_dir: PathBuf,
    pub wheel_cache: PathBuf,
}

impl Paths {
    pub fn resolve() -> Result<Self> {
        let data_dir = dirs::data_local_dir()
            .context("no data-local dir for current platform")?
            .join("metnos");
        let cache_dir = dirs::cache_dir()
            .context("no cache dir for current platform")?
            .join("metnos");
        Ok(Self {
            key_file: data_dir.join("key"),
            state_file: data_dir.join("state.json"),
            spool_dir: data_dir.join("spool"),
            runtime_dir: cache_dir.join("runtime"),
            executors_dir: cache_dir.join("executors"),
            wheel_cache: cache_dir.join("wheels"),
            data_dir,
            cache_dir,
        })
    }

    pub fn ensure(&self) -> Result<()> {
        for d in [
            &self.data_dir,
            &self.cache_dir,
            &self.spool_dir,
            &self.runtime_dir,
            &self.executors_dir,
            &self.wheel_cache,
        ] {
            std::fs::create_dir_all(d)
                .with_context(|| format!("create_dir_all({})", d.display()))?;
        }
        Ok(())
    }
}
