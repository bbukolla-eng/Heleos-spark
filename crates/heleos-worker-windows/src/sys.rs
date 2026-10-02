//! The crate's only unsafe boundary. Every FFI pointer refers to live owned
//! storage, output buffers are sized/aligned, and successful handles enter RAII
//! immediately. No raw handle or pointer escapes this module.

use crate::{CommandSpec, Error, Outcome, capture, command_line, environment_block};
use std::{
    ffi::c_void,
    fs::File,
    io::Write,
    mem::{size_of, size_of_val},
    os::windows::io::FromRawHandle,
    path::{Path, PathBuf},
    ptr::{null, null_mut},
    time::Instant,
};
use windows_sys::Win32::{
    Foundation::*,
    Security::{Authorization::*, *},
    Storage::FileSystem::*,
    System::{JobObjects::*, Pipes::CreatePipe, Threading::*},
};

/// Windows environment names use invariant ordinal case-insensitive comparison,
/// not the current locale or Unicode's multi-character uppercase expansions.
pub(super) fn sort_environment(entries: &mut [(&String, &String)]) -> Result<(), Error> {
    use std::cmp::Ordering;
    use windows_sys::Win32::Globalization::{
        CSTR_EQUAL, CSTR_GREATER_THAN, CSTR_LESS_THAN, CompareStringOrdinal,
    };
    let mut failure = None;
    let mut compare = |a: &str, b: &str| {
        let a: Vec<_> = a.encode_utf16().collect();
        let b: Vec<_> = b.encode_utf16().collect();
        // Inputs were validated to be nonempty and <32767 UTF-16 units.
        match unsafe {
            CompareStringOrdinal(a.as_ptr(), a.len() as i32, b.as_ptr(), b.len() as i32, 1)
        } {
            CSTR_LESS_THAN => Ordering::Less,
            CSTR_EQUAL => Ordering::Equal,
            CSTR_GREATER_THAN => Ordering::Greater,
            _ => {
                failure = Some(last("environment_comparison"));
                Ordering::Equal
            }
        }
    };
    entries.sort_by(|a, b| compare(a.0, b.0));
    let duplicate = entries
        .windows(2)
        .any(|pair| compare(pair[0].0, pair[1].0) == Ordering::Equal);
    if let Some(error) = failure {
        return Err(error);
    }
    if duplicate {
        return Err(Error::InvalidInput);
    }
    Ok(())
}

fn last(stage: &'static str) -> Error {
    // SAFETY: GetLastError has no pointer arguments; read immediately on failure.
    Error::Setup {
        stage,
        code: unsafe { GetLastError() },
    }
}
fn success(value: i32, stage: &'static str) -> Result<(), Error> {
    if value == 0 { Err(last(stage)) } else { Ok(()) }
}
fn status(code: u32, stage: &'static str) -> Result<(), Error> {
    if code == 0 {
        Ok(())
    } else {
        Err(Error::Setup { stage, code })
    }
}

#[derive(Debug)]
struct Handle(HANDLE);
impl Handle {
    fn checked(value: HANDLE, stage: &'static str) -> Result<Self, Error> {
        if value.is_null() || value == INVALID_HANDLE_VALUE {
            Err(last(stage))
        } else {
            Ok(Self(value))
        }
    }
    fn into_file(self) -> File {
        let value = self.0;
        std::mem::forget(self);
        // SAFETY: the unique owned pipe handle is transferred exactly once.
        unsafe { File::from_raw_handle(value) }
    }
}
impl Drop for Handle {
    fn drop(&mut self) {
        // SAFETY: Handle owns one non-null, non-pseudo handle.
        unsafe {
            CloseHandle(self.0);
        }
    }
}

struct Local(*mut c_void);
impl Drop for Local {
    fn drop(&mut self) {
        // SAFETY: only LocalAlloc-family outputs returned by security APIs.
        unsafe {
            LocalFree(self.0);
        }
    }
}

fn wide(path: &Path) -> Result<Vec<u16>, Error> {
    let value = path.to_str().ok_or(Error::InvalidInput)?;
    if value.contains('\0') || value.encode_utf16().count() >= 32767 {
        return Err(Error::InvalidInput);
    }
    Ok(value.encode_utf16().chain([0]).collect())
}

