# desktop-stage-update.ps1 -- prepare a Desktop update without touching the live install.
#
# The running Electron app launches this worker in the background. It fetches an
# exact deploy target, creates an isolated worktree under HERMES_HOME, installs
# build dependencies there, packages Desktop, and atomically publishes a stage
# manifest. The live checkout, venv, release directory, and build stamp are never
# mutated here. scripts/desktop-update.ps1 is the only consumer of a ready stage.

param(
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [string]$Branch = "main",
    [string]$BaseSha = "",
    [string]$TargetSha = "",
    [string]$StageRoot = "",
    [string]$PythonExe = "",
    [string]$NpmExe = ""
)

$ErrorActionPreference = "Stop"
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

$HermesHome = Split-Path -Parent $InstallRoot
$ExpectedStageRoot = Join-Path $HermesHome "update-stage\desktop"
if ($StageRoot -and -not [string]::Equals(
    [System.IO.Path]::GetFullPath($StageRoot),
    [System.IO.Path]::GetFullPath($ExpectedStageRoot),
    [System.StringComparison]::OrdinalIgnoreCase
)) { throw "StageRoot must be the Hermes-owned Desktop stage directory." }
$StageRoot = $ExpectedStageRoot
$Worktree = Join-Path $StageRoot "worktree"
$LockDir = Join-Path $StageRoot ".prepare-lock"
$ManifestPath = Join-Path $StageRoot "stage.json"
$ResultPath = Join-Path $StageRoot "stage-result.json"
$ProgressPath = Join-Path $StageRoot "progress.json"
$ContentHashScriptPath = Join-Path $StageRoot "compute-content-hash.py"
$LogDir = Join-Path $HermesHome "logs"
$LogPath = Join-Path $LogDir "desktop-update-stage.log"

function Write-StageLog([string]$Message) {
    $line = "{0:yyyy-MM-ddTHH:mm:ssK} {1}" -f (Get-Date), $Message
    try { Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8 } catch {}
    Write-Host $line
}

function Write-JsonAtomic([string]$Path, $Value) {
    $temporary = "$Path.tmp-$PID"
    $json = $Value | ConvertTo-Json -Depth 12
    [System.IO.File]::WriteAllText($temporary, $json, (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Write-Progress([string]$Phase, [int]$Percent, [string]$Message) {
    Write-StageLog ("{0}: {1}" -f $Phase, $Message)
    Write-JsonAtomic $ProgressPath @{
        schema = 1
        phase = $Phase
        percent = $Percent
        message = $Message
        updatedAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    }
}

function Invoke-Checked([string]$Exe, [string[]]$Arguments, [string]$Label) {
    Write-StageLog ("running {0}: {1} {2}" -f $Label, $Exe, ($Arguments -join " "))
    # Native tools routinely use stderr for successful progress (notably
    # `git fetch`). Under PowerShell 7, the script-level Stop preference can
    # promote that native stderr into a terminating ErrorRecord before we can
    # inspect $LASTEXITCODE. Native success is defined by its exit code, so
    # temporarily keep stderr collectable and restore both preferences after.
    $previousErrorActionPreference = $ErrorActionPreference
    $hasNativeErrorPreference = Test-Path Variable:\PSNativeCommandUseErrorActionPreference
    if ($hasNativeErrorPreference) {
        $previousNativeErrorPreference = $PSNativeCommandUseErrorActionPreference
    }
    try {
        $ErrorActionPreference = "Continue"
        if ($hasNativeErrorPreference) {
            $PSNativeCommandUseErrorActionPreference = $false
        }
        $output = & $Exe @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
        if ($hasNativeErrorPreference) {
            $PSNativeCommandUseErrorActionPreference = $previousNativeErrorPreference
        }
    }
    foreach ($line in $output) {
        if ("$line".Trim()) { Write-StageLog ("{0}| {1}" -f $Label, $line) }
    }
    if ($code -ne 0) {
        throw "$Label failed with exit code $code"
    }
    return @($output)
}

function Resolve-CommandPath([string]$Explicit, [string[]]$Names) {
    if ($Explicit) {
        if (-not (Test-Path -LiteralPath $Explicit)) { throw "Command does not exist: $Explicit" }
        return (Resolve-Path -LiteralPath $Explicit).Path
    }
    foreach ($name in $Names) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { return $command.Source }
    }
    throw "Required command not found: $($Names -join ', ')"
}

function Get-LastOutputLine([object]$Output) {
    $lines = @($Output)
    if ($lines.Count -eq 0) { return "" }
    return $lines[$lines.Count - 1].ToString().Trim()
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
        $file = $item
        $relative = $file.FullName.Substring($rootPath.Length).TrimStart('\').Replace('\', '/')
        $fileHash = Get-FileSha256 $file.FullName
        $records.Add("$relative`0$($file.Length)`0$fileHash")
    }
    $ordered = $records.ToArray()
    [Array]::Sort($ordered, [System.StringComparer]::Ordinal)
    $payload = [System.Text.Encoding]::UTF8.GetBytes(($ordered -join "`n"))
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($payload))).Replace("-", "").ToLowerInvariant() } finally { $sha.Dispose() }
}

