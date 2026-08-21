# Axiom staged-apply hand-off. Ordinary updates use
# scripts/desktop-update/windows.ps1; this path retains the pinned manifest,
# package adoption, and rollback contract used by Desktop Update Control.
# WHY THIS EXISTS (the frozen-binary problem): the Desktop's Update button
# used to hand off exclusively to the staged Tauri binary
# (%HERMES_HOME%\hermes-setup.exe). That binary has no self-update path --
# copy_self_to_hermes_home deliberately no-ops during --update -- so every
# updater-side fix (cache refresh #67369, marker self-adopt #74782, straggler
# handling) only reaches users when a new installer is built, signed, and
# published. In practice binaries go months stale and users hit long-fixed
# bugs on every update (the 2026-08-09 incident chain).
#
# This script lives in the repo checkout, so EVERY `hermes update` refreshes
# the very code that drives the next update. The Desktop spawns it through a
# `cmd start` wrapper (see wrapHandoffForDetachedConsole in
# apps/desktop/electron/updater-process.ts -- a bare detached+hidden
# powershell dies before -File runs) and exits; only PowerShell itself -- an
# OS component -- is "frozen".
#
# CONTRACT (keep in sync with apps/desktop/electron/main.ts):
#   cmd /d /s /c start "" powershell -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass
#     -File scripts\desktop-update.ps1
#     -InstallRoot <path>   repo checkout (HERMES_HOME\hermes-agent)
#     -Branch <ref>         branch to update against
#     -DesktopPid <pid>     the Electron main process to wait out
#     [-RelaunchExe <path>] Hermes.exe to start when done (omit = no relaunch)
#     [-StageManifest <path>] stage prepared while Desktop remained open
#     [-NoUi]               headless (tests); default shows a progress window
#     [-NoMarkerCleanup]    leave .hermes-update-in-progress in place (tests)
#
# SAFETY POSTURE: both preflight gates FAIL CLOSED. A Desktop that never
# exits, or a venv shim that never unlocks, aborts the hand-off without
# mutating the install -- a skipped update is recoverable, a half-updated
# venv is not. Every exit path (success, abort, crash) writes
# .hermes-update-result.json for the relaunched Desktop to surface, and
# relaunches the Desktop so the user is never left stranded.
#
# Marker: we claim HERMES_HOME\.hermes-update-in-progress with OUR pid as
# step 0 (the wrapper cmd.exe pid the Desktop saw is useless -- it exits
# immediately). hermes_cli/update_lock.py's ancestry rule lets our
# `hermes update` child adopt the claim; electron/update-marker.ts parks a
# relaunched Desktop on it. Cleanup only removes the marker while WE still
# own it (a handoff partner that rewrote it keeps its claim).

param(
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [string]$Branch = "main",
    [int]$DesktopPid = 0,
    [string]$RelaunchExe = "",
    [string]$StageManifest = "",
    [switch]$NoUi,
    [switch]$NoMarkerCleanup
)

$ErrorActionPreference = "Continue"
# Foreground helpers: the script is spawned via hidden `cmd start`, so its
# WinForms window does not inherit foreground rights unless we explicitly claim focus --
# and after the update we must hand focus TO the relaunched Desktop (a
# WMI-spawned process starts unfocused). AllowSetForegroundWindow lets us
# pass our foreground right on to the new Hermes.exe pid.
try {
    Add-Type -Namespace HermesHandoff -Name Win32 -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(System.IntPtr hWnd);
[DllImport("user32.dll")] public static extern bool AllowSetForegroundWindow(int dwProcessId);
[DllImport("user32.dll")] public static extern bool ShowWindow(System.IntPtr hWnd, int nCmdShow);
'@ -ErrorAction Stop
    $script:Win32 = $true
} catch { $script:Win32 = $false }
# Render UTF-8 glyphs (checkmarks, arrows) correctly in our own console echo
# too; the legacy conhost default OEM codepage shows them as mojibake.
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}
$HermesHome = Split-Path -Parent $InstallRoot
$MarkerPath = Join-Path $HermesHome ".hermes-update-in-progress"
$LogDir = Join-Path $HermesHome "logs"
$LogPath = Join-Path $LogDir "desktop-update-handoff.log"
$ResultPath = Join-Path $HermesHome ".hermes-update-result.json"
$script:Ui = $null
$script:StageSwapped = $false
$script:StageLiveDir = ""
$script:StageBackupDir = ""
$script:StageBuildStamp = Join-Path $HermesHome "desktop-build-stamp.json"
$script:StageBuildStampBackup = ""
$script:StageHadBuildStamp = $false
$script:StageData = $null
$script:AllowUiClose = $false

