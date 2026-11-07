#Requires -Version 7.0
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Stop-SupplyChain([string] $Message) { throw "SUPPLY_CHAIN_FAIL: $Message" }
if (-not $IsWindows) { Stop-SupplyChain 'requires native Windows; use the Bash entry point on macOS' }
if ($args.Count -ne 0) { Stop-SupplyChain 'unexpected arguments; there are no skip or bootstrap modes' }
foreach ($taskName in @(
    'CARGO', 'CARGO_BUILD_TARGET', 'CARGO_TARGET_DIR', 'CARGO_BUILD_TARGET_DIR', 'CARGO_BUILD_RUSTC', 'CARGO_BUILD_RUSTC_WRAPPER',
    'CARGO_BUILD_RUSTFLAGS', 'RUSTC', 'RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER', 'RUSTFLAGS',
    'CARGO_ENCODED_RUSTFLAGS', 'GITLEAKS_CONFIG', 'GITLEAKS_CONFIG_TOML', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT'
)) {
    if ([Environment]::GetEnvironmentVariable($taskName, 'Process')) {
        Stop-SupplyChain "inherited authority is forbidden: $taskName"
    }
}

function Invoke-SupplyChain {
    param([string] $Executable, [string[]] $Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { Stop-SupplyChain "command failed: $Executable (exit $LASTEXITCODE)" }
}

function Assert-CleanCandidate {
    if (@(Invoke-SupplyChain 'git' @('status', '--porcelain=v1', '--untracked-files=all', '--ignore-submodules=none')).Count -ne 0) {
        Stop-SupplyChain 'native-suite candidate must have no tracked or untracked nonignored changes'
    }
    # Status alone cannot bind ignored files or raw bytes hidden by Git filters
    # or index flags. Each critical input must exist as an exact HEAD blob.
    foreach ($taskInputPath in @('tests/verification/src/bin/verify-provenance.rs',
        'tests/verification/tests/native_suite_receipt.rs', 'scripts/verify-supply-chain.ps1', 'governance/tools.toml')) {
        $taskHeadBlob = Invoke-SupplyChain 'git' @('rev-parse', '--verify', "HEAD:$taskInputPath")
        if ((Invoke-SupplyChain 'git' @('cat-file', '-t', $taskHeadBlob)) -cne 'blob') {
            Stop-SupplyChain "native-suite candidate input is not a HEAD blob: $taskInputPath"
        }
        if ((Invoke-SupplyChain 'git' @('hash-object', '--no-filters', $taskInputPath)) -cne $taskHeadBlob) {
            Stop-SupplyChain "native-suite candidate raw input differs from HEAD: $taskInputPath"
        }
    }
}

function Invoke-NativeTranscript {
    param([string[]] $Argv, [string] $Path)
    # Drain both pipes concurrently without PowerShell's native stderr/error
    # conversion. Keep UTF-8/LF stream files and compose stdout then stderr so
    # pipe scheduling cannot change the transcript's cross-stream ordering.
    $taskProcess = [Diagnostics.Process]::new()
    $taskProcess.StartInfo.FileName = @(Get-Command $Argv[0] -CommandType Application)[0].Source
    $taskProcess.StartInfo.WorkingDirectory = $taskRoot
    $taskProcess.StartInfo.UseShellExecute = $false
    $taskProcess.StartInfo.RedirectStandardOutput = $true
    $taskProcess.StartInfo.RedirectStandardError = $true
    $taskProcess.StartInfo.StandardOutputEncoding = [Text.UTF8Encoding]::new($false, $true)
    $taskProcess.StartInfo.StandardErrorEncoding = [Text.UTF8Encoding]::new($false, $true)
    foreach ($taskArgument in $Argv[1..($Argv.Count - 1)]) {
        $taskProcess.StartInfo.ArgumentList.Add($taskArgument)
    }
    $taskWriters = @()
    $taskStarted = $false
    try {
        foreach ($taskSuffix in @('.stdout', '.stderr')) {
            $taskFile = [IO.File]::Open($Path + $taskSuffix, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
            $taskWriter = [IO.StreamWriter]::new($taskFile, [Text.UTF8Encoding]::new($false))
            $taskWriter.NewLine = "`n"
            $taskWriter.AutoFlush = $true
            $taskWriters += $taskWriter
        }
        Write-Host ('Native suite command: ' + ($Argv -join ' '))
        if (-not $taskProcess.Start()) { Stop-SupplyChain 'native suite process did not start' }
        $taskStarted = $true
        $taskReaders = @($taskProcess.StandardOutput, $taskProcess.StandardError)
        $taskReads = @($taskReaders[0].ReadLineAsync(), $taskReaders[1].ReadLineAsync())
        while ($null -ne $taskReads[0] -or $null -ne $taskReads[1]) {
            for ($taskStream = 0; $taskStream -lt 2; $taskStream++) {
                if ($null -eq $taskReads[$taskStream] -or -not $taskReads[$taskStream].IsCompleted) { continue }
                $taskLine = $taskReads[$taskStream].GetAwaiter().GetResult()
                if ($null -eq $taskLine) { $taskReads[$taskStream] = $null; continue }
                $taskWriters[$taskStream].WriteLine($taskLine)
                Write-Host $taskLine
                $taskReads[$taskStream] = $taskReaders[$taskStream].ReadLineAsync()
            }
            $taskPending = [Threading.Tasks.Task[]] @($taskReads | Where-Object { $null -ne $_ })
            if ($taskPending.Count -gt 0) {
                $null = [Threading.Tasks.Task]::WhenAny($taskPending).GetAwaiter().GetResult()
            }
        }
        $taskProcess.WaitForExit()
        $taskExitCode = $taskProcess.ExitCode
        foreach ($taskWriter in $taskWriters) { $taskWriter.Dispose() }
        $taskWriters = @()
        $taskTranscript = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
        try {
            foreach ($taskSuffix in @('.stdout', '.stderr')) {
                $taskInput = [IO.File]::OpenRead($Path + $taskSuffix)
                try { $taskInput.CopyTo($taskTranscript) }
                finally { $taskInput.Dispose() }
            }
        }
        finally { $taskTranscript.Dispose() }
        return $taskExitCode
    }
    finally {
        foreach ($taskWriter in $taskWriters) { $taskWriter.Dispose() }
        if ($taskStarted -and -not $taskProcess.HasExited) {
            $taskProcess.Kill($true)
            $taskProcess.WaitForExit()
        }
        $taskProcess.Dispose()
    }
}

function Save-NativeManifest {
    $taskManifestJson = ConvertTo-Json -InputObject $taskNativeManifest -Depth 8 -Compress
    [IO.File]::WriteAllText($taskNativeManifestPath, $taskManifestJson + "`n", [Text.UTF8Encoding]::new($false))
}

function Get-TextSha256([string] $Text) {
    $taskHasher = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($taskHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text))).Replace('-', '').ToLowerInvariant() }
    finally { $taskHasher.Dispose() }
}

