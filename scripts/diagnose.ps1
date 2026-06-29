# diagnose.ps1 -- read-only diagnostic, changes nothing on your system.
#
# Run this in a normal (non-admin) PowerShell window:
#   powershell -ExecutionPolicy Bypass -File diagnose.ps1
#
# It tells you which of the two USB passthrough paths applies to your
# Docker Desktop setup, so you don't have to guess or change any settings.

Write-Host "=== ipasideloader Windows USB diagnostic (read-only) ===" -ForegroundColor Cyan
Write-Host ""

# 1. Is Docker Desktop running at all?
$dockerRunning = $null -ne (Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue)
if ($dockerRunning) {
    Write-Host "[OK] Docker Desktop process is running." -ForegroundColor Green
} else {
    Write-Host "[!!] Docker Desktop doesn't appear to be running. Start it first." -ForegroundColor Yellow
}

# 2. Which backend is Docker Desktop using? (WSL2 vs Hyper-V)
# Docker Desktop's settings are stored in a JSON file under %APPDATA%.
# Verified structure (current Docker Desktop): the flag lives nested at
# .linuxVM.wslEngineEnabled.value -- true means WSL2, false means Hyper-V.
$settingsPath = "$env:APPDATA\Docker\settings-store.json"
$legacySettingsPath = "$env:APPDATA\Docker\settings.json"
$backend = "unknown"

foreach ($path in @($settingsPath, $legacySettingsPath)) {
    if (Test-Path $path) {
        try {
            $json = Get-Content $path -Raw | ConvertFrom-Json
            # Current format: nested under linuxVM.wslEngineEnabled.value
            if ($null -ne $json.linuxVM.wslEngineEnabled.value) {
                $backend = if ($json.linuxVM.wslEngineEnabled.value) { "WSL2" } else { "Hyper-V" }
                break
            }
            # Older format (Docker Desktop 4.34 and earlier): flat field
            if ($null -ne $json.wslEngineEnabled) {
                $backend = if ($json.wslEngineEnabled) { "WSL2" } else { "Hyper-V" }
                break
            }
        } catch {
            # File exists but couldn't be parsed -- not fatal, just inconclusive.
        }
    }
}

Write-Host ""
if ($backend -eq "WSL2") {
    Write-Host "[INFO] Docker Desktop backend: WSL2" -ForegroundColor Yellow
    Write-Host "       Docker's own USB/IP docs only officially confirm support for" -ForegroundColor Yellow
    Write-Host "       the Hyper-V backend. It MAY still work on WSL2 (some users" -ForegroundColor Yellow
    Write-Host "       report success), but it isn't guaranteed -- see usbip-attach.ps1" -ForegroundColor Yellow
    Write-Host "       to test it without changing anything else." -ForegroundColor Yellow
} elseif ($backend -eq "Hyper-V") {
    Write-Host "[OK] Docker Desktop backend: Hyper-V" -ForegroundColor Green
    Write-Host "     This is the officially documented configuration for USB/IP passthrough." -ForegroundColor Green
} else {
    Write-Host "[??] Could not determine backend automatically." -ForegroundColor Yellow
    Write-Host "     Check manually: Docker Desktop -> Settings -> General ->" -ForegroundColor Yellow
    Write-Host "     'Use the WSL 2 based engine' (checked = WSL2, unchecked = Hyper-V)." -ForegroundColor Yellow
}

# 3. Confirm the iPhone is visible to Windows at all (read-only WMI query).
Write-Host ""
Write-Host "Looking for an Apple device over USB..." -ForegroundColor Cyan
$appleDevices = Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object {
    $_.FriendlyName -like "*Apple*" -or $_.FriendlyName -like "*iPhone*"
}
if ($appleDevices) {
    foreach ($dev in $appleDevices) {
        $statusColor = if ($dev.Status -eq "OK") { "Green" } else { "Red" }
        Write-Host "  $($dev.FriendlyName) -- Status: $($dev.Status)" -ForegroundColor $statusColor
    }
} else {
    Write-Host "  No Apple device found. Plug it in, unlock it, and tap Trust if prompted." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== Next step ===" -ForegroundColor Cyan
Write-Host "Run usbip-attach.ps1 to actually test passthrough into the container."
Write-Host "It also makes no permanent changes -- it only runs commands inside a"
Write-Host "throwaway container to test connectivity, then reports what happened."