function Write-HandoffLog([string]$Message) {
    $line = "{0:yyyy-MM-ddTHH:mm:ssK} {1}" -f (Get-Date), $Message
    try { Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8 } catch {}
    Write-Host $line
    if ($script:Ui) {
        try {
            $script:Ui.Box.AppendText($Message + "`r`n")
            [System.Windows.Forms.Application]::DoEvents()
        } catch {}
    }
}

function Set-UpdateStatus([string]$Message) {
    if ($script:Ui) {
        try {
            $script:Ui.Status.Text = $Message
            [System.Windows.Forms.Application]::DoEvents()
        } catch {}
    }
}

function Set-DetailsExpanded([bool]$Expanded) {
    if (-not $script:Ui) { return }
    try {
        $script:Ui.Box.Visible = $Expanded
        $script:Ui.DetailsButton.Text = if ($Expanded) { "Hide terminal output" } else { "Show terminal output" }
        $script:Ui.Form.MinimumSize = New-Object System.Drawing.Size(620, $(if ($Expanded) { 360 } else { 218 }))
        $script:Ui.Form.Height = if ($Expanded) { 480 } else { 218 }
        [System.Windows.Forms.Application]::DoEvents()
    } catch {}
}

function Show-ProgressWindow {
    if ($NoUi) { return }
    try {
        Add-Type -AssemblyName System.Windows.Forms | Out-Null
        Add-Type -AssemblyName System.Drawing | Out-Null
        $form = New-Object System.Windows.Forms.Form
        $form.Text = "Hermes Update"
        $form.Size = New-Object System.Drawing.Size(760, 218)
        $form.MinimumSize = New-Object System.Drawing.Size(620, 218)
        $form.StartPosition = "CenterScreen"
        $form.ControlBox = $true
        $form.MinimizeBox = $true
        $form.MaximizeBox = $false
        $form.ShowInTaskbar = $true
        $form.TopMost = $false
        $form.BackColor = [System.Drawing.Color]::FromArgb(247, 248, 250)
        $form.Add_FormClosing({
            param($sender, $eventArgs)
            if (-not $script:AllowUiClose) {
                $eventArgs.Cancel = $true
            }
        })

        $header = New-Object System.Windows.Forms.Panel
        $header.Dock = "Top"
        $header.Height = 102
        $header.Padding = New-Object System.Windows.Forms.Padding(18, 14, 18, 8)
        $header.BackColor = [System.Drawing.Color]::White

        $title = New-Object System.Windows.Forms.Label
        $title.Text = "Hermes is updating"
        $title.Dock = "Top"
        $title.Height = 30
        $title.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 15)
        $title.ForeColor = [System.Drawing.Color]::FromArgb(30, 34, 42)

        $status = New-Object System.Windows.Forms.Label
        $status.Text = "Starting the secure update handoff..."
        $status.Dock = "Top"
        $status.Height = 28
        $status.Font = New-Object System.Drawing.Font("Segoe UI", 9.5)
        $status.ForeColor = [System.Drawing.Color]::FromArgb(80, 87, 99)

        $bar = New-Object System.Windows.Forms.ProgressBar
        $bar.Style = "Marquee"
        $bar.MarqueeAnimationSpeed = 30
        $bar.Dock = "Top"
        $bar.Height = 8

        $details = New-Object System.Windows.Forms.Button
        $details.Text = "Show terminal output"
        $details.Dock = "Top"
        $details.Height = 34
        $details.Padding = New-Object System.Windows.Forms.Padding(14, 0, 0, 0)
        $details.TextAlign = "MiddleLeft"
        $details.FlatStyle = "Flat"
        $details.FlatAppearance.BorderSize = 0
        $details.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 9)
        $details.ForeColor = [System.Drawing.Color]::FromArgb(80, 87, 99)
        $details.BackColor = [System.Drawing.Color]::White

        $box = New-Object System.Windows.Forms.TextBox
        $box.Multiline = $true
        $box.ReadOnly = $true
        $box.ScrollBars = "Vertical"
        $box.Dock = "Fill"
        $box.Font = New-Object System.Drawing.Font("Consolas", 9)
        $box.BackColor = [System.Drawing.Color]::FromArgb(251, 251, 252)
        $box.ForeColor = [System.Drawing.Color]::FromArgb(50, 55, 65)
        $box.BorderStyle = "FixedSingle"
        $box.Margin = New-Object System.Windows.Forms.Padding(18, 0, 18, 14)
        $box.Visible = $false

        $footer = New-Object System.Windows.Forms.Label
        $footer.Text = "You can minimize this window. Hermes will restart automatically when the update finishes."
        $footer.Dock = "Bottom"
        $footer.Height = 36
        $footer.Padding = New-Object System.Windows.Forms.Padding(18, 9, 0, 0)
        $footer.Font = New-Object System.Drawing.Font("Segoe UI", 8.5)
        $footer.ForeColor = [System.Drawing.Color]::FromArgb(100, 106, 117)

        $header.Controls.Add($bar)
        $header.Controls.Add($status)
        $header.Controls.Add($title)
        $form.Controls.Add($box)
        $form.Controls.Add($details)
        $form.Controls.Add($header)
        $form.Controls.Add($footer)
        $script:Ui = [pscustomobject]@{
            Form = $form
            Box = $box
            Status = $status
            DetailsButton = $details
        }
        $details.Add_Click({ Set-DetailsExpanded (-not $script:Ui.Box.Visible) })
        $form.Show()
        # The hidden console handoff has no foreground rights. Claim the form
        # once at launch, then leave normal window stacking/minimization alone.
        try {
            $form.Activate()
            if ($script:Win32) { [HermesHandoff.Win32]::SetForegroundWindow($form.Handle) | Out-Null }
        } catch {}
        [System.Windows.Forms.Application]::DoEvents()
    } catch {
        # Headless session / WinForms unavailable: degrade to log-only.
        $script:Ui = $null
    }
}