function Assert-Sources {
    Invoke-SupplyChain 'git' (@('diff', '--no-ext-diff', '--no-textconv', '--quiet', $taskBase, '--') + $taskSourcePaths + $taskExclusions)
    if (@(Invoke-SupplyChain 'git' (@('ls-files', '--others', '--') + $taskSourcePaths + ":(exclude,literal)$taskNativeSuitePath")).Count -ne 0) {
        Stop-SupplyChain 'unrecorded source or guest file'
    }
    foreach ($taskPath in $taskPaths) {
        if ($taskPath -cnotmatch '^[A-Za-z0-9_./-]+$') { Stop-SupplyChain 'unsupported governed source filename' }
        $taskItem = Get-Item -LiteralPath (Join-Path $taskRoot $taskPath) -Force
        if ($taskItem -is [IO.DirectoryInfo]) { Stop-SupplyChain "missing governed source: $taskPath" }
        while ($null -ne $taskItem -and $taskItem.FullName -ine $taskRoot) {
            if ($taskItem.Attributes -band [IO.FileAttributes]::ReparsePoint) { Stop-SupplyChain 'indirect governed source path' }
            $taskItem = if ($taskItem -is [IO.DirectoryInfo]) { $taskItem.Parent } else { $taskItem.Directory }
        }
        # This exact admitted addition has no blob in the historical baseline.
        if ($taskPath -ceq $taskNativeSuitePath) {
            Assert-Hash (Join-Path $taskRoot $taskPath) $taskNativeSuiteSha256
            continue
        }
        $taskHash = Invoke-SupplyChain 'git' @('hash-object', '--no-filters', $taskPath)
        $taskExpected = Invoke-SupplyChain 'git' @('rev-parse', "${taskBase}:$taskPath")
        if ($taskDeltaPaths -ccontains $taskPath) {
            if (-not $taskDeltaHashes.ContainsKey($taskPath)) { $taskDeltaHashes[$taskPath] = $taskHash }
            $taskExpected = $taskDeltaHashes[$taskPath]
        }
        if ($taskPackagingPaths -ccontains $taskPath) {
            $taskBaseline = (Invoke-SupplyChain 'git' @('show', "${taskBase}:$taskPath")) -join "`n"
            $taskBaseline += "`n"
            if (-not $taskBaseline.StartsWith("[package]`n", [StringComparison]::Ordinal)) { Stop-SupplyChain 'invalid packaging baseline' }
            $taskExpectedText = "[package]`npublish = false`n" + $taskBaseline.Substring(10)
            if ((Get-FileHash -LiteralPath (Join-Path $taskRoot $taskPath) -Algorithm SHA256).Hash.ToLowerInvariant() -cne (Get-TextSha256 $taskExpectedText)) {
                Stop-SupplyChain "raw source-byte drift: $taskPath"
            }
        }
        elseif ($taskHash -cne $taskExpected) { Stop-SupplyChain "raw source-byte drift: $taskPath" }
    }
}

function Assert-DependencyBoundary {
    # The sole lock delta is two already-resolved verifier edges. Every registry
    # identity/checksum, production closure and dependency string stays exact.
    $taskLockLines = @(Invoke-SupplyChain 'git' @('show', "${taskBase}:Cargo.lock"))
    $taskExpectedLines = [Collections.Generic.List[string]]::new()
    $taskInVerifier = $false
    $taskVerifierCount = 0
    $taskJsonCount = 0
    $taskTomlCount = 0
    foreach ($taskLine in $taskLockLines) {
        if ($taskLine -ceq '[[package]]') { $taskInVerifier = $false }
        if ($taskLine -ceq 'name = "heleos-verification"') { $taskInVerifier = $true; $taskVerifierCount++ }
        $taskExpectedLines.Add($taskLine)
        if ($taskInVerifier -and $taskLine -ceq ' "serde_jcs",') { $taskExpectedLines.Add(' "serde_json",'); $taskJsonCount++ }
        if ($taskInVerifier -and $taskLine -ceq ' "tempfile",') { $taskExpectedLines.Add(' "toml 1.1.4+spec-1.1.0",'); $taskTomlCount++ }
    }
    if ($taskVerifierCount -ne 1 -or $taskJsonCount -ne 1 -or $taskTomlCount -ne 1) { Stop-SupplyChain 'invalid verifier lock baseline' }
    if ((Get-FileHash -LiteralPath (Join-Path $taskRoot 'Cargo.lock') -Algorithm SHA256).Hash.ToLowerInvariant() -cne (Get-TextSha256 (($taskExpectedLines -join "`n") + "`n"))) {
        Stop-SupplyChain 'unapproved lock package, closure, feature, or dependency delta'
    }
    $taskRootLines = [IO.File]::ReadAllLines((Join-Path $taskRoot 'Cargo.toml'))
    if (@($taskRootLines | Where-Object { $_ -ceq 'cap-primitives = { version = "=4.0.3", default-features = false }' }).Count -ne 1) {
        Stop-SupplyChain 'cap-primitives root pin/features drift'
    }
    foreach ($taskPath in @('crates/heleos-platform-fs/Cargo.toml', 'crates/heleos-cli/Cargo.toml')) {
        $taskSection = ''
        $taskEdgeCount = 0
        foreach ($taskLine in [IO.File]::ReadAllLines((Join-Path $taskRoot $taskPath))) {
            if ($taskLine.StartsWith('[', [StringComparison]::Ordinal)) { $taskSection = $taskLine }
            if ($taskLine.Contains('cap-primitives')) {
                if ($taskLine -cne 'cap-primitives = { workspace = true }' -or $taskSection -cne "[target.'cfg(windows)'.dependencies]") {
                    Stop-SupplyChain 'cap-primitives direct edge/features drift'
                }
                $taskEdgeCount++
            }
        }
        if ($taskEdgeCount -ne 1) { Stop-SupplyChain 'cap-primitives direct edge/features drift' }
    }
    & git grep -q -E 'cap[-_]primitives' -- crates/heleos-core
    if ($LASTEXITCODE -eq 0) { Stop-SupplyChain 'core cap-primitives edge or use is forbidden' }
    if ($LASTEXITCODE -ne 1) { Stop-SupplyChain 'cannot inspect core cap-primitives authority' }
}

