#Requires -Version 7.0
<#
Offline owner entrypoint. Keep this script beside a separately trusted copy of
import-windows-native-candidate.ps1. Supply the controller's independent manifest
SHA-256 and commit pins, never pins derived from the transferred files alone.
RunDirectory must be NEW under an existing local fixed NTFS parent. The importer
creates RunDirectory/checkout and always runs -Full. Output and result.json are
published by same-directory no-replace rename; partial runs/files are retained.
No dependency installation, remote Git operation, overwrite, or cleanup occurs.
Only result.json with PASS/native_evidence=true attests the complete native gate.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $CandidateDirectory,
    [Parameter(Mandatory)][string] $RunDirectory,
    [Parameter(Mandatory)][string] $ExpectedManifestSha256,
    [Parameter(Mandatory)][string] $ExpectedCommit
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$taskExit = 1; $taskCode = 'validation_failed'; $taskRun = $null
$taskHandles = [Collections.Generic.List[IDisposable]]::new()
$taskSummary = [ordered]@{
    schema = 'heleos.windows-native-run/v1'; status = 'FAIL'
    mode = 'native_suites'; native_host = [bool] $IsWindows; native_evidence = $false
    expected_commit = $ExpectedCommit; expected_manifest_sha256 = $ExpectedManifestSha256
    run_directory = $null; run_created = $false; destination = $null
    output_file = $null; output_sha256 = $null; import_summary = $null
    started_at = [DateTime]::UtcNow.ToString('o'); finished_at = $null
    error_code = $null; error = $null
}

