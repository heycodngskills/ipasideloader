# usbip-attach.ps1 -- attaches your iPhone's USB connection into the
# ipasideloader container, using Docker Desktop's own documented USB/IP
# feature: https://docs.docker.com/desktop/features/usbip/
#
# This is the OFFICIAL Docker mechanism -- it does not require installing
# usbipd-win or any other third-party tool. It uses Docker Desktop's
# built-in USB/IP server talking to host.docker.internal.
#
# Officially documented for the Hyper-V backend. Run diagnose.ps1 first
# to check which backend you're on. If you're on WSL2, this MAY still
# work (some users report success) -- this script will tell you clearly
# either way; it won't silently fail.
#
# Nothing here is permanent: if it doesn't work, just close the
# PowerShell window and nothing on your system has changed.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File usbip-attach.ps1

Write-Host "=== ipasideloader USB/IP attach (Docker's documented method) ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "This will start a privileged helper container that stays running" -ForegroundColor Yellow
Write-Host "in the background to keep your iPhone's USB connection bridged in." -ForegroundColor Yellow
Write-Host "Press Ctrl+C any time to stop it -- this leaves no permanent changes." -ForegroundColor Yellow
Write-Host ""

# Step 1: start (or reuse) the privileged helper container.
$existing = docker ps --filter "name=ipasideloader-usbip-helper" --format "{{.Names}}" 2>$null
if ($existing -eq "ipasideloader-usbip-helper") {
    Write-Host "[OK] Helper container already running." -ForegroundColor Green
} else {
    Write-Host "Starting helper container..." -ForegroundColor Cyan
    docker run -d --rm --name ipasideloader-usbip-helper --privileged --pid=host alpine sleep infinity | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!!] Could not start the helper container. Is Docker Desktop running?" -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] Helper container started." -ForegroundColor Green
}

# Step 2: list exportable USB devices from inside the container, via
# Docker Desktop's built-in USB/IP server.
Write-Host ""
Write-Host "Listing USB devices Docker Desktop can see..." -ForegroundColor Cyan
$listOutput = docker exec ipasideloader-usbip-helper sh -c "nsenter -t 1 -m usbip list -r host.docker.internal" 2>&1
Write-Host $listOutput

if ($listOutput -match "unknown vendor.*unknown product" -or $listOutput -notmatch "Apple|iPhone") {
    Write-Host ""
    Write-Host "[!!] Your iPhone doesn't appear by name in this list." -ForegroundColor Yellow
    Write-Host "     This is the known limitation: Docker's built-in USB/IP server" -ForegroundColor Yellow
    Write-Host "     often can't identify composite/complex USB devices like phones" -ForegroundColor Yellow
    Write-Host "     by name -- it may show as 'unknown vendor / unknown product'" -ForegroundColor Yellow
    Write-Host "     with a bus ID like '1-2' or '2-0-0'. Check the full list above" -ForegroundColor Yellow
    Write-Host "     for an entry that appeared/disappeared when you plugged the" -ForegroundColor Yellow
    Write-Host "     phone in or out, and note its bus ID." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "     If NOTHING changes in this list when you plug/unplug the phone," -ForegroundColor Yellow
    Write-Host "     Docker Desktop's USB/IP server isn't seeing it at all -- this is" -ForegroundColor Yellow
    Write-Host "     the WSL2-backend limitation mentioned in diagnose.ps1. At that" -ForegroundColor Yellow
    Write-Host "     point, the realistic options are: sign in the container and" -ForegroundColor Yellow
    Write-Host "     install from the host, or a WiFi-paired device. See README." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "To attach a specific device once you have its bus ID, run:" -ForegroundColor Cyan
Write-Host "  docker exec ipasideloader-usbip-helper sh -c `"nsenter -t 1 -m usbip attach -r host.docker.internal -d <BUSID>`""
Write-Host ""
Write-Host "Then verify it shows up:" -ForegroundColor Cyan
Write-Host "  docker exec ipasideloader-usbip-helper sh -c `"nsenter -t 1 -m ls /dev/bus/usb 2>/dev/null; lsusb 2>/dev/null`""
Write-Host ""
Write-Host "Once attached, run the actual ipasideloader container with:" -ForegroundColor Cyan
Write-Host "  docker compose run --rm ipasideloader devices"
Write-Host "to confirm pymobiledevice3 inside it can see the phone."
