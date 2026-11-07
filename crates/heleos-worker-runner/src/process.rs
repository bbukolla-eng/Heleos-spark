//! Nonblocking pipe pumping avoids deadlock on a provider that never reads stdin.
use crate::{Capture, FailureCode, RunError};
use rustix::fs::{OFlags, fcntl_getfl, fcntl_setfl};
use rustix::process::{Pid, Signal, kill_process_group};
use std::io::{Read, Write};
use std::os::fd::AsFd;
use std::os::unix::process::CommandExt;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::time::{Duration, Instant};

pub(crate) struct ProcessOutput {
    pub status: ExitStatus,
    pub stdout: Capture,
    pub stderr: Capture,
}
struct ManagedChild {
    child: Child,
    reaped: bool,
}
impl ManagedChild {
    fn terminate_group(&mut self) {
        // The child becomes its own group leader before exec. Never signal group
        // 0 or 1, which have broader process-selection semantics.
        if self.child.id() > 1
            && let Ok(raw) = i32::try_from(self.child.id())
            && let Some(pid) = Pid::from_raw(raw)
        {
            let _ = kill_process_group(pid, Signal::KILL);
        }
    }
}
impl Drop for ManagedChild {
    fn drop(&mut self) {
        if !self.reaped {
            self.terminate_group();
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}

pub(crate) fn execute(
    mut command: Command,
    input: &[u8],
    limit: usize,
    deadline: Instant,
) -> Result<ProcessOutput, RunError> {
    if Instant::now() >= deadline {
        return Err(RunError::new(FailureCode::Timeout));
    }
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0);
    let child = command
        .spawn()
        .map_err(|_| RunError::new(FailureCode::Io))?;
    let mut managed = ManagedChild {
        child,
        reaped: false,
    };
    let mut stdin = managed.child.stdin.take();
    let mut stdout_pipe = managed
        .child
        .stdout
        .take()
        .ok_or_else(|| RunError::new(FailureCode::Io))?;
    let mut stderr_pipe = managed
        .child
        .stderr
        .take()
        .ok_or_else(|| RunError::new(FailureCode::Io))?;
    nonblocking(&stdout_pipe)?;
    nonblocking(&stderr_pipe)?;
    if let Some(pipe) = &stdin {
        nonblocking(pipe)?;
    }
    let mut stdout = Capture::default();
    let mut stderr = Capture::default();
    let mut written = 0;
    loop {
        drain(&mut stdout_pipe, &mut stdout, limit)?;
        drain(&mut stderr_pipe, &mut stderr, limit)?;
        if let Some(pipe) = &mut stdin {
            if written == input.len() {
                stdin = None;
            } else {
                match pipe.write(&input[written..]) {
                    Ok(count) => written += count,
                    Err(error)
                        if error.kind() == std::io::ErrorKind::WouldBlock
                            || error.kind() == std::io::ErrorKind::Interrupted => {}
                    Err(error) if error.kind() == std::io::ErrorKind::BrokenPipe => stdin = None,
                    Err(_) => return Err(RunError::new(FailureCode::Io)),
                }
            }
        }
        if let Some(status) = managed
            .child
            .try_wait()
            .map_err(|_| RunError::new(FailureCode::Io))?
        {
            managed.terminate_group();
            managed.reaped = true;
            drain(&mut stdout_pipe, &mut stdout, limit)?;
            drain(&mut stderr_pipe, &mut stderr, limit)?;
            return Ok(ProcessOutput {
                status,
                stdout,
                stderr,
            });
        }
        if Instant::now() >= deadline {
            managed.terminate_group();
            let _ = managed.child.kill();
            let _ = managed.child.wait();
            managed.reaped = true;
            drain(&mut stdout_pipe, &mut stdout, limit)?;
            drain(&mut stderr_pipe, &mut stderr, limit)?;
            let mut failure = RunError::new(FailureCode::Timeout);
            failure.stdout = Box::new(stdout);
            failure.stderr = Box::new(stderr);
            return Err(failure);
        }
        std::thread::sleep(Duration::from_millis(5));
    }
}
fn nonblocking(pipe: &impl AsFd) -> Result<(), RunError> {
    let flags = fcntl_getfl(pipe).map_err(|_| RunError::new(FailureCode::Io))?;
    fcntl_setfl(pipe, flags | OFlags::NONBLOCK).map_err(|_| RunError::new(FailureCode::Io))
}
fn drain(pipe: &mut impl Read, capture: &mut Capture, limit: usize) -> Result<(), RunError> {
    let mut buffer = [0u8; 8192];
    // A continuously writing process must not starve deadline polling.
    for _ in 0..16 {
        match pipe.read(&mut buffer) {
            Ok(0) => return Ok(()),
            Ok(count) => {
                let retain = count.min(limit.saturating_sub(capture.bytes.len()));
                capture.bytes.extend_from_slice(&buffer[..retain]);
                capture.truncated |= retain != count;
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => return Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
            Err(_) => return Err(RunError::new(FailureCode::Io)),
        }
    }
    Ok(())
}