function Close-ProgressWindow {
    if ($script:Ui) {
        try {
            $script:AllowUiClose = $true
            $script:Ui.Form.Close()
        } catch {}
        $script:Ui = $null
    }
}

function Write-Result([bool]$Ok, [int]$Code, [string]$Message) {
    # Consumed (read + deleted) by the relaunched Desktop on boot so the
    # user actually SEES how a detached update ended.
    if (-not $Ok) { Set-DetailsExpanded $true }
    try {
        $obj = @{
            ok         = $Ok
            exit_code  = $Code
            message    = $Message
            branch     = $Branch
            finished_at = [int][double]::Parse((Get-Date -UFormat %s), [System.Globalization.CultureInfo]::InvariantCulture)
        } | ConvertTo-Json -Compress
        [System.IO.File]::WriteAllText($ResultPath, $obj)
    } catch {}
}

function Remove-MarkerIfOwned {
    if ($NoMarkerCleanup) { return }
    try {
        if (Test-Path -LiteralPath $MarkerPath) {
            $firstLine = (Get-Content -LiteralPath $MarkerPath -TotalCount 1 -ErrorAction SilentlyContinue)
            if ("$firstLine".Trim() -eq "$PID") {
                Remove-Item -LiteralPath $MarkerPath -Force -ErrorAction SilentlyContinue
                Write-HandoffLog "removed update marker (owned)"
            } else {
                Write-HandoffLog "leaving update marker: owned by pid '$firstLine', not us ($PID)"
            }
        }
    } catch {}
}

function Get-LiveDirtyFingerprint {
    $tracked = & git -c core.quotepath=false -C $InstallRoot diff --name-only HEAD -- 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect tracked live checkout changes." }
    $untracked = & git -c core.quotepath=false -C $InstallRoot ls-files --others --exclude-standard 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect untracked live checkout changes." }
    $summaryLines = & git -c core.quotepath=false -C $InstallRoot diff --summary HEAD -- 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect live checkout metadata changes." }
    $paths = New-Object 'System.Collections.Generic.SortedSet[string]' ([System.StringComparer]::Ordinal)
    foreach ($line in @($tracked) + @($untracked)) {
        if ("$line") { [void]$paths.Add("$line") }
    }
    $records = New-Object System.Collections.Generic.List[string]
    foreach ($relative in $paths) {
        $candidate = Join-Path $InstallRoot $relative
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $blobLines = & git -C $InstallRoot hash-object --no-filters -- $relative 2>&1
            if ($LASTEXITCODE -ne 0) { throw "Could not hash dirty path: $relative" }
            $blob = (@($blobLines)[-1]).ToString().Trim()
        } else {
            $blob = "deleted"
        }
        $records.Add("$relative`0$blob")
    }
    $summary = (@($summaryLines) -join "`n").Trim()
    $bytes = [System.Text.Encoding]::UTF8.GetBytes("$summary`n$($records -join "`n")")
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally { $sha.Dispose() }
}

function Get-FileSha256([string]$FilePath) {
    $stream = [System.IO.File]::Open($FilePath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace("-", "").ToLowerInvariant() } finally {
        $sha.Dispose()
        $stream.Dispose()
    }
}

function Get-ArtifactTreeHash([string]$Root) {
    $rootPath = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
    $records = New-Object System.Collections.Generic.List[string]
    foreach ($item in Get-ChildItem -LiteralPath $rootPath -Recurse -Force -ErrorAction Stop) {
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Staged package contains a reparse point: $($item.FullName)"
        }
        if ($item.PSIsContainer) { continue }
        $relative = $item.FullName.Substring($rootPath.Length).TrimStart('\').Replace('\', '/')
        $fileHash = Get-FileSha256 $item.FullName
        $records.Add("$relative`0$($item.Length)`0$fileHash")
    }
    $ordered = $records.ToArray()
    [Array]::Sort($ordered, [System.StringComparer]::Ordinal)
    $payload = [System.Text.Encoding]::UTF8.GetBytes(($ordered -join "`n"))
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($payload))).Replace("-", "").ToLowerInvariant() } finally { $sha.Dispose() }
}

