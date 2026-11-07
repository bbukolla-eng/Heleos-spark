#Requires -Version 7.0
<#
Import an independently pinned, local candidate into a NEW local NTFS checkout.
Default: invoke the native gate's preflight. -Full runs its complete native gate.
Every stdout line is JSON. Failed/partial destinations are retained for diagnosis;
this script never overwrites, deletes, installs dependencies, or contacts a remote.
The manifest digest and commit must come from the controller's trusted handoff,
not be calculated from an untrusted candidate and accepted without comparison.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $CandidateDirectory,
    [Parameter(Mandatory)][string] $Destination,
    [Parameter(Mandatory)][string] $ExpectedManifestSha256,
    [Parameter(Mandatory)][string] $ExpectedCommit,
    [switch] $Full
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$taskExit = 1
$taskCode = 'validation_failed'
$taskHandles = [Collections.Generic.List[IDisposable]]::new()
$taskEnvironment = @{}
$taskSummary = [ordered]@{
    schema = 'heleos.windows-native-import/v1'; status = 'FAIL'
    mode = $(if ($Full) { 'native_suites' } else { 'preflight' })
    native_host = [bool] $IsWindows; native_evidence = $false
    commit = $null; destination = $null; destination_created = $false
    manifest_sha256 = $null; bundle_sha256 = $null; gate_summary = $null
    error_code = $null; error = $null
}