struct Sid {
    storage: [u32; 17],
}
impl Sid {
    fn new() -> Result<Self, Error> {
        let mut sid = Self { storage: [0; 17] };
        let mut length = size_of_val(&sid.storage) as u32;
        // SAFETY: aligned SECURITY_MAX_SID_SIZE (68 byte) output buffer.
        success(
            unsafe {
                CreateWellKnownSid(
                    WinWriteRestrictedCodeSid,
                    null_mut(),
                    sid.storage.as_mut_ptr().cast(),
                    &mut length,
                )
            },
            "create_write_restricted_sid",
        )?;
        Ok(sid)
    }
    fn ptr(&self) -> PSID {
        self.storage.as_ptr().cast_mut().cast()
    }
}

fn file_info(handle: &Handle) -> Result<BY_HANDLE_FILE_INFORMATION, Error> {
    let mut info = BY_HANDLE_FILE_INFORMATION::default();
    // SAFETY: live file handle and correctly sized output.
    success(
        unsafe { GetFileInformationByHandle(handle.0, &mut info) },
        "directory_identity",
    )?;
    Ok(info)
}

fn open_directory(path: &Path, acl_writer: bool) -> Result<Handle, Error> {
    let volume = path.ancestors().last().ok_or(Error::InvalidInput)?;
    let volume = wide(volume)?;
    // GetDriveTypeW ABI: 2 removable, 3 fixed. Mapped network drives are not
    // local containment roots even when the remote filesystem reports NTFS.
    if !matches!(unsafe { GetDriveTypeW(volume.as_ptr()) }, 2 | 3) {
        return Err(Error::Verification("root_not_local_volume"));
    }
    let encoded = wide(path)?;
    // Deny delete sharing: directory and ancestor names cannot be exchanged for
    // junctions after validation while this guard is alive. OPEN_REPARSE_POINT
    // makes the final-component check refer to the opened object itself.
    // SAFETY: all pointers are valid through this call; handle owned immediately.
    let handle = Handle::checked(
        unsafe {
            CreateFileW(
                encoded.as_ptr(),
                READ_CONTROL | FILE_READ_ATTRIBUTES | if acl_writer { WRITE_DAC } else { 0 },
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                null(),
                OPEN_EXISTING,
                FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
                null_mut(),
            )
        },
        "open_write_root",
    )?;
    let info = file_info(&handle)?;
    if info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY == 0
        || info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
    {
        return Err(Error::Verification("root_object_type"));
    }
    // SAFETY: live handle; reject redirected/network file systems even if their
    // lexical spelling looks like a drive path.
    if unsafe { GetFileType(handle.0) } != FILE_TYPE_DISK {
        return Err(Error::Verification("root_not_disk"));
    }
    let mut flags = 0;
    let mut filesystem = [0u16; 32];
    success(
        unsafe {
            GetVolumeInformationByHandleW(
                handle.0,
                null_mut(),
                0,
                null_mut(),
                null_mut(),
                &mut flags,
                filesystem.as_mut_ptr(),
                filesystem.len() as u32,
            )
        },
        "root_volume",
    )?;
    // Acceptance is NTFS-only. Other filesystems must earn their own native gate.
    if filesystem[..5] != [78, 84, 70, 83, 0] {
        return Err(Error::Verification("root_not_ntfs"));
    }
    let mut final_name = vec![0u16; 32768];
    let count = unsafe {
        GetFinalPathNameByHandleW(
            handle.0,
            final_name.as_mut_ptr(),
            final_name.len() as u32,
            FILE_NAME_NORMALIZED | VOLUME_NAME_DOS,
        )
    };
    if count == 0 || count as usize >= final_name.len() {
        return Err(last("root_final_path"));
    }
    final_name.truncate(count as usize);
    let opened_path =
        PathBuf::from(String::from_utf16(&final_name).map_err(|_| Error::InvalidInput)?);
    if opened_path != path {
        return Err(Error::Verification("root_path_identity"));
    }
    Ok(handle)
}

fn dacl(handle: &Handle) -> Result<(Local, *mut ACL), Error> {
    let mut acl = null_mut();
    let mut descriptor = null_mut();
    // SAFETY: security descriptor returned by API owns the ACL pointer until freed.
    status(
        unsafe {
            GetSecurityInfo(
                handle.0,
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION,
                null_mut(),
                null_mut(),
                &mut acl,
                null_mut(),
                &mut descriptor,
            )
        },
        "read_root_acl",
    )?;
    let owned = Local(descriptor);
    if acl.is_null() {
        return Err(Error::Verification("null_root_dacl"));
    }
    Ok((owned, acl))
}

