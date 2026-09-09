#Requires -Version 7.0
<#
Run from a clean, committed checkout on native Windows/NTFS with the pinned
toolchain, its targets/components, and dependencies already available offline.
The last stdout line is WINDOWS_WORKER_CONTAINMENT_SUMMARY followed by JSON.
Preflight PASS checks prerequisites only: native_evidence remains false.
This entry point never installs tools or treats cross-compilation as native proof.
#>
[CmdletBinding()]
param([switch] $PreflightOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$taskOriginalLocation = Get-Location
$taskEnvironment = @{}
$taskExitCode = 1
$taskFailureCode = 'preflight_failed'
$taskSummary = [ordered]@{
    schema_version = 1
    gate = 'windows_worker_containment'
    status = 'FAIL'
    mode = $(if ($PreflightOnly) { 'preflight' } else { 'native_suites' })
    native_host = [bool] $IsWindows
    native_evidence = $false
    source_policy = 'clean_tracked_and_nonignored_untracked'
    git_sha = $null
    repository = $null
    filesystem = $null
    os_architecture = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
    process_architecture = [Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture.ToString()
    rust_host = $null
    toolchain = '1.96.1'
    versions = [ordered]@{ powershell = $PSVersionTable.PSVersion.ToString() }
    required_native_tests = [ordered]@{
        platform = @(
            'native_containment_write_boundary_and_inherited_acl',
            'native_containment_timeout_kills_descendant_and_drains_output',
            'native_containment_provider_exit_kills_lingering_descendant',
            'native_containment_reparse_escape_denied',
            'native_containment_rejects_reparse_roots_before_launch',
            'sys::tests::native_containment_unrelated_inheritable_handle_excluded',
            'sys::tests::native_containment_acl_setup_failure_stops_before_execution',
            'sys::tests::native_containment_junction_escape_denied'
        )
        runner = @(
            'windows_containment_allowed_roots_and_escape_denial_preserve_evidence',
            'windows_containment_timeout_stops_parent_and_descendant',
            'windows_containment_literal_argv_and_bounded_output',
            'windows_containment_inventory_rejects_hardlinks_and_reparse_points',
            'windows_containment_explicit_mode_and_cleanup_fail_closed'
        )
    }
    commands = [Collections.Generic.List[object]]::new()
    error_code = $null
    error = $null
}

function Resolve-GateApplication {
    param([Parameter(Mandatory)][string] $Name)
    # Get-Command may return every matching application from PATH. Preserve
    # PATH precedence and never pass a multi-path array to a command invocation.
    $taskApplications = @(Get-Command -Name $Name -CommandType Application -ErrorAction Stop)
    if ($taskApplications.Count -eq 0) { throw "Required application is unavailable: $Name" }
    $taskApplicationPath = $taskApplications[0].Source
    if ($taskApplicationPath -isnot [string] -or
        [string]::IsNullOrWhiteSpace($taskApplicationPath) -or
        -not [IO.Path]::IsPathFullyQualified($taskApplicationPath) -or
        -not (Test-Path -LiteralPath $taskApplicationPath -PathType Leaf)) {
        throw "Required application has no usable absolute file path: $Name"
    }
    return $taskApplicationPath
}

function Invoke-GateCommand {
    param(
        [Parameter(Mandatory)][string] $Executable,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]] $Arguments
    )
    $taskRecord = [ordered]@{ executable = $Executable; arguments = $Arguments; exit_code = $null }
    $taskSummary.commands.Add($taskRecord)
    Write-Host ('RUN ' + $Executable + ' ' + ($Arguments | ConvertTo-Json -Compress))
    & $Executable @Arguments
    $taskRecord.exit_code = $LASTEXITCODE
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Executable"
    }
}

function Assert-CleanSource {
    $taskStatus = @(Invoke-GateCommand $taskGit @('status', '--porcelain=v1', '--untracked-files=all', '--ignore-submodules=none'))
    if ($taskStatus.Count -ne 0) {
        throw 'Source tree is dirty (tracked or nonignored untracked paths); freeze a clean candidate before running this gate.'
    }
    $taskCurrentSha = (Invoke-GateCommand $taskGit @('rev-parse', '--verify', 'HEAD')).Trim()
    if ($taskCurrentSha -cne $taskSummary.git_sha) { throw 'Git HEAD changed during verification.' }
}