function Restore-StagedRelease {
    if (-not $script:StageSwapped) { return }
    Write-HandoffLog "restoring the previous Desktop package after failed staged apply"
    try {
        if ($script:StageLiveDir -and (Test-Path -LiteralPath $script:StageLiveDir)) {
            Remove-Item -LiteralPath $script:StageLiveDir -Recurse -Force -ErrorAction Stop
        }
        if ($script:StageBackupDir -and (Test-Path -LiteralPath $script:StageBackupDir)) {
            Move-Item -LiteralPath $script:StageBackupDir -Destination $script:StageLiveDir -Force -ErrorAction Stop
        }
        if ($script:StageHadBuildStamp -and (Test-Path -LiteralPath $script:StageBuildStampBackup)) {
            Move-Item -LiteralPath $script:StageBuildStampBackup -Destination $script:StageBuildStamp -Force -ErrorAction Stop
        } elseif (Test-Path -LiteralPath $script:StageBuildStamp) {
            Remove-Item -LiteralPath $script:StageBuildStamp -Force -ErrorAction Stop
        }
        $script:StageSwapped = $false
        Write-HandoffLog "previous Desktop package restored"
    } catch {
        Write-HandoffLog "ERROR: could not restore previous Desktop package: $($_.Exception.Message)"
    }
}

function Start-DesktopRelaunch {
    if ($RelaunchExe -and (Test-Path -LiteralPath $RelaunchExe)) {
        Write-HandoffLog "relaunching desktop: $RelaunchExe"
        # DO NOT spawn Hermes.exe as our child: Electron/Chromium calls
        # AttachConsole(ATTACH_PARENT_PROCESS) at boot, so a Desktop launched
        # directly from this console PowerShell latches onto OUR console --
        # the console window then outlives the script (it can't close while
        # an attached process lives), and closing it kills the freshly
        # relaunched GUI with it. Create the process via WMI instead: the
        # parent becomes WmiPrvSE.exe and there is no console to inherit or
        # attach -- same detachment explorer.exe gives a normal launch.
        $spawned = $false
        try {
            $workDir = Split-Path -Parent $RelaunchExe
            $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
                CommandLine      = ('"{0}"' -f $RelaunchExe)
                CurrentDirectory = $workDir
            } -ErrorAction Stop
            if ($r -and $r.ReturnValue -eq 0) {
                Write-HandoffLog "desktop relaunched detached (pid $($r.ProcessId))"
                $spawned = $true
                # Hand our foreground rights to the new Desktop and focus its
                # main window once it exists. A WMI-spawned process starts
                # unfocused, and Windows only lets the CURRENT foreground
                # owner (us, while the progress window is up / just closed)
                # delegate that right. Poll briefly for the window: Electron
                # takes a couple seconds to create it.
                try {
                    if ($script:Win32) {
                        [HermesHandoff.Win32]::AllowSetForegroundWindow([int]$r.ProcessId) | Out-Null
                        $deadline = (Get-Date).AddSeconds(20)
                        while ((Get-Date) -lt $deadline) {
                            $hwnd = [System.IntPtr]::Zero
                            try {
                                $p = Get-Process -Id $r.ProcessId -ErrorAction Stop
                                $hwnd = $p.MainWindowHandle
                            } catch { break }  # process died; nothing to focus
                            if ($hwnd -ne [System.IntPtr]::Zero) {
                                [HermesHandoff.Win32]::ShowWindow($hwnd, 9) | Out-Null  # SW_RESTORE
                                [HermesHandoff.Win32]::SetForegroundWindow($hwnd) | Out-Null
                                Write-HandoffLog "focused relaunched desktop window"
                                break
                            }
                            Start-Sleep -Milliseconds 400
                        }
                    }
                } catch {
                    Write-HandoffLog "WARNING: could not focus relaunched desktop: $($_.Exception.Message)"
                }
            } else {
                Write-HandoffLog "WARNING: WMI relaunch returned $($r.ReturnValue); falling back"
            }
        } catch {
            Write-HandoffLog "WARNING: WMI relaunch failed: $($_.Exception.Message); falling back"
        }
        if (-not $spawned) {
            try {
                # Fallback keeps the old behavior (console tie-in and all) --
                # a tethered Desktop beats no Desktop.
                Start-Process -FilePath $RelaunchExe -WorkingDirectory (Split-Path -Parent $RelaunchExe) | Out-Null
            } catch {
                Write-HandoffLog "WARNING: desktop relaunch failed: $($_.Exception.Message)"
            }
        }
    }
}