const ROOT_ACCESS: u32 =
    FILE_GENERIC_READ | FILE_GENERIC_WRITE | FILE_GENERIC_EXECUTE | DELETE | FILE_DELETE_CHILD;
const ROOT_INHERIT: u32 = OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE;

fn verify_acl(handle: &Handle, sid: &Sid) -> Result<(), Error> {
    let (_descriptor, acl) = dacl(handle)?;
    let mut info = ACL_SIZE_INFORMATION::default();
    // SAFETY: valid API-owned ACL, sized output.
    success(
        unsafe {
            GetAclInformation(
                acl,
                (&mut info as *mut ACL_SIZE_INFORMATION).cast(),
                size_of_val(&info) as u32,
                AclSizeInformation,
            )
        },
        "root_acl_information",
    )?;
    let mut matches = 0;
    for index in 0..info.AceCount {
        let mut ace = null_mut();
        success(unsafe { GetAce(acl, index, &mut ace) }, "root_acl_entry")?;
        // SAFETY: GetAce returns a valid ACE within the descriptor's live ACL.
        let header = unsafe { &*ace.cast::<ACE_HEADER>() };
        // ACCESS_ALLOWED_ACE_TYPE is the WinNT ABI value zero.
        if header.AceType == 0 && usize::from(header.AceSize) >= size_of::<ACCESS_ALLOWED_ACE>() {
            let allowed = unsafe { &*ace.cast::<ACCESS_ALLOWED_ACE>() };
            let entry_sid = (&allowed.SidStart as *const u32).cast_mut().cast();
            if unsafe { EqualSid(entry_sid, sid.ptr()) } != 0
                && u32::from(header.AceFlags)
                    & (ROOT_INHERIT | INHERIT_ONLY_ACE | NO_PROPAGATE_INHERIT_ACE)
                    == ROOT_INHERIT
                && allowed.Mask & ROOT_ACCESS == ROOT_ACCESS
            {
                matches += 1;
            }
        }
    }
    let trustee = TRUSTEE_W {
        TrusteeForm: TRUSTEE_IS_SID,
        TrusteeType: TRUSTEE_IS_WELL_KNOWN_GROUP,
        ptstrName: sid.ptr().cast(),
        ..Default::default()
    };
    let mut effective = 0;
    status(
        unsafe { GetEffectiveRightsFromAclW(acl, &trustee, &mut effective) },
        "root_acl_effective_rights",
    )?;
    if matches != 1 || effective & ROOT_ACCESS != ROOT_ACCESS {
        return Err(Error::Verification("root_acl_readback"));
    }
    Ok(())
}

fn install_acl(handle: &Handle, sid: &Sid) -> Result<(), Error> {
    let (_descriptor, old_acl) = dacl(handle)?;
    let entry = EXPLICIT_ACCESS_W {
        grfAccessPermissions: ROOT_ACCESS,
        grfAccessMode: GRANT_ACCESS,
        grfInheritance: ROOT_INHERIT,
        Trustee: TRUSTEE_W {
            TrusteeForm: TRUSTEE_IS_SID,
            TrusteeType: TRUSTEE_IS_WELL_KNOWN_GROUP,
            ptstrName: sid.ptr().cast(),
            ..Default::default()
        },
    };
    let mut acl = null_mut();
    status(
        unsafe { SetEntriesInAclW(1, &entry, old_acl, &mut acl) },
        "merge_root_acl",
    )?;
    let _owned_acl = Local(acl.cast());
    status(
        unsafe {
            SetSecurityInfo(
                handle.0,
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION,
                null_mut(),
                null_mut(),
                acl,
                null(),
            )
        },
        "install_root_acl",
    )?;
    verify_acl(handle, sid)
}

