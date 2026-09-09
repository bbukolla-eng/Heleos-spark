#Requires -Version 7.0
<#
Import a separately pinned Foundation bundle into a NEW native Windows checkout.
ExpectedManifestSha256 and ExpectedCommit must come from the trusted controller.
All command output is emitted as JSON events and retained in the final summary;
gate output is also written under .git. Partial destinations are never cleaned.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $CandidateDirectory,
    [Parameter(Mandatory)][string] $Destination,
    [Parameter(Mandatory)][string] $ExpectedManifestSha256,
    [Parameter(Mandatory)][string] $ExpectedCommit
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$taskExit = 1
$taskCode = 'validation_failed'
$taskHandles = [Collections.Generic.List[IDisposable]]::new()
$taskEnvironment = @{}
$taskEvents = [Collections.Generic.List[object]]::new()
$taskSummary = [ordered]@{
    schema = 'heleos.foundation-windows-native-import/v1'; status = 'FAIL'
    mode = 'native_suites'; native_host = [bool] $IsWindows; native_evidence = $false
    commit = $null; destination = $null; destination_created = $false
    manifest_sha256 = $null; bundle_sha256 = $null; gate_summary = $null
    gate_exit_code = $null; gate_log = $null; error_code = $null; error = $null
    events = $taskEvents
}

