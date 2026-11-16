[CmdletBinding()]
param(
    [Alias('Upgrade')]
    [switch]$Update,
    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoWiseUrl = 'https://github.com/SteelOtter1242/repowise-ifx.git'
$RepoWiseSshUrl = 'git@github.com:SteelOtter1242/repowise-ifx.git'
$RepoWiseBranch = 'beta'
$MetricsRepoUrl = 'https://github.com/SteelOtter1242/repowise-workspace-metrics.git'
$MetricsRepoSshUrl = 'git@github.com:SteelOtter1242/repowise-workspace-metrics.git'
$MetricsBranch = 'main'

if ($Help) {
    @'
Usage: install-workspace-metrics.ps1 [-Update|-Upgrade]

Install Repowise beta and the workspace-metrics extension into REPOWISE_HOME.
This script can be downloaded and run without checking out the extension repo.
The first install uses the latest Repowise beta and extension main. Re-running
reuses the checkouts and environment. Pass -Update or -Upgrade to
fast-forward both checkouts to their current branch heads.

Set REPOWISE_HOME once to choose a different installation directory.
'@
    return
}

if ([string]::IsNullOrWhiteSpace($env:REPOWISE_HOME)) {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw 'Set REPOWISE_HOME or ensure LOCALAPPDATA is available.'
    }
    $env:REPOWISE_HOME = Join-Path $env:LOCALAPPDATA 'RepowiseWorkshop'
}

$RepoWiseHome = [System.IO.Path]::GetFullPath($env:REPOWISE_HOME)
$env:REPOWISE_HOME = $RepoWiseHome
$RepoWiseSource = Join-Path $RepoWiseHome 'repowise-ifx'
$MetricsSource = Join-Path $RepoWiseHome 'repowise-workspace-metrics'
$RepoWiseVenv = Join-Path $RepoWiseHome '.venv'
$RepoWisePython = Join-Path $RepoWiseVenv 'Scripts\python.exe'
$RepoWiseCommand = Join-Path $RepoWiseVenv 'Scripts\repowise.exe'

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,
        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList
    )

    & $Command @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE."
    }
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'git is required.'
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required (https://docs.astral.sh/uv/).'
}
New-Item -ItemType Directory -Force -Path $RepoWiseHome | Out-Null
$GitDirectory = Join-Path $RepoWiseSource '.git'

if (Test-Path -LiteralPath $GitDirectory) {
    $OriginLines = & git -C $RepoWiseSource remote get-url origin
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read the origin URL for $RepoWiseSource."
    }
    $Origin = ($OriginLines | Out-String).Trim()
    if ($Origin -notin @($RepoWiseUrl, $RepoWiseSshUrl)) {
        throw "Unexpected origin for existing checkout at ${RepoWiseSource}: $Origin"
    }

    $LocalChanges = & git -C $RepoWiseSource status --porcelain
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect the existing checkout at $RepoWiseSource."
    }
    if ($LocalChanges) {
        throw "Checkout has local changes; commit or stash them before continuing: $RepoWiseSource"
    }

}
else {
    if (Test-Path -LiteralPath $RepoWiseSource) {
        throw "Path exists but is not a Repowise git checkout: $RepoWiseSource"
    }
    Invoke-Checked -Command 'git' -ArgumentList @(
        'clone', '--branch', $RepoWiseBranch, '--single-branch',
        $RepoWiseUrl, $RepoWiseSource
    )
}

if (Test-Path -LiteralPath (Join-Path $MetricsSource '.git')) {
    $MetricsOriginLines = & git -C $MetricsSource remote get-url origin
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read the origin URL for $MetricsSource."
    }
    $MetricsOrigin = ($MetricsOriginLines | Out-String).Trim()
    if ($MetricsOrigin -notin @($MetricsRepoUrl, $MetricsRepoSshUrl)) {
        throw "Unexpected origin for existing extension checkout at ${MetricsSource}: $MetricsOrigin"
    }
    $MetricsLocalChanges = & git -C $MetricsSource status --porcelain
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect the extension checkout at $MetricsSource."
    }
    if ($MetricsLocalChanges) {
        throw "Extension checkout has local changes; commit or stash them before continuing: $MetricsSource"
    }
}
else {
    if (Test-Path -LiteralPath $MetricsSource) {
        throw "Path exists but is not an extension git checkout: $MetricsSource"
    }
    Invoke-Checked -Command 'git' -ArgumentList @(
        'clone', '--branch', $MetricsBranch, '--single-branch',
        $MetricsRepoUrl, $MetricsSource
    )
}