#[derive(Debug)]
pub(super) struct Roots {
    roots: Vec<Handle>,
    _ancestors: Vec<Handle>,
}
impl Roots {
    pub(super) fn prepare(paths: &[PathBuf; 3]) -> Result<Self, Error> {
        let sid = Sid::new()?;
        let mut ancestors = Vec::new();
        let mut roots = Vec::new();
        for path in paths {
            // Lock from the volume root down; each next component is reached
            // through already held non-reparse ancestors.
            let components: Vec<_> = path.ancestors().collect();
            for ancestor in components.into_iter().rev().filter(|p| *p != path) {
                ancestors.push(open_directory(ancestor, false)?);
            }
            let root = open_directory(path, true)?;
            install_acl(&root, &sid)?;
            roots.push(root);
        }
        Ok(Self {
            roots,
            _ancestors: ancestors,
        })
    }
    fn verify(&self) -> Result<(), Error> {
        let sid = Sid::new()?;
        for root in &self.roots {
            verify_acl(root, &sid)?;
        }
        Ok(())
    }
}

fn restricted_token() -> Result<Handle, Error> {
    let mut raw = null_mut();
    success(
        unsafe {
            OpenProcessToken(
                GetCurrentProcess(),
                TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY | TOKEN_QUERY,
                &mut raw,
            )
        },
        "open_controller_token",
    )?;
    let original = Handle::checked(raw, "open_controller_token")?;
    let sid = Sid::new()?;
    let restricting = SID_AND_ATTRIBUTES {
        Sid: sid.ptr(),
        Attributes: 0,
    };
    let mut raw = null_mut();
    success(
        unsafe {
            CreateRestrictedToken(
                original.0,
                DISABLE_MAX_PRIVILEGE | WRITE_RESTRICTED,
                0,
                null(),
                0,
                null(),
                1,
                &restricting,
                &mut raw,
            )
        },
        "create_restricted_token",
    )?;
    let token = Handle::checked(raw, "create_restricted_token")?;
    if unsafe { IsTokenRestricted(token.0) } == 0 {
        return Err(Error::Verification("token_not_restricted"));
    }
    let mut required = 0;
    unsafe {
        GetTokenInformation(token.0, TokenRestrictedSids, null_mut(), 0, &mut required);
    }
    if required < size_of::<TOKEN_GROUPS>() as u32 || required > 65536 {
        return Err(Error::Verification("restricted_sid_size"));
    }
    let mut storage = vec![0usize; (required as usize).div_ceil(size_of::<usize>())];
    success(
        unsafe {
            GetTokenInformation(
                token.0,
                TokenRestrictedSids,
                storage.as_mut_ptr().cast(),
                required,
                &mut required,
            )
        },
        "read_restricted_sids",
    )?;
    // SAFETY: API wrote a TOKEN_GROUPS into sufficiently large aligned storage.
    let groups = unsafe { &*storage.as_ptr().cast::<TOKEN_GROUPS>() };
    if groups.GroupCount != 1 || unsafe { EqualSid(groups.Groups[0].Sid, sid.ptr()) } == 0 {
        return Err(Error::Verification("restricted_sid_readback"));
    }
    Ok(token)
}

