use serde::{Deserialize, Serialize};

use crate::Result;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FaultPoint {
    AfterVaultPublish,
    AfterJobStart,
    AfterCheckpointCommit,
    BeforeAuthoritativeCommit,
    AfterAuthoritativeCommit,
}

pub trait FaultInjector: Send + Sync {
    fn inject(&self, point: FaultPoint) -> Result<()>;
}

pub(crate) struct NoFaults;

impl FaultInjector for NoFaults {
    fn inject(&self, _: FaultPoint) -> Result<()> {
        Ok(())
    }
}

pub(crate) static NO_FAULTS: NoFaults = NoFaults;
