# GitUp — One-click build script for Windows
# Run from the gitup/ folder:  .\setup_and_build.ps1

Write-Host "`n=== GitUp Setup & Build ===" -ForegroundColor Cyan

# Check Python
try {
    $pyver = python --version 2>&1
    Write-Host "  Python: $pyver" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Python not found. Install from https://python.org" -ForegroundColor Red
    exit 1
}

# Check Git
try {
    $gitver = git --version 2>&1
    Write-Host "  Git:    $gitver" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Git not found. Install from https://git-scm.com" -ForegroundColor Red
    exit 1
}

# Install deps
Write-Host "`n  Installing Python dependencies..." -ForegroundColor Yellow
pip install pyinstaller requests --quiet

# Build
Write-Host "`n  Building gitup.exe..." -ForegroundColor Yellow
python build.py

if (Test-Path "dist\gitup.exe") {
    Write-Host "`n  SUCCESS! -> dist\gitup.exe" -ForegroundColor Green
    Write-Host "  Copy gitup.exe anywhere and run it — no install needed.`n"
    
    # Optionally open the dist folder
    $open = Read-Host "  Open dist folder? (y/n)"
    if ($open -eq 'y') { explorer dist }
} else {
    Write-Host "`n  Build may have failed — check output above." -ForegroundColor Red
}
