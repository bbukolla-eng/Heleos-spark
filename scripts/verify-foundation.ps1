#Requires -Version 7.0
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $IsWindows) {
    throw 'This entry point requires native Windows. Use scripts/verify-foundation on Unix.'
}
if ($env:CARGO_BUILD_TARGET) {
    throw 'Foundation verification requires a native host build; unset CARGO_BUILD_TARGET.'
}
if ($env:CARGO_TARGET_DIR) {
    throw 'Foundation verification requires the repository target directory; unset CARGO_TARGET_DIR.'
}

function Invoke-FoundationCommand {
    param(
        [Parameter(Mandatory)][string] $Executable,
        [Parameter(Mandatory)][string[]] $Arguments
    )
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Foundation command failed with exit code ${LASTEXITCODE}: $Executable $($Arguments -join ' ')"
    }
}

$taskOriginalLocation = Get-Location
$taskEnvironmentNames = @(
    'CARGO_NET_OFFLINE',
    'RUSTUP_AUTO_INSTALL',
    'HELEOS_BIN',
    'HELEOS_PDF_GUEST'
)
$taskOriginalEnvironment = @{}
foreach ($taskName in $taskEnvironmentNames) {
    $taskOriginalEnvironment[$taskName] = [Environment]::GetEnvironmentVariable($taskName, 'Process')
}

try {
    Set-Location -LiteralPath (Join-Path $PSScriptRoot '..')
    $taskRoot = (Resolve-Path -LiteralPath '.').ProviderPath
    $env:CARGO_NET_OFFLINE = 'true'
    $env:RUSTUP_AUTO_INSTALL = '0'

    Invoke-FoundationCommand 'cargo' @('+1.96.1', 'fmt', '--all', '--check')
    Invoke-FoundationCommand 'cargo' @('+1.96.1', 'build', '--frozen', '-p', 'heleos-verification', '--bin', 'verify-provenance')
    $taskVerifier = (Resolve-Path -LiteralPath 'target/debug/verify-provenance.exe').ProviderPath
    Invoke-FoundationCommand $taskVerifier @('build-pdf-guest')
    $env:HELEOS_PDF_GUEST = (Resolve-Path -LiteralPath 'target/wasm32-wasip1/release/heleos_pdf_guest.wasm').ProviderPath
    Invoke-FoundationCommand 'cargo' @('+1.96.1', 'clippy', '--frozen', '--workspace', '--all-targets', '--all-features', '--', '-D', 'warnings')
    Invoke-FoundationCommand 'cargo' @('+1.96.1', 'build', '--frozen', '-p', 'heleos-cli', '--bin', 'heleos')
    $env:HELEOS_BIN = (Resolve-Path -LiteralPath 'target/debug/heleos.exe').ProviderPath
    Invoke-FoundationCommand 'cargo' @('+1.96.1', 'test', '--frozen', '--workspace', '--all-targets')
    Invoke-FoundationCommand 'cargo' @('+1.96.1', 'run', '--frozen', '-p', 'heleos-verification', '--bin', 'verify-provenance')
}
finally {
    foreach ($taskName in $taskEnvironmentNames) {
        [Environment]::SetEnvironmentVariable($taskName, $taskOriginalEnvironment[$taskName], 'Process')
    }
    Set-Location -LiteralPath $taskOriginalLocation.Path
}
