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

    # The global preset (u59-preset.json) instead of one title's: every game that has no title preset
    # of its own, and as the base the title presets are applied over.
    [switch] $Global,

    [string] $TitleId = 'PPSA26344'
)

$ErrorActionPreference = 'Stop'

function Fail([string] $Message) {
    Write-Error $Message
    exit 1
}

$emulator = (Resolve-Path -LiteralPath $EmulatorDir).Path
$presetName = if ($Global) { 'u59-preset.json' } else { "u59-preset.$TitleId.json" }
$target   = Join-Path $emulator $presetName
$backup   = "$target.profile-backup"
$presets  = Join-Path $PSScriptRoot '..\presets'

if ($Restore) {
    if (-not (Test-Path -LiteralPath $backup)) {
        # The caller may have applied the other preset (global vs one title): restore whichever
        # single backup is there instead of making the flag mandatory.
        $found = @(Get-ChildItem -LiteralPath $emulator -Filter 'u59-preset*.json.profile-backup' -File)
        if ($found.Count -ne 1) {
            Fail "no single profile backup in $emulator; pass -Global or check the folder"
        }
        $backup = $found[0].FullName
        $target = $backup.Substring(0, $backup.Length - '.profile-backup'.Length)
    }
    Copy-Item -LiteralPath $backup -Destination $target -Force
    Remove-Item -LiteralPath $backup -Force
    Write-Host "Restored the previous $(Split-Path -Leaf $target)."
    exit 0
}

$source = if ($Profile -eq 'fast') {
    Join-Path $presets $(if ($Global) { 'u59-preset.fast.json' } else { "u59-preset.$TitleId.fast.json" })
} else {
    Join-Path $presets $presetName
}
if (-not (Test-Path -LiteralPath $source)) {
    Fail "profile file not found: $source"
}
if (-not (Test-Path -LiteralPath $target)) {
    Fail "the emulator has no $presetName to replace: $target"
}
if (Test-Path -LiteralPath $backup) {
    Fail "a profile is already applied (backup at $backup); run -Restore first"
}

# Refuse a preset that is not valid JSON before it breaks the next launch.
$null = Get-Content -LiteralPath $source -Raw -Encoding utf8 | ConvertFrom-Json

Copy-Item -LiteralPath $target -Destination $backup
Copy-Item -LiteralPath $source -Destination $target -Force
Write-Host "Applied profile '$Profile' for $presetName."
Write-Host "  previous preset: $backup"
Write-Host "  install with    : .\echops_profile.ps1 -EmulatorDir '$EmulatorDir' -Restore"