function ConvertFrom-StrictObject {
    param([string] $Json)
    $taskDocument = [Text.Json.JsonDocument]::Parse($Json)
    try {
        if ($taskDocument.RootElement.ValueKind -ne [Text.Json.JsonValueKind]::Object) { throw 'Expected a JSON object.' }
        $taskFields = [Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
        foreach ($taskProperty in $taskDocument.RootElement.EnumerateObject()) {
            if ($taskFields.ContainsKey($taskProperty.Name)) { throw 'Duplicate JSON property.' }
            $taskFields.Add($taskProperty.Name, $taskProperty.Value.Clone())
        }
        return ,$taskFields
    } finally { $taskDocument.Dispose() }
}

function ConvertFrom-FoundationManifest {
    param([byte[]] $Bytes, [string] $ExpectedHash, [string] $Commit)
    if ($ExpectedHash -cnotmatch '^[0-9a-f]{64}$' -or $Commit -cnotmatch '^[0-9a-f]{40}$') { throw 'Expected exact lowercase SHA-256 and Git commit pins.' }
    if ($Bytes.Length -gt 65536) { throw 'Manifest exceeds 64 KiB.' }
    $taskHasher = [Security.Cryptography.SHA256]::Create()
    try { $taskHash = ([BitConverter]::ToString($taskHasher.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant() }
    finally { $taskHasher.Dispose() }
    if ($taskHash -cne $ExpectedHash) { throw 'Manifest SHA-256 mismatch.' }
    $taskFields = ConvertFrom-StrictObject ([Text.UTF8Encoding]::new($false, $true).GetString($Bytes))
    $taskExpected = @('schema', 'branch', 'commit', 'bundle', 'bundle_sha256', 'bundle_bytes', 'native_gate', 'required_filesystem', 'native_gate_status')
    if ($taskFields.Count -ne $taskExpected.Count) { throw 'Manifest property set mismatch.' }
    $taskResult = @{}
    foreach ($taskName in $taskExpected) {
        if (-not $taskFields.ContainsKey($taskName)) { throw 'Missing exact manifest property.' }
        if ($taskName -ceq 'bundle_bytes') {
            $taskLength = [long] 0
            if ($taskFields[$taskName].ValueKind -ne [Text.Json.JsonValueKind]::Number -or -not $taskFields[$taskName].TryGetInt64([ref] $taskLength) -or $taskLength -le 0) { throw 'Bundle byte length must be a positive integer.' }
            $taskResult[$taskName] = $taskLength
        } else {
            if ($taskFields[$taskName].ValueKind -ne [Text.Json.JsonValueKind]::String) { throw 'Manifest field must be a string.' }
            $taskResult[$taskName] = $taskFields[$taskName].GetString()
        }
    }
    if ($taskResult.schema -cne 'heleos.foundation-windows-native-candidate/v1' -or
        $taskResult.branch -cne "release/foundation-0.1-native-$Commit" -or
        $taskResult.commit -cne $Commit -or
        $taskResult.bundle -cne "heleos-spark-$Commit.bundle" -or
        $taskResult.bundle_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        $taskResult.native_gate -cne 'scripts/verify-supply-chain.ps1' -or
        $taskResult.required_filesystem -cne 'NTFS' -or
        $taskResult.native_gate_status -cne 'pending') { throw 'Foundation manifest policy or identity mismatch.' }
    return $taskResult
}

function Assert-FoundationHeads {
    param([string[]] $Lines, [string] $Commit)
    if ($Commit -cnotmatch '^[0-9a-f]{40}$' -or $Lines.Count -ne 1 -or $Lines[0] -cne "$Commit refs/heads/release/foundation-0.1-native-$Commit") { throw 'Bundle must advertise exactly the pinned Foundation commit and branch.' }
}

function Get-FoundationReceipt {
    param([string[]] $Lines, [int] $ExitCode, [string] $Commit)
    if ($ExitCode -ne 0 -or $Commit -cnotmatch '^[0-9a-f]{40}$') { throw 'Native gate failed or candidate pin is invalid.' }
    $taskPass = 'SUPPLY_CHAIN_LOCAL_PASS: native Windows x64/NTFS offline gate and all native test commands passed.'
    $taskPassCount = 0; $taskReceiptCount = 0; $taskReceipt = $null
    foreach ($taskLine in $Lines) {
        if ($taskLine -cmatch 'SUPPLY_CHAIN_FAIL(?:URE)?:') { throw 'Native gate reported failure.' }
        if ($taskLine.Contains('SUPPLY_CHAIN_LOCAL_PASS:', [StringComparison]::Ordinal)) {
            if ($taskLine -cne $taskPass) { throw 'Malformed native PASS line.' }
            $taskPassCount++
        }
        # The gate emits mixed diagnostics and JSON. Parse every JSON-looking
        # line strictly; identify receipts by the exact schema after parsing.
        if ($taskLine.TrimStart().StartsWith('{', [StringComparison]::Ordinal)) {
            $taskFields = ConvertFrom-StrictObject $taskLine
            $taskReceiptLike = @($taskFields.Values | Where-Object {
                $_.ValueKind -eq [Text.Json.JsonValueKind]::String -and
                $_.GetString().StartsWith('heleos.native-suite-receipt/', [StringComparison]::OrdinalIgnoreCase)
            }).Count -gt 0
            if (-not $taskFields.ContainsKey('schema') -or $taskFields['schema'].ValueKind -ne [Text.Json.JsonValueKind]::String -or
                $taskFields['schema'].GetString() -cne 'heleos.native-suite-receipt/v1') {
                if ($taskReceiptLike) { throw 'Native receipt schema mismatch.' }
                continue
            }
            $taskReceiptCount++
            $taskExpected = @{
                schema = 'heleos.native-suite-receipt/v1'; status = 'pass'; candidate_sha = $Commit
                platform = 'windows-x86_64'; filesystem = 'NTFS'
            }
            foreach ($taskName in $taskExpected.Keys) {
                if (-not $taskFields.ContainsKey($taskName) -or $taskFields[$taskName].ValueKind -ne [Text.Json.JsonValueKind]::String -or
                    $taskFields[$taskName].GetString() -cne $taskExpected[$taskName]) { throw 'Native receipt identity or platform mismatch.' }
            }
            $taskSuites = [long] 0
            if (-not $taskFields.ContainsKey('suite_count') -or $taskFields['suite_count'].ValueKind -ne [Text.Json.JsonValueKind]::Number -or
                -not $taskFields['suite_count'].TryGetInt64([ref] $taskSuites) -or $taskSuites -ne 7) { throw 'Native receipt must attest integer suite_count=7.' }
            $taskReceipt = $taskLine | ConvertFrom-Json -AsHashtable
        } elseif ($taskLine.Contains('heleos.native-suite-receipt/', [StringComparison]::Ordinal)) {
            throw 'Malformed native receipt line.'
        }
    }
    if ($taskReceiptCount -ne 1 -or $taskPassCount -ne 1) { throw 'Expected exactly one native receipt and one exact PASS line.' }
    return $taskReceipt
}

function Assert-FoundationPathSyntax {
    param([string] $Path)
    if ($Path -cnotmatch '^[A-Za-z]:\\' -or $Path.Substring(2).Contains(':') -or $Path.Contains('/')) { throw 'Use absolute local drive paths; UNC, device, alternate stream, and relative paths are forbidden.' }
    foreach ($taskPart in $Path.Substring(3).Split('\')) {
        if (-not $taskPart -or $taskPart -match '[. ]$|[<>"|?*\x00-\x1f]' -or $taskPart -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)') { throw 'Unsafe Windows path component.' }
    }
}

function Assert-FoundationVolume {
    param([string] $DriveType, [string] $Filesystem)
    if ($DriveType -cne 'Fixed' -or $Filesystem -cne 'NTFS') { throw 'Candidate and destination must use local fixed NTFS volumes.' }
}

function Assert-RealPath {
    param([string] $Path, [switch] $Directory)
    $taskItem = Get-Item -LiteralPath $Path -Force
    if ($taskItem.PSProvider.Name -cne 'FileSystem' -or $taskItem.PSIsContainer -ne [bool] $Directory) { throw 'Expected a regular filesystem path of the required type.' }
    $taskAncestor = $taskItem
    while ($null -ne $taskAncestor) {
        if ($taskAncestor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse points are forbidden in candidate and destination paths.' }
        $taskAncestor = if ($taskAncestor -is [IO.DirectoryInfo]) { $taskAncestor.Parent } else { $taskAncestor.Directory }
    }
    return $taskItem
}

function Write-ImportEvent {
    param([object] $Event)
    $taskEvents.Add($Event)
    Write-Host ($Event | ConvertTo-Json -Compress -Depth 5)
}

function Invoke-ImportGit {
    param([string[]] $Arguments)
    $taskLines = @(& $taskGit -c core.hooksPath=NUL -c core.autocrlf=false -c protocol.allow=never -c protocol.file.allow=always @Arguments 2>&1 | ForEach-Object { $_.ToString() })
    $taskResult = $LASTEXITCODE
    Write-ImportEvent ([ordered]@{ event = 'git'; arguments = $Arguments; exit_code = $taskResult; output = $taskLines })
    if ($taskResult -ne 0) { throw "Git command failed with exit code $taskResult." }
    return $taskLines
}

function Assert-ImportedHead {
    $taskHead = @(Invoke-ImportGit @('-C', $taskDestination, 'rev-parse', '--verify', 'HEAD^{commit}'))
    if ($taskHead.Count -ne 1 -or $taskHead[0] -cne $ExpectedCommit) { throw 'Imported HEAD differs from the pinned commit.' }
    $taskBranch = @(Invoke-ImportGit @('-C', $taskDestination, 'symbolic-ref', 'HEAD'))
    if ($taskBranch.Count -ne 1 -or $taskBranch[0] -cne "refs/heads/release/foundation-0.1-native-$ExpectedCommit") { throw 'Imported branch differs from the pinned ref.' }
    if (@(Invoke-ImportGit @('-C', $taskDestination, 'status', '--porcelain=v1', '--untracked-files=all', '--ignore-submodules=none')).Count) { throw 'Imported checkout is dirty.' }
}

try {
    # This must precede even input/path probes and all external commands.
    if (-not $IsWindows) { $taskCode = 'non_windows'; throw 'Import requires native Windows.' }
    if ([Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne [Runtime.InteropServices.Architecture]::X64 -or
        [Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture -ne [Runtime.InteropServices.Architecture]::X64) { throw 'Use native Windows x64 PowerShell.' }
    if (@(Get-ChildItem Env: | Where-Object { $_.Name -like 'GIT_*' }).Count) { throw 'Unset inherited GIT_* overrides before importing.' }
    Assert-FoundationPathSyntax $CandidateDirectory
    Assert-FoundationPathSyntax $Destination
    $taskCandidate = (Assert-RealPath $CandidateDirectory -Directory).FullName
    $taskDestination = [IO.Path]::GetFullPath($Destination)
    $taskSummary.destination = $taskDestination
    if (Test-Path -LiteralPath $taskDestination) { throw 'Destination already exists; choose a new path.' }
    $taskParent = (Assert-RealPath ([IO.Path]::GetDirectoryName($taskDestination)) -Directory).FullName
    foreach ($taskDirectory in @($taskCandidate, $taskParent)) {
        $taskDrive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($taskDirectory))
        $taskVolumes = @(Get-Volume -FilePath $taskDirectory -ErrorAction Stop)
        if ($taskVolumes.Count -ne 1) { throw 'Expected exactly one filesystem volume.' }
        Assert-FoundationVolume $taskDrive.DriveType.ToString() $taskVolumes[0].FileSystemType.ToString()
    }
    $taskManifestFile = Assert-RealPath (Join-Path $taskCandidate 'candidate.json')
    $taskManifestStream = [IO.File]::Open($taskManifestFile.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $taskHandles.Add($taskManifestStream)
    if ($taskManifestStream.Length -gt 65536) { throw 'Manifest exceeds 64 KiB.' }
    $taskMemory = [IO.MemoryStream]::new()
    try { $taskManifestStream.CopyTo($taskMemory); $taskManifest = ConvertFrom-FoundationManifest $taskMemory.ToArray() $ExpectedManifestSha256 $ExpectedCommit }
    finally { $taskMemory.Dispose() }
    $taskSummary.commit = $taskManifest.commit
    $taskSummary.manifest_sha256 = $ExpectedManifestSha256
    $taskBundleFile = Assert-RealPath (Join-Path $taskCandidate $taskManifest.bundle)
    # Deny writes/deletion to the exact validated manifest and bundle through import.
    $taskBundleStream = [IO.File]::Open($taskBundleFile.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $taskHandles.Add($taskBundleStream)
    if ($taskBundleStream.Length -ne $taskManifest.bundle_bytes) { throw 'Bundle byte length mismatch.' }
    $taskBundleHash = (Get-FileHash -InputStream $taskBundleStream -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($taskBundleHash -cne $taskManifest.bundle_sha256) { throw 'Bundle SHA-256 mismatch.' }
    $taskSummary.bundle_sha256 = $taskBundleHash
    $taskGit = (Get-Command git -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    foreach ($taskName in @('GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_GLOBAL', 'GIT_TERMINAL_PROMPT')) { $taskEnvironment[$taskName] = [Environment]::GetEnvironmentVariable($taskName, 'Process') }
    $env:GIT_CONFIG_NOSYSTEM = '1'; $env:GIT_CONFIG_GLOBAL = 'NUL'; $env:GIT_TERMINAL_PROMPT = '0'
    Assert-FoundationHeads @(Invoke-ImportGit @('bundle', 'list-heads', $taskBundleFile.FullName)) $ExpectedCommit
    $null = New-Item -ItemType Directory -Path $taskDestination -ErrorAction Stop
    $taskSummary.destination_created = $true
    $taskCode = 'import_failed'
    $null = Assert-RealPath $taskDestination -Directory
    Invoke-ImportGit @('clone', '--no-checkout', '--no-local', '--template=', '--single-branch', '--branch', $taskManifest.branch, '--', $taskBundleFile.FullName, $taskDestination) | Out-Null
    Invoke-ImportGit @('-C', $taskDestination, 'bundle', 'verify', $taskBundleFile.FullName) | Out-Null
    # Persist hook/protocol policy for Git subprocesses invoked by the gate.
    foreach ($taskPair in @(@('core.hooksPath', 'NUL'), @('core.autocrlf', 'false'), @('protocol.allow', 'never'), @('protocol.file.allow', 'never'))) {
        Invoke-ImportGit @('-C', $taskDestination, 'config', '--local', $taskPair[0], $taskPair[1]) | Out-Null
    }
    Invoke-ImportGit @('-C', $taskDestination, 'checkout', $taskManifest.branch) | Out-Null
    Assert-ImportedHead
    $taskGate = Assert-RealPath (Join-Path $taskDestination $taskManifest.native_gate)
    $taskGateBlob = @(Invoke-ImportGit @('-C', $taskDestination, 'rev-parse', '--verify', "HEAD:$($taskManifest.native_gate)"))
    $taskGateActual = @(Invoke-ImportGit @('-C', $taskDestination, 'hash-object', '--no-filters', $taskGate.FullName))
    if ($taskGateBlob.Count -ne 1 -or $taskGateActual.Count -ne 1 -or $taskGateBlob[0] -cne $taskGateActual[0]) { throw 'Gate bytes differ from the committed Foundation gate.' }
    $taskCode = 'native_gate_failed'
    $taskGitDirectory = (Assert-RealPath (Join-Path $taskDestination '.git') -Directory).FullName
    $taskLogPath = Join-Path $taskGitDirectory 'foundation-native-import.log'
    $taskSummary.gate_log = $taskLogPath
    $taskLog = [IO.StreamWriter]::new([IO.File]::Open($taskLogPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write), [Text.UTF8Encoding]::new($false))
    $taskLog.AutoFlush = $true
    $taskHandles.Add($taskLog)
    $taskGateLines = [Collections.Generic.List[string]]::new()
    & (Join-Path $PSHOME 'pwsh.exe') -NoLogo -NoProfile -File $taskGate.FullName 2>&1 | ForEach-Object {
        $taskLine = $_.ToString()
        $taskGateLines.Add($taskLine)
        $taskLog.WriteLine($taskLine)
        Write-ImportEvent ([ordered]@{ event = 'native_gate_output'; line = $taskLine })
    }
    $taskGateExit = $LASTEXITCODE
    $taskSummary.gate_exit_code = $taskGateExit
    $taskSummary.gate_summary = Get-FoundationReceipt $taskGateLines.ToArray() $taskGateExit $ExpectedCommit
    Assert-ImportedHead
    $taskSummary.native_evidence = $true
    $taskSummary.status = 'PASS'
    $taskExit = 0
} catch {
    $taskSummary.error_code = $taskCode
    $taskSummary.error = $_.Exception.Message
} finally {
    foreach ($taskHandle in $taskHandles) { $taskHandle.Dispose() }
    foreach ($taskName in $taskEnvironment.Keys) { [Environment]::SetEnvironmentVariable($taskName, $taskEnvironment[$taskName], 'Process') }
    Write-Output ($taskSummary | ConvertTo-Json -Compress -Depth 12)
}
exit $taskExit
