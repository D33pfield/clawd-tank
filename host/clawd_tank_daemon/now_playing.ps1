# now_playing.ps1: what is playing on Windows, for the Clawd daemon running in WSL.
#
# Started once by the daemon (now_playing.py) as a long-running powershell.exe child.
# Reads the System Media Transport Controls (any player that shows up in the Windows
# media flyout: Spotify, browsers, foobar, ...) every couple of seconds, in process, and
# writes one compact JSON line to stdout only when the state changes, plus the same line
# again every $Heartbeat seconds so a write to a dead pipe is noticed:
#
#   {"s":"playing","a":"<artist>","t":"<title>"}     s: playing | paused | stopped | none
#   {"err":"<message>"}                               then exits non-zero (no WinRT, etc.)
#
# It exits when its stdin closes (the daemon went away) or when stdout can't be written.
param([double]$Interval = 2, [int]$Heartbeat = 60)

$ErrorActionPreference = 'Stop'
# The pipe would otherwise get the OEM code page and mangle non-ASCII titles.
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false

function Emit([string]$line) {
    try {
        [Console]::Out.WriteLine($line)
        [Console]::Out.Flush()
    } catch {
        [Environment]::Exit(0)  # the reader is gone
    }
}

# Exit as soon as the daemon closes our stdin (or dies): a background runspace blocks on it.
$watch = [PowerShell]::Create()
[void]$watch.AddScript({ try { [void][Console]::In.ReadToEnd() } catch { }; [Environment]::Exit(0) })
[void]$watch.BeginInvoke()

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    $mgrType = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
    $propsType = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties, Windows.Media.Control, ContentType = WindowsRuntime]
} catch {
    Emit (@{ err = "WinRT media controls unavailable: $($_.Exception.Message)" } | ConvertTo-Json -Compress)
    exit 2
}

function Await($op, [Type]$type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    if (-not $task.Wait(5000)) { throw 'timed out' }
    $task.Result
}

function Read-State($mgr) {
    $pick = $null
    $current = $mgr.GetCurrentSession()
    # A paused tab can be "current" while another player is actually playing: prefer a playing one.
    foreach ($s in @($mgr.GetSessions())) {
        if ($s -and $s.GetPlaybackInfo().PlaybackStatus -eq 'Playing') { $pick = $s; break }
    }
    if (-not $pick) { $pick = $current }
    if (-not $pick) { return @{ s = 'none'; a = ''; t = '' } }
    $status = [string]$pick.GetPlaybackInfo().PlaybackStatus
    $state = switch ($status) { 'Playing' { 'playing' } 'Paused' { 'paused' } default { 'stopped' } }
    $props = Await ($pick.TryGetMediaPropertiesAsync()) $propsType
    $artist = [string]$props.Artist
    if (-not $artist) { $artist = [string]$props.AlbumArtist }
    # Keep the line short; the daemon trims and caps again.
    if ($artist.Length -gt 200) { $artist = $artist.Substring(0, 200) }
    $title = [string]$props.Title
    if ($title.Length -gt 200) { $title = $title.Substring(0, 200) }
    @{ s = $state; a = $artist; t = $title }
}

$mgr = $null
$last = ''
$lastAt = [DateTime]::MinValue
$fails = 0
while ($true) {
    try {
        if (-not $mgr) { $mgr = Await ($mgrType::RequestAsync()) $mgrType }
        $st = Read-State $mgr
        $fails = 0
    } catch {
        # Explorer restarts or a player vanishing mid-read: get a fresh manager next time.
        $mgr = $null
        $fails++
        $st = if ($fails -ge 3) { @{ s = 'none'; a = ''; t = '' } } else { $null }
    }
    if ($st) {
        $line = [ordered]@{ s = $st.s; a = $st.a; t = $st.t } | ConvertTo-Json -Compress
        $now = [DateTime]::UtcNow
        if ($line -ne $last -or ($now - $lastAt).TotalSeconds -ge $Heartbeat) {
            Emit $line
            $last = $line
            $lastAt = $now
        }
    }
    Start-Sleep -Milliseconds ([int]($Interval * 1000))
}