function Assert-NativeTestOutput {
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]] $Lines,
        [Parameter(Mandatory)][string[]] $RequiredTests,
        [switch] $Listed
    )
    foreach ($taskTest in $RequiredTests) {
        $taskPattern = if ($Listed) {
            '^' + [regex]::Escape($taskTest) + ': test$'
        } else {
            '^test ' + [regex]::Escape($taskTest) + ' \.\.\. ok$'
        }
        if (@($Lines | Where-Object { $_ -cmatch $taskPattern }).Count -ne 1) {
            throw "Required native test is absent, duplicated, ignored, or did not pass: $taskTest"
        }
    }
}

try {
    # This precedes Git, Cargo, filesystem probes, and every possible write.
    if (-not $IsWindows) {
        $taskFailureCode = 'non_windows'
        throw 'Native Windows is required. macOS/Unix cross-compilation is not native containment evidence.'
    }
    if ($taskSummary.process_architecture -cne $taskSummary.os_architecture) {
        throw 'PowerShell must run in the native OS architecture, without architecture emulation.'
    }
    foreach ($taskName in @(
        'CARGO', 'CARGO_BUILD_TARGET', 'CARGO_TARGET_DIR', 'CARGO_BUILD_TARGET_DIR',
        'CARGO_BUILD_RUSTC', 'CARGO_BUILD_RUSTC_WRAPPER', 'CARGO_BUILD_RUSTC_WORKSPACE_WRAPPER',
        'CARGO_BUILD_RUSTFLAGS', 'RUSTC', 'RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER',
        'RUSTFLAGS', 'RUSTDOCFLAGS', 'CARGO_ENCODED_RUSTFLAGS', 'CARGO_ENCODED_RUSTDOCFLAGS',
        'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT'
    )) {
        if ([Environment]::GetEnvironmentVariable($taskName, 'Process')) {
            throw "Unset build/Git override before running the native gate: $taskName"
        }
    }
    if (@(Get-ChildItem Env: | Where-Object { $_.Name -like 'CARGO_TARGET_*' }).Count -ne 0) {
        throw 'Unset CARGO_TARGET_* overrides; target runners must not replace native test execution.'
    }

    Set-Location -LiteralPath (Join-Path $PSScriptRoot '..')
    $taskRoot = (Get-Location).ProviderPath
    $taskSummary.repository = $taskRoot
    if ($taskRoot.StartsWith('\\', [StringComparison]::Ordinal)) {
        throw 'Network/UNC worktrees cannot provide local NTFS evidence.'
    }
    $taskAncestor = Get-Item -LiteralPath $taskRoot -Force
    while ($null -ne $taskAncestor) {
        if ($taskAncestor.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Repository ancestors must not be reparse points.'
        }
        foreach ($taskConfig in @('.cargo/config', '.cargo/config.toml')) {
            if (Test-Path -LiteralPath (Join-Path $taskAncestor.FullName $taskConfig)) {
                throw 'Cargo ancestor configuration is not supported by this native evidence gate.'
            }
        }
        $taskAncestor = $taskAncestor.Parent
    }
    $taskCargoHome = if ($env:CARGO_HOME) { $env:CARGO_HOME } else { Join-Path ([Environment]::GetFolderPath('UserProfile')) '.cargo' }
    foreach ($taskConfig in @('config', 'config.toml')) {
        if (Test-Path -LiteralPath (Join-Path $taskCargoHome $taskConfig)) {
            throw 'Cargo home configuration is not supported by this native evidence gate.'
        }
    }
    # Get-Volume resolves the volume containing the file, including mount points.
    $taskVolumes = @(Get-Volume -FilePath (Join-Path $taskRoot 'Cargo.toml') -ErrorAction Stop)
    if ($taskVolumes.Count -ne 1 -or $taskVolumes[0].FileSystemType -ine 'NTFS') {
        throw 'Repository must reside on one local NTFS volume.'
    }
    $taskSummary.filesystem = 'NTFS'
    $taskGit = Resolve-GateApplication 'git'
    $taskCargo = Resolve-GateApplication 'cargo'
    $taskRustc = Resolve-GateApplication 'rustc'
    $taskRustup = Resolve-GateApplication 'rustup'
    $taskSummary.git_sha = (Invoke-GateCommand $taskGit @('rev-parse', '--verify', 'HEAD')).Trim()
    if ($taskSummary.git_sha -cnotmatch '^[0-9a-f]{40}$') { throw 'Expected an exact Git SHA-1 commit identity.' }
    $taskGitRoot = (Invoke-GateCommand $taskGit @('rev-parse', '--show-toplevel')).Trim()
    if ([IO.Path]::GetFullPath($taskGitRoot) -ine [IO.Path]::GetFullPath($taskRoot)) {
        throw 'Script directory does not identify the Git worktree root.'
    }
    Assert-CleanSource
    $taskPins = [regex]::Matches((Get-Content -Raw -LiteralPath 'rust-toolchain.toml'), '(?m)^channel\s*=\s*"([^"]+)"\s*$')
    if ($taskPins.Count -ne 1 -or $taskPins[0].Groups[1].Value -cne '1.96.1') {
        throw 'rust-toolchain.toml must match the reviewed 1.96.1 gate pin.'
    }
    foreach ($taskName in @('CARGO_NET_OFFLINE', 'RUSTUP_AUTO_INSTALL', 'RUST_TEST_THREADS', 'TEMP', 'TMP', 'HELEOS_BIN', 'HELEOS_PDF_GUEST', 'HELEOS_NATIVE_GIT')) {
        $taskEnvironment[$taskName] = [Environment]::GetEnvironmentVariable($taskName, 'Process')
    }
    $env:CARGO_NET_OFFLINE = 'true'
    $env:RUSTUP_AUTO_INSTALL = '0'
    $env:RUST_TEST_THREADS = '1'
    $env:HELEOS_NATIVE_GIT = $taskGit
    $taskSummary.versions.git = (Invoke-GateCommand $taskGit @('--version')) -join "`n"
    $taskSummary.versions.rustup = (Invoke-GateCommand $taskRustup @('--version')) -join "`n"
    $taskSummary.versions.cargo = (Invoke-GateCommand $taskCargo @('+1.96.1', '--version', '--verbose')) -join "`n"
    $taskRustVersion = (Invoke-GateCommand $taskRustc @('+1.96.1', '--version', '--verbose')) -join "`n"
    $taskSummary.versions.rustc = $taskRustVersion
    $taskHostMatch = [regex]::Match($taskRustVersion, '(?m)^host: ([^\r\n]+)')
    $taskExpectedHost = switch ($taskSummary.os_architecture) {
        'X64' { 'x86_64-pc-windows-msvc' }
        'Arm64' { 'aarch64-pc-windows-msvc' }
        default { throw 'Only native x64 or ARM64 Windows MSVC hosts are supported.' }
    }
    if (-not $taskHostMatch.Success -or $taskHostMatch.Groups[1].Value -cne $taskExpectedHost) {
        throw 'Pinned Rust compiler host must match the native Windows OS architecture.'
    }
    $taskSummary.rust_host = $taskExpectedHost
    $taskSummary.versions.clippy = (Invoke-GateCommand $taskCargo @('+1.96.1', 'clippy', '--version')) -join "`n"
    $taskSummary.versions.rustfmt = (Invoke-GateCommand $taskCargo @('+1.96.1', 'fmt', '--version')) -join "`n"
    $taskTargets = @(Invoke-GateCommand $taskRustup @('target', 'list', '--installed', '--toolchain', '1.96.1'))
    foreach ($taskTarget in @($taskExpectedHost, 'x86_64-pc-windows-msvc', 'wasm32-wasip1')) {
        if ($taskTargets -cnotcontains $taskTarget) { throw "Required target is not installed: $taskTarget" }
    }
    $taskSummary.versions.installed_targets = $taskTargets

    if (-not $PreflightOnly) {
        $taskFailureCode = 'verification_failed'
        # Keep test temp directories on the repository's verified NTFS volume.
        foreach ($taskPath in @('target', 'target/windows-worker-containment-tmp')) {
            if (Test-Path -LiteralPath $taskPath) {
                $taskItem = Get-Item -LiteralPath $taskPath -Force
                if (-not $taskItem.PSIsContainer -or ($taskItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                    throw 'Native target/temp directory must be a real directory, not a reparse point.'
                }
            }
        }
        $taskTemp = [IO.Directory]::CreateDirectory((Join-Path $taskRoot 'target/windows-worker-containment-tmp')).FullName
        $env:TEMP = $taskTemp
        $env:TMP = $taskTemp
        Invoke-GateCommand $taskCargo @('+1.96.1', 'fmt', '--all', '--', '--check') | Out-Host
        Invoke-GateCommand $taskCargo @('+1.96.1', 'test', '--locked', '--offline', '-p', 'heleos-worker-windows', '--all-targets', '--all-features', '--', '--list', '--color', 'never') | Tee-Object -Variable taskNativeList | Out-Host
        Assert-NativeTestOutput @($taskNativeList) $taskSummary.required_native_tests.platform -Listed
        Invoke-GateCommand $taskCargo @('+1.96.1', 'test', '--locked', '--offline', '-p', 'heleos-worker-runner', '--all-targets', '--all-features', '--', '--list', '--color', 'never') | Tee-Object -Variable taskNativeList | Out-Host
        Assert-NativeTestOutput @($taskNativeList) $taskSummary.required_native_tests.runner -Listed
        Invoke-GateCommand $taskCargo @('+1.96.1', 'test', '--locked', '--offline', '-p', 'heleos-worker-windows', '--all-targets', '--all-features', '--', '--color', 'never', '--format', 'pretty') | Tee-Object -Variable taskNativeOutput | Out-Host
        Assert-NativeTestOutput @($taskNativeOutput) $taskSummary.required_native_tests.platform
        Invoke-GateCommand $taskCargo @('+1.96.1', 'test', '--locked', '--offline', '-p', 'heleos-worker-runner', '--all-targets', '--all-features', '--', '--color', 'never', '--format', 'pretty') | Tee-Object -Variable taskNativeOutput | Out-Host
        Assert-NativeTestOutput @($taskNativeOutput) $taskSummary.required_native_tests.runner
        Invoke-GateCommand $taskCargo @('+1.96.1', 'build', '--locked', '--offline', '-p', 'heleos-verification', '--bin', 'verify-provenance') | Out-Host
        $taskVerifier = (Resolve-Path -LiteralPath 'target/debug/verify-provenance.exe').ProviderPath
        Invoke-GateCommand $taskVerifier @('build-pdf-guest') | Out-Host
        $env:HELEOS_PDF_GUEST = (Resolve-Path -LiteralPath 'target/wasm32-wasip1/release/heleos_pdf_guest.wasm').ProviderPath
        Invoke-GateCommand $taskCargo @('+1.96.1', 'clippy', '--locked', '--offline', '-p', 'heleos-worker-windows', '--all-targets', '--all-features', '--', '-D', 'warnings') | Out-Host
        Invoke-GateCommand $taskCargo @('+1.96.1', 'clippy', '--locked', '--offline', '-p', 'heleos-worker-runner', '--all-targets', '--all-features', '--', '-D', 'warnings') | Out-Host
        Invoke-GateCommand $taskCargo @('+1.96.1', 'clippy', '--locked', '--offline', '--workspace', '--all-targets', '--all-features', '--', '-D', 'warnings') | Out-Host
        Invoke-GateCommand $taskCargo @('+1.96.1', 'check', '--locked', '--offline', '--workspace', '--all-targets', '--target', 'x86_64-pc-windows-msvc') | Out-Host
        Invoke-GateCommand $taskCargo @('+1.96.1', 'build', '--locked', '--offline', '-p', 'heleos-cli', '--bin', 'heleos') | Out-Host
        $env:HELEOS_BIN = (Resolve-Path -LiteralPath 'target/debug/heleos.exe').ProviderPath
        Invoke-GateCommand $taskCargo @('+1.96.1', 'test', '--locked', '--offline', '--workspace', '--all-targets', '--all-features') | Out-Host
        # Native equivalent of scripts/verify-provenance; no shell dependency.
        Invoke-GateCommand $taskVerifier @() | Out-Host
        Invoke-GateCommand $taskGit @('diff', '--check') | Out-Host
    }
    Assert-CleanSource
    $taskSummary.native_evidence = -not [bool] $PreflightOnly
    $taskSummary.status = 'PASS'
    $taskExitCode = 0
}
catch {
    $taskSummary.error_code = $taskFailureCode
    $taskSummary.error = $_.Exception.Message
}
finally {
    foreach ($taskName in $taskEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($taskName, $taskEnvironment[$taskName], 'Process')
    }
    Set-Location -LiteralPath $taskOriginalLocation.Path
    Write-Output ('WINDOWS_WORKER_CONTAINMENT_SUMMARY ' + ($taskSummary | ConvertTo-Json -Depth 8 -Compress))
}
exit $taskExitCode
