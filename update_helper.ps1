param(
    [Parameter(Mandatory = $true)]
    [int]$ParentPid,
    [Parameter(Mandatory = $true)]
    [string]$InstallDir,
    [Parameter(Mandatory = $true)]
    [string]$StagingDir,
    [Parameter(Mandatory = $true)]
    [string]$BackupDir,
    [Parameter(Mandatory = $true)]
    [string]$ExeName,
    [string]$RestartArgs = ""
)

Set-Location $env:TEMP
$logPath = Join-Path $env:TEMP "AccessibleIPTVClient_update.log"

function Write-Log {
    param([string]$Message)
    $stamp = (Get-Date).ToString("o")
    Add-Content -Path $logPath -Value "$stamp $Message"
}

Write-Log "Updater started. Waiting for PID $ParentPid."

$deadline = (Get-Date).AddMinutes(5)
while ((Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
}

if (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) {
    Write-Log "Process $ParentPid did not exit within timeout."
}

if (-not (Test-Path -LiteralPath $StagingDir)) {
    Write-Log "Staging directory missing: $StagingDir"
    exit 1
}

$parentDir = Split-Path -Parent $InstallDir
if ($parentDir -and -not (Test-Path -LiteralPath $parentDir)) {
    New-Item -ItemType Directory -Path $parentDir | Out-Null
}

if (Test-Path -LiteralPath $BackupDir) {
    Remove-Item -LiteralPath $BackupDir -Recurse -Force
}

try {
    if (Test-Path -LiteralPath $InstallDir) {
        Move-Item -LiteralPath $InstallDir -Destination $BackupDir -Force
        Write-Log "Moved current install to backup: $BackupDir"
    }
} catch {
    Write-Log "Failed to move install to backup: $($_.Exception.Message)"
    exit 1
}

try {
    Move-Item -LiteralPath $StagingDir -Destination $InstallDir -Force
    Write-Log "Installed update to $InstallDir"

    # Restore configuration if it existed
    $oldConfig = Join-Path $BackupDir "iptvclient.conf"
    $newConfig = Join-Path $InstallDir "iptvclient.conf"
    if (Test-Path -LiteralPath $oldConfig) {
        try {
            Copy-Item -LiteralPath $oldConfig -Destination $newConfig -Force
            Write-Log "Restored configuration from backup."
        } catch {
            Write-Log "Failed to restore configuration: $($_.Exception.Message)"
        }
    }
} catch {
    Write-Log "Failed to move staging into place: $($_.Exception.Message)"
    if (Test-Path -LiteralPath $BackupDir -and -not (Test-Path -LiteralPath $InstallDir)) {
        try {
            Move-Item -LiteralPath $BackupDir -Destination $InstallDir -Force
            Write-Log "Rollback completed."
        } catch {
            Write-Log "Rollback failed: $($_.Exception.Message)"
        }
    }
    exit 1
}

$exePath = Join-Path $InstallDir $ExeName
if (Test-Path -LiteralPath $exePath) {
    Write-Log "Restarting app: $exePath"
    if ($RestartArgs) {
        Start-Process -FilePath $exePath -WorkingDirectory $InstallDir -ArgumentList $RestartArgs
    } else {
        Start-Process -FilePath $exePath -WorkingDirectory $InstallDir
    }
} else {
    Write-Log "Executable not found after update: $exePath"
    exit 1
}

Write-Log "Updater completed."
exit 0
