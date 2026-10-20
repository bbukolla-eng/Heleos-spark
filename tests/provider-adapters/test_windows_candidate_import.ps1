#Requires -Version 7.0
# Portable contract tests; these do not simulate or claim native Windows evidence.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$taskScript = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../scripts/import-windows-native-candidate.ps1'))
if (-not (Test-Path -LiteralPath $taskScript)) { throw 'FAIL: candidate importer is missing' }
$taskTokens = $null
$taskErrors = $null
$taskAst = [Management.Automation.Language.Parser]::ParseFile($taskScript, [ref] $taskTokens, [ref] $taskErrors)
if ($taskErrors.Count) { throw ($taskErrors | Out-String) }
# Load actual pure validators without executing the Windows-only entry point.
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
function Read-TestManifest([string] $Json, [string] $Pin = '') {
    $taskBytes = [Text.Encoding]::UTF8.GetBytes($Json)
    if (-not $Pin) {
        $taskHasher = [Security.Cryptography.SHA256]::Create()
        try { $Pin = ([BitConverter]::ToString($taskHasher.ComputeHash($taskBytes))).Replace('-', '').ToLowerInvariant() }
        finally { $taskHasher.Dispose() }
    }
    ConvertFrom-CandidateManifest $taskBytes $Pin ('a' * 40)
}
$taskValid = '{"schema":"heleos.windows-native-candidate/v1","branch":"build/agent-control-foundation","commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","bundle":"heleos-spark-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.bundle","bundle_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","bundle_bytes":123,"native_gate":"scripts/verify-windows-worker-containment.ps1","required_filesystem":"NTFS","native_gate_status":"pending"}'
$taskManifest = Read-TestManifest $taskValid
if ($taskManifest.commit -cne ('a' * 40) -or $taskManifest.bundle_bytes -ne 123) { throw 'FAIL: valid manifest was not retained' }
$taskCount++
Expect-Rejected { Read-TestManifest $taskValid ('0' * 64) } 'manifest with mismatched independently supplied digest'
Expect-Rejected { Read-TestManifest ($taskValid.Replace('"branch":"build/agent-control-foundation"', '"branch":"main"')) } 'other branch'
Expect-Rejected { Read-TestManifest ($taskValid.Replace('"commit":"' + ('a' * 40), '"commit":"' + ('c' * 40))) } 'other commit'
Expect-Rejected { Read-TestManifest ($taskValid.Replace('heleos-spark-aaaa', '../heleos-spark-aaaa')) } 'bundle traversal'
Expect-Rejected { Read-TestManifest ($taskValid.Replace('"bundle_bytes":123', '"bundle_bytes":0')) } 'empty bundle'
Expect-Rejected { Read-TestManifest ($taskValid.Replace('"bundle_bytes":123', '"bundle_bytes":1.5')) } 'fractional bundle length'
Expect-Rejected { Read-TestManifest ($taskValid.Replace('"bundle_bytes":123', '"bundle_bytes":"123"')) } 'string bundle length'
Expect-Rejected { Read-TestManifest ($taskValid.Replace('"NTFS"', '"ReFS"')) } 'other filesystem'
Expect-Rejected { Read-TestManifest ($taskValid.Replace('scripts/verify-windows-worker-containment.ps1', '../arbitrary.ps1')) } 'arbitrary gate'
Expect-Rejected { Read-TestManifest ($taskValid.Replace('"pending"', '"pass"')) } 'preclaimed acceptance'
Expect-Rejected { Read-TestManifest ($taskValid.Replace('{', '{"schema":"evil",')) } 'duplicate JSON key'
Expect-Rejected { Read-TestManifest ($taskValid.Replace('{', '{"extra":true,')) } 'unknown JSON key'
Expect-Rejected { Read-TestManifest ($taskValid.Replace('"schema"', '"Schema"')) } 'case-shifted JSON key'
Expect-Rejected { Read-TestManifest '{invalid json' } 'malformed JSON'
$taskHead = ('a' * 40) + ' refs/heads/build/agent-control-foundation'
Assert-CandidateHeads @($taskHead) ('a' * 40)
$taskCount++
Expect-Rejected { Assert-CandidateHeads @($taskHead, $taskHead) ('a' * 40) } 'duplicate bundle heads'
Expect-Rejected { Assert-CandidateHeads @((('b' * 40) + ' refs/heads/build/agent-control-foundation')) ('a' * 40) } 'wrong bundle commit'
Expect-Rejected { Assert-CandidateHeads @((('a' * 40) + ' refs/heads/main')) ('a' * 40) } 'wrong bundle ref'
$taskGate = [pscustomobject]@{ status = 'PASS'; git_sha = ('a' * 40); native_host = $true; filesystem = 'NTFS'; mode = 'preflight'; native_evidence = $false }
Assert-CandidateGate $taskGate 0 ('a' * 40) $false
$taskCount++
Expect-Rejected { Assert-CandidateGate $taskGate 1 ('a' * 40) $false } 'nonzero gate exit with PASS text'
Expect-Rejected { Assert-CandidateGate $taskGate 0 ('a' * 40) $true } 'preflight passed as full evidence'
Expect-Rejected { Assert-CandidateGate $taskGate 0 ('b' * 40) $false } 'gate attestation for wrong commit'
$taskGate.native_host = 'true'
Expect-Rejected { Assert-CandidateGate $taskGate 0 ('a' * 40) $false } 'string native-host claim'
$taskGate.native_host = $true
$taskGate.mode = 'native_suites'; $taskGate.native_evidence = $true
Assert-CandidateGate $taskGate 0 ('a' * 40) $true
$taskCount++
$taskGate.status = 'FAIL'
Expect-Rejected { Assert-CandidateGate $taskGate 0 ('a' * 40) $true } 'zero-exit failure summary'
if (-not $IsWindows) {
    # Removing the host guard would probe nonexistent input or create the destination.
    $taskMissing = Join-Path ([IO.Path]::GetTempPath()) ('heleos-import-no-write-' + [guid]::NewGuid().ToString('N'))
    $taskOutput = & (Join-Path $PSHOME 'pwsh') -NoLogo -NoProfile -File $taskScript -CandidateDirectory $taskMissing -Destination (Join-Path $taskMissing 'checkout') -ExpectedManifestSha256 ('0' * 64) -ExpectedCommit ('a' * 40)
    if ($LASTEXITCODE -ne 1) { throw 'FAIL: non-Windows invocation did not fail' }
    $taskSummary = ($taskOutput | Select-Object -Last 1) | ConvertFrom-Json
    if ($taskSummary.error_code -cne 'non_windows' -or $taskSummary.native_evidence -or $taskSummary.destination_created -or (Test-Path -LiteralPath $taskMissing)) { throw 'FAIL: non-Windows rejection mutated paths or claimed evidence' }
    $taskCount++
}
Write-Output ("PASS: $taskCount portable importer checks; native Windows execution is not covered")
