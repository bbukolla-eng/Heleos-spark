#Requires -Version 7.0
# Portable behavioral checks. These do not attest native Windows execution.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$taskScript = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../scripts/run-windows-native-candidate.ps1'))
if (-not (Test-Path -LiteralPath $taskScript)) { throw 'FAIL: native candidate launcher is missing' }
$taskTokens = $null; $taskErrors = $null
$taskAst = [Management.Automation.Language.Parser]::ParseFile($taskScript, [ref] $taskTokens, [ref] $taskErrors)
if ($taskErrors.Count) { throw ($taskErrors | Out-String) }
foreach ($taskFunction in $taskAst.FindAll({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
    . ([scriptblock]::Create($taskFunction.Extent.Text))
}
$taskCount = 0
function Expect-Rejected([scriptblock] $Action, [string] $Name) {
    $taskRejected = $false
    try { & $Action | Out-Null } catch { $taskRejected = $true }
    if (-not $taskRejected) { throw "FAIL: accepted $Name" }
    $script:taskCount++
}
$taskCommit = 'a' * 40; $taskPin = 'b' * 64
$taskManifestJson = '{"schema":"heleos.windows-native-candidate/v1","commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'
$taskManifestBytes = [Text.Encoding]::UTF8.GetBytes($taskManifestJson)
# Use an independent hash implementation solely to construct the input pin.
$taskHasher = [Security.Cryptography.SHA256]::Create()
try { $taskManifestDigest = [Convert]::ToHexString($taskHasher.ComputeHash($taskManifestBytes)).ToLowerInvariant() }
finally { $taskHasher.Dispose() }
function Read-TestPinnedManifest([byte[]] $Bytes, [string] $Pin, [string] $Commit) {
    $taskStream = [IO.MemoryStream]::new($Bytes)
    try { Read-RunPinnedManifest $taskStream $Pin $Commit } finally { $taskStream.Dispose() }
}
$taskReadManifest = Read-TestPinnedManifest $taskManifestBytes $taskManifestDigest $taskCommit
if ($taskReadManifest.commit -cne $taskCommit) { throw 'FAIL: valid independent pin rejected' }
$taskCount++
Expect-Rejected { Read-TestPinnedManifest $taskManifestBytes ('0' * 64) $taskCommit } 'mismatched independent manifest digest'
Expect-Rejected { Read-TestPinnedManifest $taskManifestBytes $taskManifestDigest ('c' * 40) } 'mismatched independent commit pin'
Expect-Rejected { Read-TestPinnedManifest $taskManifestBytes $taskManifestDigest.ToUpperInvariant() $taskCommit } 'noncanonical hash pin'
Expect-Rejected { Read-TestPinnedManifest $taskManifestBytes $taskManifestDigest 'short' } 'abbreviated commit pin'
Expect-Rejected { Read-TestPinnedManifest ([byte[]]::new(65537)) $taskManifestDigest $taskCommit } 'oversized manifest'
$taskDestination = 'C:\runs\one\checkout'
$taskValid = [ordered]@{
    schema = 'heleos.windows-native-import/v1'; status = 'PASS'; mode = 'native_suites'
    native_host = $true; native_evidence = $true; commit = $taskCommit
    destination = $taskDestination; destination_created = $true
    manifest_sha256 = $taskPin; bundle_sha256 = ('c' * 64)
    gate_summary = @{ status = 'PASS'; mode = 'native_suites'; native_host = $true; native_evidence = $true; git_sha = $taskCommit; filesystem = 'NTFS' }
    error_code = $null; error = $null
} | ConvertTo-Json -Depth 8 -Compress
$taskGood = ConvertFrom-RunImportOutput @('{"event":"git"}', $taskValid) 0 $taskCommit $taskPin $taskDestination
if ($taskGood.commit -cne $taskCommit) { throw 'FAIL: correct pinned result rejected' }
$taskCount++
Expect-Rejected { ConvertFrom-RunImportOutput @($taskValid) 1 $taskCommit $taskPin $taskDestination } 'nonzero import with PASS text'
Expect-Rejected { ConvertFrom-RunImportOutput @($taskValid, $taskValid) 0 $taskCommit $taskPin $taskDestination } 'duplicate import summaries'
Expect-Rejected { ConvertFrom-RunImportOutput @('not JSON', $taskValid) 0 $taskCommit $taskPin $taskDestination } 'malformed import output'
Expect-Rejected { ConvertFrom-RunImportOutput @($taskValid) 0 ('d' * 40) $taskPin $taskDestination } 'wrong commit'
Expect-Rejected { ConvertFrom-RunImportOutput @($taskValid) 0 $taskCommit ('e' * 64) $taskDestination } 'wrong manifest digest'
Expect-Rejected { ConvertFrom-RunImportOutput @($taskValid) 0 $taskCommit $taskPin 'C:\other\checkout' } 'wrong destination'
foreach ($taskMutation in @(
    @{ From = '"native_evidence":true'; To = '"native_evidence":"true"' },
    @{ From = '"native_host":true'; To = '"native_host":false' },
    @{ From = '"destination_created":true'; To = '"destination_created":false' },
    @{ From = '"mode":"native_suites"'; To = '"mode":"preflight"' },
    @{ From = '"filesystem":"NTFS"'; To = '"filesystem":"ReFS"' },
    @{ From = '"git_sha":"' + $taskCommit + '"'; To = '"git_sha":"' + ('d' * 40) + '"' },
    @{ From = '"error":null'; To = '"error":"failed"' },
    @{ From = '"status":"PASS"'; To = '"status":"FAIL","status":"PASS"' }
)) {
    Expect-Rejected { ConvertFrom-RunImportOutput @($taskValid.Replace($taskMutation.From, $taskMutation.To)) 0 $taskCommit $taskPin $taskDestination } ('inconsistent result: ' + $taskMutation.From)
}
foreach ($taskBadPath in @('relative', '\\server\share\run', 'C:\run\..\escape', 'C:\run\foo:stream', 'C:\run\NUL', 'C:\run\tail.', 'C:\run\', 'C:/run', 'C:\run\COM1.log')) {
    Expect-Rejected { Assert-RunLocalPath $taskBadPath } ('unsafe path ' + $taskBadPath)
}
Assert-RunLocalPath 'C:\runs\new-run'
$taskCount++
$taskScratch = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ('../../target/heleos-launcher-contract-' + [guid]::NewGuid().ToString('N'))))
$null = New-Item -ItemType Directory -Path $taskScratch
$taskResultPath = Join-Path $taskScratch 'result.json'
Write-RunAtomicText $taskResultPath '{"status":"FAIL","native_evidence":false}'
$null = Get-RunRealPath $taskScratch -Directory
$null = Get-RunRealPath $taskResultPath
Expect-Rejected { Get-RunRealPath $taskResultPath -Directory } 'regular file as directory'
Expect-Rejected { Get-RunRealPath $taskScratch } 'directory as file'
if (-not $IsWindows) {
    $taskLink = Join-Path $taskScratch 'linked-parent'
    $null = New-Item -ItemType SymbolicLink -Path $taskLink -Target $taskScratch
    Expect-Rejected { Get-RunRealPath (Join-Path $taskLink 'result.json') } 'symlink ancestor'
}
if (([IO.File]::ReadAllText($taskResultPath) | ConvertFrom-Json).native_evidence) { throw 'FAIL: atomic output changed result' }
Expect-Rejected { Write-RunAtomicText $taskResultPath 'overwrite' } 'preexisting result'
if ([IO.File]::ReadAllText($taskResultPath) -cne '{"status":"FAIL","native_evidence":false}') { throw 'FAIL: result overwritten' }
$taskPartial = Join-Path $taskScratch 'blocked.json.partial'
[IO.File]::WriteAllText($taskPartial, 'retained')
Expect-Rejected { Write-RunAtomicText (Join-Path $taskScratch 'blocked.json') 'overwrite' } 'preexisting partial result'
if ([IO.File]::ReadAllText($taskPartial) -cne 'retained') { throw 'FAIL: partial result overwritten' }
# Run a real child PowerShell to catch missing -Full, argument splitting, exit-code
# swallowing, and failure-output loss. The fixture performs no native work.
$taskFixture = Join-Path $taskScratch 'import fixture.ps1'
[IO.File]::WriteAllText($taskFixture, @'
param($CandidateDirectory, $Destination, $ExpectedManifestSha256, $ExpectedCommit, [switch] $Full)
if (-not $Full -or $CandidateDirectory -cne 'candidate with spaces' -or $Destination -cne 'destination with spaces' -or $ExpectedManifestSha256 -cne ('b' * 64) -or $ExpectedCommit -cne ('a' * 40)) { exit 23 }
Write-Output '{"event":"fixture","native_evidence":false}'
[Console]::Error.WriteLine('retained diagnostic')
exit 7
'@)
$taskLog = Join-Path $taskScratch 'import-output.jsonl'
$taskExe = Join-Path $PSHOME $(if ($IsWindows) { 'pwsh.exe' } else { 'pwsh' })
$taskExit = Invoke-RunImporter $taskExe $taskFixture 'candidate with spaces' 'destination with spaces' $taskPin $taskCommit $taskLog
if ($taskExit -ne 7 -or -not ([IO.File]::ReadAllText($taskLog).Contains('retained diagnostic')) -or -not ([IO.File]::ReadAllText($taskLog).Contains('fixture')) -or (Test-Path -LiteralPath ($taskLog + '.partial'))) { throw 'FAIL: child arguments, exit or retained output differs' }
$taskCount++
Expect-Rejected { Invoke-RunImporter $taskExe $taskFixture 'candidate with spaces' 'destination with spaces' $taskPin $taskCommit $taskLog } 'existing child output'
if (-not $IsWindows) {
    $taskMissing = Join-Path $taskScratch 'must-not-create'
    $taskOutput = & $taskExe -NoLogo -NoProfile -File $taskScript -CandidateDirectory $taskMissing -RunDirectory (Join-Path $taskMissing 'run') -ExpectedManifestSha256 $taskPin -ExpectedCommit $taskCommit
    if ($LASTEXITCODE -ne 1) { throw 'FAIL: non-Windows launcher did not fail' }
    $taskSummary = ($taskOutput | Select-Object -Last 1) | ConvertFrom-Json
    if ($taskSummary.error_code -cne 'non_windows' -or $taskSummary.native_evidence -or $taskSummary.run_created -or (Test-Path -LiteralPath $taskMissing)) { throw 'FAIL: non-Windows launch touched paths or claimed evidence' }
    $taskCount++
}
Write-Output "PASS: $taskCount portable launcher checks; native Windows execution is not covered; scratch retained at $taskScratch"
