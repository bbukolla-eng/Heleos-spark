//! Privileged, fixed controller Git execution. Providers never use this path;
//! they enter the restricted-token/Job backend in `containment` directly.
use crate::{Capture, FailureCode, RunError};
use std::{
    io::Read,
    os::windows::process::ExitStatusExt,
    process::{Child, Command, ExitStatus, Stdio},
    sync::mpsc,
    time::{Duration, Instant},
};

pub(crate) struct ProcessOutput {
    pub status: ExitStatus,
    pub stdout: Capture,
    pub stderr: Capture,
}

pub(crate) fn from_windows_outcome(
    outcome: heleos_worker_windows::Outcome,
) -> Result<ProcessOutput, RunError> {
    let stdout = Capture {
        bytes: outcome.stdout.bytes,
        truncated: outcome.stdout.truncated,
    };
    let stderr = Capture {
        bytes: outcome.stderr.bytes,
        truncated: outcome.stderr.truncated,
    };
    if outcome.timed_out {
        let mut error = RunError::new(FailureCode::Timeout);
        error.stdout = Box::new(stdout);
        error.stderr = Box::new(stderr);
        return Err(error);
    }
    Ok(ProcessOutput {
        status: ExitStatus::from_raw(outcome.exit_code),
        stdout,
        stderr,
    })
}

struct ControllerChild(Child);
impl Drop for ControllerChild {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

// Only local Git controller commands with empty stdin reach this entrypoint.
// Reader threads drain beyond retention; they cannot block deadline polling.
pub(crate) fn execute(
    mut command: Command,
    input: &[u8],
    limit: usize,
    deadline: Instant,
) -> Result<ProcessOutput, RunError> {
    if !input.is_empty() || limit == 0 || limit > crate::HARD_BYTE_LIMIT {
        return Err(RunError::new(FailureCode::InvalidConfiguration));
    }
    if Instant::now() >= deadline {
        return Err(RunError::new(FailureCode::Timeout));
    }
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = ControllerChild(
        command
            .spawn()
            .map_err(|_| RunError::new(FailureCode::Io))?,
    );
    let stdout = reader(
        child
            .0
            .stdout
            .take()
            .ok_or_else(|| RunError::new(FailureCode::Io))?,
        limit,
    );
    let stderr = reader(
        child
            .0
            .stderr
            .take()
            .ok_or_else(|| RunError::new(FailureCode::Io))?,
        limit,
    );
    let status = loop {
        if let Some(status) = child
            .0
            .try_wait()
            .map_err(|_| RunError::new(FailureCode::Io))?
        {
            break status;
        }
        if Instant::now() >= deadline {
            return Err(RunError::new(FailureCode::Timeout));
        }
        std::thread::sleep(Duration::from_millis(5));
    };
    let receive = |channel: mpsc::Receiver<Result<Capture, RunError>>| {
        channel
            .recv_timeout(deadline.saturating_duration_since(Instant::now()))
            .map_err(|error| {
                RunError::new(if matches!(error, mpsc::RecvTimeoutError::Timeout) {
                    FailureCode::Timeout
                } else {
                    FailureCode::Io
                })
            })?
    };
    Ok(ProcessOutput {
        status,
        stdout: receive(stdout)?,
        stderr: receive(stderr)?,
    })
}

fn reader(
    mut pipe: impl Read + Send + 'static,
    limit: usize,
) -> mpsc::Receiver<Result<Capture, RunError>> {
    let (send, receive) = mpsc::sync_channel(1);
    std::thread::spawn(move || {
        let result = heleos_worker_windows::capture(&mut pipe, limit)
            .map(|capture| Capture {
                bytes: capture.bytes,
                truncated: capture.truncated,
            })
            .map_err(crate::containment::windows_error);
        let _ = send.send(result);
    });
    receive
}
