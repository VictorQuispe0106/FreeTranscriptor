<#
.SYNOPSIS
    Turns Windows Live Captions into a Markdown transcript.

.DESCRIPTION
    Windows Live Captions (Win+Ctrl+L) already transcribes whatever is playing
    through the speakers. This script reads that window through UI Automation and
    appends every new caption line to a Markdown file.

    No audio capture, no cloud, no driver needed: it works even when the WASAPI
    capture endpoints of this machine are broken.

    This file is deliberately ASCII-only: Windows PowerShell 5.1 reads .ps1 files
    using the ANSI codepage unless a UTF-8 BOM is present.

.PARAMETER Out
    Markdown file to append to. Defaults to transcripts\<timestamp>_live-captions.md

.PARAMETER IntervalMs
    Polling interval. Live Captions keeps a rolling buffer, so do not go much above 800.

.PARAMETER CaptionsPid
    Pin a specific LiveCaptions.exe process instead of auto-detecting it.

.PARAMETER Dump
    Print the current caption buffer once and exit (no file writes).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File captions_watch.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File captions_watch.ps1 -Dump
#>
param(
    [string]$Out,
    [int]$IntervalMs = 250,
    [int]$CaptionsPid = 0,
    [switch]$Dump,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
# Redirected stdout in PowerShell 5.1 defaults to the console OEM codepage;
# Python decodes our stdout as UTF-8, so pin UTF-8 here or accents arrive
# replaced with U+FFFD. Harmless for interactive use.
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$AE = [System.Windows.Automation.AutomationElement]
$TS = [System.Windows.Automation.TreeScope]
$CT = [System.Windows.Automation.ControlType]
$Utf8 = New-Object System.Text.UTF8Encoding($false)
$EmDash = [string][char]0x2014

function Get-CaptionsWindow {
    param([int]$TargetPid)

    if ($TargetPid -gt 0) {
        $condition = New-Object System.Windows.Automation.PropertyCondition($AE::ProcessIdProperty, $TargetPid)
        return $AE::RootElement.FindFirst($TS::Children, $condition)
    }
    $process = Get-Process -Name LiveCaptions -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $process) { return $null }
    $condition = New-Object System.Windows.Automation.PropertyCondition($AE::ProcessIdProperty, $process.Id)
    return $AE::RootElement.FindFirst($TS::Children, $condition)
}