if ($Update) {
    Invoke-Checked -Command 'git' -ArgumentList @(
        '-C', $RepoWiseSource, 'fetch', 'origin', $RepoWiseBranch
    )
    Invoke-Checked -Command 'git' -ArgumentList @(
        '-C', $MetricsSource, 'fetch', 'origin', $MetricsBranch
    )
    Invoke-Checked -Command 'git' -ArgumentList @(
        '-C', $RepoWiseSource, 'merge-base', '--is-ancestor', 'HEAD', "origin/$RepoWiseBranch"
    )
    Invoke-Checked -Command 'git' -ArgumentList @(
        '-C', $MetricsSource, 'merge-base', '--is-ancestor', 'HEAD', "origin/$MetricsBranch"
    )
    Invoke-Checked -Command 'git' -ArgumentList @(
        '-C', $RepoWiseSource, 'merge', '--ff-only', "origin/$RepoWiseBranch"
    )
    Invoke-Checked -Command 'git' -ArgumentList @(
        '-C', $MetricsSource, 'merge', '--ff-only', "origin/$MetricsBranch"
    )
}
if (-not (Test-Path -LiteralPath (Join-Path $MetricsSource 'pyproject.toml'))) {
    throw "Workspace-metrics project file was not found in $MetricsSource."
}

if (-not (Test-Path -LiteralPath $RepoWisePython)) {
    Invoke-Checked -Command 'uv' -ArgumentList @(
        'venv', '--python', '3.11', $RepoWiseVenv
    )
}

Invoke-Checked -Command 'uv' -ArgumentList @(
    'pip', 'install', '--python', $RepoWisePython,
    '--reinstall-package', 'repowise',
    '--reinstall-package', 'repowise-workspace-metrics',
    $RepoWiseSource, $MetricsSource
)

$CompatibilityCheck = @'
from importlib.metadata import version

installed = version("repowise").split("+", 1)[0]
if installed != "0.61.0":
    raise SystemExit(
        f"workspace-metrics requires Repowise 0.61.0; found {installed}. "
        "The beta update is not compatible with this extension revision."
    )

try:
    from repowise.core.repo_config import analysis_paths
    from repowise.core.test_paths import is_test_related_path
except ImportError as exc:
    raise SystemExit(f"Repowise beta is missing required metrics helpers: {exc}") from exc
'@
Invoke-Checked -Command $RepoWisePython -ArgumentList @('-c', $CompatibilityCheck)
Invoke-Checked -Command $RepoWiseCommand -ArgumentList @('plugins')
Invoke-Checked -Command $RepoWiseCommand -ArgumentList @('workspace-metrics', '--help')

$InstalledCommitLines = & git -C $RepoWiseSource rev-parse --short HEAD
if ($LASTEXITCODE -ne 0) {
    throw "Could not read the installed Repowise commit in $RepoWiseSource."
}
$InstalledCommit = ($InstalledCommitLines | Out-String).Trim()
$MetricsInstalledCommitLines = & git -C $MetricsSource rev-parse --short HEAD
if ($LASTEXITCODE -ne 0) {
    throw "Could not read the installed extension commit in $MetricsSource."
}
$MetricsInstalledCommit = ($MetricsInstalledCommitLines | Out-String).Trim()

Write-Host ''
Write-Host "Installed Repowise at $InstalledCommit"
Write-Host "Installed workspace-metrics at $MetricsInstalledCommit"
Write-Host "Environment: $RepoWiseVenv"
Write-Host "Run commands with: $RepoWiseCommand"
