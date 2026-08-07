param(
    [switch]$SkipFrontend,
    [switch]$SkipBackend,
    [switch]$SkipDesktopInstall,
    [switch]$Installer
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Frontend = Join-Path $Root "frontend"
$Desktop = Join-Path $Root "desktop"
$Backend = Join-Path $Root "backend"
$BackendBuild = Join-Path $Desktop "build\backend"

function Require-Command {
    param([string]$Name, [string]$Hint)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was not found on PATH. $Hint"
    }
}

function Run-Step {
    param([string]$Label, [scriptblock]$Step)
    Write-Host "`n==> $Label" -ForegroundColor Cyan
    & $Step
}

Require-Command "node" "Install Node.js 20 or newer."
Require-Command "npm" "Install Node.js 20 or newer."
Require-Command "python" "Install Python 3.10 or newer."

if (-not $SkipFrontend) {
    Run-Step "Installing frontend dependencies" {
        Push-Location $Frontend
        try { npm ci } finally { Pop-Location }
    }
    Run-Step "Building frontend" {
        Push-Location $Frontend
        try { npm run build } finally { Pop-Location }
    }
}

if (-not (Test-Path (Join-Path $Frontend "dist\index.html"))) {
    throw "frontend/dist/index.html is missing. Build the frontend first."
}

if (-not $SkipBackend) {
    python -c "import PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller is missing. Install it with: python -m pip install pyinstaller"
    }
    New-Item -ItemType Directory -Force -Path $BackendBuild | Out-Null
    Run-Step "Bundling Python backend" {
        Push-Location $Root
        try {
            python -m PyInstaller --noconfirm --clean `
                --distpath $BackendBuild `
                --workpath (Join-Path $Desktop "build\pyinstaller") `
                (Join-Path $Backend "ols_backend.spec")
        } finally { Pop-Location }
    }
}

if (-not (Test-Path (Join-Path $BackendBuild "ols-backend.exe"))) {
    throw "Backend bundle is missing: $BackendBuild\ols-backend.exe"
}

if (-not $SkipDesktopInstall) {
    Run-Step "Installing desktop packaging dependencies" {
        Push-Location $Desktop
        try { npm ci } finally { Pop-Location }
    }
}

Run-Step "Creating Windows package" {
    Push-Location $Desktop
    try {
        if ($Installer) { npm run dist:installer }
        else { npm run dist }
    } finally { Pop-Location }
}

Write-Host "`nPackage created under $Desktop\dist" -ForegroundColor Green