function Assert-RunLocalPath {
    param([string] $Path)
    if ($Path -cnotmatch '^[A-Za-z]:\\' -or $Path.Substring(2).Contains(':') -or $Path.Contains('/')) { throw 'Use an absolute local drive path, without UNC, devices or alternate streams.' }
    foreach ($taskPart in $Path.Substring(3).Split('\')) {
        if (-not $taskPart -or $taskPart -match '[. ]$|[<>"|?*\x00-\x1f]' -or $taskPart -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)') { throw 'Unsafe Windows path component.' }
    }
}

function Get-RunRealPath {
    param([string] $Path, [switch] $Directory)
    $taskItem = Get-Item -LiteralPath $Path -Force
    if ($taskItem.PSProvider.Name -cne 'FileSystem' -or $taskItem.PSIsContainer -ne [bool] $Directory) { throw 'Expected a regular filesystem path of the required type.' }
    $taskAncestor = $taskItem
    while ($null -ne $taskAncestor) {
        if ($taskAncestor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse points are forbidden in handoff paths.' }
        $taskAncestor = if ($taskAncestor -is [IO.DirectoryInfo]) { $taskAncestor.Parent } else { $taskAncestor.Directory }
    }
    return $taskItem
}

function Assert-RunVolume {
    param([string] $Path)
    $taskDrive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($Path))
    $taskVolumes = @(Get-Volume -FilePath $Path -ErrorAction Stop)
    if ($taskDrive.DriveType -ne [IO.DriveType]::Fixed -or $taskVolumes.Count -ne 1 -or $taskVolumes[0].FileSystemType -ine 'NTFS') { throw 'Handoff paths must reside on a local fixed NTFS volume.' }
}

function Assert-RunJsonProperties {
    param([Text.Json.JsonElement] $Element)
    if ($Element.ValueKind -eq [Text.Json.JsonValueKind]::Object) {
        $taskNames = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        foreach ($taskProperty in $Element.EnumerateObject()) {
            if (-not $taskNames.Add($taskProperty.Name)) { throw 'Duplicate JSON property in handoff evidence.' }
            Assert-RunJsonProperties $taskProperty.Value
        }
    } elseif ($Element.ValueKind -eq [Text.Json.JsonValueKind]::Array) {
        foreach ($taskValue in $Element.EnumerateArray()) { Assert-RunJsonProperties $taskValue }
    }
}

function ConvertFrom-RunJson {
    param([string] $Json)
    $taskDocument = [Text.Json.JsonDocument]::Parse($Json)
    try {
        if ($taskDocument.RootElement.ValueKind -ne [Text.Json.JsonValueKind]::Object) { throw 'Expected a JSON object.' }
        Assert-RunJsonProperties $taskDocument.RootElement
        return ConvertFrom-Json -InputObject $Json
    } finally { $taskDocument.Dispose() }
}

function Read-RunPinnedManifest {
    param([IO.Stream] $Stream, [string] $ManifestHash, [string] $Commit)
    if ($ManifestHash -cnotmatch '^[0-9a-f]{64}$' -or $Commit -cnotmatch '^[0-9a-f]{40}$') { throw 'Supply exact independently obtained lowercase manifest SHA-256 and commit pins.' }
    if ($Stream.Length -gt 65536) { throw 'Manifest exceeds 64 KiB.' }
    $Stream.Position = 0
    $taskActualHash = (Get-FileHash -InputStream $Stream -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($taskActualHash -cne $ManifestHash) { throw 'Manifest SHA-256 differs from the independent pin.' }
    $Stream.Position = 0
    $taskReader = [IO.StreamReader]::new($Stream, [Text.UTF8Encoding]::new($false, $true), $false, 4096, $true)
    try { $taskManifest = ConvertFrom-RunJson $taskReader.ReadToEnd() } finally { $taskReader.Dispose() }
    if ($taskManifest.commit -cne $Commit -or $taskManifest.schema -cne 'heleos.windows-native-candidate/v1') { throw 'Manifest differs from the independent commit pin or schema.' }
    return $taskManifest
}

function ConvertFrom-RunImportOutput {
    param([string[]] $Lines, [int] $ExitCode, [string] $Commit, [string] $ManifestHash, [string] $Destination)
    $taskSummaries = @(
        foreach ($taskLine in $Lines) {
            $taskValue = ConvertFrom-RunJson $taskLine
            if ($taskValue.PSObject.Properties.Name -ccontains 'schema' -and $taskValue.schema -ceq 'heleos.windows-native-import/v1') { $taskValue }
        }
    )
    if ($taskSummaries.Count -ne 1) { throw 'Expected exactly one importer summary.' }
    $taskImport = $taskSummaries[0]
    if ($ExitCode -ne 0 -or $taskImport.status -cne 'PASS' -or $taskImport.mode -cne 'native_suites' -or
        $taskImport.native_host -isnot [bool] -or -not $taskImport.native_host -or
        $taskImport.native_evidence -isnot [bool] -or -not $taskImport.native_evidence -or
        $taskImport.destination_created -isnot [bool] -or -not $taskImport.destination_created -or
        $taskImport.commit -cne $Commit -or $taskImport.manifest_sha256 -cne $ManifestHash -or
        $taskImport.destination -cne $Destination -or $taskImport.bundle_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        $null -ne $taskImport.error_code -or $null -ne $taskImport.error) { throw 'Importer failed or returned inconsistent candidate evidence.' }
    $taskGate = $taskImport.gate_summary
    if ($taskGate.status -cne 'PASS' -or $taskGate.mode -cne 'native_suites' -or
        $taskGate.native_host -isnot [bool] -or -not $taskGate.native_host -or
        $taskGate.native_evidence -isnot [bool] -or -not $taskGate.native_evidence -or
        $taskGate.git_sha -cne $Commit -or $taskGate.filesystem -cne 'NTFS') { throw 'Full native gate evidence is missing or inconsistent.' }
    return $taskImport
}

function Write-RunAtomicText {
    param([string] $Path, [string] $Text)
    if (Test-Path -LiteralPath $Path) { throw 'Output already exists; preserve it.' }
    $taskPartial = $Path + '.partial'
    $taskStream = [IO.File]::Open($taskPartial, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $taskBytes = [Text.UTF8Encoding]::new($false).GetBytes($Text)
        $taskStream.Write($taskBytes, 0, $taskBytes.Length)
        $taskStream.Flush($true)
    } finally { $taskStream.Dispose() }
    [IO.File]::Move($taskPartial, $Path)
}

function Invoke-RunImporter {
    param([string] $Executable, [string] $Importer, [string] $Candidate, [string] $Destination, [string] $ManifestHash, [string] $Commit, [string] $OutputPath)
    if (Test-Path -LiteralPath $OutputPath) { throw 'Import output already exists; preserve it.' }
    $taskPartial = $OutputPath + '.partial'
    $taskStream = [IO.File]::Open($taskPartial, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    $taskWriter = [IO.StreamWriter]::new($taskStream, [Text.UTF8Encoding]::new($false), 4096, $true)
    try {
        # A PowerShell argument array preserves literal paths; no command string
        # or shell interpolation. Capture stderr as well as stdout on failure.
        $taskArguments = @('-NoLogo', '-NoProfile', '-NonInteractive', '-File', $Importer,
            '-CandidateDirectory', $Candidate, '-Destination', $Destination,
            '-ExpectedManifestSha256', $ManifestHash, '-ExpectedCommit', $Commit, '-Full')
        $PSNativeCommandUseErrorActionPreference = $false
        & $Executable @taskArguments 2>&1 | ForEach-Object { $taskWriter.WriteLine($_.ToString()); $taskWriter.Flush() }
        $taskResult = $LASTEXITCODE
    } finally {
        $taskWriter.Dispose()
        try { $taskStream.Flush($true) } finally { $taskStream.Dispose() }
        [IO.File]::Move($taskPartial, $OutputPath)
    }
    return $taskResult
}

try {
    # This must precede path probes, output creation, and external commands.
    if (-not $IsWindows) { $taskCode = 'non_windows'; throw 'The native handoff requires native Windows.' }
    if ([Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne [Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture) { throw 'Use PowerShell in the native OS architecture.' }
    if ($ExpectedManifestSha256 -cnotmatch '^[0-9a-f]{64}$' -or $ExpectedCommit -cnotmatch '^[0-9a-f]{40}$') { throw 'Supply exact independently obtained lowercase manifest SHA-256 and commit pins.' }
    Assert-RunLocalPath $CandidateDirectory
    Assert-RunLocalPath $RunDirectory
    $taskCandidate = (Get-RunRealPath $CandidateDirectory -Directory).FullName
    $taskRun = [IO.Path]::GetFullPath($RunDirectory)
    if (Test-Path -LiteralPath $taskRun) { throw 'RunDirectory already exists; choose a new path.' }
    $taskParent = (Get-RunRealPath ([IO.Path]::GetDirectoryName($taskRun)) -Directory).FullName
    Assert-RunVolume $taskCandidate
    Assert-RunVolume $taskParent
    $taskManifestPath = (Get-RunRealPath (Join-Path $taskCandidate 'candidate.json')).FullName
    $taskImporter = (Get-RunRealPath (Join-Path $PSScriptRoot 'import-windows-native-candidate.ps1')).FullName
    foreach ($taskFile in @($taskManifestPath, $taskImporter)) {
        $taskHandles.Add([IO.File]::Open($taskFile, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read))
    }
    $null = Read-RunPinnedManifest $taskHandles[0] $ExpectedManifestSha256 $ExpectedCommit
    $null = New-Item -ItemType Directory -Path $taskRun -ErrorAction Stop
    $taskSummary.run_created = $true; $taskSummary.run_directory = $taskRun
    $taskSummary.destination = Join-Path $taskRun 'checkout'
    $taskSummary.output_file = Join-Path $taskRun 'import-output.jsonl'
    $taskCode = 'import_failed'
    $taskImportExit = Invoke-RunImporter (Join-Path $PSHOME 'pwsh.exe') $taskImporter $taskCandidate $taskSummary.destination $ExpectedManifestSha256 $ExpectedCommit $taskSummary.output_file
    $taskSummary.output_sha256 = (Get-FileHash -LiteralPath $taskSummary.output_file -Algorithm SHA256).Hash.ToLowerInvariant()
    $taskSummary.import_summary = ConvertFrom-RunImportOutput ([IO.File]::ReadAllLines($taskSummary.output_file)) $taskImportExit $ExpectedCommit $ExpectedManifestSha256 $taskSummary.destination
    $taskSummary.status = 'PASS'; $taskSummary.native_evidence = $true
    $taskExit = 0
} catch {
    $taskSummary.error_code = $taskCode; $taskSummary.error = $_.Exception.Message
} finally {
    foreach ($taskHandle in $taskHandles) { $taskHandle.Dispose() }
    $taskSummary.finished_at = [DateTime]::UtcNow.ToString('o')
    if ($taskSummary.run_created) {
        try { Write-RunAtomicText (Join-Path $taskRun 'result.json') ($taskSummary | ConvertTo-Json -Depth 16 -Compress) }
        catch {
            $taskSummary.status = 'FAIL'; $taskSummary.native_evidence = $false; $taskExit = 1
            $taskSummary.error_code = 'result_write_failed'; $taskSummary.error = $_.Exception.Message
        }
    }
    Write-Output ($taskSummary | ConvertTo-Json -Depth 16 -Compress)
}
exit $taskExit
