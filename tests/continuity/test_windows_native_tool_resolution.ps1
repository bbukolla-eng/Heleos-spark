#Requires -Version 7.0
# Exercise the real gate assignments against duplicate real PATH applications.
# Loading only these AST nodes preserves the full gate's non-Windows rejection.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$taskScript = Join-Path $PSScriptRoot '../../scripts/verify-windows-worker-containment.ps1'
$taskTokens = $null
$taskErrors = $null
$taskAst = [Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path -LiteralPath $taskScript).ProviderPath, [ref]$taskTokens, [ref]$taskErrors
)
if ($taskErrors.Count -ne 0) { throw 'ASSERTION FAILED: native gate has parser errors' }

# Before the repair the assignments execute their original Get-Command lookup;
# after it, load the same production helper the assignments call. No tool mocks.
$taskHelper = $taskAst.Find({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -ceq 'Resolve-GateApplication'
}, $true)
if ($null -ne $taskHelper) { . ([scriptblock]::Create($taskHelper.Extent.Text)) }

$taskAssignments = @{}
foreach ($taskTool in @('Git', 'Cargo', 'Rustc', 'Rustup')) {
    $taskVariable = 'task' + $taskTool
    $taskNodes = @($taskAst.FindAll({
        param($node)
        $node -is [Management.Automation.Language.AssignmentStatementAst] -and
        $node.Left -is [Management.Automation.Language.VariableExpressionAst] -and
        $node.Left.VariablePath.UserPath -ceq $taskVariable
    }, $true))
    if ($taskNodes.Count -ne 1) { throw "ASSERTION FAILED: expected one lookup assignment for $taskTool" }
    $taskAssignments[$taskTool] = [scriptblock]::Create($taskNodes[0].Extent.Text)
}

$taskOriginalPath = $env:PATH
$taskFixture = Join-Path ([IO.Path]::GetTempPath()) ('heleos-native-tool-resolution-' + [guid]::NewGuid().ToString('N'))
$taskFirst = [IO.Directory]::CreateDirectory((Join-Path $taskFixture 'first tools & spaces')).FullName
$taskSecond = [IO.Directory]::CreateDirectory((Join-Path $taskFixture 'second tools')).FullName
$taskEmpty = [IO.Directory]::CreateDirectory((Join-Path $taskFixture 'empty tools')).FullName
$taskExtension = if ($IsWindows) { '.exe' } else { '' }
$taskExecutable = Join-Path $PSHOME ('pwsh' + $taskExtension)

try {
    foreach ($taskTool in @('git', 'cargo', 'rustc', 'rustup')) {
        foreach ($taskDirectory in @($taskFirst, $taskSecond)) {
            Copy-Item -LiteralPath $taskExecutable -Destination (Join-Path $taskDirectory ($taskTool + $taskExtension))
        }
    }
    $env:PATH = $taskFirst + [IO.Path]::PathSeparator + $taskSecond
    foreach ($taskTool in @('Git', 'Cargo', 'Rustc', 'Rustup')) {
        $taskName = $taskTool.ToLowerInvariant()
        $taskMatches = @(Get-Command -Name $taskName -CommandType Application -ErrorAction Stop)
        if ($taskMatches.Count -ne 2) { throw "ASSERTION FAILED: fixture did not expose two PATH matches for $taskName" }
        . $taskAssignments[$taskTool]
        $taskResolved = Get-Variable -Name ('task' + $taskTool) -ValueOnly
        $taskExpected = Join-Path $taskFirst ($taskName + $taskExtension)
        if ($taskResolved -isnot [string] -or $taskResolved -cne $taskExpected) {
            throw "ASSERTION FAILED: $taskName must resolve to one string containing the first PATH application"
        }
        if (-not [IO.Path]::IsPathFullyQualified($taskResolved) -or
            -not (Test-Path -LiteralPath $taskResolved -PathType Leaf)) {
            throw "ASSERTION FAILED: $taskName did not resolve to a usable absolute file path"
        }
    }

    # Resolution follows actual PATH order, not an incidental sorted path name.
    $env:PATH = $taskSecond + [IO.Path]::PathSeparator + $taskFirst
    foreach ($taskTool in @('Git', 'Cargo', 'Rustc', 'Rustup')) {
        . $taskAssignments[$taskTool]
        $taskResolved = Get-Variable -Name ('task' + $taskTool) -ValueOnly
        $taskExpected = Join-Path $taskSecond ($taskTool.ToLowerInvariant() + $taskExtension)
        if ($taskResolved -isnot [string] -or $taskResolved -cne $taskExpected) {
            throw "ASSERTION FAILED: $taskTool ignored reversed PATH precedence"
        }
    }

    $env:PATH = $taskEmpty
    foreach ($taskTool in @('Git', 'Cargo', 'Rustc', 'Rustup')) {
        $taskRejected = $false
        try { . $taskAssignments[$taskTool] }
        catch { $taskRejected = $true }
        if (-not $taskRejected) { throw "ASSERTION FAILED: missing $taskTool must fail closed" }
    }
    'PASS: git/cargo/rustc/rustup select the first real PATH application and reject missing tools'
}
finally {
    $env:PATH = $taskOriginalPath
    Remove-Item -LiteralPath $taskFixture -Recurse -Force
}