fn job() -> Result<Handle, Error> {
    let job = Handle::checked(unsafe { CreateJobObjectW(null(), null()) }, "create_job")?;
    let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    success(
        unsafe {
            SetInformationJobObject(
                job.0,
                JobObjectExtendedLimitInformation,
                (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                size_of_val(&limits) as u32,
            )
        },
        "set_job_limits",
    )?;
    let mut readback = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
    success(
        unsafe {
            QueryInformationJobObject(
                job.0,
                JobObjectExtendedLimitInformation,
                (&mut readback as *mut JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                size_of_val(&readback) as u32,
                null_mut(),
            )
        },
        "read_job_limits",
    )?;
    if readback.BasicLimitInformation.LimitFlags != JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE {
        return Err(Error::Verification("job_limit_readback"));
    }
    Ok(job)
}

fn pipe(parent_reads: bool) -> Result<(Handle, Handle), Error> {
    let attributes = SECURITY_ATTRIBUTES {
        nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: null_mut(),
        bInheritHandle: 1,
    };
    let mut read = null_mut();
    let mut write = null_mut();
    success(
        unsafe { CreatePipe(&mut read, &mut write, &attributes, 0) },
        "create_pipe",
    )?;
    let read = Handle::checked(read, "pipe_read")?;
    let write = Handle::checked(write, "pipe_write")?;
    let (parent, child) = if parent_reads {
        (read, write)
    } else {
        (write, read)
    };
    success(
        unsafe { SetHandleInformation(parent.0, HANDLE_FLAG_INHERIT, 0) },
        "clear_parent_pipe_inheritance",
    )?;
    Ok((parent, child))
}

struct Attributes {
    storage: Vec<usize>,
}
impl Attributes {
    fn new(handles: &[HANDLE; 3]) -> Result<Self, Error> {
        let mut bytes = 0;
        unsafe {
            InitializeProcThreadAttributeList(null_mut(), 1, 0, &mut bytes);
        }
        if bytes == 0 || bytes > 65536 {
            return Err(Error::Verification("attribute_list_size"));
        }
        let mut attributes = Self {
            storage: vec![0; bytes.div_ceil(size_of::<usize>())],
        };
        if let Err(error) = success(
            unsafe { InitializeProcThreadAttributeList(attributes.ptr(), 1, 0, &mut bytes) },
            "initialize_handle_list",
        ) {
            // No initialized list exists, so suppress DeleteProcThreadAttributeList.
            attributes.storage.clear();
            return Err(error);
        }
        success(
            unsafe {
                UpdateProcThreadAttribute(
                    attributes.ptr(),
                    0,
                    PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
                    handles.as_ptr().cast(),
                    size_of_val(handles),
                    null_mut(),
                    null(),
                )
            },
            "allowlist_pipe_handles",
        )?;
        Ok(attributes)
    }
    fn ptr(&mut self) -> LPPROC_THREAD_ATTRIBUTE_LIST {
        self.storage.as_mut_ptr().cast()
    }
}
impl Drop for Attributes {
    fn drop(&mut self) {
        if !self.storage.is_empty() {
            unsafe {
                DeleteProcThreadAttributeList(self.ptr());
            }
        }
    }
}

struct ChildGuard {
    job: Handle,
    process: Handle,
}
impl ChildGuard {
    fn finish(self) -> Result<(), Error> {
        success(
            unsafe { TerminateJobObject(self.job.0, 1) },
            "terminate_provider_job",
        )?;
        if unsafe { WaitForSingleObject(self.process.0, INFINITE) } != WAIT_OBJECT_0 {
            return Err(last("reap_provider"));
        }
        // Drop also closes the kill-on-close job; pipe joins happen afterwards.
        Ok(())
    }
}
impl Drop for ChildGuard {
    fn drop(&mut self) {
        // Kill the entire job even after a normal primary exit, then reap the
        // primary. TerminateProcess also covers pre-assignment failures. This
        // runs before scoped pipe-thread joins on every unwind/error path.
        unsafe {
            TerminateJobObject(self.job.0, 1);
            TerminateProcess(self.process.0, 1);
            WaitForSingleObject(self.process.0, INFINITE);
        }
    }
}

pub(super) fn execute(spec: CommandSpec, stdin: Vec<u8>) -> Result<Outcome, Error> {
    spec.roots.guard.verify()?;
    let token = restricted_token()?;
    let job = job()?;
    let (stdin_parent, stdin_child) = pipe(false)?;
    let (stdout_parent, stdout_child) = pipe(true)?;
    let (stderr_parent, stderr_child) = pipe(true)?;
    let handles = [stdin_child.0, stdout_child.0, stderr_child.0];
    let mut attributes = Attributes::new(&handles)?;
    let application = wide(&spec.executable)?;
    let directory = wide(&spec.directory)?;
    let mut argv = vec![spec.executable.to_str().ok_or(Error::InvalidInput)?.into()];
    argv.extend(spec.arguments);
    let mut command = command_line(&argv)?;
    let environment = environment_block(&spec.environment)?;
    let mut startup = STARTUPINFOEXW::default();
    startup.StartupInfo.cb = size_of_val(&startup) as u32;
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdInput = stdin_child.0;
    startup.StartupInfo.hStdOutput = stdout_child.0;
    startup.StartupInfo.hStdError = stderr_child.0;
    startup.lpAttributeList = attributes.ptr();
    let mut information = PROCESS_INFORMATION::default();
    if spec.deadline <= Instant::now() {
        return Err(Error::DeadlineExpired);
    }
    success(
        unsafe {
            CreateProcessAsUserW(
                token.0,
                application.as_ptr(),
                command.as_mut_ptr(),
                null(),
                null(),
                1,
                CREATE_SUSPENDED
                    | CREATE_UNICODE_ENVIRONMENT
                    | EXTENDED_STARTUPINFO_PRESENT
                    | CREATE_NO_WINDOW,
                environment.as_ptr().cast(),
                directory.as_ptr(),
                &startup.StartupInfo,
                &mut information,
            )
        },
        "create_suspended_provider",
    )?;
    // Successful CreateProcessAsUserW guarantees both owned handles. Construct
    // cleanup immediately before any fallible operation can strand the child.
    let child = ChildGuard {
        job,
        process: Handle(information.hProcess),
    };
    let thread = Handle(information.hThread);
    success(
        unsafe { AssignProcessToJobObject(child.job.0, child.process.0) },
        "assign_provider_to_job",
    )?;
    let mut in_job = 0;
    success(
        unsafe { IsProcessInJob(child.process.0, child.job.0, &mut in_job) },
        "verify_provider_job",
    )?;
    if in_job == 0 {
        return Err(Error::Verification("provider_not_in_job"));
    }
    // Re-read the ACL immediately before resume, after all setup has completed.
    spec.roots.guard.verify()?;
    drop(stdin_child);
    drop(stdout_child);
    drop(stderr_child);
    let stdout = stdout_parent.into_file();
    let stderr = stderr_parent.into_file();
    let mut input = stdin_parent.into_file();
    std::thread::scope(|scope| {
        // Moving guard inside scope ensures unwind kills the job BEFORE scope
        // waits on readers/writer. A blocked pipe therefore cannot strand scope.
        let child = child;
        let out = scope.spawn(move || capture(stdout, spec.output_limit));
        let err = scope.spawn(move || capture(stderr, spec.output_limit));
        let writer = scope.spawn(move || input.write_all(&stdin));
        if spec.deadline <= Instant::now() {
            return Err(Error::DeadlineExpired);
        }
        if unsafe { ResumeThread(thread.0) } != 1 {
            return Err(last("resume_provider"));
        }
        let mut timed_out = false;
        loop {
            let now = Instant::now();
            if now >= spec.deadline {
                timed_out = true;
                break;
            }
            let remaining = spec.deadline.duration_since(now).as_millis().clamp(1, 50) as u32;
            match unsafe { WaitForSingleObject(child.process.0, remaining) } {
                WAIT_OBJECT_0 => break,
                WAIT_TIMEOUT => continue,
                _ => return Err(last("wait_provider")),
            }
        }
        let mut exit_code = 1;
        if !timed_out {
            success(
                unsafe { GetExitCodeProcess(child.process.0, &mut exit_code) },
                "provider_exit_code",
            )?;
        }
        child.finish()?;
        let stdout = out.join().map_err(|_| Error::ReaderPanicked)??;
        let stderr = err.join().map_err(|_| Error::ReaderPanicked)??;
        match writer.join().map_err(|_| Error::ReaderPanicked)? {
            Ok(()) => (),
            Err(error) if timed_out || error.kind() == std::io::ErrorKind::BrokenPipe => (),
            Err(error) => return Err(error.into()),
        }
        Ok(Outcome {
            exit_code,
            timed_out,
            stdout,
            stderr,
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::WritableRoots;
    use std::time::Duration;
    use windows_sys::Win32::System::Console::{
        GetStdHandle, STD_ERROR_HANDLE, STD_INPUT_HANDLE, STD_OUTPUT_HANDLE,
    };

    fn fixture() -> (tempfile::TempDir, WritableRoots) {
        let base = tempfile::tempdir().unwrap();
        let paths =
            ["checkout", "home", "tmp"].map(|name| base.path().canonicalize().unwrap().join(name));
        for path in &paths {
            std::fs::create_dir(path).unwrap();
        }
        let roots = WritableRoots::prepare(&paths[0], &paths[1], &paths[2]).unwrap();
        (base, roots)
    }

    fn spec(roots: WritableRoots, env: Vec<(String, String)>) -> CommandSpec {
        CommandSpec::new(
            std::env::current_exe().unwrap().canonicalize().unwrap(),
            vec![
                "--exact".into(),
                "sys::tests::provider_handle_fixture".into(),
                "--nocapture".into(),
            ],
            roots.paths()[0].clone(),
            env,
            Instant::now() + Duration::from_secs(10),
            4096,
            roots,
        )
        .unwrap()
    }

    // Break caught: inheriting all handles leaks already-granted controller file
    // authority, bypassing later restricted-token filesystem access checks.
    #[test]
    fn native_containment_unrelated_inheritable_handle_excluded() {
        let (base, roots) = fixture();
        let sentinel = base
            .path()
            .canonicalize()
            .unwrap()
            .join("controller-secret-handle");
        std::fs::write(&sentinel, b"frozen").unwrap();
        let name = wide(&sentinel).unwrap();
        let sa = SECURITY_ATTRIBUTES {
            nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: null_mut(),
            bInheritHandle: 1,
        };
        let handle = Handle::checked(
            unsafe {
                CreateFileW(
                    name.as_ptr(),
                    FILE_GENERIC_READ | FILE_GENERIC_WRITE,
                    FILE_SHARE_READ | FILE_SHARE_WRITE,
                    &sa,
                    OPEN_EXISTING,
                    FILE_ATTRIBUTE_NORMAL,
                    null_mut(),
                )
            },
            "test_inheritable_file",
        )
        .unwrap();
        let info = file_info(&handle).unwrap();
        let result = crate::execute(
            spec(
                roots,
                vec![
                    ("FORBIDDEN_HANDLE".into(), (handle.0 as usize).to_string()),
                    (
                        "FORBIDDEN_VOLUME".into(),
                        info.dwVolumeSerialNumber.to_string(),
                    ),
                    ("FORBIDDEN_HIGH".into(), info.nFileIndexHigh.to_string()),
                    ("FORBIDDEN_LOW".into(), info.nFileIndexLow.to_string()),
                ],
            ),
            vec![],
        )
        .unwrap();
        assert_eq!(result.exit_code, 0, "{result:?}");
        assert!(!result.timed_out);
        assert!(String::from_utf8_lossy(&result.stdout.bytes).contains("handle-boundary-proved"));
        assert_eq!(std::fs::read(&sentinel).unwrap(), b"frozen");
    }

    // Break caught: trusting preparation without fresh readback resumes the
    // provider after the restricting-SID permission has been removed.
    #[test]
    fn native_containment_acl_setup_failure_stops_before_execution() {
        let (_base, roots) = fixture();
        let marker = roots.paths()[0].join("provider-ran");
        let sid = Sid::new().unwrap();
        let handle = &roots.guard.roots[0];
        let (_descriptor, acl) = dacl(handle).unwrap();
        let entry = EXPLICIT_ACCESS_W {
            grfAccessPermissions: 0,
            grfAccessMode: REVOKE_ACCESS,
            grfInheritance: ROOT_INHERIT,
            Trustee: TRUSTEE_W {
                TrusteeForm: TRUSTEE_IS_SID,
                TrusteeType: TRUSTEE_IS_WELL_KNOWN_GROUP,
                ptstrName: sid.ptr().cast(),
                ..Default::default()
            },
        };
        let mut replacement = null_mut();
        status(
            unsafe { SetEntriesInAclW(1, &entry, acl, &mut replacement) },
            "test_revoke_acl",
        )
        .unwrap();
        let _owned = Local(replacement.cast());
        status(
            unsafe {
                SetSecurityInfo(
                    handle.0,
                    SE_FILE_OBJECT,
                    DACL_SECURITY_INFORMATION,
                    null_mut(),
                    null_mut(),
                    replacement,
                    null(),
                )
            },
            "test_remove_acl",
        )
        .unwrap();
        let result = crate::execute(
            spec(
                roots,
                vec![("WRITE_MARKER".into(), marker.to_str().unwrap().into())],
            ),
            vec![],
        );
        assert!(
            matches!(result, Err(Error::Verification("root_acl_readback"))),
            "{result:?}"
        );
        assert!(!marker.exists());
    }

    #[test]
    fn provider_handle_fixture() {
        if let Ok(junction) = std::env::var("JUNCTION_WRITE") {
            assert_eq!(
                std::fs::write(junction, b"escaped").unwrap_err().kind(),
                std::io::ErrorKind::PermissionDenied
            );
            println!("junction-boundary-proved");
        }
        if let Ok(marker) = std::env::var("WRITE_MARKER") {
            std::fs::write(marker, b"ran").unwrap();
        }
        let Ok(value) = std::env::var("FORBIDDEN_HANDLE") else {
            return;
        };
        let raw = value.parse::<usize>().unwrap() as HANDLE;
        // A numeric handle value can be reused in the child; compare identity,
        // never confuse an unrelated child handle with an inherited sentinel.
        let mut info = BY_HANDLE_FILE_INFORMATION::default();
        if unsafe { GetFileInformationByHandle(raw, &mut info) } != 0 {
            let expected = ["FORBIDDEN_VOLUME", "FORBIDDEN_HIGH", "FORBIDDEN_LOW"]
                .map(|name| std::env::var(name).unwrap().parse::<u32>().unwrap());
            assert_ne!(
                [
                    info.dwVolumeSerialNumber,
                    info.nFileIndexHigh,
                    info.nFileIndexLow
                ],
                expected
            );
        }
        for kind in [STD_INPUT_HANDLE, STD_OUTPUT_HANDLE, STD_ERROR_HANDLE] {
            let handle = unsafe { GetStdHandle(kind) };
            assert!(!handle.is_null() && handle != INVALID_HANDLE_VALUE);
            assert_eq!(unsafe { GetFileType(handle) }, FILE_TYPE_PIPE);
        }
        println!("handle-boundary-proved");
    }

    // A real mount-point reparse object exercises junction traversal separately
    // from the symbolic-link fixture; no command interpreter is involved.
    #[test]
    fn native_containment_junction_escape_denied() {
        use windows_sys::Win32::System::{IO::DeviceIoControl, Ioctl::FSCTL_SET_REPARSE_POINT};
        let (base, roots) = fixture();
        let outside = base.path().canonicalize().unwrap().join("outside-junction");
        std::fs::create_dir(&outside).unwrap();
        let junction = roots.paths()[0].join("junction");
        std::fs::create_dir(&junction).unwrap();
        let print = outside.to_str().unwrap().strip_prefix(r"\\?\").unwrap();
        let substitute: Vec<u16> = format!(r"\??\{print}").encode_utf16().collect();
        let print: Vec<u16> = print.encode_utf16().collect();
        let mut data = vec![0u8; 16];
        // REPARSE_DATA_BUFFER mount-point layout from the WinNT ABI.
        data[0..4].copy_from_slice(&0xA0000003u32.to_le_bytes());
        data[10..12].copy_from_slice(&u16::try_from(substitute.len() * 2).unwrap().to_le_bytes());
        data[12..14].copy_from_slice(
            &u16::try_from((substitute.len() + 1) * 2)
                .unwrap()
                .to_le_bytes(),
        );
        data[14..16].copy_from_slice(&u16::try_from(print.len() * 2).unwrap().to_le_bytes());
        for unit in substitute.into_iter().chain([0]).chain(print).chain([0]) {
            data.extend(unit.to_le_bytes());
        }
        let length = u16::try_from(data.len() - 8).unwrap();
        data[4..6].copy_from_slice(&length.to_le_bytes());
        let name = wide(&junction).unwrap();
        let handle = Handle::checked(
            unsafe {
                CreateFileW(
                    name.as_ptr(),
                    FILE_GENERIC_WRITE,
                    0,
                    null(),
                    OPEN_EXISTING,
                    FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
                    null_mut(),
                )
            },
            "test_junction_open",
        )
        .unwrap();
        let mut returned = 0;
        success(
            unsafe {
                DeviceIoControl(
                    handle.0,
                    FSCTL_SET_REPARSE_POINT,
                    data.as_ptr().cast(),
                    data.len() as u32,
                    null_mut(),
                    0,
                    &mut returned,
                    null_mut(),
                )
            },
            "test_junction_create",
        )
        .unwrap();
        drop(handle);
        assert!(WritableRoots::prepare(&junction, &roots.paths()[1], &roots.paths()[2]).is_err());
        let result = crate::execute(
            spec(
                roots,
                vec![(
                    "JUNCTION_WRITE".into(),
                    junction.join("escaped").to_str().unwrap().into(),
                )],
            ),
            vec![],
        )
        .unwrap();
        assert_eq!(result.exit_code, 0, "{result:?}");
        assert!(String::from_utf8_lossy(&result.stdout.bytes).contains("junction-boundary-proved"));
        assert!(!outside.join("escaped").exists());
    }
}
