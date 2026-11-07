#Requires -Version 7.0
# Portable behavior checks, never native Windows acceptance evidence.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$taskScript = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../scripts/import-foundation-native-candidate.ps1'))
if (-not (Test-Path -LiteralPath $taskScript)) { throw 'FAIL: Foundation candidate importer is missing' }
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
function Read-TestManifest([string] $Json, [string] $Pin = '') {
    $taskBytes = [Text.Encoding]::UTF8.GetBytes($Json)
    if (-not $Pin) {
        $taskHasher = [Security.Cryptography.SHA256]::Create()
        try { $Pin = ([BitConverter]::ToString($taskHasher.ComputeHash($taskBytes))).Replace('-', '').ToLowerInvariant() }
        finally { $taskHasher.Dispose() }
    }
    ConvertFrom-FoundationManifest $taskBytes $Pin ('a' * 40)
}
$taskValid = '{"schema":"heleos.foundation-windows-native-candidate/v1","branch":"release/foundation-0.1-native-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","bundle":"heleos-spark-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.bundle","bundle_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","bundle_bytes":123,"native_gate":"scripts/verify-supply-chain.ps1","required_filesystem":"NTFS","native_gate_status":"pending"}'
$taskManifest = Read-TestManifest $taskValid
if ($taskManifest.commit -cne ('a' * 40) -or $taskManifest.bundle_bytes -ne 123) { throw 'FAIL: valid manifest was not retained' }
$taskCount++
Expect-Rejected { Read-TestManifest $taskValid ('0' * 64) } 'untrusted manifest digest'
foreach ($taskMutation in @(
    @('foundation-windows-native-candidate/v1', 'windows-native-candidate/v1'),
    @('release/foundation-0.1-native-aaaa', 'release/foundation-0.1-native-baaa'),
    @('"commit":"aaaa', '"commit":"baaa'),
    @('heleos-spark-aaaa', '../heleos-spark-aaaa'),
    @('"bundle_sha256":"bbbb', '"bundle_sha256":"BBBB'),
    @('"bundle_bytes":123', '"bundle_bytes":0'),
    @('"bundle_bytes":123', '"bundle_bytes":1.5'),
    @('"bundle_bytes":123', '"bundle_bytes":"123"'),
    @('"NTFS"', '"ReFS"'),
    @('scripts/verify-supply-chain.ps1', 'scripts/verify-windows-worker-containment.ps1'),
    @('"pending"', '"pass"'),
    @('{', '{"schema":"evil",'),
    @('{', '{"extra":true,'),
    @('"schema"', '"Schema"')
)) { Expect-Rejected { Read-TestManifest ($taskValid.Replace($taskMutation[0], $taskMutation[1])) } ($taskMutation -join ' -> ') }
Expect-Rejected { Read-TestManifest '{invalid json' } 'malformed manifest JSON'
Expect-Rejected { Read-TestManifest '[]' } 'non-object manifest'
Expect-Rejected { ConvertFrom-FoundationManifest ([byte[]]@(255)) ('0' * 64) ('a' * 40) } 'invalid manifest bytes'
$taskHead = ('a' * 40) + ' refs/heads/release/foundation-0.1-native-' + ('a' * 40)
Assert-FoundationHeads @($taskHead) ('a' * 40)
$taskCount++
foreach ($taskHeads in @(@(), @($taskHead, $taskHead), @($taskHead.Replace('refs/heads/release', 'refs/tags/release')), @($taskHead.Replace('aaaa', 'bbbb')))) {
    Expect-Rejected { Assert-FoundationHeads $taskHeads ('a' * 40) } 'missing, duplicate or mismatched bundle head'
}
$taskReceipt = '{"schema":"heleos.native-suite-receipt/v1","status":"pass","candidate_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","platform":"windows-x86_64","filesystem":"NTFS","suite_count":7,"manifest_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","transcript_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}'
$taskPass = 'SUPPLY_CHAIN_LOCAL_PASS: native Windows x64/NTFS offline gate and all native test commands passed.'
$taskEvidence = Get-FoundationReceipt @('ordinary diagnostic', $taskReceipt, $taskPass) 0 ('a' * 40)
if ($taskEvidence.status -cne 'pass' -or $taskEvidence.suite_count -ne 7) { throw 'FAIL: missing accepted receipt' }
$taskCount++
Expect-Rejected { Get-FoundationReceipt @($taskReceipt, $taskPass) 1 ('a' * 40) } 'nonzero gate exit'
Expect-Rejected { Get-FoundationReceipt @($taskReceipt, $taskPass) 0 ('b' * 40) } 'wrong candidate'
foreach ($taskMutation in @(
    @('native-suite-receipt/v1', 'native-suite-receipt/v2'), @('"pass"', '"fail"'),
    @('windows-x86_64', 'macos-aarch64'), @('"NTFS"', '"ReFS"'),
    @('"suite_count":7', '"suite_count":6'), @('"suite_count":7', '"suite_count":"7"'),
    @('"suite_count":7', '"suite_count":7.0'), @('"suite_count":7', '"suite_count":true'),
    @('{', '{"schema":"evil",'), @('"status"', '"Status"'), @('"status":"pass",', '')
)) { Expect-Rejected { Get-FoundationReceipt @($taskReceipt.Replace($taskMutation[0], $taskMutation[1]), $taskPass) 0 ('a' * 40) } ($taskMutation -join ' -> ') }
foreach ($taskLines in @(
    @($taskPass), @($taskReceipt), @($taskReceipt, $taskReceipt, $taskPass),
    @($taskReceipt, $taskPass, $taskPass), @($taskReceipt, ($taskPass + ' forged')),
    @($taskReceipt, $taskPass, 'SUPPLY_CHAIN_FAILURE: injected failure'),
    @($taskReceipt, $taskPass, 'SUPPLY_CHAIN_FAIL: actual gate failure'),
    @($taskReceipt, $taskPass, '{"schema":"heleos.native-suite-receipt/v1",broken'),
    @($taskReceipt, $taskPass, '{"schema":"heleos.native-suite-receipt/v1","schema":"other"}'),
    @($taskReceipt, $taskPass, ($taskReceipt.Replace('native-suite-receipt/v1', 'native-suite-receipt/v2'))),
    @($taskReceipt, $taskPass, ($taskReceipt.Replace('"schema"', '"Schema"'))),
    @($taskReceipt, $taskPass, '{broken JSON}')
)) { Expect-Rejected { Get-FoundationReceipt $taskLines 0 ('a' * 40) } ('missing, duplicated, malformed or contradictory native evidence: ' + ($taskLines -join ' | ')) }
Assert-FoundationVolume 'Fixed' 'NTFS'
$taskCount++
Expect-Rejected { Assert-FoundationVolume 'Network' 'NTFS' } 'network NTFS drive'
Expect-Rejected { Assert-FoundationVolume 'Fixed' 'ReFS' } 'non-NTFS drive'
Assert-FoundationPathSyntax 'C:\candidate\checkout'
$taskCount++
foreach ($taskPath in @('relative', '\\server\share', 'C:\candidate\..\checkout', 'C:\candidate:stream', 'C:\candidate\NUL', 'C:\candidate\trailing.', 'C:/candidate')) {
    Expect-Rejected { Assert-FoundationPathSyntax $taskPath } 'unsafe local path'
}
if (-not $IsWindows) {
    $taskMissing = Join-Path ([IO.Path]::GetTempPath()) ('heleos-foundation-import-no-write-' + [guid]::NewGuid().ToString('N'))
    $taskOutput = & (Join-Path $PSHOME 'pwsh') -NoLogo -NoProfile -File $taskScript -CandidateDirectory $taskMissing -Destination (Join-Path $taskMissing 'checkout') -ExpectedManifestSha256 ('0' * 64) -ExpectedCommit ('a' * 40)
    if ($LASTEXITCODE -ne 1) { throw 'FAIL: non-Windows invocation did not fail' }
    $taskSummary = ($taskOutput | Select-Object -Last 1) | ConvertFrom-Json
    if ($taskSummary.schema -cne 'heleos.foundation-windows-native-import/v1' -or $taskSummary.error_code -cne 'non_windows' -or $taskSummary.native_host -or $taskSummary.native_evidence -or $taskSummary.destination_created -or (Test-Path -LiteralPath $taskMissing)) { throw 'FAIL: non-Windows rejection mutated paths or claimed evidence' }
    $taskCount++
}
Write-Output "PASS: $taskCount portable Foundation importer checks; native Windows execution is not covered"