function Get-CaptionLines {
    param($Window)

    if (-not $Window) { return @() }
    $condition = New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty, $CT::Text)
    $elements = $Window.FindAll($TS::Descendants, $condition)

    # Live Captions renders the rolling buffer in a single Text element; keep the longest.
    $best = ''
    foreach ($element in $elements) {
        $value = $element.Current.Name
        if (-not $value) {
            try {
                $pattern = $element.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
                $value = $pattern.DocumentRange.GetText(20000)
            } catch { }
        }
        if ($value -and $value.Length -gt $best.Length) { $best = $value }
    }
    if (-not $best) { return @() }

    return @($best -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Get-NewLines {
    <#
        Live Captions keeps a rolling buffer that scrolls and revises its last line.
        Overlap the consumed tail with the settled head, and never emit the last line
        because it may still be growing.
    #>
    param([string[]]$Consumed, [string[]]$Lines)

    if (-not $Lines -or $Lines.Count -lt 2) { return @() }
    $settled = @($Lines[0..($Lines.Count - 2)])
    if (-not $settled.Count) { return @() }

    $limit = [Math]::Min($Consumed.Count, $settled.Count)
    $overlap = 0
    for ($size = $limit; $size -ge 1; $size--) {
        $matches = $true
        for ($index = 0; $index -lt $size; $index++) {
            if ($Consumed[$Consumed.Count - $size + $index] -ne $settled[$index]) { $matches = $false; break }
        }
        if ($matches) { $overlap = $size; break }
    }

    if ($overlap -ge $settled.Count) { return @() }
    return @($settled[$overlap..($settled.Count - 1)])
}

# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

if ($Dump) {
    $window = Get-CaptionsWindow -TargetPid $CaptionsPid
    if (-not $window) { Write-Error 'Live Captions is not running. Start it with Win+Ctrl+L.'; exit 1 }
    $lines = Get-CaptionLines -Window $window
    if (-not $lines) { 'no captions yet'; exit 0 }
    $lines | ForEach-Object { $_ }
    exit 0
}

if (-not $Out) {
    $stampName = Get-Date -Format 'yyyy-MM-dd_HHmmss'
    $Out = Join-Path (Join-Path (Get-Location) 'transcripts') ($stampName + '_live-captions.md')
}
$Out = [System.IO.Path]::GetFullPath($Out)
$directory = [System.IO.Path]::GetDirectoryName($Out)
if (-not (Test-Path $directory)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }

$window = Get-CaptionsWindow -TargetPid $CaptionsPid
if (-not $window) {
    Write-Error 'Live Captions is not running. Start it with Win+Ctrl+L, join the meeting, then rerun.'
    exit 1
}

# A restart (the app offers this when the watcher dies) appends to the same
# file: the header is written only for a new transcript.
$headerLines = @(
    ('# Transcript en vivo ' + $EmDash + ' ' + (Get-Date -Format 'yyyy-MM-dd HH:mm')),
    '',
    '- **Fuente**: Windows Live Captions (UI Automation)',
    ('- **Archivo**: ' + $Out),
    '',
    '---',
    ''
)
$appendMode = (Test-Path $Out) -and ((Get-Item $Out).Length -gt 0)
if (-not $appendMode) {
    [System.IO.File]::WriteAllText($Out, ($headerLines -join "`n") + "`n", $Utf8)
}

$consumed = New-Object System.Collections.Generic.List[string]
if ($appendMode) {
    # Seed the dedupe buffer from what the file already holds, so the first
    # reading of the restarted watcher does not re-emit the settled tail.
    Get-Content -LiteralPath $Out | ForEach-Object {
        if ($_ -match '^- \*\*\[[0-9:.,]+\]\*\* (.*)$') { [void]$consumed.Add($Matches[1]) }
    }
    while ($consumed.Count -gt 200) { $consumed.RemoveAt(0) }
    if (-not $Quiet) { Write-Host ('resume  : ' + $consumed.Count + ' line(s) already on file') }
}
elseif (-not $Quiet) {
    Write-Host 'source : Windows Live Captions (UI Automation)'
    Write-Host ('output : ' + $Out)
}

if (-not $Quiet) {
    Write-Host 'source : Windows Live Captions (UI Automation)'
    Write-Host ('output : ' + $Out)
    Write-Host "reading - Ctrl+C to stop`n"
}

$emitted = 0
$lastTail = $null
$misses = 0

try {
    while ($true) {
        try {
            if (-not $window) { $window = Get-CaptionsWindow -TargetPid $CaptionsPid }
            $lines = Get-CaptionLines -Window $window
        } catch {
            # UIA invalidates the element when the overlay redraws; re-acquire next tick.
            $window = $null
            $lines = @()
        }

        if ($lines.Count) { $misses = 0 }
        else {
            # The window disappeared (closed, crashed, pin removed): after a
            # bounded number of empty reads, die so the app can offer a
            # restart instead of spinning silently forever.
            $misses++
            if ($misses -gt 60) {
                # Write-Host, NOT Write-Error: with ErrorActionPreference=Stop
                # a Write-Error becomes a terminating error and the process
                # dies with exit 1, never reaching this exit 2 that the app
                # maps to its restart offer.
                Write-Host 'Live Captions window lost - stopping (app may restart).'
                exit 2
            }
        }

        foreach ($line in (Get-NewLines -Consumed $consumed.ToArray() -Lines $lines)) {
            # S4: the wall-clock stamp, not elapsed time, so the first batch
            # carries the real time instead of a burst of 00:00:00.
            $text = '- **[{0:HH\:mm\:ss}]** {1}' -f (Get-Date), $line
            [System.IO.File]::AppendAllText($Out, $text + "`n", $Utf8)
            if (-not $Quiet) { Write-Host $text }
            $consumed.Add($line)
            $emitted++
            while ($consumed.Count -gt 200) { $consumed.RemoveAt(0) }
        }

        # Live tail: the still-growing last line goes to stdout only, never to
        # the file, so the app can show speech the moment it is heard.
        if ($lines.Count -gt 0) {
            $tail = $lines[$lines.Count - 1]
            if ($tail -and -not $consumed.Contains($tail) -and $tail -ne $lastTail) {
                $lastTail = $tail
                if (-not $Quiet) { Write-Output ('> ' + $tail) }
            }
        }

        Start-Sleep -Milliseconds $IntervalMs
    }
} finally {
    Write-Host ('{0} line(s) -> {1}' -f $emitted, $Out)
}