function Get-DirtyContentFingerprint([string]$Git, [string]$Root) {
    $paths = New-Object 'System.Collections.Generic.SortedSet[string]' ([System.StringComparer]::Ordinal)
    foreach ($line in Invoke-Checked $Git @("-c", "core.quotepath=false", "-C", $Root, "diff", "--name-only", "HEAD", "--") "git-dirty-tracked") {
        if ("$line") { [void]$paths.Add("$line") }
    }
    foreach ($line in Invoke-Checked $Git @("-c", "core.quotepath=false", "-C", $Root, "ls-files", "--others", "--exclude-standard") "git-dirty-untracked") {
        if ("$line") { [void]$paths.Add("$line") }
    }
    $summary = ((Invoke-Checked $Git @("-c", "core.quotepath=false", "-C", $Root, "diff", "--summary", "HEAD", "--") "git-dirty-summary") -join "`n").Trim()
    $records = New-Object System.Collections.Generic.List[string]
    foreach ($relative in $paths) {
        $candidate = Join-Path $Root $relative
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $blob = Get-LastOutputLine (Invoke-Checked $Git @("-C", $Root, "hash-object", "--no-filters", "--", $relative) "git-dirty-hash")
        } else {
            $blob = "deleted"
        }
        $records.Add("$relative`0$blob")
    }
    $payload = [System.Text.Encoding]::UTF8.GetBytes("$summary`n$($records -join "`n")")
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($payload))).Replace("-", "").ToLowerInvariant() } finally { $sha.Dispose() }
}

$ok = $false
$lockOwned = $false
$lockToken = [Guid]::NewGuid().ToString("N")
try {
    New-Item -ItemType Directory -Path $StageRoot, $LogDir -Force | Out-Null

    # Directory creation is the cross-process lock. Recover only when its owner
    # no longer exists; a second live preparer must fail rather than corrupt the
    # shared worktree.
    if (Test-Path -LiteralPath $LockDir) {
        $ownerPath = Join-Path $LockDir "owner.json"
        $ownerPid = 0
        try { $ownerPid = [int]((Get-Content -Raw -LiteralPath $ownerPath | ConvertFrom-Json).pid) } catch {}
        if ($ownerPid -gt 0 -and (Get-Process -Id $ownerPid -ErrorAction SilentlyContinue)) {
            throw "Desktop update preparation is already running (pid $ownerPid)."
        }
        $staleLock = "$LockDir.stale-$lockToken"
        Move-Item -LiteralPath $LockDir -Destination $staleLock -ErrorAction Stop
        Remove-Item -LiteralPath $staleLock -Recurse -Force -ErrorAction Stop
    }
    New-Item -ItemType Directory -Path $LockDir -ErrorAction Stop | Out-Null
    $lockOwned = $true
    Write-JsonAtomic (Join-Path $LockDir "owner.json") @{
        pid = $PID
        token = $lockToken
        startedAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        targetSha = $TargetSha
    }
    Remove-Item -LiteralPath $ResultPath -Force -ErrorAction SilentlyContinue

    $git = Resolve-CommandPath "" @("git.exe", "git")
    $npm = Resolve-CommandPath $NpmExe @("npm.cmd", "npm.exe", "npm")
    if (-not $PythonExe) {
        $PythonExe = Join-Path $InstallRoot "venv\Scripts\python.exe"
    }
    $python = Resolve-CommandPath $PythonExe @()

    Write-Progress "fetching" 5 "Fetching origin/$Branch while Desktop remains available"
    Invoke-Checked $git @("-C", $InstallRoot, "fetch", "origin", $Branch, "--prune") "git-fetch" | Out-Null
    $resolvedTargetSha = Get-LastOutputLine (Invoke-Checked $git @("-C", $InstallRoot, "rev-parse", "refs/remotes/origin/$Branch") "git-target")
    $resolvedBaseSha = Get-LastOutputLine (Invoke-Checked $git @("-C", $InstallRoot, "rev-parse", "HEAD") "git-base")
    if ($resolvedTargetSha -notmatch "^[0-9a-fA-F]{40}$") { throw "origin/$Branch did not resolve to a commit" }
    if ($resolvedBaseSha -notmatch "^[0-9a-fA-F]{40}$") { throw "live HEAD did not resolve to a commit" }
    if ($TargetSha -and $TargetSha -ne $resolvedTargetSha) { throw "origin/$Branch moved before preparation started. Refresh and prepare again." }
    if ($BaseSha -and $BaseSha -ne $resolvedBaseSha) { throw "The live checkout changed before preparation started. Refresh and prepare again." }
    $TargetSha = $resolvedTargetSha
    $BaseSha = $resolvedBaseSha

    $dirtyText = ((Invoke-Checked $git @("-C", $InstallRoot, "status", "--porcelain=v1", "--untracked-files=all") "git-status") -join "`n").Trim()
    $dirtyFingerprint = Get-DirtyContentFingerprint $git $InstallRoot

    Write-Progress "worktree" 15 "Creating isolated target worktree"
    if (Test-Path -LiteralPath $Worktree) {
        try { Invoke-Checked $git @("-C", $InstallRoot, "worktree", "remove", "--force", $Worktree) "worktree-remove" | Out-Null } catch {
            Remove-Item -LiteralPath $Worktree -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    Invoke-Checked $git @("-C", $InstallRoot, "worktree", "prune") "worktree-prune" | Out-Null
    Invoke-Checked $git @("-C", $InstallRoot, "worktree", "add", "--detach", $Worktree, $targetSha) "worktree-add" | Out-Null

    Write-Progress "dependencies" 25 "Installing target build dependencies in the isolated worktree"
    Push-Location $Worktree
    try {
        Invoke-Checked $npm @("ci", "--include=dev", "--no-audit", "--no-fund") "npm-ci" | Out-Null
    } finally { Pop-Location }

    $DesktopDir = Join-Path $Worktree "apps\desktop"
    Write-Progress "building" 55 "Packaging the target Desktop application"
    Push-Location $DesktopDir
    try {
        Invoke-Checked $npm @("run", "pack") "desktop-pack" | Out-Null
    } finally { Pop-Location }

    $ArtifactDir = Join-Path $DesktopDir "release\win-unpacked"
    $ArtifactExe = Join-Path $ArtifactDir "Hermes.exe"
    if (-not (Test-Path -LiteralPath $ArtifactExe)) { throw "Packaged Desktop executable is missing: $ArtifactExe" }

    Write-Progress "verifying" 88 "Hashing the complete packaged application and target source"
    $artifactHash = Get-FileSha256 $ArtifactExe
    $artifactTreeHash = Get-ArtifactTreeHash $ArtifactDir
    $env:HERMES_STAGE_PROJECT_ROOT = $Worktree
    $env:HERMES_STAGE_INSTALL_ROOT = $InstallRoot
    $hashCode = @'
import os, sys
from pathlib import Path
sys.path.insert(0, os.environ["HERMES_STAGE_INSTALL_ROOT"])
from hermes_cli.main import _compute_desktop_content_hash
print(_compute_desktop_content_hash(Path(os.environ["HERMES_STAGE_PROJECT_ROOT"])))
'@
    [System.IO.File]::WriteAllText(
        $ContentHashScriptPath,
        $hashCode,
        (New-Object System.Text.UTF8Encoding($false))
    )
    $contentHash = Get-LastOutputLine (Invoke-Checked $python @($ContentHashScriptPath) "content-hash")
    if ($contentHash -notmatch "^[0-9a-fA-F]{64}$") { throw "Desktop content hash was invalid" }

    $buildStampPath = Join-Path $StageRoot "desktop-build-stamp.json"
    Write-JsonAtomic $buildStampPath @{
        contentHash = $contentHash
        sourceMode = $false
        builtAt = (Get-Date).ToUniversalTime().ToString("o")
        sourceRevision = $targetSha
        staged = $true
    }

    $manifest = @{
        schemaVersion = 1
        branch = $Branch
        installRoot = (Resolve-Path -LiteralPath $InstallRoot).Path
        baseSha = $baseSha
        targetSha = $targetSha
        liveDirty = [bool]$dirtyText
        liveDirtyFingerprint = $dirtyFingerprint
        stageRoot = $StageRoot
        worktree = $Worktree
        artifactDir = $ArtifactDir
        artifactPath = $ArtifactExe
        artifactSha256 = $artifactHash
        artifactTreeSha256 = $artifactTreeHash
        buildStampPath = $buildStampPath
        logPath = $LogPath
        createdAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    }
    Write-JsonAtomic $ManifestPath $manifest
    Write-Progress "ready" 100 "Update prepared. Restart Hermes to finish applying $($targetSha.Substring(0, 10))."
    Write-JsonAtomic $ResultPath @{
        schema = 1
        ok = $true
        phase = "ready"
        message = "Update prepared and ready to restart."
        targetSha = $targetSha
        manifestPath = $ManifestPath
        finishedAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    }
    $ok = $true
    exit 0
} catch {
    $message = $_.Exception.Message
    Write-StageLog "FAILED: $message"
    try {
        Write-Progress "failed" 100 $message
        Write-JsonAtomic $ResultPath @{
            schema = 1
            ok = $false
            phase = "failed"
            message = $message
            targetSha = $targetSha
            finishedAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        }
    } catch {}
    exit 1
} finally {
    if ($lockOwned) {
        try {
            $owner = Get-Content -Raw -LiteralPath (Join-Path $LockDir "owner.json") | ConvertFrom-Json
            if ($owner.token -eq $lockToken) {
                Remove-Item -LiteralPath $LockDir -Recurse -Force -ErrorAction Stop
            }
        } catch {
            Write-StageLog "WARNING: could not release preparation lock: $($_.Exception.Message)"
        }
    }
    Remove-Item Env:\HERMES_STAGE_PROJECT_ROOT -ErrorAction SilentlyContinue
    Remove-Item Env:\HERMES_STAGE_INSTALL_ROOT -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $ContentHashScriptPath -Force -ErrorAction SilentlyContinue
    if (-not $ok) {
        Remove-Item -LiteralPath $ManifestPath -Force -ErrorAction SilentlyContinue
    }
}