# Read only canonical one-line fields outside TOML multiline strings. Do not
# evaluate registry contents or silently take the first of duplicate fields.
function Get-Admission([string] $Tool, [string] $Key) {
    $taskValues = @()
    $taskRecordCount = 0
    $taskMultiline = $false
    $taskSelected = $false
    foreach ($taskLine in [IO.File]::ReadAllLines((Join-Path $taskRoot 'governance/tools.toml'))) {
        if ($taskLine.Contains("'''")) { Stop-SupplyChain 'unsupported literal-multiline admission syntax' }
        $taskQuotes = [regex]::Matches($taskLine, '"""').Count
        if ($taskQuotes -gt 0) {
            if (($taskQuotes % 2) -ne 0) { $taskMultiline = -not $taskMultiline }
            continue
        }
        if (-not $taskMultiline -and $taskLine -ceq '[[tool]]') { $taskSelected = $false }
        if (-not $taskMultiline -and $taskLine -cmatch '^name = "([^"]+)"$') {
            $taskSelected = $Matches[1] -ceq $Tool
            if ($taskSelected) { $taskRecordCount++ }
        }
        if (-not $taskMultiline -and $taskSelected -and $taskLine -match ('^\s*' + [regex]::Escape($Key) + '\s*=')) {
            if ($taskLine -notmatch ('^\s*' + [regex]::Escape($Key) + '\s*=\s*"([A-Za-z0-9_.:+/-]+)"$')) {
                Stop-SupplyChain "noncanonical admission: $Key"
            }
            $taskValues += $Matches[1]
        }
    }
    if ($taskMultiline -or $taskValues.Count -ne 1) { Stop-SupplyChain "missing or ambiguous admission: $Tool.$Key" }
    if ($Tool -ceq 'heleos-foundation-verifier' -and $taskRecordCount -ne 1) {
        Stop-SupplyChain 'missing or ambiguous heleos-foundation-verifier record'
    }
    return $taskValues[0]
}

function Assert-Hash([string] $Path, [string] $Expected) {
    if ($Expected -cnotmatch '^[a-f0-9]{64}$') { Stop-SupplyChain 'invalid SHA-256 admission' }
    $taskItem = Get-Item -LiteralPath $Path -Force
    if ($taskItem.PSIsContainer -or ($taskItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Stop-SupplyChain 'indirect hashed input'
    }
    $taskParent = $taskItem.Directory
    while ($null -ne $taskParent -and $taskParent.FullName -ine $taskRoot) {
        if ($taskParent.Attributes -band [IO.FileAttributes]::ReparsePoint) { Stop-SupplyChain 'indirect hashed input path' }
        $taskParent = $taskParent.Parent
    }
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Expected) {
        Stop-SupplyChain 'checksum drift'
    }
}

