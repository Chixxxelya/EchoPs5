#Requires -Version 7.0
<#
.SYNOPSIS
    Start and finish one repeatable EchoPS/KytyPS5 benchmark run.

.DESCRIPTION
    The launcher applies the bundled presets itself, so an environment variable set in the shell is
    not reliably seen by the game. This helper edits the title preset instead, keeps a backup, and
    restores it afterwards:

        .\echops_bench_run.ps1 -Action Start -EmulatorDir 'D:\workPs' -Tag baseline
        <start Ghost of Yotei, play the recorded route, exit the game>
        .\echops_bench_run.ps1 -Action Finish -EmulatorDir 'D:\workPs' -Tag baseline

    The run writes _Benchmark\<tag>.frames.csv, .pace.csv and .pace.csv.waits.csv, and Finish turns
    them into <tag>.json plus a printed summary. Nothing else is changed, and the preset is restored
    from its backup even if the run produced no files.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('Start', 'Finish')]
    [string] $Action,

    [Parameter(Mandatory)]
    [string] $EmulatorDir,

    [Parameter(Mandatory)]
    [string] $Tag,

    [string] $TitleId = 'PPSA26344',

    # Optional recorded pad route (KYTY_TEST_PAD_RECORD output).
    [string] $Replay,

    # Long compile spans go to the same .waits.csv. Off by default: it adds logging work.
    [switch] $StallTrace,

    [string] $Python
)

$ErrorActionPreference = 'Stop'

function Fail([string] $Message) {
    Write-Error $Message
    exit 1
}

$emulator = (Resolve-Path -LiteralPath $EmulatorDir).Path
if (-not (Test-Path -LiteralPath (Join-Path $emulator 'kyty_emulator.exe'))) {
    Fail "kyty_emulator.exe not found in $emulator"
}

$presetPath = Join-Path $emulator "u59-preset.$TitleId.json"
if (-not (Test-Path -LiteralPath $presetPath)) {
    Fail "title preset not found: $presetPath"
}
$backupPath = "$presetPath.bench-backup"
$benchDir   = Join-Path $emulator '_Benchmark'
$statePath  = Join-Path $benchDir 'active-run.json'

function Read-Preset([string] $Path) {
    $text = Get-Content -LiteralPath $Path -Raw -Encoding utf8
    if ([string]::IsNullOrWhiteSpace($text)) { Fail "empty preset: $Path" }
    return $text | ConvertFrom-Json -AsHashtable
}

function Write-Preset([string] $Path, [hashtable] $Preset) {
    # Depth 5 keeps nested values; the file is restored from its backup afterwards anyway.
    $Preset | ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath $Path -Encoding utf8 -NoNewline
}

switch ($Action) {
    'Start' {
        if (Test-Path -LiteralPath $backupPath) {
            Fail ("a benchmark run is already started for $TitleId; finish it first " +
                  "(backup: $backupPath)")
        }
        New-Item -ItemType Directory -Path $benchDir -Force | Out-Null
        Copy-Item -LiteralPath $presetPath -Destination $backupPath

        $preset = Read-Preset $presetPath
        $frameLog = Join-Path $benchDir "$Tag.frames.csv"
        $paceLog  = Join-Path $benchDir "$Tag.pace.csv"

        # Relative paths resolve against the emulator's working folder, which is its own.
        $preset['KYTY_FRAME_TIMES_LOG'] = "_Benchmark\$Tag.frames.csv"
        $preset['KYTY_PACE_LOG']        = "_Benchmark\$Tag.pace.csv"
        $preset['KYTY_GPU_TIMING']      = '1'
        if ($StallTrace) {
            $preset['KYTY_STALL_TRACE'] = '1'
        }
        if ($Replay) {
            $replayPath = (Resolve-Path -LiteralPath $Replay).Path
            $preset['KYTY_TEST_PAD_REPLAY'] = $replayPath
        }
        Write-Preset $presetPath $preset

        @{
            tag        = $Tag
            title_id   = $TitleId
            frames     = $frameLog
            pace       = $paceLog
            replay     = if ($Replay) { (Resolve-Path -LiteralPath $Replay).Path } else { $null }
            started_at = (Get-Date).ToString('o')
        } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $statePath -Encoding utf8

        Write-Host "Benchmark '$Tag' armed for $TitleId."
        Write-Host "  preset backup : $backupPath"
        Write-Host "  frame log     : $frameLog"
        Write-Host "  pace log      : $paceLog"
        if (-not $Replay) {
            Write-Warning 'No -Replay route: the scene is not reproducible between runs. Record one with KYTY_TEST_PAD_RECORD first.'
        }
        Write-Host ''
        Write-Host 'Now start Ghost of Yotei from the launcher, play the route, then exit the game.'
        Write-Host "Then run: .\echops_bench_run.ps1 -Action Finish -EmulatorDir '$EmulatorDir' -Tag '$Tag'"
    }

    'Finish' {
        if (-not (Test-Path -LiteralPath $backupPath)) {
            Fail "no start backup found at $backupPath; nothing to finish"
        }
        Copy-Item -LiteralPath $backupPath -Destination $presetPath -Force
        Remove-Item -LiteralPath $backupPath -Force

        $state = $null
        if (Test-Path -LiteralPath $statePath) {
            $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
            Remove-Item -LiteralPath $statePath -Force
        }

        $frames = if ($state) { $state.frames } else { Join-Path $benchDir "$Tag.frames.csv" }
        $pace   = if ($state) { $state.pace }   else { Join-Path $benchDir "$Tag.pace.csv" }
        $waits  = "$pace.waits.csv"
        if (-not (Test-Path -LiteralPath $frames)) {
            Write-Warning "preset restored, but no frame log was written: $frames"
            Write-Warning 'The game probably did not reach gameplay, or it exited before the first flip.'
            exit 0
        }

        $analyzer = Join-Path $PSScriptRoot 'kyty_bench.py'
        if (-not (Test-Path -LiteralPath $analyzer)) {
            Fail "analyzer not found next to this script: $analyzer"
        }
        if (-not $Python) {
            $Python = (Get-Command py, python -ErrorAction SilentlyContinue | Select-Object -First 1).Source
        }
        if (-not $Python) { Fail 'Python 3 not found; pass -Python <path to python.exe>' }

        $report = Join-Path $benchDir "$Tag.json"
        $arguments = @($analyzer, 'analyze', '--frames', $frames, '--label', $Tag, '--output', $report)
        if (Test-Path -LiteralPath $pace)  { $arguments += @('--pace', $pace) }
        if (Test-Path -LiteralPath $waits) { $arguments += @('--waits', $waits) }
        $emulatorLog = Join-Path $emulator '_kyty.txt'
        if (Test-Path -LiteralPath $emulatorLog) { $arguments += @('--emulator-log', $emulatorLog) }

        & $Python @arguments
        if ($LASTEXITCODE -ne 0) { Fail "analyzer failed with exit code $LASTEXITCODE" }
        Write-Host ''
        Write-Host "Preset restored. Report: $report"
    }
}
