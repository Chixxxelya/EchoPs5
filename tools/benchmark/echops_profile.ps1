#Requires -Version 7.0
<#
.SYNOPSIS
    Switch Ghost of Yotei's title preset between the shipped profile and the aggressive one.

.DESCRIPTION
    The emulator loads u59-preset.<title id>.json from its own folder, so a profile is that file.
    This helper installs a profile from tools\presets and keeps the previous file as
    u59-preset.<title id>.json.profile-backup:

        .\echops_profile.ps1 -EmulatorDir 'D:\workPs' -Profile fast
        <play, measure>
        .\echops_profile.ps1 -EmulatorDir 'D:\workPs' -Restore

    "fast" trades picture correctness for frame time: relaxed GPU hazards, a shortened pipeline-skip
    wait, more command batching and occlusion results biased by skipped draws. It is an experiment,
    not a recommendation: compare it against the shipped profile on the same route, and keep the
    shipped one if the picture or pacing gets worse.
#>
[CmdletBinding(DefaultParameterSetName = 'Apply')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Apply')]
    [Parameter(Mandatory, ParameterSetName = 'Restore')]
    [string] $EmulatorDir,

    [Parameter(Mandatory, ParameterSetName = 'Apply')]
    [ValidateSet('shipped', 'fast')]
    [string] $Profile,

    [Parameter(Mandatory, ParameterSetName = 'Restore')]
    [switch] $Restore,

    [string] $TitleId = 'PPSA26344'
)

$ErrorActionPreference = 'Stop'

function Fail([string] $Message) {
    Write-Error $Message
    exit 1
}

$emulator = (Resolve-Path -LiteralPath $EmulatorDir).Path
$target   = Join-Path $emulator "u59-preset.$TitleId.json"
$backup   = "$target.profile-backup"
$presets  = Join-Path $PSScriptRoot '..\presets'

if ($Restore) {
    if (-not (Test-Path -LiteralPath $backup)) {
        Fail "no profile backup at $backup; nothing to restore"
    }
    Copy-Item -LiteralPath $backup -Destination $target -Force
    Remove-Item -LiteralPath $backup -Force
    Write-Host "Restored the previous $TitleId preset."
    exit 0
}

$source = if ($Profile -eq 'fast') {
    Join-Path $presets "u59-preset.$TitleId.fast.json"
} else {
    Join-Path $presets "u59-preset.$TitleId.json"
}
if (-not (Test-Path -LiteralPath $source)) {
    Fail "profile file not found: $source"
}
if (-not (Test-Path -LiteralPath $target)) {
    Fail "the emulator has no $TitleId preset to replace: $target"
}
if (Test-Path -LiteralPath $backup) {
    Fail "a profile is already applied (backup at $backup); run -Restore first"
}

# Refuse a preset that is not valid JSON before it breaks the next launch.
$null = Get-Content -LiteralPath $source -Raw -Encoding utf8 | ConvertFrom-Json

Copy-Item -LiteralPath $target -Destination $backup
Copy-Item -LiteralPath $source -Destination $target -Force
Write-Host "Applied profile '$Profile' for $TitleId."
Write-Host "  previous preset: $backup"
Write-Host "  install with    : .\echops_profile.ps1 -EmulatorDir '$EmulatorDir' -Restore"