function Assert-DenyAudit {
    $taskDirectory = Get-Item -LiteralPath $taskDenyWorkspace -Force
    if (-not ($taskDirectory -is [IO.DirectoryInfo]) -or ($taskDirectory.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Stop-SupplyChain 'indirect cargo-deny audit workspace'
    }
    $taskDirectories = [Collections.Generic.Stack[IO.DirectoryInfo]]::new()
    $taskDirectories.Push($taskDirectory)
    $taskFound = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    while ($taskDirectories.Count -gt 0) {
        foreach ($taskEntry in $taskDirectories.Pop().EnumerateFileSystemInfos()) {
            if ($taskEntry.Attributes -band ([IO.FileAttributes]::ReparsePoint -bor [IO.FileAttributes]::Device)) {
                Stop-SupplyChain 'special or indirect cargo-deny audit entry'
            }
            if ($taskEntry -is [IO.DirectoryInfo]) { $taskDirectories.Push($taskEntry); continue }
            if (-not ($taskEntry -is [IO.FileInfo])) { Stop-SupplyChain 'special cargo-deny audit entry' }
            $taskRelative = [IO.Path]::GetRelativePath($taskDenyWorkspace, $taskEntry.FullName).Replace('\', '/')
            if (-not $taskAuditHashes.Contains($taskRelative) -or -not $taskFound.Add($taskRelative)) {
                Stop-SupplyChain 'cargo-deny audit inventory drift'
            }
        }
    }
    if ($taskFound.Count -ne $taskAuditHashes.Count) { Stop-SupplyChain 'cargo-deny audit inventory drift' }
    foreach ($taskPath in $taskAuditHashes.Keys) {
        Assert-Hash (Join-Path $taskRoot $taskPath) $taskAuditSourceHashes[$taskPath]
        Assert-Hash (Join-Path $taskDenyWorkspace $taskPath) $taskAuditHashes[$taskPath]
    }
    Assert-Hash (Join-Path $taskDenyWorkspace 'Cargo.lock') $taskAuditSourceHashes['Cargo.lock']
    Assert-Hash 'deny.toml' $taskPolicySha256
    Assert-Sources
}

function Initialize-DenyAudit {
    Assert-Sources
    $script:taskDenyEvidence = Join-Path $taskRoot ('target/supply-chain-deny.' + [guid]::NewGuid().ToString('N'))
    $script:taskDenyWorkspace = Join-Path $taskDenyEvidence 'workspace'
    $null = New-Item -ItemType Directory -Path $taskDenyWorkspace
    $script:taskAuditSourceHashes = [ordered]@{}
    $script:taskAuditHashes = [ordered]@{}
    foreach ($taskPath in $taskPaths) {
        if ($taskPath -cnotmatch '^(Cargo\.toml|Cargo\.lock|crates/.+|tests/verification/.+)$') { continue }
        $taskSource = Join-Path $taskRoot $taskPath
        $taskItem = Get-Item -LiteralPath $taskSource -Force
        if (-not ($taskItem -is [IO.FileInfo])) { Stop-SupplyChain 'special cargo-deny audit source' }
        while ($null -ne $taskItem -and $taskItem.FullName -ine $taskRoot) {
            if ($taskItem.Attributes -band ([IO.FileAttributes]::ReparsePoint -bor [IO.FileAttributes]::Device)) {
                Stop-SupplyChain 'special or indirect cargo-deny audit source'
            }
            $taskItem = if ($taskItem -is [IO.DirectoryInfo]) { $taskItem.Parent } else { $taskItem.Directory }
        }
        $taskHash = (Get-FileHash -LiteralPath $taskSource -Algorithm SHA256).Hash.ToLowerInvariant()
        Assert-Hash $taskSource $taskHash
        $taskDestination = Join-Path $taskDenyWorkspace $taskPath
        $null = [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($taskDestination))
        [IO.File]::Copy($taskSource, $taskDestination, $false)
        Assert-Hash $taskDestination $taskHash
        $taskAuditSourceHashes[$taskPath] = $taskHash
        $taskAuditHashes[$taskPath] = $taskHash
    }
    if ($taskAuditHashes.Count -eq 0) { Stop-SupplyChain 'empty cargo-deny audit inventory' }
    foreach ($taskPath in $taskAuditPackagingPaths) {
        # Only these three disposable PDF manifests get this single insertion.
        # Their original bytes remain authoritative for every build and SBOM.
        $taskBaseline = (Invoke-SupplyChain 'git' @('show', "${taskBase}:$taskPath")) -join "`n"
        $taskBaseline += "`n"
        if (-not $taskBaseline.StartsWith("[package]`n", [StringComparison]::Ordinal)) { Stop-SupplyChain 'invalid cargo-deny packaging baseline' }
        $taskExpectedText = "[package]`npublish = false`n" + $taskBaseline.Substring(10)
        $taskDestination = Join-Path $taskDenyWorkspace $taskPath
        Assert-Hash $taskDestination $taskAuditSourceHashes[$taskPath]
        [IO.File]::WriteAllText($taskDestination, $taskExpectedText, [Text.UTF8Encoding]::new($false))
        $taskAuditHashes[$taskPath] = Get-TextSha256 $taskExpectedText
    }
    foreach ($taskPair in @(@('source-hashes.sha256', $taskAuditSourceHashes), @('audit-hashes.sha256', $taskAuditHashes))) {
        $taskHashLines = @($taskPair[1].Keys | ForEach-Object { $taskPair[1][$_] + '  ' + $_ })
        [IO.File]::WriteAllText((Join-Path $taskDenyEvidence $taskPair[0]), (($taskHashLines -join "`n") + "`n"), [Text.UTF8Encoding]::new($false))
    }
    Assert-DenyAudit
}

function Invoke-DenyAudit {
    Initialize-DenyAudit
    & cargo +1.96.1 deny --manifest-path (Join-Path $taskDenyWorkspace 'Cargo.toml') --config (Join-Path $taskRoot 'deny.toml') `
        --workspace --all-features --offline --frozen check --deny warnings advisories bans licenses sources `
        *> (Join-Path $taskDenyEvidence 'cargo-deny.log')
    $taskStatus = $LASTEXITCODE
    Assert-DenyAudit
    Write-Output ('Cargo-deny audit evidence retained under ' + [IO.Path]::GetRelativePath($taskRoot, $taskDenyEvidence) + " (exit $taskStatus)")
    if ($taskStatus -ne 0) { Stop-SupplyChain 'cargo-deny audit failed; inspect retained cargo-deny.log' }
}

function Assert-Tool([string] $Name, [string] $Pin, [string] $Checksum, [string] $Version, [string[]] $VersionArguments) {
    if ((Get-Admission $Name 'version_or_digest') -cne $Pin) { Stop-SupplyChain "tool version admission drift: $Name" }
    if ((Get-Admission $Name 'registry_checksum') -cne $Checksum) { Stop-SupplyChain "tool source-archive admission drift: $Name" }
    $taskCommand = Get-Command $Name -CommandType Application -ErrorAction Stop
    $taskVersion = @(Invoke-SupplyChain $taskCommand.Source $VersionArguments)
    if ($taskVersion.Count -ne 1 -or $taskVersion[0] -cne $Version) { Stop-SupplyChain "version drift: $Name" }
}

function Assert-Advisory([string] $Path) {
    $taskDirectory = Get-Item -LiteralPath $Path -Force
    if (-not $taskDirectory.PSIsContainer -or ($taskDirectory.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Stop-SupplyChain 'missing or indirect advisory snapshot'
    }
    $taskCommit = Get-Admission 'cargo-deny' 'advisory_data_snapshot_commit'
    if ($taskCommit -cnotmatch '^[a-f0-9]{40}$') { Stop-SupplyChain 'invalid advisory commit' }
    if ((Invoke-SupplyChain 'git' @('-C', $Path, 'rev-parse', 'HEAD')) -cne $taskCommit) {
        Stop-SupplyChain 'advisory commit drift'
    }
    $taskOrigin = Invoke-SupplyChain 'git' @('-C', $Path, 'remote', 'get-url', 'origin')
    $taskExpectedOrigin = Get-Admission 'cargo-deny' 'advisory_data_origin'
    if (($taskOrigin -creplace '\.git$', '') -cne ($taskExpectedOrigin -creplace '\.git$', '')) {
        Stop-SupplyChain 'unexpected advisory origin'
    }
    if (@(Invoke-SupplyChain 'git' @('-C', $Path, 'status', '--porcelain', '--untracked-files=all')).Count -ne 0) {
        Stop-SupplyChain 'advisory snapshot has modified or extra files'
    }
    foreach ($taskFile in @(Invoke-SupplyChain 'git' @('-C', $Path, 'ls-tree', '-r', '--name-only', $taskCommit))) {
        if ($taskFile -cnotmatch '^[A-Za-z0-9_./-]+$') { Stop-SupplyChain 'unsupported advisory filename' }
        $taskItem = Get-Item -LiteralPath (Join-Path $Path $taskFile) -Force
        while ($null -ne $taskItem -and $taskItem.FullName -ine $taskDirectory.FullName) {
            if ($taskItem.Attributes -band [IO.FileAttributes]::ReparsePoint) { Stop-SupplyChain 'indirect advisory file' }
            $taskItem = if ($taskItem -is [IO.DirectoryInfo]) { $taskItem.Parent } else { $taskItem.Directory }
        }
        if ((Invoke-SupplyChain 'git' @('-C', $Path, 'hash-object', '--no-filters', $taskFile)) -cne
            (Invoke-SupplyChain 'git' @('-C', $Path, 'rev-parse', "${taskCommit}:$taskFile"))) {
            Stop-SupplyChain 'raw advisory file drift'
        }
    }
    if ((Invoke-SupplyChain 'git' @('-C', $Path, 'rev-parse', 'HEAD^{tree}')) -cne
        (Get-Admission 'cargo-deny' 'advisory_data_snapshot_tree')) { Stop-SupplyChain 'advisory tree drift' }
    $taskTreeText = (Invoke-SupplyChain 'git' @('-C', $Path, 'ls-tree', '-r', '--full-tree', 'HEAD')) -join "`n"
    $taskTreeText += "`n"
    $taskHasher = [Security.Cryptography.SHA256]::Create()
    try { $taskTreeHash = [BitConverter]::ToString($taskHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($taskTreeText))).Replace('-', '').ToLowerInvariant() }
    finally { $taskHasher.Dispose() }
    if ($taskTreeHash -cne (Get-Admission 'cargo-deny' 'advisory_data_snapshot_sha256')) { Stop-SupplyChain 'advisory snapshot checksum drift' }
    $taskTime = [DateTimeOffset]::ParseExact((Get-Admission 'cargo-deny' 'advisory_data_snapshot_time'),
        "yyyy-MM-dd'T'HH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AssumeUniversal)
    $taskAge = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - $taskTime.ToUnixTimeSeconds()
    if ($taskAge -lt 0 -or $taskAge -gt 604800) { Stop-SupplyChain 'future or stale advisory snapshot; refresh only during explicit bootstrap' }
}

$taskOriginalLocation = Get-Location
$taskEnvironmentNames = @('CARGO_NET_OFFLINE', 'RUSTUP_AUTO_INSTALL', 'RUSTUP_TOOLCHAIN',
    'GIT_NO_REPLACE_OBJECTS', 'GIT_TERMINAL_PROMPT', 'GIT_NO_LAZY_FETCH', 'SOURCE_DATE_EPOCH', 'HELEOS_BIN', 'HELEOS_PDF_GUEST',
    'RUST_TEST_THREADS')
$taskOriginalEnvironment = @{}
foreach ($taskName in $taskEnvironmentNames) {
    $taskOriginalEnvironment[$taskName] = [Environment]::GetEnvironmentVariable($taskName, 'Process')
}
try {
    Set-Location -LiteralPath (Join-Path $PSScriptRoot '..')
    $taskRoot = (Resolve-Path -LiteralPath '.').ProviderPath
    $taskBase = 'c1596c4cc536155ad3a052cfec2c9b8951814aba'
    $env:CARGO_NET_OFFLINE = 'true'
    $env:RUSTUP_AUTO_INSTALL = '0'
    $env:RUSTUP_TOOLCHAIN = '1.96.1'
    $env:GIT_NO_REPLACE_OBJECTS = '1'
    $env:GIT_TERMINAL_PROMPT = '0'
    $env:GIT_NO_LAZY_FETCH = '1'
    # Frozen failure-path tests compare a default temp namespace before and
    # after an operation. Serialize libtest so sibling cases cannot race those
    # observations; production and native temp-parent behavior are unchanged.
    $env:RUST_TEST_THREADS = '1'
    if ([Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture.ToString() -cne 'X64' -or
        [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString() -cne 'X64') {
        Stop-SupplyChain 'requires native Windows x64'
    }
    foreach ($taskTool in @('cargo', 'rustc', 'git')) { $null = Get-Command $taskTool -CommandType Application }
    if ((Invoke-SupplyChain 'git' @('rev-parse', '--show-toplevel')).Replace('/', '\') -ine $taskRoot) {
        Stop-SupplyChain 'wrong repository root'
    }
    Invoke-SupplyChain 'git' @('cat-file', '-e', "$taskBase^{commit}")
    if ((Invoke-SupplyChain 'git' @('rev-parse', '--is-shallow-repository')) -cne 'false') {
        Stop-SupplyChain 'full-history secret scan requires complete local Git history'
    }
    if (@(Invoke-SupplyChain 'git' @('for-each-ref', '--format=%(refname)', 'refs/replace')).Count -ne 0) {
        Stop-SupplyChain 'Git replacement refs are forbidden'
    }
    $taskGrafts = Invoke-SupplyChain 'git' @('rev-parse', '--git-path', 'info/grafts')
    if (Test-Path -LiteralPath $taskGrafts) { Stop-SupplyChain 'Git grafts are forbidden for full-history scanning' }
    $taskSourcePaths = @('Cargo.toml', 'Cargo.lock', 'rust-toolchain.toml', 'crates', 'tests/verification',
        'artifacts/pdf-guest', 'scripts/verify-provenance', 'scripts/verify-foundation', 'scripts/verify-foundation.ps1')
    $taskDeltaPaths = @('Cargo.lock', 'tests/verification/Cargo.toml', 'tests/verification/src/bin/verify-provenance.rs')
    $taskNativeSuitePath = 'tests/verification/tests/native_suite_receipt.rs'
    $taskPackagingPaths = @('crates/heleos-cli/Cargo.toml', 'crates/heleos-core/Cargo.toml',
        'crates/heleos-platform-fs/Cargo.toml')
    $taskAuditPackagingPaths = @('crates/heleos-pdf-guest/Cargo.toml', 'crates/heleos-pdf-protocol/Cargo.toml',
        'crates/heleos-test-fixtures/Cargo.toml')
    $taskExclusions = @($taskDeltaPaths + $taskPackagingPaths + $taskNativeSuitePath | ForEach-Object { ":(exclude,literal)$_" })
    $taskPaths = @(Invoke-SupplyChain 'git' (@('ls-tree', '-r', '--name-only', $taskBase, '--') + $taskSourcePaths))
    if ($taskPaths.Count -eq 0) { Stop-SupplyChain 'empty governed source inventory' }
    $taskPaths += $taskNativeSuitePath
    $taskDeltaHashes = @{}
    # The original registry uses LF UTF-8; the new records may only append.
    $taskPriorGovernance = (Invoke-SupplyChain 'git' @('show', "${taskBase}:governance/tools.toml")) -join "`n"
    $taskPriorGovernance += "`n"
    if (-not [IO.File]::ReadAllText((Join-Path $taskRoot 'governance/tools.toml')).StartsWith($taskPriorGovernance, [StringComparison]::Ordinal)) {
        Stop-SupplyChain 'historical governance was changed; Task 10 admissions must be append-only'
    }
    $taskVerifierSha256 = Get-Admission 'heleos-foundation-verifier' 'source_sha256'
    $taskNativeSuiteSha256 = Get-Admission 'heleos-foundation-verifier' 'native_suite_test_sha256'
    $taskWindowsGateSha256 = Get-Admission 'heleos-foundation-verifier' 'windows_gate_sha256'
    Assert-Hash $PSCommandPath $taskWindowsGateSha256
    Assert-Hash 'tests/verification/src/bin/verify-provenance.rs' $taskVerifierSha256
    Assert-Sources
    Assert-DependencyBoundary
    foreach ($taskConfig in @('.gitleaks.toml', '.gitleaksignore', '.cargo/audit.toml', '.cargo/config', '.cargo/config.toml', '.deny.toml', '.cargo/deny.toml', 'deny.exceptions.toml')) {
        if (Test-Path -LiteralPath $taskConfig) { Stop-SupplyChain "unadmitted security-tool override: $taskConfig" }
    }
    Assert-Tool 'cargo-deny' '0.20.2' 'e528dfcbe739af7ce37a77d3d6df1b29dd6887b1c701d888820c0f16b864f737' 'cargo-deny 0.20.2' @('--version')
    Assert-Tool 'cargo-audit' '0.22.2' '700c2b240f7fd330c24b675fe429f73a5b676531fcc6300400b2b67f155ba12a' 'cargo-audit 0.22.2' @('--version')
    Assert-Tool 'cargo-cyclonedx' '0.5.9' '5d162f67705f0f5038759d73bf546a083bf30e8677c2e944b416bca48d9d69a8' 'cargo-cyclonedx-cyclonedx 0.5.9' @('cyclonedx', '--version')
    if ((Get-Admission 'gitleaks' 'version_or_digest') -cne '8.30.1' -or
        (Get-Admission 'gitleaks' 'windows_x64_archive_sha256') -cne 'd29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e') {
        Stop-SupplyChain 'gitleaks archive/version admission drift'
    }
    if ((Invoke-SupplyChain 'gitleaks' @('version')) -cne '8.30.1') { Stop-SupplyChain 'gitleaks version drift' }
    $taskPolicySha256 = Get-Admission 'cargo-deny' 'policy_sha256'
    Assert-Hash 'deny.toml' $taskPolicySha256
    Assert-Hash 'tests/verification/src/bin/verify-provenance.rs' $taskVerifierSha256
    $taskSbomSha256 = Get-Admission 'cargo-cyclonedx' 'generated_sbom_sha256'
    Assert-Hash 'artifacts/sbom/heleos-foundation-0.1.cdx.json' $taskSbomSha256
    $taskGovernanceSha256 = (Get-FileHash -LiteralPath 'governance/tools.toml' -Algorithm SHA256).Hash.ToLowerInvariant()
    $taskCargoHome = if ($env:CARGO_HOME) { $env:CARGO_HOME } else { Join-Path ([Environment]::GetFolderPath('UserProfile')) '.cargo' }
    $taskVolumes = @(Get-Volume -FilePath (Join-Path $taskRoot 'Cargo.lock'))
    if ($taskVolumes.Count -ne 1 -or $taskVolumes[0].FileSystemType.ToString() -cne 'NTFS') {
        Stop-SupplyChain 'native workspace is not proven to be on NTFS'
    }
    $taskEvidence = Join-Path $taskRoot ('target/supply-chain.' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $taskEvidence
    foreach ($taskField in @('advisory_data_origin', 'advisory_data_snapshot_commit', 'advisory_data_snapshot_tree', 'advisory_data_snapshot_sha256', 'advisory_data_snapshot_time')) {
        if ((Get-Admission 'cargo-deny' $taskField) -cne (Get-Admission 'cargo-audit' $taskField)) {
            Stop-SupplyChain 'cargo-deny and cargo-audit snapshot admissions differ'
        }
    }
    Assert-Advisory (Join-Path $taskCargoHome 'advisory-db')
    $taskDenyEntries = @(Get-ChildItem -LiteralPath (Join-Path $taskCargoHome 'advisory-dbs') -Force)
    $taskDenyDbs = @($taskDenyEntries | Where-Object { $_.PSIsContainer })
    foreach ($taskEntry in $taskDenyEntries) {
        if (($taskEntry.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            (-not $taskEntry.PSIsContainer -and $taskEntry.Name -cne 'db.lock')) {
            Stop-SupplyChain 'unexpected entry in cargo-deny advisory database root'
        }
    }
    if ($taskDenyDbs.Count -ne 1) { Stop-SupplyChain 'expected exactly one cargo-deny advisory database' }
    Assert-Advisory $taskDenyDbs[0].FullName

    # Only the verifier package may build before Task 9's shared guest launcher.
    Invoke-SupplyChain 'cargo' @('+1.96.1', 'build', '--frozen', '-p', 'heleos-verification', '--bin', 'verify-provenance')
    $taskVerifier = Join-Path $taskRoot 'target/debug/verify-provenance.exe'
    Invoke-SupplyChain $taskVerifier @('verify-sbom', 'artifacts/sbom/heleos-foundation-0.1.cdx.json')
    $taskBuildReportText = Invoke-SupplyChain $taskVerifier @('build-pdf-guest')
    $taskBuildReport = $taskBuildReportText | ConvertFrom-Json -AsHashtable
    if ($taskBuildReport['status'] -cne 'pass' -or $taskBuildReport['builds_compared'] -cne '2') {
        Stop-SupplyChain 'Task 9 did not attest two successful native guest builds'
    }
    $taskBuildReportText | Set-Content -LiteralPath (Join-Path $taskEvidence 'guest-build.json') -Encoding utf8NoBOM
    Invoke-SupplyChain $taskVerifier @()
    Invoke-SupplyChain 'cargo' @('+1.96.1', 'metadata', '--frozen', '--all-features', '--format-version', '1') |
        Set-Content -LiteralPath (Join-Path $taskEvidence 'metadata.json') -Encoding utf8NoBOM
    Invoke-DenyAudit
    Invoke-SupplyChain 'cargo' @('+1.96.1', 'audit', '--no-fetch', '--db', (Join-Path $taskCargoHome 'advisory-db'), '--deny', 'warnings')
    # Scan exactly Git source inputs, excluding ignored target/cache copies.
    $taskSecretEvidence = Join-Path ([IO.Path]::GetTempPath()) ('heleos-supply-chain.' + [guid]::NewGuid().ToString('N'))
    $taskSecretSnapshot = Join-Path $taskSecretEvidence 'source'
    $null = New-Item -ItemType Directory -Path $taskSecretSnapshot
    $taskScanPaths = @(Invoke-SupplyChain 'git' @('ls-files', '--cached', '--others', '--exclude-standard'))
    if ($taskScanPaths.Count -eq 0) { Stop-SupplyChain 'empty secret-scan source inventory' }
    foreach ($taskPath in $taskScanPaths) {
        if ($taskPath -cnotmatch '^[A-Za-z0-9_./-]+$') { Stop-SupplyChain 'unsupported secret-scan source input' }
        $taskSource = Join-Path $taskRoot $taskPath
        $taskItem = Get-Item -LiteralPath $taskSource -Force
        if ($taskItem -is [IO.DirectoryInfo]) { Stop-SupplyChain 'unsupported secret-scan source input' }
        while ($null -ne $taskItem -and $taskItem.FullName -ine $taskRoot) {
            if ($taskItem.Attributes -band [IO.FileAttributes]::ReparsePoint) { Stop-SupplyChain 'indirect secret-scan source input' }
            $taskItem = if ($taskItem -is [IO.DirectoryInfo]) { $taskItem.Parent } else { $taskItem.Directory }
        }
        $taskDestination = Join-Path $taskSecretSnapshot $taskPath
        $null = [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($taskDestination))
        [IO.File]::Copy($taskSource, $taskDestination, $false)
        if ((Get-FileHash -LiteralPath $taskSource -Algorithm SHA256).Hash -cne
            (Get-FileHash -LiteralPath $taskDestination -Algorithm SHA256).Hash) { Stop-SupplyChain 'secret-scan source copy drift' }
    }
    $taskHistoryReport = Join-Path $taskSecretEvidence 'history.json'
    $taskCurrentReport = Join-Path $taskSecretEvidence 'current.json'
    & gitleaks git --redact=100 --ignore-gitleaks-allow --log-opts=--all --exit-code 0 --report-format json --report-path $taskHistoryReport . `
        *> (Join-Path $taskSecretEvidence 'history.log')
    if ($LASTEXITCODE -ne 0) { Stop-SupplyChain 'history secret scan failed; redacted diagnostics retained in OS-temp evidence' }
    & gitleaks dir --redact=100 --ignore-gitleaks-allow --exit-code 0 --report-format json --report-path $taskCurrentReport $taskSecretSnapshot `
        *> (Join-Path $taskSecretEvidence 'current.log')
    if ($LASTEXITCODE -ne 0) { Stop-SupplyChain 'current-source secret scan failed; redacted diagnostics retained in OS-temp evidence' }
    # Exit-code 0 only collects reports. Semantic exact-finding adjudication is
    # mandatory; this is not an ignore or suppression of new findings.
    Invoke-SupplyChain $taskVerifier @('verify-secret-scan', $taskHistoryReport, $taskCurrentReport)
    $env:HELEOS_BIN = Join-Path $taskRoot 'target/debug/heleos.exe'
    $env:HELEOS_PDF_GUEST = Join-Path $taskRoot 'target/wasm32-wasip1/release/heleos_pdf_guest.wasm'
    Invoke-SupplyChain 'cargo' @('+1.96.1', 'clippy', '--frozen', '--workspace', '--all-targets', '--all-features', '--', '-D', 'warnings')
    Invoke-SupplyChain 'cargo' @('+1.96.1', 'build', '--frozen', '-p', 'heleos-cli', '--bin', 'heleos')
    # The helper-library suite includes the frozen five-symbol metadata/source
    # contract and one local unsafe publication-call audit, as on macOS.
    Assert-CleanCandidate
    $taskNativeCandidate = Invoke-SupplyChain 'git' @('rev-parse', 'HEAD')
    if ($taskNativeCandidate -cnotmatch '^[a-f0-9]{40}$') { Stop-SupplyChain 'invalid native-suite candidate HEAD' }
    $taskNativeCases = @(
        @{ Id = 'core-backup-restore'; Selectors = @('-p', 'heleos-core', '--test', 'backup_restore') },
        @{ Id = 'core-store'; Selectors = @('-p', 'heleos-core', '--lib', 'store::tests') },
        @{ Id = 'core-backup'; Selectors = @('-p', 'heleos-core', '--lib', 'backup::tests') },
        @{ Id = 'platform-fs'; Selectors = @('-p', 'heleos-platform-fs', '--lib') },
        @{ Id = 'cli-unit'; Selectors = @('-p', 'heleos-cli', '--bin', 'heleos') },
        @{ Id = 'cli-integration'; Selectors = @('-p', 'heleos-cli', '--test', 'cli') },
        @{ Id = 'workspace-all'; Selectors = @('--workspace', '--all-targets', '--all-features') }
    )
    $taskNativeSuites = @(
        foreach ($taskCase in $taskNativeCases) {
            $taskRunArgv = @('cargo', '+1.96.1', 'test', '--frozen') + $taskCase.Selectors
            [ordered]@{
                id = $taskCase.Id
                run_argv = $taskRunArgv
                list_argv = $taskRunArgv + @('--', '--list')
                list_exit_code = $null
                run_exit_code = $null
                list_path = $taskCase.Id + '.list.txt'
                run_path = $taskCase.Id + '.run.txt'
            }
        }
    )
    $taskNativeManifest = [ordered]@{
        schema = 'heleos.native-suite-transcripts/v1'
        candidate_sha = $taskNativeCandidate
        platform = 'windows-x86_64'
        filesystem = 'NTFS'
        suites = $taskNativeSuites
    }
    $taskNativeManifestPath = Join-Path $taskEvidence 'native-suite-manifest.json'
    Save-NativeManifest
    Write-Output ('Native suite evidence retained under ' + $taskEvidence)
    foreach ($taskSuite in $taskNativeSuites) {
        $taskSuite.list_exit_code = Invoke-NativeTranscript $taskSuite.list_argv (Join-Path $taskEvidence $taskSuite.list_path)
        Save-NativeManifest
        if ($taskSuite.list_exit_code -ne 0) { Stop-SupplyChain ("native suite listing failed: " + $taskSuite.id + " (exit " + $taskSuite.list_exit_code + ")") }
        $taskSuite.run_exit_code = Invoke-NativeTranscript $taskSuite.run_argv (Join-Path $taskEvidence $taskSuite.run_path)
        Save-NativeManifest
        if ($taskSuite.run_exit_code -ne 0) { Stop-SupplyChain ("native suite execution failed: " + $taskSuite.id + " (exit " + $taskSuite.run_exit_code + ")") }
    }
    Assert-CleanCandidate
    if ((Invoke-SupplyChain 'git' @('rev-parse', 'HEAD')) -cne $taskNativeCandidate) {
        Stop-SupplyChain 'candidate HEAD changed during native suites'
    }
    $taskNativeReceiptText = @(Invoke-SupplyChain $taskVerifier @('verify-native-suite', $taskNativeManifestPath))
    if ($taskNativeReceiptText.Count -ne 1) { Stop-SupplyChain 'native-suite verifier must emit one canonical JSON receipt' }
    $taskNativeReceipt = $taskNativeReceiptText[0] | ConvertFrom-Json -AsHashtable
    if ($taskNativeReceipt['status'] -cne 'pass' -or
        $taskNativeReceipt['candidate_sha'] -cne $taskNativeCandidate -or
        $taskNativeReceipt['platform'] -cne 'windows-x86_64' -or
        $taskNativeReceipt['filesystem'] -cne 'NTFS' -or
        $taskNativeReceipt['suite_count'] -ne 7) {
        Stop-SupplyChain 'native-suite verifier receipt does not attest this candidate and all seven native suites'
    }
    # Preserve the verifier's canonical JSON text; do not reserialize it through
    # PowerShell (which can change JSON key ordering or encoding).
    Assert-CleanCandidate
    if ((Invoke-SupplyChain 'git' @('rev-parse', 'HEAD')) -cne $taskNativeCandidate) {
        Stop-SupplyChain 'candidate HEAD changed before native-suite receipt issuance'
    }
    [IO.File]::WriteAllText((Join-Path $taskEvidence 'native-suite-receipt.json'), $taskNativeReceiptText[0] + "`n", [Text.UTF8Encoding]::new($false))
    Write-Output $taskNativeReceiptText[0]

    # Rust owns full-graph generation, the fixed epoch, two-root comparison and
    # semantic validation; raw cargo-cyclonedx member output is not an aggregate.
    $taskGeneratedSbom = Join-Path $taskEvidence 'heleos-foundation-0.1.cdx.json'
    Invoke-SupplyChain $taskVerifier @('build-sbom', $taskGeneratedSbom)
    Invoke-SupplyChain $taskVerifier @('verify-sbom', $taskGeneratedSbom)
    Assert-Hash $taskGeneratedSbom $taskSbomSha256
    Assert-Hash 'artifacts/sbom/heleos-foundation-0.1.cdx.json' $taskSbomSha256
    Invoke-SupplyChain $taskVerifier @('verify-sbom', 'artifacts/sbom/heleos-foundation-0.1.cdx.json')
    Invoke-SupplyChain $taskVerifier @()
    Assert-Sources
    Assert-Hash 'deny.toml' $taskPolicySha256
    Assert-Hash 'tests/verification/src/bin/verify-provenance.rs' $taskVerifierSha256
    Assert-Hash 'governance/tools.toml' $taskGovernanceSha256
    Assert-Advisory (Join-Path $taskCargoHome 'advisory-db')
    Assert-Advisory $taskDenyDbs[0].FullName
    Write-Output ('Candidate SBOM evidence retained under ' + [IO.Path]::GetRelativePath($taskRoot, $taskEvidence))
    Write-Output ('Redacted secret-scan evidence retained under ' + $taskSecretEvidence)
    Write-Output 'SUPPLY_CHAIN_LOCAL_PASS: native Windows x64/NTFS offline gate and all native test commands passed.'
    Write-Output 'DEFERRED_PLATFORM_GATE: cap-primitives Windows build-probe/API source attestation and Windows no-egress evidence require separately captured native evidence.'
    Write-Output 'DEFERRED_RELEASE_GATE: same-candidate macOS/Windows CI, GitHub App owner dispositions, and release attestation remain separate gates; this result is a local candidate only.'
}
finally {
    foreach ($taskName in $taskEnvironmentNames) {
        [Environment]::SetEnvironmentVariable($taskName, $taskOriginalEnvironment[$taskName], 'Process')
    }
    Set-Location -LiteralPath $taskOriginalLocation.Path
}