function ConvertFrom-CandidateManifest {
    param([byte[]] $Bytes, [string] $ExpectedHash, [string] $Commit)
    if ($ExpectedHash -cnotmatch '^[0-9a-f]{64}$' -or $Commit -cnotmatch '^[0-9a-f]{40}$') { throw 'Expected exact lowercase SHA-256 and Git commit pins.' }
    if ($Bytes.Length -gt 65536) { throw 'Manifest exceeds 64 KiB.' }
    $taskHasher = [Security.Cryptography.SHA256]::Create()
    try { $taskHash = ([BitConverter]::ToString($taskHasher.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant() }
    finally { $taskHasher.Dispose() }
    if ($taskHash -cne $ExpectedHash) { throw 'Manifest SHA-256 mismatch.' }
    $taskDecoder = [Text.UTF8Encoding]::new($false, $true)
    $taskDocument = [Text.Json.JsonDocument]::Parse($taskDecoder.GetString($Bytes))
    try {
        if ($taskDocument.RootElement.ValueKind -ne [Text.Json.JsonValueKind]::Object) { throw 'Manifest must be a JSON object.' }
        $taskFields = [Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
        foreach ($taskProperty in $taskDocument.RootElement.EnumerateObject()) {
            if ($taskFields.ContainsKey($taskProperty.Name)) { throw 'Duplicate manifest property.' }
            $taskFields.Add($taskProperty.Name, $taskProperty.Value.Clone())
        }
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
        if ($taskResult.schema -cne 'heleos.windows-native-candidate/v1' -or
            $taskResult.branch -cne 'build/agent-control-foundation' -or
            $taskResult.commit -cne $Commit -or
            $taskResult.bundle -cne "heleos-spark-$Commit.bundle" -or
            $taskResult.bundle_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            $taskResult.native_gate -cne 'scripts/verify-windows-worker-containment.ps1' -or
            $taskResult.required_filesystem -cne 'NTFS' -or
            $taskResult.native_gate_status -cne 'pending') { throw 'Candidate manifest policy or identity mismatch.' }
        return $taskResult
    } finally { $taskDocument.Dispose() }
}

function Assert-CandidateHeads {
    param([string[]] $Lines, [string] $Commit)
    if ($Lines.Count -ne 1 -or $Lines[0] -cne "$Commit refs/heads/build/agent-control-foundation") { throw 'Bundle must advertise exactly the pinned commit and branch, with no other refs.' }
}

function Assert-CandidateGate {
    param([object] $Summary, [int] $ExitCode, [string] $Commit, [bool] $FullRun)
    $taskMode = if ($FullRun) { 'native_suites' } else { 'preflight' }
    if ($ExitCode -ne 0 -or $Summary.status -cne 'PASS' -or $Summary.git_sha -cne $Commit -or
        $Summary.native_host -isnot [bool] -or $Summary.native_host -ne $true -or
        $Summary.filesystem -cne 'NTFS' -or $Summary.mode -cne $taskMode -or
        $Summary.native_evidence -isnot [bool] -or $Summary.native_evidence -ne $FullRun) { throw 'Native gate failed or returned inconsistent evidence.' }
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

function Invoke-ImportGit {
    param([string[]] $Arguments)
    $taskLines = @(& $taskGit -c core.hooksPath=NUL -c core.autocrlf=false -c protocol.allow=never -c protocol.file.allow=always @Arguments 2>&1 | ForEach-Object { $_.ToString() })
    $taskResult = $LASTEXITCODE
    Write-Host (([ordered]@{ event = 'git'; arguments = $Arguments; exit_code = $taskResult; output = $taskLines } | ConvertTo-Json -Compress -Depth 4))
    if ($taskResult -ne 0) { throw "Git command failed with exit code $taskResult." }
    return $taskLines
}

try {
    # Must precede even input/path probes and all external commands.
    if (-not $IsWindows) { $taskCode = 'non_windows'; throw 'Import requires native Windows.' }
    if ([Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne [Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture) { throw 'Use PowerShell in the native OS architecture.' }
    if (@(Get-ChildItem Env: | Where-Object { $_.Name -like 'GIT_*' }).Count) { throw 'Unset inherited GIT_* overrides before importing.' }
    foreach ($taskPath in @($CandidateDirectory, $Destination)) {
        if ($taskPath -cnotmatch '^[A-Za-z]:\\' -or $taskPath.Substring(2).Contains(':') -or $taskPath.Contains('/')) { throw 'Use absolute local drive paths; UNC, device, alternate stream, and relative paths are forbidden.' }
        foreach ($taskPart in $taskPath.Substring(3).Split('\')) {
            if (-not $taskPart -or $taskPart -match '[. ]$|[<>"|?*\x00-\x1f]' -or $taskPart -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)') { throw 'Unsafe Windows path component.' }
        }
    }
    $taskCandidate = (Assert-RealPath $CandidateDirectory -Directory).FullName
    $taskDestination = [IO.Path]::GetFullPath($Destination)
    $taskSummary.destination = $taskDestination
    if (Test-Path -LiteralPath $taskDestination) { throw 'Destination already exists; choose a new path.' }
    $taskParent = (Assert-RealPath ([IO.Path]::GetDirectoryName($taskDestination)) -Directory).FullName
    $taskDrive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($taskParent))
    $taskVolumes = @(Get-Volume -FilePath $taskParent -ErrorAction Stop)
    if ($taskDrive.DriveType -ne [IO.DriveType]::Fixed -or $taskVolumes.Count -ne 1 -or $taskVolumes[0].FileSystemType -ine 'NTFS') { throw 'Destination parent must be on a local fixed NTFS volume.' }
    $taskManifestFile = Assert-RealPath (Join-Path $taskCandidate 'candidate.json')
    $null = Assert-RealPath (Join-Path $taskCandidate 'README.md')
    $taskManifestStream = [IO.File]::Open($taskManifestFile.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $taskHandles.Add($taskManifestStream)
    if ($taskManifestStream.Length -gt 65536) { throw 'Manifest exceeds 64 KiB.' }
    $taskMemory = [IO.MemoryStream]::new()
    try { $taskManifestStream.CopyTo($taskMemory); $taskManifest = ConvertFrom-CandidateManifest $taskMemory.ToArray() $ExpectedManifestSha256 $ExpectedCommit }
    finally { $taskMemory.Dispose() }
    $taskSummary.commit = $taskManifest.commit
    $taskSummary.manifest_sha256 = $ExpectedManifestSha256
    $taskBundleFile = Assert-RealPath (Join-Path $taskCandidate $taskManifest.bundle)
    # Keep deny-write/delete handles alive through clone and checkout.
    $taskBundleStream = [IO.File]::Open($taskBundleFile.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $taskHandles.Add($taskBundleStream)
    if ($taskBundleStream.Length -ne $taskManifest.bundle_bytes) { throw 'Bundle byte length mismatch.' }
    $taskBundleHash = (Get-FileHash -InputStream $taskBundleStream -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($taskBundleHash -cne $taskManifest.bundle_sha256) { throw 'Bundle SHA-256 mismatch.' }
    $taskSummary.bundle_sha256 = $taskBundleHash
    $taskGit = (Get-Command git -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    foreach ($taskName in @('GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_GLOBAL', 'GIT_TERMINAL_PROMPT')) { $taskEnvironment[$taskName] = [Environment]::GetEnvironmentVariable($taskName, 'Process') }
    $env:GIT_CONFIG_NOSYSTEM = '1'; $env:GIT_CONFIG_GLOBAL = 'NUL'; $env:GIT_TERMINAL_PROMPT = '0'
    Assert-CandidateHeads @(Invoke-ImportGit @('bundle', 'list-heads', $taskBundleFile.FullName)) $ExpectedCommit
    # Without -Force, creation refuses a preexisting directory, including a race.
    $null = New-Item -ItemType Directory -Path $taskDestination -ErrorAction Stop
    $taskSummary.destination_created = $true
    $taskCode = 'import_failed'
    Invoke-ImportGit @('clone', '--no-checkout', '--no-local', '--template=', '--single-branch', '--branch', $taskManifest.branch, '--', $taskBundleFile.FullName, $taskDestination) | Out-Null
    Invoke-ImportGit @('-C', $taskDestination, 'bundle', 'verify', $taskBundleFile.FullName) | Out-Null
    $taskHead = @(Invoke-ImportGit @('-C', $taskDestination, 'rev-parse', '--verify', 'HEAD^{commit}'))
    if ($taskHead.Count -ne 1 -or $taskHead[0] -cne $ExpectedCommit) { throw 'Cloned HEAD differs from the pinned commit.' }
    $taskBranch = @(Invoke-ImportGit @('-C', $taskDestination, 'symbolic-ref', 'HEAD'))
    if ($taskBranch.Count -ne 1 -or $taskBranch[0] -cne 'refs/heads/build/agent-control-foundation') { throw 'Cloned branch differs from the pinned ref.' }
    Invoke-ImportGit @('-C', $taskDestination, 'checkout', $taskManifest.branch) | Out-Null
    $taskHead = @(Invoke-ImportGit @('-C', $taskDestination, 'rev-parse', '--verify', 'HEAD^{commit}'))
    if ($taskHead.Count -ne 1 -or $taskHead[0] -cne $ExpectedCommit) { throw 'Checked-out HEAD differs from the pinned commit.' }
    $taskStatus = @(Invoke-ImportGit @('-C', $taskDestination, 'status', '--porcelain=v1', '--untracked-files=all', '--ignore-submodules=none'))
    if ($taskStatus.Count) { throw 'Imported checkout is dirty.' }
    $taskGate = Assert-RealPath (Join-Path $taskDestination $taskManifest.native_gate)
    foreach ($taskName in $taskEnvironment.Keys) { [Environment]::SetEnvironmentVariable($taskName, $taskEnvironment[$taskName], 'Process') }
    $taskCode = 'native_gate_failed'
    $taskGateArguments = @('-NoLogo', '-NoProfile', '-File', $taskGate.FullName)
    if (-not $Full) { $taskGateArguments += '-PreflightOnly' }
    $taskGateLines = @(& (Join-Path $PSHOME 'pwsh.exe') @taskGateArguments 2>&1 | ForEach-Object { $_.ToString() })
    $taskGateExit = $LASTEXITCODE
    foreach ($taskLine in $taskGateLines) { Write-Host ((@{ event = 'native_gate_output'; line = $taskLine } | ConvertTo-Json -Compress)) }
    $taskSummaries = @($taskGateLines | Where-Object { $_.StartsWith('WINDOWS_WORKER_CONTAINMENT_SUMMARY ', [StringComparison]::Ordinal) })
    if ($taskSummaries.Count -ne 1) { throw 'Native gate did not return exactly one summary.' }
    $taskGateSummary = $taskSummaries[0].Substring('WINDOWS_WORKER_CONTAINMENT_SUMMARY '.Length) | ConvertFrom-Json
    $taskSummary.gate_summary = $taskGateSummary
    Assert-CandidateGate $taskGateSummary $taskGateExit $ExpectedCommit ([bool] $Full)
    $taskSummary.native_evidence = [bool] $Full
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