function Invoke-StreamedHermes([string]$Exe, [string[]]$HermesArgs, [string]$Tag) {
    # Start-Process + output file + poll keeps the WinForms window pumping
    # during long silent stretches (pip installs); a blocking pipeline would
    # freeze the marquee. Returns @{ Code; Output }.
    $outFile = Join-Path $env:TEMP ("hermes-handoff-{0}-{1}.out" -f $Tag, $PID)
    $errFile = Join-Path $env:TEMP ("hermes-handoff-{0}-{1}.err" -f $Tag, $PID)
    Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
    # System.Diagnostics.Process directly: Start-Process's .ExitCode is
    # unreliably $null under PS 5.1 even with the Handle-touch workaround.
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    # .Arguments string (PS 5.1 / .NET Framework has no ArgumentList).
    # Args here are fixed flags + a branch ref; quote each defensively.
    $psi.Arguments = ($HermesArgs | ForEach-Object { '"{0}"' -f ($_ -replace '"', '\"') }) -join ' '
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    # hermes update prints UTF-8 (checkmarks, arrows, box glyphs). PS 5.1
    # defaults these readers to the OEM codepage, which mangles every
    # multi-byte glyph into mojibake in the console AND the progress box.
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    # And ask the child to actually EMIT UTF-8: Python decides its stdio
    # encoding from the console codepage when attached to one.
    $psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8"
    $psi.EnvironmentVariables["PYTHONUTF8"] = "1"
    $psi.CreateNoWindow = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    $outWriter = [System.IO.File]::CreateText($outFile)
    $errWriter = [System.IO.File]::CreateText($errFile)
    # Pump synchronously in small reads so the UI stays alive; stderr is
    # drained at the end (hermes update is stdout-dominant).
    while (-not $proc.HasExited) {
        while (-not $proc.StandardOutput.EndOfStream) {
            $ln = $proc.StandardOutput.ReadLine()
            if ($null -ne $ln) {
                $outWriter.WriteLine($ln)
                if ($ln.Trim()) { Write-HandoffLog ("{0}| {1}" -f $Tag, $ln) }
            }
            if ($script:Ui) { [System.Windows.Forms.Application]::DoEvents() }
        }
        Start-Sleep -Milliseconds 150
        if ($script:Ui) { [System.Windows.Forms.Application]::DoEvents() }
    }
    while (-not $proc.StandardOutput.EndOfStream) {
        $ln = $proc.StandardOutput.ReadLine()
        if ($null -ne $ln) {
            $outWriter.WriteLine($ln)
            if ($ln.Trim()) { Write-HandoffLog ("{0}| {1}" -f $Tag, $ln) }
        }
    }
    $errText = $proc.StandardError.ReadToEnd()
    if ($errText) {
        $errWriter.Write($errText)
        foreach ($ln in ($errText -split "`r?`n")) {
            if ($ln.Trim()) { Write-HandoffLog ("{0}!| {1}" -f $Tag, $ln) }
        }
    }
    $outWriter.Close(); $errWriter.Close()
    $proc.WaitForExit()
    $code = $proc.ExitCode
    $all = ""
    try { $all = [System.IO.File]::ReadAllText($outFile) } catch {}
    if ($errText) { $all += "`n" + $errText }
    Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
    return @{ Code = $code; Output = $all }
}

$finalCode = 1
$finalMsg = "update did not complete"
try {
    New-Item -ItemType Directory -Path $LogDir -Force -ErrorAction SilentlyContinue | Out-Null
    Remove-Item -LiteralPath $ResultPath -Force -ErrorAction SilentlyContinue
    Show-ProgressWindow
    Set-UpdateStatus "Preparing the update handoff..."
    Write-HandoffLog "hand-off start: root=$InstallRoot branch=$Branch desktopPid=$DesktopPid pid=$PID"

    # -- 0. Claim the update marker with OUR pid ---------------------------
    try {
        $epoch = [int][double]::Parse((Get-Date -UFormat %s), [System.Globalization.CultureInfo]::InvariantCulture)
        # WriteAllText for byte-exact LF framing: Set-Content emits CRLF and
        # the marker contract (Rust/TS/Python readers) is "<pid>\n<ts>\n".
        [System.IO.File]::WriteAllText($MarkerPath, "$PID`n$epoch`n")
        Write-HandoffLog "claimed update marker (pid $PID)"
    } catch {
        Write-HandoffLog "WARNING: could not write update marker: $($_.Exception.Message)"
    }

    # -- 1. Wait for the Desktop to exit (FAIL CLOSED) ----------------------
    if ($DesktopPid -gt 0) {
        Set-UpdateStatus "Waiting for Hermes to close safely..."
        $deadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $deadline) {
            $proc = Get-Process -Id $DesktopPid -ErrorAction SilentlyContinue
            if (-not $proc) { break }
            Start-Sleep -Milliseconds 300
            if ($script:Ui) { [System.Windows.Forms.Application]::DoEvents() }
        }
        if (Get-Process -Id $DesktopPid -ErrorAction SilentlyContinue) {
            # A live Desktop means a live backend re-locking the venv at any
            # moment. Updating under it is how installs brick. Abort.
            $finalCode = 4
            $finalMsg = "Update aborted: the Hermes window (pid $DesktopPid) did not exit within 30s. Nothing was changed. Close Hermes fully and try again."
            Write-HandoffLog $finalMsg
            exit $finalCode
        }
        Write-HandoffLog "desktop exited"
    }

    # -- 2. Wait for the venv shim to unlock (FAIL CLOSED) ------------------
    Set-UpdateStatus "Checking that the installation is ready..."
    $shim = Join-Path $InstallRoot "venv\Scripts\hermes.exe"
    if (Test-Path -LiteralPath $shim) {
        $unlocked = $false
        $deadline = (Get-Date).AddSeconds(20)
        while ((Get-Date) -lt $deadline) {
            try {
                $fs = [System.IO.File]::Open($shim, 'Open', 'ReadWrite', 'None')
                $fs.Close()
                $unlocked = $true
                break
            } catch {
                Start-Sleep -Milliseconds 400
                if ($script:Ui) { [System.Windows.Forms.Application]::DoEvents() }
            }
        }
        if (-not $unlocked) {
            # Something still maps the venv. --force-ing past it guarantees a
            # half-updated venv (the exact 2026-08-09 Access-denied brick).
            $finalCode = 5
            $finalMsg = "Update aborted: another process is still holding the Hermes install open (venv\Scripts\hermes.exe locked after 20s). Nothing was changed. Close other Hermes windows/terminals and try again."
            Write-HandoffLog $finalMsg
            exit $finalCode
        }
        Write-HandoffLog "venv shim unlocked"
    }

    # -- 3. Revalidate and adopt a prepared Desktop package -----------------
    # Electron validates before quitting so ordinary preflight failures leave
    # the app open. Repeat every check here after exit to close TOCTOU gaps.
    if ($StageManifest) {
        Set-UpdateStatus "Validating the prepared Desktop package..."
        $expectedStageRoot = Join-Path $HermesHome "update-stage\desktop"
        $expectedManifest = Join-Path $expectedStageRoot "stage.json"
        if (-not [string]::Equals(
            [System.IO.Path]::GetFullPath($StageManifest),
            [System.IO.Path]::GetFullPath($expectedManifest),
            [System.StringComparison]::OrdinalIgnoreCase
        )) { throw "Staged update manifest is outside the Hermes stage directory." }
        if (-not (Test-Path -LiteralPath $StageManifest)) { throw "Staged update manifest is missing." }
        try { $script:StageData = Get-Content -Raw -LiteralPath $StageManifest | ConvertFrom-Json } catch {
            throw "Staged update manifest is malformed. Prepare the update again."
        }
        if ($script:StageData.schemaVersion -ne 1) {
            throw "Staged update manifest is not ready. Prepare the update again."
        }
        if ($script:StageData.branch -ne $Branch) { throw "Staged branch does not match the requested update branch." }
        if (-not [string]::Equals(
            [System.IO.Path]::GetFullPath($script:StageData.installRoot),
            [System.IO.Path]::GetFullPath($InstallRoot),
            [System.StringComparison]::OrdinalIgnoreCase
        )) { throw "Staged install root does not match this Hermes installation." }

        $artifactDir = [System.IO.Path]::GetFullPath($script:StageData.artifactDir)
        $artifactExe = [System.IO.Path]::GetFullPath($script:StageData.artifactPath)
        $stageStamp = [System.IO.Path]::GetFullPath($script:StageData.buildStampPath)
        $stageWorktree = [System.IO.Path]::GetFullPath($script:StageData.worktree)
        $expectedWorktree = [System.IO.Path]::GetFullPath((Join-Path $expectedStageRoot "worktree"))
        $expectedArtifactDir = [System.IO.Path]::GetFullPath((Join-Path $expectedWorktree "apps\desktop\release\win-unpacked"))
        $expectedArtifactExe = [System.IO.Path]::GetFullPath((Join-Path $expectedArtifactDir "Hermes.exe"))
        $expectedStageStamp = [System.IO.Path]::GetFullPath((Join-Path $expectedStageRoot "desktop-build-stamp.json"))
        if (-not [string]::Equals($stageWorktree, $expectedWorktree, [System.StringComparison]::OrdinalIgnoreCase) -or
            -not [string]::Equals($artifactDir, $expectedArtifactDir, [System.StringComparison]::OrdinalIgnoreCase) -or
            -not [string]::Equals($artifactExe, $expectedArtifactExe, [System.StringComparison]::OrdinalIgnoreCase) -or
            -not [string]::Equals($stageStamp, $expectedStageStamp, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Staged update paths do not match the Hermes-owned stage layout."
        }
        if (-not (Test-Path -LiteralPath $artifactDir -PathType Container) -or
            -not (Test-Path -LiteralPath $artifactExe -PathType Leaf) -or
            -not (Test-Path -LiteralPath $stageStamp -PathType Leaf)) {
            throw "Staged Desktop package is incomplete. Prepare the update again."
        }

        $currentHead = (& git -C $InstallRoot rev-parse HEAD 2>&1 | Select-Object -Last 1).ToString().Trim()
        if ($LASTEXITCODE -ne 0 -or $currentHead -ne $script:StageData.baseSha) {
            throw "The live checkout changed after preparation. Prepare the update again."
        }
        Write-HandoffLog "refreshing origin/$Branch before staged apply"
        & git -C $InstallRoot fetch origin $Branch --prune 2>&1 | ForEach-Object { if ("$_".Trim()) { Write-HandoffLog "fetch| $_" } }
        if ($LASTEXITCODE -ne 0) { throw "Could not refresh origin/$Branch. Nothing was changed." }
        $remoteTarget = (& git -C $InstallRoot rev-parse "refs/remotes/origin/$Branch" 2>&1 | Select-Object -Last 1).ToString().Trim()
        if ($LASTEXITCODE -ne 0 -or $remoteTarget -ne $script:StageData.targetSha) {
            throw "origin/$Branch changed after preparation. Prepare the newer update before restarting."
        }
        if ((Get-LiveDirtyFingerprint) -ne $script:StageData.liveDirtyFingerprint) {
            throw "The live checkout's uncommitted changes changed after preparation. Prepare the update again."
        }
        $actualArtifactHash = Get-FileSha256 $artifactExe
        if ($actualArtifactHash -ne "$($script:StageData.artifactSha256)".ToLowerInvariant()) {
            throw "Staged Desktop package failed integrity validation. Prepare the update again."
        }
        $actualTreeHash = Get-ArtifactTreeHash $artifactDir
        if ($actualTreeHash -ne "$($script:StageData.artifactTreeSha256)".ToLowerInvariant()) {
            throw "Staged Desktop package tree failed integrity validation. Prepare the update again."
        }
        $stampData = Get-Content -Raw -LiteralPath $stageStamp | ConvertFrom-Json
        if ($stampData.sourceRevision -ne $script:StageData.targetSha -or -not $stampData.contentHash) {
            throw "Staged Desktop build stamp does not match the target revision."
        }

        $script:StageLiveDir = Join-Path $InstallRoot "apps\desktop\release\win-unpacked"
        $script:StageBackupDir = Join-Path $InstallRoot ("apps\desktop\release\win-unpacked.pre-stage-{0}" -f $PID)
        $script:StageBuildStampBackup = "$($script:StageBuildStamp).pre-stage-$PID"
        if (-not (Test-Path -LiteralPath $script:StageLiveDir)) {
            throw "The current Desktop package is missing, so staged apply has no rollback target. Use the normal Desktop build once, then prepare again."
        }
        if (Test-Path -LiteralPath $script:StageBackupDir) {
            Remove-Item -LiteralPath $script:StageBackupDir -Recurse -Force -ErrorAction Stop
        }
        if (Test-Path -LiteralPath $script:StageBuildStampBackup) {
            Remove-Item -LiteralPath $script:StageBuildStampBackup -Force -ErrorAction Stop
        }
        if (Test-Path -LiteralPath $script:StageBuildStamp) {
            Copy-Item -LiteralPath $script:StageBuildStamp -Destination $script:StageBuildStampBackup -Force -ErrorAction Stop
            $script:StageHadBuildStamp = $true
        }
        try {
            Set-UpdateStatus "Activating the prepared Desktop package..."
            Move-Item -LiteralPath $script:StageLiveDir -Destination $script:StageBackupDir -ErrorAction Stop
            $script:StageSwapped = $true
            Move-Item -LiteralPath $artifactDir -Destination $script:StageLiveDir -ErrorAction Stop
            Copy-Item -LiteralPath $stageStamp -Destination $script:StageBuildStamp -Force -ErrorAction Stop
            Write-HandoffLog "adopted staged Desktop package for $($script:StageData.targetSha.Substring(0, 10))"
        } catch {
            Restore-StagedRelease
            throw
        }
    }

    # -- 4. Run the update from the CURRENT checkout ------------------------
    # --force skips only the hermes.exe shim guard, which step 2 just PROVED
    # is unlocked; the venv-python holder guard (orphan reap included) stays
    # active. Our marker claim is adopted by the child via update_lock.py's
    # process-ancestry rule.
    $hermesExe = Join-Path $InstallRoot "venv\Scripts\hermes.exe"
    if (-not (Test-Path -LiteralPath $hermesExe)) {
        $finalCode = 3
        $finalMsg = "Update aborted: $hermesExe is missing. The install needs repair (run the Hermes installer or `hermes doctor`)."
        Write-HandoffLog $finalMsg
        exit $finalCode
    }
    $updateArgs = @("update", "--yes", "--gateway", "--force", "--branch", $Branch)
    if ($script:StageData -and $script:StageData.targetSha) {
        $updateArgs += @("--target-sha", "$($script:StageData.targetSha)")
    }
    Set-UpdateStatus "Installing Hermes code and dependencies..."
    Write-HandoffLog ("running: hermes " + ($updateArgs -join " "))
    $res = Invoke-StreamedHermes $hermesExe $updateArgs "update"
    Write-HandoffLog "hermes update exit code: $($res.Code)"

    if ($res.Code -ne 0 -and $res.Code -ne 2) {
        # One retry for the update-boundary class (fresh code on disk, stale
        # code in memory). Exit 2 ("close all Hermes windows") is not retryable.
        Set-UpdateStatus "Retrying with the refreshed updater..."
        Write-HandoffLog "first attempt failed; retrying once (freshly pulled fix loads on the second run)"
        $res = Invoke-StreamedHermes $hermesExe $updateArgs "update"
        Write-HandoffLog "retry exit code: $($res.Code)"
    }

    # -- 5. Truthful completion: don't trust exit 0 -------------------------
    # `hermes update` treats a Desktop GUI build failure as NON-fatal (prints
    # a one-line warning, exits 0). For a Desktop-DRIVEN update that warning
    # is fatal: we would relaunch the old exe and call it success. Detect it,
    # retry the build once, and propagate honestly.
    $desktopBuildFailed = $false
    Set-UpdateStatus "Verifying the Desktop build..."
    if ($res.Code -eq 0 -and $res.Output -match "Desktop build failed") {
        Set-UpdateStatus "Repairing the Desktop build..."
        Write-HandoffLog "hermes update reported a desktop build failure (non-fatal there, fatal here); retrying build"
        $rebuild = Invoke-StreamedHermes $hermesExe @("desktop", "--force-build", "--build-only") "rebuild"
        Write-HandoffLog "desktop rebuild exit code: $($rebuild.Code)"
        if ($rebuild.Code -ne 0) { $desktopBuildFailed = $true }
    }

    if ($res.Code -eq 0 -and -not $desktopBuildFailed) {
        $finalCode = 0
        $finalMsg = "Update complete."
        Set-UpdateStatus "Update complete. Restarting Hermes..."
    } elseif ($desktopBuildFailed) {
        $finalCode = 6
        $finalMsg = "Code and dependencies updated, but the Desktop app REBUILD FAILED - you are running the previous build. Run `hermes desktop --force-build` from a terminal to retry."
    } else {
        $finalCode = $res.Code
        $finalMsg = "hermes update failed (exit $($res.Code)). See logs\desktop-update-handoff.log."
    }
    if ($finalCode -ne 0) { Set-UpdateStatus "The update could not finish. Restoring Hermes..." }
    exit $finalCode
} finally {
    if ($finalCode -ne 0) {
        Restore-StagedRelease
    } elseif ($script:StageSwapped) {
        # The normal updater accepted the pinned target and the staged build
        # stamp, so the package is now authoritative. Remove rollback only
        # after truthful completion has been established.
        Remove-Item -LiteralPath $script:StageBackupDir -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $script:StageBuildStampBackup -Force -ErrorAction SilentlyContinue
        try {
            if ($script:StageData.worktree) {
                & git -C $InstallRoot worktree remove --force $script:StageData.worktree 2>&1 | ForEach-Object {
                    if ("$_".Trim()) { Write-HandoffLog "stage-cleanup| $_" }
                }
                & git -C $InstallRoot worktree prune 2>&1 | Out-Null
            }
        } catch {
            Write-HandoffLog "WARNING: staged worktree cleanup failed: $($_.Exception.Message)"
        }
        Remove-Item -LiteralPath $StageManifest -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $script:StageData.buildStampPath -Force -ErrorAction SilentlyContinue
        $consumedStageRoot = Split-Path -Parent $StageManifest
        Remove-Item -LiteralPath (Join-Path $consumedStageRoot "progress.json") -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath (Join-Path $consumedStageRoot "stage-result.json") -Force -ErrorAction SilentlyContinue
        $script:StageSwapped = $false
        Write-HandoffLog "consumed staged update"
    }
    Write-Result ($finalCode -eq 0) $finalCode $finalMsg
    Remove-MarkerIfOwned
    Close-ProgressWindow
    Start-DesktopRelaunch
}
